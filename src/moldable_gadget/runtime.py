"""Advertise approved actions and sensors, and reconcile device outcomes."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from websockets.exceptions import ConnectionClosed, InvalidStatus

from .client import GadgetClient
from .errors import ConnectionLost, GadgetError, ProtocolError, RemoteError
from .journal import CommandJournal
from .protocol import Json, Object, date, dumps, object_value, string, timestamp
from .schemas import check_input, check_scalar, identifier, validate_input, validate_scalar

ActionHandler = Callable[[Object], Awaitable[Object]]


@dataclass(frozen=True)
class Action:
    id: str
    description: str
    input_schema: Object
    handler: ActionHandler

    def descriptor(self) -> Object:
        identifier(self.id)
        string(self.description, limit=512)
        check_input(self.input_schema)
        return {"id": self.id, "description": self.description, "inputSchema": self.input_schema}


@dataclass(frozen=True)
class Sensor:
    id: str
    description: str
    schema: Object
    unit: str | None = None

    def descriptor(self) -> Object:
        identifier(self.id)
        string(self.description, limit=512)
        check_scalar(self.schema)
        descriptor: Object = {"id": self.id, "description": self.description, "schema": self.schema}
        if self.unit is not None:
            descriptor["unit"] = string(self.unit, limit=32)
        return descriptor


class GadgetRuntime:
    """One runtime per connected client. Handlers must be async and cancellation-aware.

    A handler returns a small JSON object only after observing the actual device
    outcome. Crashes, timeouts, and cancellation during an effect remain unknown;
    the journal never repeats that command automatically.
    """

    def __init__(
        self, client: GadgetClient, *, actions: list[Action], sensors: list[Sensor]
    ) -> None:
        if client.grant is None:
            raise GadgetError("Device actions require a scoped gadget pairing.")
        if len(actions) > 16 or len(sensors) > 16:
            raise ValueError("At most 16 actions and 16 sensors are supported.")
        self.client = client
        self.actions = {action.id: action for action in actions}
        self.sensors = {sensor.id: sensor for sensor in sensors}
        if len(self.actions) != len(actions) or len(self.sensors) != len(sensors):
            raise ValueError("Capability identifiers must be unique within their kind.")
        self.manifest: Object = {
            "actions": [action.descriptor() for action in actions],
            "sensors": [sensor.descriptor() for sensor in sensors],
        }
        self.journal = CommandJournal(client.store.directory / "commands.sqlite3")
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._fingerprints: Object = {}
        self._declared = False
        self._closed = False

    async def declare(self) -> Object:
        result = await self.client.gadget_request("capabilities.set", {"manifest": self.manifest})
        self._fingerprints = object_value(result.get("fingerprints"))
        self._declared = True
        return result

    async def observe(
        self,
        sensor_id: str,
        value: Json,
        *,
        observation_id: str | None = None,
        observed_at: str | None = None,
    ) -> Object:
        if self._closed:
            raise GadgetError("Gadget runtime is closed.")
        sensor = self.sensors.get(sensor_id)
        if sensor is None:
            raise ValueError("Unknown sensor.")
        validate_scalar(sensor.schema, value)
        return await self.client.gadget_request(
            "observations.append",
            {
                "observationId": observation_id or str(uuid.uuid4()),
                "sensorId": sensor_id,
                "value": value,
                "observedAt": observed_at or timestamp(),
            },
        )

    async def flush_results(self) -> None:
        for result in self.journal.pending_results():
            await self.client.gadget_request("commands.result", result)
            self.journal.reported(string(result["commandId"]))

    async def run_once(self) -> None:
        if self._closed:
            raise GadgetError("Gadget runtime is closed.")
        if not self._declared:
            await self.declare()
        await self.flush_results()
        response = await self.client.gadget_request("commands.poll", {})
        commands = response.get("commands")
        if not isinstance(commands, list) or len(commands) > 8:
            raise ProtocolError("Invalid gadget command batch.")
        for value in commands:
            command = object_value(value)
            command_id = string(command.get("id"), limit=64)
            if command_id in self._tasks:
                if command.get("cancelRequested") is True:
                    self._tasks[command_id].cancel()
                continue
            fingerprint = hashlib.sha256(
                dumps(
                    {
                        key: command.get(key)
                        for key in ("actionId", "input", "capabilityFingerprint")
                    }
                ).encode()
            ).hexdigest()
            if not self.journal.begin(command_id, fingerprint):
                continue
            task = asyncio.create_task(self._execute(command))
            self._tasks[command_id] = task
            task.add_done_callback(lambda done, key=command_id: self._finished(key, done))
        await asyncio.sleep(0)
        await self.flush_results()

    def _finished(self, command_id: str, task: asyncio.Task[None]) -> None:
        self._tasks.pop(command_id, None)
        if task.cancelled():
            # Cancellation can happen before the coroutine enters its try block.
            self.journal.finish(command_id, "cancelled", {"reason": "Cancelled before execution."})

    async def _execute(self, command: Object) -> None:
        command_id = string(command["id"])
        started = False
        try:
            action_id = string(command.get("actionId"), limit=64)
            action = self.actions.get(action_id)
            if action is None or self._fingerprints.get("action:" + action_id) != command.get(
                "capabilityFingerprint"
            ):
                self.journal.finish(
                    command_id,
                    "failed",
                    {"reason": "Action is absent or its approved schema changed."},
                )
                return
            value = object_value(command.get("input"))
            validate_input(action.input_schema, value)
            remaining = (date(command.get("expiresAt")) - datetime.now(UTC)).total_seconds()
            if remaining <= 0 or command.get("cancelRequested") is True:
                self.journal.finish(
                    command_id, "cancelled", {"reason": "Cancelled or expired before execution."}
                )
                return
            started = True
            async with asyncio.timeout(min(remaining, 120)):
                result = object_value(await action.handler(value))
            if len(dumps(result).encode()) > 4096:
                raise ValueError("Handler result exceeds 4096 bytes.")
            self.journal.finish(command_id, "succeeded", result)
        except (asyncio.CancelledError, TimeoutError):
            self.journal.finish(
                command_id,
                "unknown",
                {"reason": "Execution was interrupted; the physical outcome must be checked."},
            )
        except Exception as error:
            # A handler can throw after changing hardware. Do not turn an
            # exception into a false assertion that no effect occurred.
            self.journal.finish(
                command_id,
                "unknown" if started else "failed",
                {
                    "reason": "Handler did not confirm an outcome."
                    if started
                    else "Invalid command.",
                    "errorType": type(error).__name__,
                },
            )

    async def run(self, *, poll_interval: float = 1) -> None:
        if poll_interval < 0.25:
            raise ValueError("Poll interval must be at least 0.25 seconds.")
        delay = 1
        reconnect = False
        try:
            while True:
                try:
                    if reconnect:
                        await self.client.reconnect()
                        self._declared = False
                        reconnect = False
                    await self.run_once()
                    delay = 1
                    await asyncio.sleep(poll_interval)
                except RemoteError as error:
                    if error.code not in {"desktop_offline", "request_timeout", "rate_limited"}:
                        raise
                    reconnect = True
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 30)
                except (
                    ConnectionLost,
                    OSError,
                    TimeoutError,
                    httpx.TransportError,
                    ConnectionClosed,
                ):
                    reconnect = True
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 30)
                except InvalidStatus as error:
                    if error.response.status_code < 500 and error.response.status_code != 429:
                        raise
                    reconnect = True
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 30)
        finally:
            await self.close()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.journal.close()
