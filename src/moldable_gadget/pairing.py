"""Provision an independent controller identity from a desktop pairing link."""

from __future__ import annotations

import platform
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .errors import GadgetError, PairingRequired, ProtocolError, RemoteError
from .protocol import (
    Object,
    b64,
    date,
    dumps,
    loads,
    object_value,
    routing,
    sign,
    string,
    timestamp,
    unb64,
)
from .storage import StateStore

DEFAULT_RELAY = "https://relay.moldable.sh"


def relay_origin(url: str, *, websocket: bool = False, local: bool = False) -> str:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise ProtocolError("Invalid Relay address.") from error
    secure = "wss" if websocket else "https"
    insecure = "ws" if websocket else "http"
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.scheme not in (secure, insecure)
    ):
        raise ProtocolError("Invalid Relay address.")
    if parsed.scheme == insecure and not (
        local and parsed.hostname in ("127.0.0.1", "localhost", "::1")
    ):
        raise ProtocolError("Relay must use TLS; local development requires explicit opt-in.")
    if not websocket and (parsed.path not in ("", "/") or parsed.query):
        raise ProtocolError("Relay origin cannot include a path or query.")
    scheme = "https" if parsed.scheme == secure else "http"
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    suffix = f":{port}" if port and port != (443 if scheme == "https" else 80) else ""
    return f"{scheme}://{host}{suffix}"


def parse_setup(link: str, relay: str = DEFAULT_RELAY, *, local: bool = False) -> Object:
    if len(link) > 16_384:
        raise ProtocolError("Pairing link is too large.")
    parsed = urlsplit(link.strip())
    query = parse_qs(parsed.query, strict_parsing=True)
    if (
        parsed.scheme != "moldable-remote"
        or parsed.netloc != "pair"
        or parsed.path
        or parsed.fragment
        or set(query) != {"setup"}
        or len(query["setup"]) != 1
    ):
        raise ProtocolError("Expected a Moldable pairing link copied from the desktop.")
    setup = object_value(loads(unb64(query["setup"][0])))
    if (
        setup.get("v") != 2
        or type(setup.get("minProtocol")) is not int
        or type(setup.get("maxProtocol")) is not int
        or not setup["minProtocol"] <= 2 <= setup["maxProtocol"]
    ):
        raise ProtocolError("Pairing does not support Relay protocol v2.")
    if date(setup.get("expiresAt")) <= datetime.now(UTC):
        raise PairingRequired("Pairing link expired. Copy a new link from the desktop.")
    expected = relay_origin(relay, local=local)
    if relay_origin(string(setup.get("relayURL")), local=local) != expected:
        raise ProtocolError("Pairing link uses a different Relay than the configured origin.")
    routing(setup.get("pairingId"))
    routing(setup.get("secret"))
    unb64(setup.get("e2eeKey"), 32)
    desktop = object_value(setup.get("desktop"))
    routing(desktop.get("id"))
    string(desktop.get("name"), limit=128)
    unb64(desktop.get("publicKey"), 32)
    return setup


async def post_json(url: str, payload: Object) -> Object:
    # No redirects: bearer-equivalent claim/refresh credentials stay at the chosen origin.
    async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as http:
        async with http.stream(
            "POST",
            url,
            content=dumps(payload),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        ) as response:
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                if len(raw) > 64 * 1024:
                    raise ProtocolError("Relay HTTP response is too large.")
            if not response.is_success:
                # Do not echo server bodies, which could reflect provisioning secrets.
                try:
                    code = routing(object_value(loads(bytes(raw))).get("code", "relay_error"))
                except ProtocolError:
                    code = "relay_error"
                raise RemoteError(code, f"Relay request rejected (HTTP {response.status_code}).")
            return object_value(loads(bytes(raw)))


def validate_state(state: Object, *, local: bool = False) -> None:
    if state.get("version") != 1:
        raise ProtocolError("Unsupported credential file version.")
    for key in ("deviceId", "desktopId", "sessionId"):
        routing(state.get(key))
    for key in ("privateKey", "desktopPublicKey", "e2eeKey"):
        unb64(state.get(key), 32)
    for key in ("accessToken", "refreshToken"):
        routing(state.get(key))
    for key in ("expiresAt", "refreshExpiresAt"):
        date(state.get(key))
    origin = relay_origin(string(state.get("relayURL")), local=local)
    ws = string(state.get("websocketURL"))
    if relay_origin(ws, websocket=True, local=local) != origin:
        raise ProtocolError("WebSocket origin does not match the paired Relay.")
    parsed = urlsplit(ws)
    if parsed.path != "/v1/remote/connect" or parse_qs(parsed.query) != {
        "desktopId": [state["desktopId"]]
    }:
        raise ProtocolError("WebSocket target does not match the paired desktop.")


