"""Push-to-talk transcription through a host-negotiated Realtime data channel.

No response is requested from the voice model. The resulting text goes through
normal Bot admission, preserving the Bot's tools, approvals and conversation.
"""

from __future__ import annotations

import asyncio
import base64
import uuid
from contextlib import suppress

from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription

from ..client import GadgetClient
from ..errors import GadgetError, ProtocolError
from ..protocol import Object, dumps, loads, object_value, string
from .audio import decode_audio, pcm_wav


class Transcription:
    def __init__(self, client: GadgetClient, workspace: str, bot: str) -> None:
        self.client, self.workspace, self.bot = client, workspace, bot

    async def transcribe(self, pcm: bytes, rate: int) -> str:
        self.client._require_workspace(self.workspace)
        converted = await asyncio.to_thread(
            decode_audio, pcm_wav(pcm, rate), 24_000, max_seconds=60
        )
        config = object_value(await self.client._request("voice.config", {}))
        connectors = config.get("connectors")
        if not isinstance(connectors, list) or not any(
            isinstance(item, dict)
            and item.get("id") == "realtime-api"
            and item.get("available") is True
            for item in connectors
        ):
            raise GadgetError(
                "Enable the Realtime API voice connector on the desktop for transcription."
            )
        session_id = "gadget-" + str(uuid.uuid4())
        negotiation_id = str(uuid.uuid4())
        # Provider has publicly reachable ICE candidates. No third-party STUN service is needed.
        peer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        peer.addTransceiver("audio", direction="sendrecv")
        channel = peer.createDataChannel("oai-events", ordered=True)
        opened = asyncio.Event()
        events: asyncio.Queue[Object | Exception] = asyncio.Queue(maxsize=128)

        @channel.on("open")
        def on_open() -> None:
            opened.set()

        @channel.on("message")
        def on_message(message: str | bytes) -> None:
            try:
                value: Object | Exception = object_value(loads(message))
            except (GadgetError, ValueError) as error:
                value = error
            if events.full():
                events.get_nowait()
                value = ProtocolError("Voice event queue overflowed.")
            events.put_nowait(value)

        @peer.on("connectionstatechange")
        def on_state() -> None:
            if peer.connectionState in ("failed", "closed") and not events.full():
                events.put_nowait(
                    GadgetError("Voice connection closed before transcription completed.")
                )
                opened.set()

        async def next_event(kind: str) -> Object:
            while True:
                event = await events.get()
                if isinstance(event, Exception):
                    raise event
                if event.get("type") == "error":
                    error = object_value(event.get("error"))
                    raise GadgetError(string(error.get("message"), limit=4096))
                if event.get("type") == kind:
                    return event
                if event.get("type") == "conversation.item.input_audio_transcription.failed":
                    raise GadgetError("The provider could not transcribe this recording.")

        def send(value: Object) -> None:
            if channel.readyState != "open":
                raise GadgetError("Voice data channel is not open.")
            channel.send(dumps(value))

        try:
            async with asyncio.timeout(90):
                await peer.setLocalDescription(await peer.createOffer())
                assert peer.localDescription is not None
                answer = object_value(
                    await self.client._request(
                        "voice.session.negotiate",
                        {
                            "workspaceID": self.workspace,
                            "sessionID": session_id,
                            "conversationID": f"workspace:{self.workspace}:direct:{self.bot}",
                            "negotiationID": negotiation_id,
                            "providerEpoch": 1,
                            "connector": "realtime-api",
                            "sdp": peer.localDescription.sdp,
                        },
                    )
                )
                if (
                    answer.get("sessionID") != session_id
                    or answer.get("negotiationID") != negotiation_id
                    or answer.get("connector") != "realtime-api"
                ):
                    raise ProtocolError("Voice negotiation response does not match this session.")
                await peer.setRemoteDescription(
                    RTCSessionDescription(
                        sdp=string(answer.get("sdp"), limit=65_536), type="answer"
                    )
                )
                await opened.wait()
                await next_event("session.created")
                send(
                    {
                        "type": "session.update",
                        "session": {
                            "type": "realtime",
                            "tools": [],
                            "tool_choice": "none",
                            "audio": {
                                "input": {
                                    "turn_detection": None,
                                    "format": {"type": "audio/pcm", "rate": 24000},
                                }
                            },
                        },
                    }
                )
                # The SDP call's initial configuration can arrive as session.updated
                # after session.created. Wait for our requested state, not merely the
                # first update; no audio leaves the bridge before this barrier.
                async with asyncio.timeout(10):
                    while True:
                        updated = await next_event("session.updated")
                        session = object_value(updated.get("session"))
                        audio = object_value(session.get("audio"))
                        input_audio = object_value(audio.get("input"))
                        if (
                            "turn_detection" in input_audio
                            and input_audio["turn_detection"] is None
                            and session.get("tools") == []
                            and session.get("tool_choice") == "none"
                        ):
                            break
                send({"type": "input_audio_buffer.clear"})
                await next_event("input_audio_buffer.cleared")
                for offset in range(0, len(converted), 12_000):
                    # Keep the SCTP queue bounded even when a network stalls.
                    while channel.bufferedAmount > 64_000:
                        if peer.connectionState in ("closed", "failed"):
                            raise GadgetError("Voice connection failed while uploading recording.")
                        await asyncio.sleep(0.01)
                    send(
                        {
                            "type": "input_audio_buffer.append",
                            "audio": base64.b64encode(converted[offset : offset + 12_000]).decode(),
                        }
                    )
                send({"type": "input_audio_buffer.commit"})
                committed = await next_event("input_audio_buffer.committed")
                transcript = await next_event(
                    "conversation.item.input_audio_transcription.completed"
                )
                if transcript.get("item_id") != committed.get("item_id"):
                    raise ProtocolError("Transcript does not belong to this recording.")
                text = string(transcript.get("transcript"), limit=24_000).strip()
                if not text:
                    raise GadgetError("No speech was recognized. Hold the button and try again.")
                return text
        finally:
            await peer.close()
            # Also end the host control session after timeout/cancellation or partial negotiation.
            with suppress(GadgetError, TimeoutError):
                async with asyncio.timeout(5):
                    await self.client._request(
                        "voice.session.end",
                        {
                            "sessionID": session_id,
                            "reason": "user_end",
                        },
                    )
