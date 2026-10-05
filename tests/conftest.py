"""Independent synthetic Relay/desktop peer; never connects to a real account.

The peer implements only the existing wire contract needed to exercise the SDK.
It does not claim to verify desktop persistence, model execution or authorization.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestServer
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from moldable_gadget.protocol import Json, Object


def enc(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def dec(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def expiry(minutes: int) -> str:
    return (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()


class Peer:
    def __init__(self) -> None:
        self.key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
        self.public = enc(
            self.key.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
        )
        self.room_key = bytes(reversed(range(32)))
        self.keys = {
            direction: AESGCM(
                HKDF(
                    algorithm=hashes.SHA256(),
                    length=32,
                    salt=hashlib.sha256(b"moldable-relay-e2ee-v1").digest(),
                    info=f"desktop:desktop-test:{direction}".encode(),
                ).derive(self.room_key)
            )
            for direction in ("controller-to-desktop", "desktop-to-controller")
        }
        self.origin = ""
        self.claims: list[Object] = []
        self.refreshes: list[Object] = []
        self.requests: list[tuple[Object, Object]] = []
        self.socket: web.WebSocketResponse | None = None
        self.device: Object = {}
        self.token = "synthetic-access-0"
        self.refresh_token = "synthetic-refresh-0"
        self.session = "synthetic-session-0"
        self.access_expiry = expiry(60)
        self.claim_desktop_id = "desktop-test"
        self.refresh_error: str | None = None
        self.drop_next_request = False
        self.reverse_two = False
        self._held: list[Object] = []
        self.selected_workspace = "other-workspace"
        self.ws_connections = 0
        self.ws_rejections: list[int] = []
        self.conversation = False
        self.mutations: dict[str, Object] = {}
        self.app = web.Application()
        self.app.router.add_post("/v1/pairings/claim", self.claim)
        self.app.router.add_post("/v1/sessions/refresh", self.rotate)
        self.app.router.add_get("/v1/remote/connect", self.connect)

    def setup_link(self, **changes: Json) -> str:
        setup: Object = {
            "v": 2,
            "minProtocol": 2,
            "maxProtocol": 2,
            "pairingId": "synthetic-pairing",
            "secret": "synthetic-secret",
            "relayURL": self.origin,
            "expiresAt": expiry(5),
            "e2eeKey": enc(self.room_key),
            "desktop": {"id": "desktop-test", "name": "Test Mac", "publicKey": self.public},
        }
        setup.update(changes)
        return "moldable-remote://pair?setup=" + enc(json.dumps(setup).encode())

    def credentials(self) -> Object:
        return {
            "sessionId": self.session,
            "accessToken": self.token,
            "refreshToken": self.refresh_token,
            "expiresAt": self.access_expiry,
            "refreshExpiresAt": expiry(43_200),
            "websocketURL": self.origin.replace("http://", "ws://")
            + "/v1/remote/connect?desktopId=desktop-test",
        }

    async def claim(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.claims.append(body)
        assert set(body) == {"pairingId", "secret", "device"}
        assert body["pairingId"] == "synthetic-pairing" and body["secret"] == "synthetic-secret"
        self.device = body["device"]
        return web.json_response(
            {
                **self.credentials(),
                "desktop": {
                    "id": self.claim_desktop_id,
                    "name": "Test Mac",
                    "publicKey": self.public,
                },
            }
        )

    async def rotate(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.refreshes.append(body)
        fields = [
            "moldable-relay-refresh-v1",
            body["sessionId"],
            body["deviceId"],
            body["role"],
            body["refreshToken"],
            body["signedAt"],
            body["idempotencyKey"],
        ]
        Ed25519PublicKey.from_public_bytes(dec(self.device["publicKey"])).verify(
            dec(body["signature"]), "\n".join(fields).encode()
        )
        assert body["idempotencyKey"] == body["sessionId"]
        assert body["role"] == "controller"
        if self.refresh_error:
            return web.json_response({"code": self.refresh_error}, status=401)
        self.session = "synthetic-session-1"
        self.token = "synthetic-access-1"
        self.refresh_token = "synthetic-refresh-1"
        self.access_expiry = expiry(60)
        return web.json_response(self.credentials())

    def envelope(
        self, payload: Json, *, request_id: str | None = None, sequence: int | None = None
    ) -> Object:
        frame: Object = {"type": "res" if request_id else "event", "messageId": str(uuid.uuid4())}
        if request_id:
            frame["id"] = request_id
        else:
            frame["event"] = "remote.event"
        fields = [frame.get(key, "") for key in ("type", "id", "method", "event", "messageId")]
        nonce = os.urandom(12)
        aad = "\n".join(["moldable-relay-e2ee-v1", "desktop-to-controller", *fields]).encode()
        ciphertext = self.keys["desktop-to-controller"].encrypt(
            nonce, json.dumps(payload, separators=(",", ":")).encode(), aad
        )
        frame["encrypted"] = {
            "v": 1,
            "alg": "A256GCM",
            "nonce": enc(nonce),
            "ciphertext": enc(ciphertext),
        }
        frame["signature"] = enc(
            self.key.sign(
                "\n".join(
                    ["moldable-remote-envelope-signature-v1", *fields, enc(nonce), enc(ciphertext)]
                ).encode()
            )
        )
        if sequence is not None:
            frame["seq"] = sequence
        return frame

    async def connect(self, request: web.Request) -> web.StreamResponse:
        assert request.headers["Authorization"] == "Bearer " + self.token
        self.ws_connections += 1
        if self.ws_rejections:
            return web.json_response({"error": "unauthorized"}, status=self.ws_rejections.pop(0))
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        self.socket = socket
        await socket.send_json(
            {"type": "event", "event": "connect.challenge", "params": {"nonce": "test-nonce"}}
        )
        proof = await socket.receive_json()
        params = proof["params"]
        fields = [
            "moldable-relay-v2",
            "test-nonce",
            self.session,
            self.device["id"],
            "controller",
            "2",
            "2",
            params["signedAt"],
        ]
        Ed25519PublicKey.from_public_bytes(dec(self.device["publicKey"])).verify(
            dec(params["signature"]), "\n".join(fields).encode()
        )
        assert params["sessionId"] == self.session
        await socket.send_json({"type": "res", "id": proof["id"], "result": {"protocol": 2}})
        async for message in socket:
            if message.type != web.WSMsgType.TEXT:
                break
            frame = json.loads(message.data)
            fields = [frame.get(key, "") for key in ("type", "id", "method", "event", "messageId")]
            aad = "\n".join(["moldable-relay-e2ee-v1", "controller-to-desktop", *fields]).encode()
            encrypted = frame["encrypted"]
            body = json.loads(
                self.keys["controller-to-desktop"].decrypt(
                    dec(encrypted["nonce"]), dec(encrypted["ciphertext"]), aad
                )
            )
            self.requests.append((frame, body))
            if frame["method"] == "remote.hello":
                nonce = body["clientNonce"]
                canonical = f"moldable-remote-desktop-proof-v1\n{nonce}\ndesktop-test\n2"
                await socket.send_json(
                    self.envelope(
                        {
                            "type": "ready",
                            "desktopName": "Test Mac",
                            "selectedWorkspaceID": self.selected_workspace,
                            "workspaces": [{"id": "qa", "name": "Moldable QA"}],
                            "desktopProof": {
                                "nonce": nonce,
                                "signature": enc(self.key.sign(canonical.encode())),
                            },
                        }
                    )
                )
                result: Json = {"ok": True}
            elif frame["method"] == "workspace.select":
                assert set(body) == {"workspaceID"}
                self.selected_workspace = body["workspaceID"]
                result = {"ok": True}
            else:
                assert body["workspaceId"] == self.selected_workspace
                if self.drop_next_request:
                    self.drop_next_request = False
                    await socket.close()
                    break
                if self.conversation and frame["method"] == "bot.message.append":
                    mutation = body["clientMutationId"]
                    if mutation not in self.mutations:
                        self.mutations[mutation] = {
                            "message": {
                                "turnId": "turn-" + str(len(self.mutations)),
                                "threadId": "thread-1",
                            },
                            "event": {"cursor": "cursor-1"},
                        }
                    result = self.mutations[mutation]
                elif self.conversation and frame["method"] == "bot.events.replay":
                    result = {
                        "events": [
                            {
                                "type": "message.assistant",
                                "sourceId": "turn:turn-0",
                                "message": {"body": {"text": "The blue button is ready."}},
                            },
                            {"type": "turn.state", "turnId": "turn-0", "state": "completed"},
                        ],
                        "nextCursor": "cursor-3",
                        "hasMore": False,
                        "resetRequired": False,
                    }
                elif frame["method"] == "bot.transcript":
                    result = {
                        "messages": [],
                        "interactions": [],
                        "latestCursor": "cursor-1",
                        "hasMore": False,
                    }
                elif frame["method"] == "bot.events.replay":
                    result = {
                        "events": [],
                        "activeStatuses": [],
                        "nextCursor": "cursor-2",
                        "hasMore": False,
                        "resetRequired": False,
                    }
                else:
                    result = {"receivedMethod": frame["method"]}
            response = self.envelope({"result": result}, request_id=frame["id"])
            if self.reverse_two and frame["method"] != "remote.hello":
                self._held.append(response)
                if len(self._held) == 2:
                    for pending in reversed(self._held):
                        await socket.send_json(pending)
                    self._held.clear()
                    self.reverse_two = False
            else:
                await socket.send_json(response)
        return socket


@pytest_asyncio.fixture
async def peer() -> AsyncIterator[Peer]:
    peer = Peer()
    async with TestServer(peer.app) as server:
        peer.origin = str(server.make_url("/")).rstrip("/")
        yield peer
        if peer.socket is not None:
            await peer.socket.close()
