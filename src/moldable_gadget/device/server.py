"""Authenticated embedded-device WebSocket endpoint, separate from Relay."""

from __future__ import annotations

import asyncio
import hashlib
import os
import ssl
import unicodedata
import uuid
from collections import OrderedDict
from contextlib import suppress
from typing import TYPE_CHECKING

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from ..errors import GadgetError, ProtocolError
from ..protocol import Object, dumps, loads, object_value, string
from .backend import BotBackend
from .registry import DeviceRegistry
from .wire import SUBPROTOCOL, Utterance, decode_b64, identity, pcm_rate, standard_b64, verify_auth

if TYPE_CHECKING:
    from .audio import DeviceAudio


class DeviceBridge:
    def __init__(
        self, registry: DeviceRegistry, backend: BotBackend, audio: DeviceAudio | None = None
    ) -> None:
        self.registry, self.backend = registry, backend
        self.connection: ServerConnection | None = None
        self.audio = audio

    async def handle(self, socket: ServerConnection) -> None:
        if socket.request is None or socket.request.path != "/gadget":
            await socket.close(1008, "Use /gadget")
            return
        task: asyncio.Task[None] | None = None
        identifier = ""
        ascii_only = False
        seen: OrderedDict[str, str] = OrderedDict()
        recording: Utterance | None = None
        recording_started = 0.0
        mic_rate: int | None = None
        speaker_rate: int | None = None
        transcribing = False
        response_epoch = 0

        async def send(value: Object) -> None:
            # Recheck on output, not only on incoming input: revocation also cuts off replies.
            if identifier and value.get("type") not in ("challenge", "welcome", "pairing", "error"):
                record = self.registry.lookup(identifier)
                if not record or record.get("status") != "approved":
                    raise ProtocolError("Device access was revoked.")
            if ascii_only and isinstance(value.get("text"), str):
                value = dict(value)
                value["text"] = (
                    unicodedata.normalize("NFKD", value["text"]).encode("ascii", "replace").decode()
                )
            await socket.send(dumps(value))

        async def send_binary(data: bytes) -> None:
            record = self.registry.lookup(identifier)
            if not record or record.get("status") != "approved":
                raise ProtocolError("Device access was revoked.")
            await socket.send(data)

        async def run_text(text: str, *, spoken: bool = False) -> None:
            replies: list[str] = []
            epoch = response_epoch

            async def forward(event: Object) -> None:
                await send(event)
                if event.get("type") == "reply" and event.get("interim") is not True:
                    replies.append(string(event.get("text"), limit=128_000))
                if (
                    event.get("type") == "turn.end"
                    and event.get("outcome") == "success"
                    and epoch == response_epoch
                    and spoken
                    and self.audio
                    and speaker_rate
                    and replies
                ):
                    await self.audio.speak("\n\n".join(replies), speaker_rate, send, send_binary)

            try:
                await self.backend.text(text, forward)
            except (GadgetError, TimeoutError) as error:
                await send({"type": "error", "code": "request_failed", "message": str(error)})
                await send({"type": "turn.end", "outcome": "failure"})

        async def transcribe(utterance: Utterance, pcm: bytes) -> None:
            nonlocal transcribing
            assert self.audio is not None
            transcribing = True
            try:
                await send({"type": "status", "text": "Transcribing"})
                text = await self.audio.transcription.transcribe(pcm, utterance.rate)
                await send({"type": "transcript", "text": text})
                transcribing = False
                await run_text(text, spoken=True)
            except (GadgetError, TimeoutError) as error:
                await send({"type": "error", "code": "voice_failed", "message": str(error)})
            finally:
                transcribing = False
                await send({"type": "status", "text": ""})

        try:
            async with asyncio.timeout(10):
                hello = object_value(loads(await socket.recv()))
                identifier = identity(hello)
                known = self.registry.lookup(identifier)
                if known and known.get("status") == "revoked":
                    raise ProtocolError(
                        "Device access was revoked; forget it before enrolling again."
                    )
                nonce = standard_b64(os.urandom(16))
                await send({"type": "challenge", "nonce": nonce, "enrolled": known is not None})
                auth = object_value(loads(await socket.recv()))
                key = verify_auth(
                    auth, identifier, nonce, decode_b64(known.get("key"), 32) if known else None
                )
                record = self.registry.pending(identifier, key, string(hello.get("name"), limit=80))
                if self.connection is not None:
                    raise ProtocolError("This bridge already has a device connected.")
                self.connection = socket
                caps = object_value(hello.get("caps", {}))
                if caps.get("mic") is not None:
                    mic_rate = pcm_rate(object_value(caps["mic"]).get("rate"))
                if caps.get("speaker") is not None:
                    speaker_rate = pcm_rate(object_value(caps["speaker"]).get("rate"))
                display = object_value(caps.get("display", {}))
                ascii_only = display.get("charset") == "ascii"
                approved = record.get("status") == "approved"
                await send(
                    {
                        "type": "welcome",
                        "paired": approved,
                        "proto": 1,
                        "session": str(uuid.uuid4()),
                        "server": "moldable",
                        "heartbeat_s": 20,
                    }
                )
                if not approved:
                    await send(
                        {
                            "type": "pairing",
                            "code": identifier,
                            "command": "moldable-gadget device approve " + identifier,
                        }
                    )
            last_ping = asyncio.get_running_loop().time()
            while True:
                record = self.registry.lookup(identifier)
                if not record or record.get("status") == "revoked":
                    await socket.send(dumps({"type": "unpaired"}))
                    break
                if not approved and record.get("status") == "approved":
                    approved = True
                    await send({"type": "paired"})
                if recording and asyncio.get_running_loop().time() - recording_started > 65:
                    recording = None
                    await send({"type": "notice", "text": "Recording timed out; please try again."})
                try:
                    frame = await asyncio.wait_for(socket.recv(), 1)
                except TimeoutError:
                    if asyncio.get_running_loop().time() - last_ping >= 20:
                        await socket.send(dumps({"type": "ping"}))
                        last_ping = asyncio.get_running_loop().time()
                    continue
                if isinstance(frame, bytes):
                    if not approved or recording is None:
                        raise ProtocolError("No authorized audio recording is active.")
                    recording.append(frame)
                    continue
                message = object_value(loads(frame))
                kind = message.get("type")
                if kind == "ping":
                    await socket.send(dumps({"type": "pong", "ts": message.get("ts")}))
                elif kind == "pong":
                    continue
                elif not approved:
                    raise ProtocolError("Approve this device on the bridge before using it.")
                elif kind == "audio.start":
                    if self.audio is None or mic_rate is None:
                        raise ProtocolError("Voice is not enabled for this device.")
                    if recording is not None:
                        raise ProtocolError("A recording is already active.")
                    response_epoch += 1
                    await self.audio.stop()
                    if task and not task.done() and transcribing:
                        task.cancel()
                        with suppress(asyncio.CancelledError):
                            await task
                    if task and not task.done():
                        await self.backend.cancel()
                        # Finish/reconcile an admitted turn; never drop its uncertain journal.
                        try:
                            await asyncio.wait_for(asyncio.shield(task), 5)
                        except TimeoutError:
                            raise ProtocolError(
                                "Previous request is still stopping; try again."
                            ) from None
                    recording = Utterance.start(message, mic_rate)
                    recording_started = asyncio.get_running_loop().time()
                elif kind == "audio.end":
                    if recording is None:
                        raise ProtocolError("No recording is active.")
                    utterance, recording = recording, None
                    try:
                        pcm = utterance.finish(message)
                    except ProtocolError as error:
                        await send({"type": "notice", "text": str(error)})
                        continue
                    task = asyncio.create_task(transcribe(utterance, pcm))
                elif kind == "audio.cancel":
                    recording = None
                elif kind == "text":
                    if recording is not None:
                        raise ProtocolError("Finish or cancel the recording before sending text.")
                    text = string(message.get("text"), limit=24_000)
                    if len(text.encode()) > 24_000 or not text.strip():
                        raise ProtocolError("Text must contain 1–24000 UTF-8 bytes.")
                    request_id = string(message.get("id"), limit=128)
                    digest = hashlib.sha256(text.encode()).hexdigest()
                    if request_id in seen:
                        if seen[request_id] != digest:
                            raise ProtocolError("Request ID was reused with different text.")
                        await send({"type": "notice", "text": "This request was already received."})
                        continue
                    if task and not task.done():
                        await send({"type": "notice", "text": "A request is already running."})
                    else:
                        if task:
                            with suppress(asyncio.CancelledError):
                                await task
                        response_epoch += 1
                        if self.audio:
                            await self.audio.stop()
                        seen[request_id] = digest
                        if len(seen) > 128:
                            seen.popitem(last=False)
                        task = asyncio.create_task(run_text(text))
                elif kind == "cancel":
                    response_epoch += 1
                    recording = None
                    if task and not task.done() and transcribing:
                        task.cancel()
                        with suppress(asyncio.CancelledError):
                            await task
                    if self.audio:
                        await self.audio.stop()
                    await self.backend.cancel()
                elif kind in ("state", "event"):
                    # Observations never imply instructions, a turn, or speech.
                    continue
                elif kind not in ("action.result",):
                    await send({"type": "notice", "text": "This input is not enabled."})
        except (GadgetError, TimeoutError) as error:
            with suppress(ConnectionClosed):
                await socket.send(
                    dumps({"type": "error", "code": "rejected", "message": str(error)})
                )
        except ConnectionClosed:
            pass
        finally:
            if self.audio and self.connection is socket:
                with suppress(Exception):
                    await self.audio.stop()
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError, ConnectionClosed, GadgetError):
                    await task
            if self.connection is socket:
                self.connection = None
            await socket.close()

    async def run(self, host: str, port: int, tls: ssl.SSLContext | None = None) -> None:
        if host not in ("127.0.0.1", "::1", "localhost") and tls is None:
            raise GadgetError("A non-loopback device endpoint requires a TLS certificate and key.")
        async with serve(
            self.handle,
            host,
            port,
            ssl=tls,
            subprotocols=[SUBPROTOCOL],
            max_size=32_768,
            max_queue=8,
            compression=None,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
        ):
            await asyncio.Future()
