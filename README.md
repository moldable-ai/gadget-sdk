# Moldable Gadget SDK

Build devices that talk to your Moldable desktop. A button can send a request
to a Bot; a small screen can follow the same conversation you see on your Mac.
Your desktop owns the conversation and runs the Bot with its existing context.

**Developer preview.** This repository currently provides a Python client and
CLI for trusted Linux devices, Raspberry Pi projects, and macOS development.
It uses Moldable's existing encrypted Remote pairing. An embedded-device
[bridge](docs/device-bridge.md) adds device enrollment, text, and an experimental
push-to-talk voice path. Device actions and sensor ingestion remain planned in
the [delivery plan](docs/roadmap.md).
There is no published package or physically verified hardware release yet.

The separate **Anything Devices** repository provides Node 01, Orbit 01 and
Frame 01 plans, including the embedded build/setup workflow. This Python
package runs on the trusted host; its bridge connects the board firmware.

Pairing currently grants general remote-controller access. Selecting a workspace
or Bot in this SDK does not narrow that grant. Use devices and code you trust;
see [security](SECURITY.md) and the [architecture](docs/architecture.md).

## Install from source

Use Python 3.11 or newer on Linux or macOS:

```sh
git clone https://github.com/moldable-ai/moldable-gadget-sdk.git
cd moldable-gadget-sdk
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

## Pair with your desktop

1. Open Moldable's Remote device-pairing dialog on your Mac. The current desktop
   UI labels this **Pair Moldable for iOS**; choose **Copy pairing link**.
2. On your device, run:

   ```sh
   moldable-gadget pair --name "Workshop button"
   ```

3. Paste the link at the hidden prompt. It expires after five minutes and can
   be used once. Keep the link private; it contains encryption material.
4. Discover your workspace and Bot IDs:

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
No shell execution or inbound hardware control is enabled by this client.

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
