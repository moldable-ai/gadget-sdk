from __future__ import annotations

import asyncio
import json
import stat
from pathlib import Path

import pytest
from conftest import Peer, expiry

from moldable_gadget import (
    ConnectionLost,
    GadgetClient,
    GadgetError,
    PairingRequired,
    ProtocolError,
    ReplayRequired,
    StateStore,
    pair,
)


async def provision(peer: Peer, directory: Path) -> None:
    with StateStore(directory) as store:
        await pair(store, peer.setup_link(), "Test gadget", relay=peer.origin, local=True)


async def test_pair_connect_scoped_requests_and_replay_use_the_host_wire_contract(
    peer: Peer, tmp_path: Path
) -> None:
    directory = tmp_path / "device"
    await provision(peer, directory)
    assert stat.S_IMODE((directory / "state.json").stat().st_mode) == 0o600
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    async with GadgetClient(directory, allow_insecure_localhost=True) as client:
        assert client.ready["desktopName"] == "Test Mac"
        with pytest.raises(GadgetError, match="select_workspace"):
            await client.send_text("qa", "bot-1", "hello", mutation_id="mutation-1")
        await client.select_workspace("qa")
        await client.bots("qa")
        await client.send_text(
            "qa", "bot-1", "hello", mutation_id="mutation-1", thread_id="thread-1"
        )
        page = await client.transcript("qa", "bot-1", before_sequence=22, limit=2)
        await client.replay("qa", "bot-1", after_cursor=page["latestCursor"])
        await client.interrupt("qa", "bot-1", "turn-1", thread_id="thread-1")
    params = {frame["method"]: body for frame, body in peer.requests}
    assert params["bot.message.append"] == {
        "workspaceId": "qa",
        "botId": "bot-1",
        "clientMutationId": "mutation-1",
        "body": {"text": "hello", "format": "plain"},
        "threadId": "thread-1",
    }
    assert params["bot.transcript"]["beforeSequence"] == 22
    assert params["bot.events.replay"]["afterCursor"] == "cursor-1"
    assert params["bot.turn.interrupt"] == {
        "workspaceId": "qa",
        "botId": "bot-1",
        "turnId": "turn-1",
        "threadId": "thread-1",
    }
    for frame, _ in peer.requests:
        assert "params" not in frame and "encrypted" in frame
        assert "privateKey" not in json.dumps(frame)


async def test_parallel_replies_are_correlated_and_disconnect_does_not_resend(
    peer: Peer, tmp_path: Path
) -> None:
    directory = tmp_path / "device"
    await provision(peer, directory)
    async with GadgetClient(directory, allow_insecure_localhost=True) as client:
        await client.select_workspace("qa")
        peer.reverse_two = True
        bots, transcript = await asyncio.gather(client.bots("qa"), client.transcript("qa", "bot-1"))
        assert bots == {"receivedMethod": "bots.list"}
        assert transcript["latestCursor"] == "cursor-1"
        peer.drop_next_request = True
        with pytest.raises(ConnectionLost):
            await client.send_text("qa", "bot-1", "pressed", mutation_id="durable-button-1")
        await client.reconnect()
        sent = [body for frame, body in peer.requests if frame["method"] == "bot.message.append"]
        assert len(sent) == 1
        await client.send_text("qa", "bot-1", "pressed", mutation_id="durable-button-1")
        sent_frames = [
            frame for frame, _ in peer.requests if frame["method"] == "bot.message.append"
        ]
        assert len(sent_frames) == 2
        assert sent_frames[0]["messageId"] != sent_frames[1]["messageId"]
        assert [
            body["clientMutationId"]
            for frame, body in peer.requests
            if frame["method"] == "bot.message.append"
        ] == ["durable-button-1"] * 2


async def test_refresh_is_signed_persisted_and_single_writer(peer: Peer, tmp_path: Path) -> None:
    directory = tmp_path / "device"
    peer.access_expiry = expiry(-1)
    await provision(peer, directory)
    async with GadgetClient(directory, allow_insecure_localhost=True):
        with pytest.raises(GadgetError, match="another gadget process"):
            with StateStore(directory):
                pass
        saved = json.loads((directory / "state.json").read_text())
        assert saved["accessToken"] == "synthetic-access-1"
        assert saved["sessionId"] == "synthetic-session-1"
    assert len(peer.refreshes) == 1
    assert peer.refreshes[0]["idempotencyKey"] == "synthetic-session-0"


