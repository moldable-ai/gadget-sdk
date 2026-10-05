"""Moldable Relay v2 wire encoding. No transport or credential persistence."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import uuid
from datetime import UTC, datetime
from typing import TypeAlias

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .errors import ProtocolError

Json: TypeAlias = "None | bool | int | float | str | list[Json] | dict[str, Json]"
Object: TypeAlias = dict[str, Json]
MAX_FRAME_BYTES = 1024 * 1024
MARKER = "moldable-relay-e2ee-v1"


def object_value(value: Json) -> Object:
    if not isinstance(value, dict):
        raise ProtocolError("Expected a JSON object.")
    return value


def string(value: Json, *, limit: int = 4096, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit or (not empty and not value):
        raise ProtocolError("Missing or invalid string field.")
    return value


def routing(value: Json, *, empty: bool = False) -> str:
    result = string(value, limit=128, empty=empty)
    if any(char in result for char in "\r\n\0"):
        raise ProtocolError("Invalid routing field.")
    return result


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def unb64(value: Json, size: int | None = None) -> bytes:
    encoded = string(value, limit=MAX_FRAME_BYTES)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", encoded):
        raise ProtocolError("Invalid base64url field.")
    try:
        decoded = base64.b64decode(
            encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True
        )
    except ValueError as error:
        raise ProtocolError("Invalid base64url field.") from error
    if (size is not None and len(decoded) != size) or b64(decoded) != encoded:
        raise ProtocolError("Invalid encoded field length or encoding.")
    return decoded


def dumps(value: Json) -> str:
    try:
        encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, RecursionError) as error:
        raise ProtocolError("Payload is not valid JSON.") from error
    if len(encoded.encode()) > MAX_FRAME_BYTES:
        raise ProtocolError("Frame exceeds the 1 MiB limit.")
    return encoded


def loads(raw: str | bytes) -> Json:
    if len(raw.encode() if isinstance(raw, str) else raw) > MAX_FRAME_BYTES:
        raise ProtocolError("Frame exceeds the 1 MiB limit.")
    try:
        return json.loads(raw, parse_constant=_invalid_constant, object_pairs_hook=_unique_fields)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ProtocolError("Invalid JSON frame.") from error


def _invalid_constant(_: str) -> None:
    raise ValueError("Non-finite number")


def _unique_fields(pairs: list[tuple[str, Json]]) -> Object:
    result: Object = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate field")
        result[key] = value
    return result


def timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def date(value: Json) -> datetime:
    try:
        parsed = datetime.fromisoformat(string(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Missing timezone")
        return parsed
    except ValueError as error:
        raise ProtocolError("Invalid expiry timestamp.") from error


def sign(seed: str, canonical: str) -> str:
    return b64(Ed25519PrivateKey.from_private_bytes(unb64(seed, 32)).sign(canonical.encode()))


def verify(public_key: str, signature: Json, canonical: str) -> None:
    try:
        Ed25519PublicKey.from_public_bytes(unb64(public_key, 32)).verify(
            unb64(signature, 64), canonical.encode()
        )
    except InvalidSignature as error:
        raise ProtocolError("Desktop identity verification failed.") from error


def connect_proof(nonce: str, session_id: str, device_id: str, seed: str) -> Object:
    signed_at = timestamp()
    fields = [
        "moldable-relay-v2",
        routing(nonce),
        routing(session_id),
        routing(device_id),
        "controller",
        "2",
        "2",
        signed_at,
    ]
    return {
        "nonce": nonce,
        "sessionId": session_id,
        "deviceId": device_id,
        "role": "controller",
        "minProtocol": 2,
        "maxProtocol": 2,
        "signedAt": signed_at,
        "signature": sign(seed, "\n".join(fields)),
    }


class RoomCipher:
    def __init__(self, room_key: str, desktop_id: str, desktop_public_key: str) -> None:
        key = unb64(room_key, 32)
        routing(desktop_id)
        unb64(desktop_public_key, 32)
        self.public_key = desktop_public_key
        self._keys = {
            direction: AESGCM(
                HKDF(
                    algorithm=hashes.SHA256(),
                    length=32,
                    salt=hashlib.sha256(MARKER.encode()).digest(),
                    info=f"desktop:{desktop_id}:{direction}".encode(),
                ).derive(key)
            )
            for direction in ("controller-to-desktop", "desktop-to-controller")
        }

    def request(self, request_id: str, method: str, params: Object) -> Object:
        frame: Object = {
            "type": "req",
            "id": routing(request_id),
            "method": routing(method),
            "messageId": str(uuid.uuid4()),
        }
        direction = "controller-to-desktop"
        nonce = os.urandom(12)
        encrypted = self._keys[direction].encrypt(
            nonce, dumps(params).encode(), self._aad(frame, direction)
        )
        frame["encrypted"] = {
            "v": 1,
            "alg": "A256GCM",
            "nonce": b64(nonce),
            "ciphertext": b64(encrypted),
        }
        dumps(frame)  # enforce the encoded limit, including encryption overhead
        return frame

    def decode(self, frame: Object) -> Json:
        kind = frame.get("type")
        if kind not in ("res", "event"):
            raise ProtocolError("Unexpected encrypted frame type.")
        try:
            uuid.UUID(string(frame.get("messageId")))
        except ValueError as error:
            raise ProtocolError("Invalid message ID.") from error
        if kind == "res":
            routing(frame.get("id"))
        elif frame.get("event") != "remote.event":
            raise ProtocolError("Unexpected application event.")
        encrypted = object_value(frame.get("encrypted"))
        if (
            type(encrypted.get("v")) is not int
            or encrypted["v"] != 1
            or encrypted.get("alg") != "A256GCM"
        ):
            raise ProtocolError("Unsupported encryption version.")
        fields = self._fields(frame)
        nonce = unb64(encrypted.get("nonce"), 12)
        ciphertext = unb64(encrypted.get("ciphertext"))
        canonical = "\n".join(
            ["moldable-remote-envelope-signature-v1", *fields, b64(nonce), b64(ciphertext)]
        )
        verify(self.public_key, frame.get("signature"), canonical)
        try:
            plaintext = self._keys["desktop-to-controller"].decrypt(
                nonce, ciphertext, self._aad(frame, "desktop-to-controller")
            )
        except InvalidTag as error:
            raise ProtocolError("Payload authentication failed.") from error
        return loads(plaintext)

    @staticmethod
    def _fields(frame: Object) -> list[str]:
        return [
            routing(frame.get(key, ""), empty=True)
            for key in ("type", "id", "method", "event", "messageId")
        ]

    @classmethod
    def _aad(cls, frame: Object, direction: str) -> bytes:
        return "\n".join([MARKER, direction, *cls._fields(frame)]).encode()