async def pair(
    store: StateStore,
    link: str,
    name: str,
    *,
    relay: str = DEFAULT_RELAY,
    local: bool = False,
) -> None:
    """Claim a new pairing. The caller holds the store's exclusive lock."""
    try:
        store.load()
    except PairingRequired:
        pass
    else:
        raise GadgetError(
            "A pairing already exists. Revoke it on the desktop, then forget locally."
        )
    setup = parse_setup(link, relay, local=local)
    string(name, limit=128)
    private = Ed25519PrivateKey.generate()
    seed = b64(
        private.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
    )
    public = b64(
        private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
    )
    device_id = str(uuid.uuid4())
    result = await post_json(
        relay_origin(relay, local=local) + "/v1/pairings/claim",
        {
            "pairingId": setup["pairingId"],
            "secret": setup["secret"],
            "device": {
                "id": device_id,
                "name": name,
                "platform": platform.system().lower(),
                "model": "Moldable Gadget Python",
                "appVersion": "0.1.0a1",
                "publicKey": public,
            },
        },
    )
    expected = object_value(setup["desktop"])
    desktop = object_value(result.get("desktop"))
    if desktop.get("id") != expected["id"] or desktop.get("publicKey") != expected["publicKey"]:
        raise ProtocolError("Pairing response does not match the desktop in the link.")
    state: Object = {
        "version": 1,
        "deviceId": device_id,
        "deviceName": name,
        "privateKey": seed,
        "desktopId": expected["id"],
        "desktopPublicKey": expected["publicKey"],
        "desktopName": expected["name"],
        "e2eeKey": setup["e2eeKey"],
        "relayURL": relay_origin(relay, local=local),
    }
    for key in (
        "sessionId",
        "accessToken",
        "refreshToken",
        "expiresAt",
        "refreshExpiresAt",
        "websocketURL",
    ):
        state[key] = result.get(key)
    validate_state(state, local=local)
    store.save(state)


async def refresh(
    store: StateStore, state: Object, *, force: bool = False, local: bool = False
) -> Object:
    validate_state(state, local=local)
    if date(state["refreshExpiresAt"]) <= datetime.now(UTC):
        store.forget()
        raise PairingRequired("Pairing refresh expired. Pair this device again.")
    if not force and date(state["expiresAt"]) > datetime.now(UTC) + timedelta(minutes=5):
        return state
    signed_at = timestamp()
    proof: Object = {
        "sessionId": state["sessionId"],
        "deviceId": state["deviceId"],
        "role": "controller",
        "refreshToken": state["refreshToken"],
        "signedAt": signed_at,
        "idempotencyKey": state["sessionId"],
    }
    canonical = "\n".join(
        [
            "moldable-relay-refresh-v1",
            *[
                string(proof[key])
                for key in (
                    "sessionId",
                    "deviceId",
                    "role",
                    "refreshToken",
                    "signedAt",
                    "idempotencyKey",
                )
            ],
        ]
    )
    proof["signature"] = sign(string(state["privateKey"]), canonical)
    try:
        result = await post_json(string(state["relayURL"]) + "/v1/sessions/refresh", proof)
    except RemoteError as error:
        if error.code in {
            "refresh_invalid",
            "refresh_already_used",
            "refresh_reuse_detected",
            "invalid_refresh_token",
            "refresh_expired",
            "device_revoked",
            "session_revoked",
            "invalid_session",
        }:
            store.forget()
            raise PairingRequired("Refresh is no longer recoverable. Pair again.") from error
        raise
    updated = state.copy()
    for key in ("sessionId", "accessToken", "refreshToken", "expiresAt", "refreshExpiresAt"):
        updated[key] = result.get(key)
    validate_state(updated, local=local)
    store.save(updated)  # commit rotation durably before any reconnect
    return updated