@pytest.mark.parametrize("signal", ["event", "close"])
async def test_revocation_erases_pairing_and_ends_live_reader(
    peer: Peer, tmp_path: Path, signal: str
) -> None:
    directory = tmp_path / "device"
    await provision(peer, directory)
    async with GadgetClient(directory, allow_insecure_localhost=True) as client:
        iterator = client.events()
        pending = asyncio.create_task(anext(iterator))
        await asyncio.sleep(0)
        if signal == "event":
            await peer.socket.send_json({"type": "event", "event": "access.revoked"})
        else:
            await peer.socket.close(code=4003)
        with pytest.raises(PairingRequired):
            await asyncio.wait_for(pending, 2)
        assert not (directory / "state.json").exists()


@pytest.mark.parametrize("failure", ["signature", "plaintext", "gap", "duplicate"])
async def test_live_events_authentication_ordering_and_deduplication(
    peer: Peer, tmp_path: Path, failure: str
) -> None:
    directory = tmp_path / "device"
    await provision(peer, directory)
    async with GadgetClient(directory, allow_insecure_localhost=True) as client:
        iterator = client.events()
        pending = asyncio.create_task(anext(iterator))
        await asyncio.sleep(0)
        event = peer.envelope({"type": "channel.event", "workspaceId": "qa"}, sequence=1)
        if failure == "signature":
            event["signature"] = "A" * 86
        elif failure == "plaintext":
            event = {"type": "event", "event": "remote.event", "params": {"type": "channel.event"}}
        await peer.socket.send_json(event)
        if failure in ("signature", "plaintext"):
            with pytest.raises(ProtocolError):
                await asyncio.wait_for(pending, 2)
            return
        assert (await asyncio.wait_for(pending, 2))["workspaceId"] == "qa"
        next_event = asyncio.create_task(anext(iterator))
        await asyncio.sleep(0)
        if failure == "duplicate":
            await peer.socket.send_json(event)
            await peer.socket.send_json(peer.envelope({"type": "distinct"}, sequence=2))
            assert (await asyncio.wait_for(next_event, 2))["type"] == "distinct"
        else:
            await peer.socket.send_json(peer.envelope({"type": "missed"}, sequence=3))
            with pytest.raises(ReplayRequired):
                await asyncio.wait_for(next_event, 2)
        await iterator.aclose()


@pytest.mark.parametrize(
    "code", ["refresh_invalid", "refresh_reuse_detected", "refresh_already_used"]
)
async def test_unrecoverable_refresh_requires_new_pairing(
    peer: Peer, tmp_path: Path, code: str
) -> None:
    directory = tmp_path / "device"
    peer.access_expiry = expiry(-1)
    await provision(peer, directory)
    peer.refresh_error = code
    with pytest.raises(PairingRequired):
        async with GadgetClient(directory, allow_insecure_localhost=True):
            pass
    assert not (directory / "state.json").exists()


async def test_pairing_response_cannot_replace_the_pinned_desktop(
    peer: Peer, tmp_path: Path
) -> None:
    peer.claim_desktop_id = "another-desktop"
    with pytest.raises(ProtocolError, match="does not match"):
        await provision(peer, tmp_path / "device")
    assert not (tmp_path / "device/state.json").exists()


@pytest.mark.parametrize("revoked", [False, True])
async def test_upgrade_401_refreshes_once_or_erases_revoked_pairing(
    peer: Peer, tmp_path: Path, revoked: bool
) -> None:
    directory = tmp_path / "device"
    await provision(peer, directory)
    peer.ws_rejections = [401]
    if revoked:
        peer.refresh_error = "device_revoked"
        with pytest.raises(PairingRequired):
            async with GadgetClient(directory, allow_insecure_localhost=True):
                pass
        assert not (directory / "state.json").exists()
        assert peer.ws_connections == 1
    else:
        async with GadgetClient(directory, allow_insecure_localhost=True) as client:
            assert client.ready["desktopName"] == "Test Mac"
        assert peer.ws_connections == 2
    assert len(peer.refreshes) == 1
    assert not any(frame["method"] == "bot.message.append" for frame, _ in peer.requests)
