"""Bounded mono PCM conversion using FFmpeg libraries supplied by PyAV."""

from __future__ import annotations

import io
import wave

import av

from ..device.wire import MAX_PCM_SECONDS, pcm_rate
from ..errors import GadgetError


def pcm_wav(pcm: bytes, rate: int) -> bytes:
    pcm_rate(rate)
    if len(pcm) % 2 or not 0 < len(pcm) <= rate * 2 * MAX_PCM_SECONDS:
        raise GadgetError("Invalid recording length.")
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(pcm)
    return stream.getvalue()


def decode_audio(data: bytes, rate: int, *, max_seconds: int = 600) -> bytes:
    pcm_rate(rate)
    if not data or len(data) > 16 * 1024 * 1024:
        raise GadgetError("Encoded audio exceeds its size limit.")
    output = bytearray()
    resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)

    def append(frames: list[av.AudioFrame]) -> None:
        for frame in frames:
            length = frame.samples * 2
            if len(output) + length > rate * 2 * max_seconds:
                raise GadgetError("Decoded audio exceeds its duration limit.")
            output.extend(bytes(frame.planes[0])[:length])

    try:
        with av.open(io.BytesIO(data)) as source:
            for frame in source.decode(audio=0):
                append(resampler.resample(frame))
            append(resampler.resample(None))
    except (av.FFmpegError, ValueError) as error:
        raise GadgetError("Unable to decode audio.") from error
    if not output:
        raise GadgetError("Audio contains no samples.")
    return bytes(output)
