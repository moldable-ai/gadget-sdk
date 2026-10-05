"""Independent implementation of the public Gadget Protocol v1 device contract.

Protocol provenance and upstream revision are in THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import struct
from dataclasses import dataclass, field

from ..errors import ProtocolError
from ..protocol import Json, Object, string

SUBPROTOCOL = "hermes-gadget.v1"
AUTH_CONTEXT = "hermes-gadget/v1"
RATES = (8000, 16000, 24000, 32000, 44100, 48000)
MAX_PCM_SECONDS = 60
MAX_BINARY_BYTES = 16_384


def integer(value: Json, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ProtocolError(f"Expected integer in {low}..{high}.")
    return value


def standard_b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def decode_b64(value: Json, size: int) -> bytes:
    try:
        data = base64.b64decode(string(value, limit=size * 2 + 8), validate=True)
    except (ValueError, binascii.Error) as error:
        raise ProtocolError("Invalid device credential encoding.") from error
    if len(data) != size:
        raise ProtocolError("Invalid device credential size.")
    return data


def device_id(key: bytes) -> str:
    return "hg-" + hashlib.sha256(key).hexdigest()[:16]


def identity(hello: Object) -> str:
    if hello.get("type") != "hello" or hello.get("proto") != 1:
        raise ProtocolError("Expected Gadget Protocol v1 hello.")
    value = string(hello.get("device_id"), limit=19)
    if not re.fullmatch(r"hg-[0-9a-f]{16}", value):
        raise ProtocolError("Invalid device identity.")
    return value


def verify_auth(auth: Object, identifier: str, nonce: str, known_key: bytes | None) -> bytes:
    if auth.get("type") != "auth":
        raise ProtocolError("Expected device authentication.")
    if known_key is None:
        key = decode_b64(auth.get("key"), 32)
        if not hmac.compare_digest(device_id(key), identifier):
            raise ProtocolError("Device key does not match identity.")
        return key
    supplied = decode_b64(auth.get("mac"), 32)
    expected = hmac.digest(known_key, f"{AUTH_CONTEXT}|{identifier}|{nonce}".encode(), "sha256")
    if not hmac.compare_digest(supplied, expected):
        raise ProtocolError("Device authentication failed.")
    return known_key


def pcm_rate(value: Json) -> int:
    result = integer(value, 8000, 48000)
    if result not in RATES:
        raise ProtocolError("Unsupported PCM sample rate.")
    return result


def binary_frame(channel: int, stream: int, sequence: int, payload: bytes) -> bytes:
    if not 1 <= channel <= 3 or not 0 <= stream <= 255 or not payload:
        raise ProtocolError("Invalid media frame.")
    if len(payload) > MAX_BINARY_BYTES - 4:
        raise ProtocolError("Media frame exceeds bound.")
    return struct.pack("<BBH", channel, stream, sequence & 65535) + payload


@dataclass
class Utterance:
    """One bounded, ordered recording; discard the whole object after any error."""

    identifier: str
    stream: int
    rate: int
    sequence: int = 0
    pcm: bytearray = field(default_factory=bytearray)

    @classmethod
    def start(cls, message: Object, advertised_rate: int) -> Utterance:
        if message.get("format") != "pcm16" or message.get("mode") != "hold":
            raise ProtocolError("Only push-to-talk mono PCM16 is accepted.")
        rate = pcm_rate(message.get("rate"))
        if rate != advertised_rate:
            raise ProtocolError("Recording rate differs from declared microphone.")
        return cls(
            string(message.get("id"), limit=128), integer(message.get("stream"), 0, 255), rate
        )

    def append(self, frame: bytes) -> bytes:
        if not 6 <= len(frame) <= MAX_BINARY_BYTES or (len(frame) - 4) % 2:
            raise ProtocolError("Invalid PCM frame length.")
        channel, stream, sequence = struct.unpack_from("<BBH", frame)
        if (channel, stream, sequence) != (1, self.stream, self.sequence):
            raise ProtocolError("Audio stream or sequence mismatch; recording discarded.")
        chunk = frame[4:]
        if len(self.pcm) + len(chunk) > self.rate * 2 * MAX_PCM_SECONDS:
            raise ProtocolError("Recording exceeds 60 seconds.")
        self.sequence = (self.sequence + 1) & 65535
        self.pcm.extend(chunk)
        return chunk

    def finish(self, message: Object) -> bytes:
        if message.get("id") != self.identifier or message.get("stream") != self.stream:
            raise ProtocolError("Audio end does not match the recording.")
        if len(self.pcm) < self.rate // 2:
            raise ProtocolError("Recording is shorter than 250 ms.")
        # The measured sample count, never a device's duration_ms, controls bounds.
        return bytes(self.pcm)
