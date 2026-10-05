"""Speech assets stay scoped to the paired host; no provider key on the bridge."""

from __future__ import annotations

import asyncio
import base64
import binascii
from collections.abc import AsyncIterator

from ..client import GadgetClient
from ..device.wire import integer
from ..errors import GadgetError, ProtocolError
from ..protocol import object_value, string
from .audio import decode_audio

VOICES = ("marin", "cedar", "coral", "ash", "sage", "verse", "alloy", "nova", "onyx")


class Speech:
    def __init__(self, client: GadgetClient, workspace: str, *, voice: str = "marin") -> None:
        if voice not in VOICES:
            raise ValueError("Unsupported speech voice.")
        self.client, self.workspace, self.voice = client, workspace, voice

    async def pcm(self, text: str, rate: int) -> AsyncIterator[bytes]:
        """Yield each host-planned speech section; cancellation stops further fetches."""
        self.client._require_workspace(self.workspace)
        if not text.strip() or len(text) > 32_000:
            raise GadgetError("Speech requires 1–32000 characters.")
        index, count, total = 0, 1, 0
        while index < count:
            asset = object_value(
                await self.client._request(
                    "speech.generate",
                    {
                        "workspaceId": self.workspace,
                        "text": text,
                        "format": "markdown",
                        "partIndex": index,
                        "voice": self.voice,
                    },
                )
            )
            if integer(asset.get("partIndex"), 0, 127) != index:
                raise ProtocolError("Speech section differs from request.")
            count = integer(asset.get("partCount"), 1, 128)
            identifier = string(asset.get("assetId"), limit=64)
            size = integer(asset.get("byteLength"), 1, 16 * 1024 * 1024)
            if asset.get("mimeType") != "audio/mpeg":
                raise ProtocolError("Unsupported speech asset format.")
            data = bytearray()
            while len(data) < size:
                part = object_value(
                    await self.client._request(
                        "speech.asset.fetch",
                        {
                            "workspaceId": self.workspace,
                            "assetId": identifier,
                            "offset": len(data),
                        },
                    )
                )
                if (
                    part.get("assetId") != identifier
                    or part.get("offset") != len(data)
                    or part.get("byteLength") != size
                ):
                    raise ProtocolError("Speech asset changed while reading.")
                try:
                    chunk = base64.b64decode(string(part.get("data"), limit=65_536), validate=True)
                except (ValueError, binascii.Error) as error:
                    raise ProtocolError("Invalid speech asset encoding.") from error
                if not chunk or len(data) + len(chunk) > size:
                    raise ProtocolError("Speech asset has an invalid chunk size.")
                data.extend(chunk)
            pcm = await asyncio.to_thread(decode_audio, bytes(data), rate)
            total += len(pcm)
            if total > rate * 2 * 600:
                raise GadgetError("Spoken reply exceeds ten minutes; read the rest on the desktop.")
            yield pcm
            index += 1
