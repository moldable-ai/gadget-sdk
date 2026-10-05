"""Device voice: recorded push-to-talk and paced, interruptible PCM playback."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress

from ..errors import GadgetError
from ..voice.speech import Speech
from ..voice.transcription import Transcription
from .backend import Send
from .wire import binary_frame


class DeviceAudio:
    def __init__(self, transcription: Transcription, speech: Speech) -> None:
        self.transcription, self.speech = transcription, speech
        self.playback: asyncio.Task[None] | None = None
        self.stream = 0

    async def stop(self) -> None:
        if self.playback:
            self.playback.cancel()
            with suppress(asyncio.CancelledError, GadgetError):
                await self.playback
            self.playback = None

    async def speak(
        self, text: str, rate: int, send: Send, binary: Callable[[bytes], Awaitable[None]]
    ) -> None:
        await self.stop()
        self.stream = (self.stream + 1) % 256
        stream = self.stream

        async def play() -> None:
            started = False
            try:
                sequence = 0
                elapsed = 0.0
                baseline = 0.0
                async for pcm in self.speech.pcm(text, rate):
                    if not started:
                        await send(
                            {
                                "type": "audio.start",
                                "stream": stream,
                                "rate": rate,
                                "format": "pcm16",
                            }
                        )
                        started = True
                        baseline = asyncio.get_running_loop().time()
                    else:
                        # A slow synthesis section never causes a burst of catch-up audio.
                        baseline = max(baseline, asyncio.get_running_loop().time() - elapsed)
                    frame_size = rate * 2 // 25  # 40 ms, bounded far below a device jitter buffer.
                    for offset in range(0, len(pcm), frame_size):
                        now = asyncio.get_running_loop().time()
                        if now - (baseline + elapsed) > 0.04:
                            baseline = now - elapsed
                        delay = baseline + elapsed - now
                        if delay > 0:
                            await asyncio.sleep(delay)
                        chunk = pcm[offset : offset + frame_size]
                        await binary(binary_frame(1, stream, sequence, chunk))
                        sequence = (sequence + 1) % 65536
                        elapsed += len(chunk) / (rate * 2)
                # end means all PCM is delivered, not that a physical speaker was heard.
                await send({"type": "audio.end", "stream": stream})
            except BaseException:
                if started:
                    with suppress(Exception):
                        await send({"type": "audio.abort", "stream": stream})
                raise

        async def report() -> None:
            try:
                await play()
            except GadgetError as error:
                await send({"type": "notice", "text": "Speech unavailable: " + str(error)})

        self.playback = asyncio.create_task(report())
