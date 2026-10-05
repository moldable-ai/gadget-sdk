# Moldable Gadget SDK

Build devices that talk to your Moldable desktop. A button can send a request
to a Bot; a small screen can follow the same conversation you see on your Mac.
Your desktop owns the conversation and runs the Bot with its existing context.

**Developer preview.** The Python client and CLI support trusted Linux devices,
Raspberry Pi projects, and macOS development. Current source adds host-enforced
workspace/Bot grants, individually approved actions and sensors, and a durable
device command journal. These features require the matching desktop build and
Relay support; see [compatibility and verification](docs/verification.md).

The [embedded bridge](docs/device-bridge.md) also supports text and experimental
push-to-talk. Its legacy voice path still requires a broad Remote pairing;
it is not included in a scoped gadget grant. The separate **Anything Devices**
repository provides Node 01, Orbit 01 and Frame 01 plans and board setup.

Use **Settings → Remote → Add gadget** for scoped access. A legacy pairing from
**Pair Moldable for iOS** continues to grant general Remote controller access.
The published 0.1 alpha is the legacy client; current source is 0.2 development.
See [security](SECURITY.md) and the [delivery plan](docs/roadmap.md).

## Install the alpha

Use Python 3.11 or newer on Linux or macOS. In a virtual environment:

```sh
python -m pip install https://github.com/moldable-ai/gadget-sdk/releases/download/v0.1.0a1/moldable_gadget_sdk-0.1.0a1-py3-none-any.whl
```

The [release](https://github.com/moldable-ai/gadget-sdk/releases/tag/v0.1.0a1)
includes the wheel, source archive, and SHA-256 checksums. It is a software
preview with the access limitations described above.

## Install from source

Use Python 3.11 or newer on Linux or macOS:

```sh
git clone https://github.com/moldable-ai/gadget-sdk.git moldable-gadget-sdk
cd moldable-gadget-sdk
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

## Pair with your desktop

1. On a compatible desktop, open **Settings → Remote → Add gadget**, give it
   a name, and choose its Bot in the current workspace. Create the pairing and
   choose **Copy pairing link**. If Add gadget is absent, update the desktop
   before using actions or sensors.
2. On your device, run:

   ```sh
   moldable-gadget pair --name "Workshop button"
   ```

3. Paste the link at the hidden prompt. It expires after five minutes and can
   be used once. Keep the link private; it contains encryption material.
4. Read your assigned workspace and Bot IDs:

   ```sh
   moldable-gadget workspaces
   moldable-gadget bots --workspace YOUR_WORKSPACE_ID
   ```

The desktop must stay online with Remote enabled. Each gadget needs a separate
pairing. Credentials are stored with owner-only permissions under
`~/.local/state/moldable-gadget` (or `$XDG_STATE_HOME/moldable-gadget`). Use
`--state-dir /private/device-directory` before the command for another gadget.
Only one process may use a state directory at a time.

## Send a message and read the reply

```sh
moldable-gadget send --workspace YOUR_WORKSPACE_ID --bot YOUR_BOT_ID \
  "What should I prepare for our next workshop?"
moldable-gadget watch --workspace YOUR_WORKSPACE_ID --bot YOUR_BOT_ID
```

`send` returns the host acknowledgement, not a generated answer. It prints a
mutation ID to stderr; preserve and reuse that ID with `--mutation-id` if the
connection drops before acknowledgement. `watch` prints a transcript snapshot
and durable event pages as JSON. Ctrl-C stops watching without stopping the Bot.
Use `interrupt --workspace ... --bot ... --turn ... --thread ...` for an explicit turn stop.
Run `moldable-gadget --help` for all commands.

## Use the Python API

```python
import asyncio
import uuid
from moldable_gadget import GadgetClient


async def main():
    mutation_id = str(uuid.uuid4())  # persist this with the intent before sending
    async with GadgetClient() as gadget:
        await gadget.select_workspace("YOUR_WORKSPACE_ID")
        receipt = await gadget.send_text(
            "YOUR_WORKSPACE_ID",
            "YOUR_BOT_ID",
            "The workshop button was pressed.",
            mutation_id=mutation_id,
        )
        print(receipt)


asyncio.run(main())
```

Start with the [terminal button example](examples/terminal-button.py). It keeps
the intent and mutation ID on disk so an interrupted run can retry safely.
See [API and recovery](docs/python-api.md) before building a long-lived device.
To add approved device control, run the [light-and-sensor example](examples/light-and-sensor.py):

```sh
python examples/light-and-sensor.py
```

Approve its capabilities in Settings → Remote, then ask the assigned Bot to
turn the **simulated desk light** on and report its temperature. The light is
file-backed and the temperature is synthetic; no hardware is required. Follow
[actions and sensors](docs/actions-and-sensors.md) to connect actual hardware.
The SDK has no arbitrary shell or file-access API.

## Develop

```sh
uv sync --all-extras
uv run --all-extras pytest
uv run --all-extras ruff check .
uv build
```

Tests use synthetic keys and an isolated local peer; they require no Moldable
account or hardware. See [verification](docs/verification.md) for the distinction
between protocol tests and live desktop/device validation.

Code and documentation are available under the [MIT license](LICENSE).
[Contributing](CONTRIBUTING.md) · [Protocol](docs/protocol.md) ·
[Third-party notices](THIRD_PARTY_NOTICES.md)
