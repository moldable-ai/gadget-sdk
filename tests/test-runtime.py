"""Runtime effects and recovery through the real encrypted client transport."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
from conftest import Peer, expiry

from moldable_gadget import (
    Action,
    ConnectionLost,
    GadgetClient,
    GadgetError,
    GadgetRuntime,
    Sensor,
    StateStore,
    pair,
)
from moldable_gadget.journal import CommandJournal
from moldable_gadget.protocol import Object

INPUT: Object = {
    "type": "object",
    "properties": {"on": {"type": "boolean"}},
    "required": ["on"],
    "additionalProperties": False,
}


async def provision(peer: Peer, directory: Path) -> None:
    peer.gadget = {
        "version": 1,
        "grantId": str(uuid.uuid4()),
        "workspaceId": "qa",
        "botId": "bot-1",
    }
    with StateStore(directory) as store:
        await pair(store, peer.setup_link(), "Test gadget", relay=peer.origin, local=True)


class DevicePeer:
    def __init__(self) -> None:
        self.commands: list[Object] = []
        self.results: list[Object] = []
        self.readings: list[Object] = []

    def command(self, **changes: object) -> Object:
        command: Object = {
            "id": str(uuid.uuid4()),
            "actionId": "light",
            "input": {"on": True},
            "capabilityFingerprint": "approved-light",
            "expiresAt": expiry(1),
            "cancelRequested": False,
        }
        command.update(changes)
        self.commands.append(command)
        return command

    async def handle(self, method: str, params: Object) -> Object:
        if method == "gadget.capabilities.set":
            return {"fingerprints": {"action:light": "approved-light"}, "approved": {}}
        if method == "gadget.commands.poll":
            return {"commands": self.commands}
        if method == "gadget.commands.result":
            self.results.append(params)
            return {"ok": True}
        if method == "gadget.observations.append":
            self.readings.append(params)
            return {"ok": True}
        raise AssertionError(method)


async def test_duplicate_delivery_and_lost_result_ack_do_not_repeat_effect(
    peer: Peer, tmp_path: Path
) -> None:
    directory = tmp_path / "device"
    await provision(peer, directory)
    remote = DevicePeer()
    peer.gadget_handler = remote.handle
    command = remote.command()
    effects: list[Object] = []

    async def light(value: Object) -> Object:
        effects.append(value)
        return {"on": value["on"]}

    async with GadgetClient(directory, allow_insecure_localhost=True) as client:
        assert client.ready["botId"] == "bot-1"
        with pytest.raises(GadgetError):
            await client.select_workspace("another-workspace")
        with pytest.raises(GadgetError):
            await client.send_text("qa", "another-bot", "hello", mutation_id="mutation-1")
        runtime = GadgetRuntime(
            client, actions=[Action("light", "Set the light", INPUT, light)], sensors=[]
        )
        await runtime.run_once()
        await runtime.run_once()
        assert effects == [{"on": True}]
        peer.drop_next_method = "gadget.commands.result"
        with pytest.raises(ConnectionLost):
            await runtime.run_once()
        await client.reconnect()
        await runtime.run_once()
        await runtime.close()
    # Recreate both client and runtime against the persisted journal.
    async with GadgetClient(directory, allow_insecure_localhost=True) as client:
        runtime = GadgetRuntime(
            client, actions=[Action("light", "Set the light", INPUT, light)], sensors=[]
        )
        await runtime.run_once()
        await runtime.close()
    assert len(effects) == 1
    assert remote.results[-1] == {
        "commandId": command["id"],
        "status": "succeeded",
        "result": {"on": True},
    }
    assert not any(frame["method"] == "workspace.select" for frame, _ in peer.requests)


@pytest.mark.parametrize(
    "change",
    [
        {"input": {"on": "yes"}},
        {"capabilityFingerprint": "different-schema"},
        {"expiresAt": expiry(-1)},
        {"cancelRequested": True},
    ],
)
async def test_invalid_stale_cancelled_commands_never_call_handler(
    peer: Peer, tmp_path: Path, change: dict[str, object]
) -> None:
    await provision(peer, tmp_path / "device")
    remote = DevicePeer()
    remote.command(**change)
    peer.gadget_handler = remote.handle
    effects: list[Object] = []

    async def light(value: Object) -> Object:
        effects.append(value)
        return {}

    async with GadgetClient(tmp_path / "device", allow_insecure_localhost=True) as client:
        runtime = GadgetRuntime(
            client, actions=[Action("light", "Set light", INPUT, light)], sensors=[]
        )
        await runtime.run_once()
        await runtime.close()
    assert effects == []
    assert remote.results[-1]["status"] in {"failed", "cancelled"}


async def test_cancel_after_effect_and_restart_retain_unknown(peer: Peer, tmp_path: Path) -> None:
    directory = tmp_path / "device"
    await provision(peer, directory)
    remote = DevicePeer()
    command = remote.command()
    peer.gadget_handler = remote.handle
    effects: list[bool] = []

    async def light(value: Object) -> Object:
        effects.append(True)
        await asyncio.Event().wait()
        return {}

    async with GadgetClient(directory, allow_insecure_localhost=True) as client:
        runtime = GadgetRuntime(
            client, actions=[Action("light", "Set light", INPUT, light)], sensors=[]
        )
        await runtime.run_once()
        command["cancelRequested"] = True
        await runtime.run_once()
        await runtime.close()
    async with GadgetClient(directory, allow_insecure_localhost=True) as client:
        runtime = GadgetRuntime(
            client, actions=[Action("light", "Set light", INPUT, light)], sensors=[]
        )
        await runtime.run_once()
        await runtime.close()
    assert effects == [True]
    assert remote.results[-1]["status"] == "unknown"


def test_unfinished_process_journal_recovers_without_reexecution(tmp_path: Path) -> None:
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    journal = CommandJournal(directory / "commands.sqlite3")
    assert journal.begin("command-1", "fingerprint")
    journal.close()  # Represents process exit with no terminal receipt.
    journal = CommandJournal(directory / "commands.sqlite3")
    assert not journal.begin("command-1", "fingerprint")
    assert journal.pending_results()[0]["status"] == "unknown"
    with pytest.raises(GadgetError, match="different content"):
        journal.begin("command-1", "changed")
    journal.close()


async def test_sensor_validates_value_and_keeps_timestamp_and_id(
    peer: Peer, tmp_path: Path
) -> None:
    await provision(peer, tmp_path / "device")
    remote = DevicePeer()
    peer.gadget_handler = remote.handle
    async with GadgetClient(tmp_path / "device", allow_insecure_localhost=True) as client:
        runtime = GadgetRuntime(
            client,
            actions=[],
            sensors=[
                Sensor(
                    "temperature",
                    "Room temperature",
                    {"type": "number", "minimum": -40, "maximum": 125},
                    "°C",
                )
            ],
        )
        with pytest.raises(ValueError):
            await runtime.observe("temperature", True)
        observed_at = expiry(-1)
        observation_id = str(uuid.uuid4())
        await runtime.observe(
            "temperature", 22.5, observed_at=observed_at, observation_id=observation_id
        )
        await runtime.close()
    assert remote.readings == [
        {
            "sensorId": "temperature",
            "value": 22.5,
            "observationId": observation_id,
            "observedAt": observed_at,
        }
    ]


async def test_light_example_survives_temporary_offline_sensor_response(
    peer: Peer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib.util
    from functools import partial

    directory = tmp_path / "device"
    await provision(peer, directory)
    remote = DevicePeer()
    peer.gadget_handler = remote.handle
    peer.method_errors["gadget.observations.append"] = ["desktop_offline"]
    example_path = Path(__file__).parents[1] / "examples" / "light-and-sensor.py"
    spec = importlib.util.spec_from_file_location("light_example", example_path)
    assert spec is not None and spec.loader is not None
    example = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(example)
    monkeypatch.setattr(
        example, "GadgetClient", partial(GadgetClient, allow_insecure_localhost=True)
    )
    task = asyncio.create_task(example.main(directory, 22.5))
    try:
        async with asyncio.timeout(20):
            while not remote.readings:
                if task.done():
                    await task
                    pytest.fail("Example exited during temporary host unavailability")
                await asyncio.sleep(0.05)
        assert remote.readings[0]["value"] == 22.5
        assert not task.done()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
