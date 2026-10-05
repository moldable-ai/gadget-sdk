"""Optional voice media tests: independent PCM expectations and playback clock."""

from __future__ import annotations

import asyncio
import io
import math
import struct
import time
import wave

import pytest

pytest.importorskip("av")

from moldable_gadget.device.audio import DeviceAudio  # noqa: E402
from moldable_gadget.errors import GadgetError  # noqa: E402
from moldable_gadget.voice.audio import decode_audio  # noqa: E402


def wav_tone() -> bytes:
    # Independent stereo input fixture: 400 Hz at 48 kHz, 250 ms.
    source = io.BytesIO()
    with wave.open(source, "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(48000)
        for index in range(12000):
            sample = round(8000 * math.sin(2 * math.pi * 400 * index / 48000))
            output.writeframesraw(struct.pack("<hh", sample, sample))
    return source.getvalue()


def test_audio_decode_preserves_time_pitch_and_mono_layout() -> None:
    pcm = decode_audio(wav_tone(), 16000)
    samples = struct.unpack("<" + "h" * (len(pcm) // 2), pcm)
    assert len(samples) == 4000
    rising = sum(a <= 0 < b for a, b in zip(samples, samples[1:], strict=False))
    assert 99 <= rising <= 101
    assert max(samples) > 7000
    with pytest.raises(GadgetError, match="duration"):
        decode_audio(wav_tone(), 16000, max_seconds=0)
    with pytest.raises(GadgetError, match="decode"):
        decode_audio(b"not audio", 16000)


async def test_playback_is_paced_and_cancel_aborts_without_a_late_tail() -> None:
    class SpeechFixture:
        async def pcm(self, text: str, rate: int):
            yield b"\x01\0" * rate  # one second, enough to distinguish pacing from a burst

    audio = DeviceAudio(None, SpeechFixture())
    events, frames, times = [], [], []
    fifth = asyncio.Event()

    async def send(event):
        events.append(event)

    async def binary(frame):
        frames.append(frame)
        times.append(time.monotonic())
        if len(frames) == 5:
            fifth.set()

    await audio.speak("Synthetic", 16000, send, binary)
    await asyncio.wait_for(fifth.wait(), 2)
    await audio.stop()
    count = len(frames)
    await asyncio.sleep(0.1)
    assert len(frames) == count == 5
    assert times[-1] - times[0] >= 0.12
    assert [struct.unpack_from("<BBH", frame) for frame in frames] == [
        (1, 1, index) for index in range(5)
    ]
    assert all(len(frame) == 1284 for frame in frames)
    assert events[0]["type"] == "audio.start"
    assert events[-1] == {"type": "audio.abort", "stream": 1}
    assert not any(event["type"] == "audio.end" for event in events)


async def test_transcription_waits_for_manual_mode_on_a_real_data_channel() -> None:
    from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription

    from moldable_gadget.voice.transcription import Transcription

    class VoiceHost:
        """Independent protocol peer: no ASR claim, actual ICE/DTLS/SCTP transport."""

        def __init__(self):
            self.peer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
            self.manual = False
            self.audio = bytearray()
            self.ended = False

            @self.peer.on("datachannel")
            def connected(channel):
                import base64
                import json

                def send(event):
                    channel.send(json.dumps(event))

                # Model the provider's delayed initial update that broke the live probe.
                send({"type": "session.created"})
                send(
                    {
                        "type": "session.updated",
                        "session": {
                            "audio": {"input": {"turn_detection": {"type": "server_vad"}}},
                            "tools": [{"type": "function"}],
                            "tool_choice": "auto",
                        },
                    }
                )

                @channel.on("message")
                def receive(raw):
                    event = json.loads(raw)
                    if event["type"] == "session.update":
                        assert event["session"]["audio"]["input"]["turn_detection"] is None
                        assert event["session"]["tools"] == []
                        self.manual = True
                        send({"type": "session.updated", "session": event["session"]})
                    elif event["type"] == "input_audio_buffer.clear":
                        assert self.manual
                        send({"type": "input_audio_buffer.cleared"})
                    elif event["type"] == "input_audio_buffer.append":
                        assert self.manual
                        self.audio.extend(base64.b64decode(event["audio"], validate=True))
                    elif event["type"] == "input_audio_buffer.commit":
                        send({"type": "input_audio_buffer.committed", "item_id": "recording-1"})
                        send(
                            {
                                "type": "conversation.item.input_audio_transcription.completed",
                                "item_id": "recording-1",
                                "transcript": "Synthetic transcript",
                            }
                        )
                    else:
                        pytest.fail("Unexpected provider event, including autonomous response")

        def _require_workspace(self, workspace):
            assert workspace == "qa"

        async def _request(self, method, params):
            if method == "voice.config":
                return {"connectors": [{"id": "realtime-api", "available": True}]}
            if method == "voice.session.end":
                self.ended = True
                return {"ended": True}
            assert method == "voice.session.negotiate"
            assert params["workspaceID"] == "qa"
            assert params["conversationID"] == "workspace:qa:direct:bot-1"
            await self.peer.setRemoteDescription(RTCSessionDescription(params["sdp"], "offer"))
            await self.peer.setLocalDescription(await self.peer.createAnswer())
            return {
                "sessionID": params["sessionID"],
                "negotiationID": params["negotiationID"],
                "connector": "realtime-api",
                "sdp": self.peer.localDescription.sdp,
            }

    host = VoiceHost()
    try:
        result = await Transcription(host, "qa", "bot-1").transcribe(b"\0\0" * 4000, 16000)
        assert result == "Synthetic transcript"
        assert len(host.audio) == 12000  # 250 ms, mono PCM16 at the negotiated 24 kHz
        assert host.ended
    finally:
        await host.peer.close()
