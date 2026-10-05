"""Provision and exercise a device from a terminal."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import ssl
import sys
import uuid
import warnings
from pathlib import Path

import httpx
from websockets.exceptions import WebSocketException

from .client import GadgetClient
from .errors import ConnectionLost, GadgetError, RemoteError, ReplayRequired
from .pairing import DEFAULT_RELAY, pair
from .protocol import Json, Object, string
from .storage import StateStore, default_state_directory


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Connect a trusted gadget to Moldable.")
    root.add_argument("--state-dir", default=str(default_state_directory()))
    root.add_argument(
        "--allow-insecure-localhost",
        action="store_true",
        help="Allow HTTP/WS only on loopback for a local development Relay",
    )
    sub = root.add_subparsers(dest="command", required=True)
    provision = sub.add_parser("pair", help="Paste a fresh pairing link at a hidden prompt")
    provision.add_argument("--name", required=True)
    provision.add_argument(
        "--relay", default=DEFAULT_RELAY, help="Explicitly trust this Relay origin"
    )
    sub.add_parser("status", help="Print non-secret local pairing metadata")
    sub.add_parser("forget", help="Delete local credentials; revoke on the desktop separately")
    sub.add_parser("workspaces", help="List workspaces from the authenticated desktop")
    bots = sub.add_parser("bots")
    bots.add_argument("--workspace", required=True)
    for command in ("send", "transcript", "watch", "interrupt"):
        cmd = sub.add_parser(command)
        cmd.add_argument("--workspace", required=True)
        cmd.add_argument("--bot", required=True)
        if command == "send":
            cmd.add_argument("text", nargs="?", help="Omit to read text from standard input")
            cmd.add_argument("--mutation-id", help="Reuse this ID if acknowledgement was lost")
            cmd.add_argument("--thread")
        elif command == "interrupt":
            cmd.add_argument("--turn", required=True)
            cmd.add_argument("--thread", required=True)
        elif command == "watch":
            cmd.add_argument("--after-cursor", help="Resume an existing durable semantic cursor")
        else:
            cmd.add_argument("--before-sequence", type=int)
    device = sub.add_parser("device", help="Manage embedded devices enrolled with this bridge")
    device.add_argument("action", choices=("list", "approve", "revoke", "forget"))
    device.add_argument("identifier", nargs="?")
    bridge = sub.add_parser("bridge", help="Serve a fixed Bot to an enrolled embedded device")
    bridge.add_argument("action", choices=("serve", "recover"))
    bridge.add_argument("--workspace", required=True)
    bridge.add_argument("--bot", required=True)
    bridge.add_argument("--host", default="127.0.0.1")
    bridge.add_argument("--port", type=int, default=8765)
    bridge.add_argument("--tls-cert")
    bridge.add_argument("--tls-key")
    bridge.add_argument(
        "--voice", action="store_true", help="Enable recorded push-to-talk (voice extra)"
    )
    return root


def output(value: Json) -> None:
    print(json.dumps(value, ensure_ascii=True), flush=True)


async def run(args: argparse.Namespace) -> None:
    if args.command == "device":
        from .device.registry import DeviceRegistry

        registry = DeviceRegistry(Path(args.state_dir) / "devices")
        if args.action == "list":
            output(registry.list())
        else:
            if not args.identifier:
                raise GadgetError("A device identifier is required.")
            getattr(registry, args.action)(args.identifier)
            output({"device": args.identifier, "action": args.action})
        return
    if args.command in ("pair", "status", "forget"):
        with StateStore(args.state_dir) as store:
            if args.command == "pair":
                print(
                    "Use Settings → Remote → Add gadget for restricted workspace/Bot access.",
                    file=sys.stderr,
                )
                # getpass otherwise falls back to echoed input when no terminal is available.
                with warnings.catch_warnings():
                    warnings.simplefilter("error", getpass.GetPassWarning)
                    link = getpass.getpass("Moldable pairing link (hidden): ")
                await pair(
                    store, link, args.name, relay=args.relay, local=args.allow_insecure_localhost
                )
                saved = store.load()
                output(
                    {
                        "paired": True,
                        "grant": saved.get("gadget"),
                        "legacyController": "gadget" not in saved,
                    }
                )
            elif args.command == "forget":
                store.forget()
                output({"forgottenLocally": True, "revokeOnDesktop": True})
            else:
                state = store.load()
                output(
                    {
                        key: state.get(key)
                        for key in (
                            "deviceId",
                            "deviceName",
                            "desktopId",
                            "desktopName",
                            "expiresAt",
                            "gadget",
                        )
                    }
                )
        return
    async with GadgetClient(
        args.state_dir, allow_insecure_localhost=args.allow_insecure_localhost
    ) as client:
        if args.command != "workspaces":
            await client.select_workspace(args.workspace)
        if args.command == "bridge":
            from .device.backend import BotBackend
            from .device.registry import DeviceRegistry
            from .device.server import DeviceBridge

            tls = None
            if bool(args.tls_cert) != bool(args.tls_key):
                raise GadgetError("Provide both --tls-cert and --tls-key.")
            if args.tls_cert:
                tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                tls.minimum_version = ssl.TLSVersion.TLSv1_2
                tls.load_cert_chain(args.tls_cert, args.tls_key)
            with StateStore(Path(args.state_dir) / "outbox") as journal:
                backend = BotBackend(client, args.workspace, args.bot, journal)
                if args.action == "recover":

                    async def print_event(event: Object) -> None:
                        output(event)

                    await backend.recover(print_event)
                else:
                    registry = DeviceRegistry(Path(args.state_dir) / "devices")
                    audio = None
                    if args.voice:
                        try:
                            from .device.audio import DeviceAudio
                            from .voice.speech import Speech
                            from .voice.transcription import Transcription
                        except ImportError as error:
                            raise GadgetError(
                                "Install the voice extra: pip install -e '.[voice]'"
                            ) from error
                        audio = DeviceAudio(
                            Transcription(client, args.workspace, args.bot),
                            Speech(client, args.workspace),
                        )
                    await DeviceBridge(registry, backend, audio).run(args.host, args.port, tls)
        elif args.command == "workspaces":
            output(
                [{"id": client.grant["workspaceId"]}]
                if client.grant
                else client.ready.get("workspaces")
            )
        elif args.command == "bots":
            output(await client.bots(args.workspace))
        elif args.command == "send":
            mutation = args.mutation_id or str(uuid.uuid4())
            print("Mutation ID (retain for retries): " + mutation, file=sys.stderr)
            text = args.text if args.text is not None else sys.stdin.read(24_001)
            output(
                await client.send_text(
                    args.workspace, args.bot, text, mutation_id=mutation, thread_id=args.thread
                )
            )
        elif args.command == "interrupt":
            output(
                await client.interrupt(args.workspace, args.bot, args.turn, thread_id=args.thread)
            )
        elif args.command == "transcript":
            output(
                await client.transcript(
                    args.workspace, args.bot, before_sequence=args.before_sequence
                )
            )
        else:
            await watch(client, args)


async def watch(client: GadgetClient, args: argparse.Namespace) -> None:
    cursor = args.after_cursor
    retry_delay = 1
    while True:
        try:
            if cursor is None:
                snapshot = await client.transcript(args.workspace, args.bot)
                output({"type": "snapshot", "page": snapshot})
                cursor = string(snapshot.get("latestCursor"))
            page = await client.replay(args.workspace, args.bot, after_cursor=cursor)
            if page.get("resetRequired"):
                cursor = None
                output({"type": "resetRequired"})
                continue
            output({"type": "replay", "page": page})
            previous = cursor
            if page.get("nextCursor") is not None:
                cursor = string(page["nextCursor"])
            if page.get("hasMore") and cursor == previous:
                raise GadgetError("Replay page did not advance its cursor.")
            retry_delay = 1
            if not page.get("hasMore"):
                await asyncio.sleep(2)
        except RemoteError as error:
            if error.code == "cursor_stale":
                cursor = None
                continue
            raise
        except (ConnectionLost, ReplayRequired, OSError, httpx.TransportError, WebSocketException):
            # Retain the semantic cursor; no mutation is retried by this read-only loop.
            while True:
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 30)
                try:
                    await client.reconnect()
                    break
                except (ConnectionLost, OSError, httpx.TransportError, WebSocketException):
                    continue


def main() -> None:
    args = parser().parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except (
        GadgetError,
        OSError,
        ValueError,
        TimeoutError,
        httpx.HTTPError,
        WebSocketException,
        getpass.GetPassWarning,
    ) as error:
        # Raw transport exceptions can contain headers, URLs or response bodies.
        message = str(error) if isinstance(error, GadgetError) else type(error).__name__
        print("moldable-gadget: " + message, file=sys.stderr)
        raise SystemExit(1) from None
