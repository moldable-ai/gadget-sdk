"""An Enter key stands in for a physical button; pending intent survives restart.

Run with --workspace ID --bot ID. Ctrl-C stops. This example sends text only.
The outbox is separate from the SDK's credential store.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from pathlib import Path

from moldable_gadget import GadgetClient, PairingRequired, StateStore, default_state_directory
from moldable_gadget.protocol import Json, Object, string


async def deliver(state_dir: Path, intent: Object) -> Json:
    async with GadgetClient(state_dir) as gadget:
        await gadget.select_workspace(string(intent["workspaceId"]))
        return await gadget.send_text(
            string(intent["workspaceId"]),
            string(intent["botId"]),
            string(intent["text"]),
            mutation_id=string(intent["mutationId"]),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--bot", required=True)
    parser.add_argument("--text", default="The workshop button was pressed.")
    parser.add_argument("--state-dir", type=Path, default=default_state_directory())
    parser.add_argument(
        "--outbox-dir",
        type=Path,
        default=default_state_directory().with_name("moldable-button-outbox"),
    )
    args = parser.parse_args()
    if args.state_dir.resolve() == args.outbox_dir.resolve():
        parser.error("Credential and outbox directories must differ.")
    with StateStore(args.outbox_dir) as outbox:
        while True:
            try:
                intent = outbox.load()
                # Do not redirect an existing intent to a newly selected Bot/workspace.
                print("A pending request exists for", intent["workspaceId"], intent["botId"])
                input("Enter retries that exact request; Ctrl-C leaves it pending. ")
            except PairingRequired:
                input("Press Enter to send the button request; Ctrl-C exits. ")
                intent = {
                    "workspaceId": args.workspace,
                    "botId": args.bot,
                    "text": args.text,
                    "mutationId": str(uuid.uuid4()),
                }
                outbox.save(intent)
            receipt = asyncio.run(deliver(args.state_dir, intent))
            print(json.dumps(receipt, ensure_ascii=True))
            outbox.forget()  # only clear after the host acknowledges


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
