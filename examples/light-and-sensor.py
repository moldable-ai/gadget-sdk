"""A file-backed demo light plus a synthetic temperature sensor.

Pair first with Settings → Remote → Add gadget, then run this file. Approve
set-light and temperature in Settings. This example does not drive real hardware.
Replace set_light/read_temperature with verified hardware I/O for your device.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from pathlib import Path

from moldable_gadget import Action, ConnectionLost, GadgetClient, GadgetRuntime, RemoteError, Sensor
from moldable_gadget.protocol import Object
from moldable_gadget.storage import default_state_directory


async def main(directory: Path, temperature: float) -> None:
    async with GadgetClient(directory) as client:
        light_path = client.store.directory / "demo-light.json"

        def write_light(value: Object) -> Object:
            # Set an absolute target state rather than toggle: it is easier for
            # both people and hardware adapters to reconcile an uncertain result.
            state = {"on": value["on"]}
            descriptor, pending = tempfile.mkstemp(prefix=".demo-light-", dir=light_path.parent)
            try:
                with os.fdopen(descriptor, "w") as stream:
                    json.dump(state, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(pending, light_path)
                directory_fd = os.open(light_path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                Path(pending).unlink(missing_ok=True)
            actual = json.loads(light_path.read_text())
            print(f"Demo light confirmed: {'on' if actual['on'] else 'off'}", flush=True)
            return {"on": actual["on"], "simulated": True}

        async def set_light(value: Object) -> Object:
            return await asyncio.to_thread(write_light, value)

        runtime = GadgetRuntime(
            client,
            actions=[
                Action(
                    "set-light",
                    "Set the simulated desk light on or off (file-backed demo).",
                    {
                        "type": "object",
                        "properties": {"on": {"type": "boolean"}},
                        "required": ["on"],
                        "additionalProperties": False,
                    },
                    set_light,
                )
            ],
            sensors=[
                Sensor(
                    "temperature",
                    "Synthetic room temperature for the light-and-sensor demo.",
                    {"type": "number", "minimum": -40, "maximum": 125},
                    "°C",
                )
            ],
        )
        declaration = await runtime.declare()
        print(f"Connected to {client.ready['botName']}. Approve capabilities in Settings → Remote.")
        print(f"Current approvals: {list(declaration.get('approved', {}))}")

        async def readings() -> None:
            while True:
                try:
                    await runtime.observe("temperature", temperature)
                except RemoteError as error:
                    if error.code != "gadget_request_denied":
                        raise
                    print("Temperature is waiting for approval in Settings → Remote.", flush=True)
                except (ConnectionLost, OSError):
                    pass  # The runtime reconnects; the next sample has a new timestamp.
                await asyncio.sleep(15)

        try:
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(runtime.run())
                tasks.create_task(readings())
        finally:
            await runtime.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=default_state_directory())
    parser.add_argument("--temperature", type=float, default=22.5)
    args = parser.parse_args()
    try:
        asyncio.run(main(args.state_dir, args.temperature))
    except KeyboardInterrupt:
        pass
