"""Exercise a separately built, pinned embedded core against the real bridge.

Run with the upstream Python package installed and --library pointing at its
compiled hgsim library. Only the desktop peer is synthetic; device C++, device
transport, bridge WebSocket, controller encryption, and journal are real.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
from pathlib import Path

from aiohttp.test_utils import TestServer
from hermes_gadget.sim import Simulator
from websockets.asyncio.server import serve

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from conftest import Peer  # noqa: E402

from moldable_gadget import GadgetClient, StateStore, pair  # noqa: E402
from moldable_gadget.device.backend import BotBackend  # noqa: E402
from moldable_gadget.device.registry import DeviceRegistry  # noqa: E402
from moldable_gadget.device.server import DeviceBridge  # noqa: E402
from moldable_gadget.device.wire import SUBPROTOCOL  # noqa: E402


async def run(library: Path, output: Path, board: str, persistent: bool) -> None:
    await asyncio.to_thread(output.mkdir, parents=True, exist_ok=True)
    library_hash = hashlib.sha256(await asyncio.to_thread(library.read_bytes)).hexdigest()
    with tempfile.TemporaryDirectory(prefix="gadget-core-check-") as temporary:
        root = Path(temporary)
        peer = Peer()
        peer.conversation = True
        async with TestServer(peer.app) as desktop:
            peer.origin = str(desktop.make_url("/")).rstrip("/")
            with StateStore(root / "controller") as store:
                await pair(
                    store, peer.setup_link(), "Core verification", relay=peer.origin, local=True
                )
            async with GadgetClient(root / "controller", allow_insecure_localhost=True) as client:
                await client.select_workspace("qa")
                registry = DeviceRegistry(root / "devices")
                with StateStore(root / "outbox") as journal:
                    bridge = DeviceBridge(registry, BotBackend(client, "qa", "bot-1", journal))
                    async with serve(
                        bridge.handle, "127.0.0.1", 0, subprotocols=[SUBPROTOCOL]
                    ) as endpoint:
                        port = endpoint.sockets[0].getsockname()[1]
                        sim = Simulator(
                            url=f"ws://127.0.0.1:{port}/gadget",
                            board=board,
                            library=library,
                            state_dir=root / "sim",
                        )

                        async def until(predicate):
                            async with asyncio.timeout(10):
                                while not predicate():
                                    sim.step()
                                    await asyncio.sleep(0.01)

                        try:
                            sim.start()
                            await until(lambda: sim.last_received("pairing"))
                            identifier = registry.list()[0]["id"]
                            registry.approve(identifier)
                            await until(lambda: sim.last_received("paired"))
                            if persistent:
                                sim.press("talk")
                                sim.step()
                                sim.release("talk")
                            else:
                                sim.type_text("Is the blue button ready?")
                            await until(lambda: sim.last_received("turn.end"))
                            assert sim.last_received("reply")["text"] == "The blue button is ready."
                            assert sim.last_received("turn.end")["outcome"] == "success"
                            sim.screenshot(output / "round-reply.png")
                            if persistent:
                                # Wait beyond the normal animated-core reply linger.
                                async def advance(seconds: float) -> None:
                                    deadline = asyncio.get_running_loop().time() + seconds
                                    while asyncio.get_running_loop().time() < deadline:
                                        sim.step()
                                        await asyncio.sleep(0.01)

                                await advance(8)
                                sim.screenshot(output / "persistent-before.png")
                                await advance(24)
                                sim.screenshot(output / "persistent-after.png")
                                assert (output / "persistent-before.png").read_bytes() == (
                                    output / "persistent-after.png"
                                ).read_bytes(), "persistent display changed without a new request"
                                assert sim.last_received("audio.start") is None
                            # Reconnect the same core/NVS: the bridge must now require HMAC.
                            sim.set_network(False)
                            await until(lambda: bridge.connection is None)
                            sim.set_network(True)
                            count = len([m for m in sim.received if m.get("type") == "welcome"])
                            await until(
                                lambda: (
                                    len([m for m in sim.received if m.get("type") == "welcome"])
                                    > count
                                )
                            )
                            assert sim.last_received("challenge")["enrolled"] is True
                            assert sim.last_received("welcome")["paired"] is True
                            registry.revoke(identifier)
                            await until(lambda: sim.last_received("unpaired"))
                            report = {
                                "expectedCoreRevision": "323e3303ab68981f810fc3208119cd8a22e64af0",
                                "librarySha256": library_hash,
                                "board": board,
                                "persistentDisplay": "pass" if persistent else "not-tested",
                                "desktop": "synthetic",
                                "physicalHardware": False,
                                "enrollment": "pass",
                                "textReply": "pass",
                                "hmacReconnect": "pass",
                                "revocation": "pass",
                                "mutations": len(peer.mutations),
                            }
                            (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
                            print(json.dumps(report))
                        finally:
                            sim.set_network(False)
                            await until(lambda: bridge.connection is None)
                            await asyncio.sleep(0.05)
                            sim.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--board", default="sim-466x466-round")
    parser.add_argument("--persistent", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.library, args.output, args.board, args.persistent))
