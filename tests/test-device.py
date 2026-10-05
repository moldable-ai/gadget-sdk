"""Real socket boundary tests for device auth, fixed routing and durable recovery.

Independent device wire messages and the synthetic encrypted desktop peer exercise
both sides of the bridge. They do not claim a native host or firmware pass.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import struct
from pathlib import Path

import pytest
from conftest import Peer
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from moldable_gadget import ConnectionLost, GadgetClient, ProtocolError, StateStore, pair
from moldable_gadget.device.backend import BotBackend
from moldable_gadget.device.registry import DeviceRegistry
from moldable_gadget.device.server import DeviceBridge
from moldable_gadget.device.wire import Utterance


async def test_device_enrollment_routes_text_and_revocation_cuts_off_live_socket(
    peer: Peer, tmp_path: Path
) -> None:
    peer.conversation = True
    directory = tmp_path / "client"
    with StateStore(directory) as store:
        await pair(store, peer.setup_link(), "Bridge", relay=peer.origin, local=True)
    registry = DeviceRegistry(tmp_path / "registry")
    key = bytes(range(32))
    identifier = "hg-" + hashlib.sha256(key).hexdigest()[:16]
    async with GadgetClient(directory, allow_insecure_localhost=True) as client:
        await client.select_workspace("qa")
        with StateStore(tmp_path / "outbox") as journal:
            backend = BotBackend(client, "qa", "bot-1", journal)
            bridge = DeviceBridge(registry, backend)
            async with serve(bridge.handle, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                async with connect(f"ws://127.0.0.1:{port}/gadget") as device:
                    await device.send(
                        json.dumps(
                            {
                                "type": "hello",
                                "proto": 1,
                                "device_id": identifier,
                                "name": "Desk button",
                                "caps": {},
                            }
                        )
                    )
                    challenge = json.loads(await device.recv())
                    assert challenge["enrolled"] is False
                    await device.send(
                        json.dumps({"type": "auth", "key": base64.b64encode(key).decode()})
                    )
                    assert json.loads(await device.recv())["paired"] is False
                    assert json.loads(await device.recv())["type"] == "pairing"
                    assert len(peer.mutations) == 0
                    registry.approve(identifier)
                    assert json.loads(await asyncio.wait_for(device.recv(), 3))["type"] == "paired"
                    await device.send(
                        json.dumps(
                            {
                                "type": "text",
                                "id": "t1",
                                "text": "Button?",
                                "workspace": "forbidden",
                                "bot": "forbidden",
                            }
                        )
                    )
                    assert json.loads(await device.recv())["type"] == "turn.start"
                    assert json.loads(await device.recv())["text"] == "The blue button is ready."
                    assert json.loads(await device.recv())["outcome"] == "success"
                    assert journal.load()["pending"] is False
                    assert len(peer.mutations) == 1
                    registry.revoke(identifier)
                    assert (
                        json.loads(await asyncio.wait_for(device.recv(), 3))["type"] == "unpaired"
                    )
                # Knowledge of the key cannot undo an owner revocation.
                async with connect(f"ws://127.0.0.1:{port}/gadget") as device:
                    await device.send(
                        json.dumps(
                            {
                                "type": "hello",
                                "proto": 1,
                                "device_id": identifier,
                                "name": "Desk button",
                                "caps": {},
                            }
                        )
                    )
                    assert json.loads(await device.recv())["code"] == "rejected"
    append = [body for frame, body in peer.requests if frame["method"] == "bot.message.append"]
    assert len(append) == 1
    assert append[0]["workspaceId"] == "qa" and append[0]["botId"] == "bot-1"


async def test_uncertain_request_is_journaled_and_recovered_with_same_mutation(
    peer: Peer, tmp_path: Path
) -> None:
    peer.conversation = True
    directory = tmp_path / "client"
    with StateStore(directory) as store:
        await pair(store, peer.setup_link(), "Bridge", relay=peer.origin, local=True)
    events = []

    async def collect(event):
        events.append(event)

    async with GadgetClient(directory, allow_insecure_localhost=True) as client:
        await client.select_workspace("qa")
        with StateStore(tmp_path / "outbox") as journal:
            backend = BotBackend(client, "qa", "bot-1", journal)
            peer.drop_next_request = True
            with pytest.raises(ConnectionLost):
                await backend.text("Was the button pressed?", collect)
            saved = journal.load()
            assert saved["pending"] is True
            await client.reconnect()
            await backend.recover(collect)
            assert journal.load()["pending"] is False
    requests = [body for frame, body in peer.requests if frame["method"] == "bot.message.append"]
    assert len(requests) == 2
    assert requests[0]["clientMutationId"] == requests[1]["clientMutationId"] == saved["mutation"]
    assert events[-1]["outcome"] == "success"


@pytest.mark.parametrize("fault", ["gap", "stream", "channel", "odd", "oversize"])
def test_corrupt_audio_cannot_become_a_recording(fault: str) -> None:
    recording = Utterance.start(
        {"id": "a1", "stream": 7, "rate": 16000, "format": "pcm16", "mode": "hold"}, 16000
    )
    channel, stream, sequence, payload = 1, 7, 0, b"\0\0" * 320
    if fault == "gap":
        sequence = 1
    elif fault == "stream":
        stream = 8
    elif fault == "channel":
        channel = 2
    elif fault == "odd":
        payload += b"\0"
    elif fault == "oversize":
        payload *= 100
    with pytest.raises(ProtocolError):
        recording.append(struct.pack("<BBH", channel, stream, sequence) + payload)
    assert recording.pcm == b""


def test_audio_duration_uses_samples_not_device_claim_and_sequence_wraps() -> None:
    recording = Utterance.start(
        {"id": "a1", "stream": 7, "rate": 8000, "format": "pcm16", "mode": "hold"}, 8000
    )
    recording.sequence = 65535
    recording.append(struct.pack("<BBH", 1, 7, 65535) + b"\x01\0" * 2000)
    recording.append(struct.pack("<BBH", 1, 7, 0) + b"\x02\0" * 2000)
    assert recording.finish({"id": "a1", "stream": 7, "duration_ms": 999999}) == (
        b"\x01\0" * 2000 + b"\x02\0" * 2000
    )
