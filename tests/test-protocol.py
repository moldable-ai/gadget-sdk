from __future__ import annotations

import copy
import json
import os
import uuid
from pathlib import Path

import pytest
from conftest import Peer, expiry

from moldable_gadget import GadgetError, PairingRequired, ProtocolError, StateStore
from moldable_gadget.pairing import parse_setup, relay_origin
from moldable_gadget.protocol import RoomCipher, loads, unb64

VECTOR = json.loads((Path(__file__).parent / "fixtures/protocol-v2.json").read_text())


def test_python_wire_bytes_match_independent_node_crypto_vector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cipher = RoomCipher(VECTOR["roomKey"], VECTOR["desktopId"], VECTOR["desktopPublicKey"])
    monkeypatch.setattr(os, "urandom", lambda length: unb64(VECTOR["nonce"]))
    monkeypatch.setattr(uuid, "uuid4", lambda: uuid.UUID(VECTOR["request"]["messageId"]))
    assert (
        cipher.request(VECTOR["request"]["id"], "bot.message.append", VECTOR["params"])
        == VECTOR["request"]
    )
    assert cipher.decode(VECTOR["response"]) == VECTOR["result"]


@pytest.mark.parametrize("field", ["id", "messageId", "ciphertext", "nonce", "signature"])
def test_routing_and_encrypted_payload_tampering_are_rejected(field: str) -> None:
    frame = copy.deepcopy(VECTOR["response"])
    if field in ("ciphertext", "nonce"):
        value = frame["encrypted"][field]
        frame["encrypted"][field] = ("A" if value[0] != "A" else "B") + value[1:]
    elif field in ("id", "messageId"):
        frame[field] = "00000000-0000-4000-8000-000000000099"
    else:
        frame[field] = "A" * 86
    cipher = RoomCipher(VECTOR["roomKey"], VECTOR["desktopId"], VECTOR["desktopPublicKey"])
    with pytest.raises(ProtocolError):
        cipher.decode(frame)


@pytest.mark.parametrize(
    "url,websocket",
    [
        ("http://relay.moldable.sh", False),
        ("https://relay.moldable.sh@evil.example", False),
        ("https://relay.moldable.sh/path", False),
        ("https://relay.moldable.sh?token=secret", False),
        ("ws://192.168.1.5/socket", True),
    ],
)
def test_untrusted_or_cleartext_origins_rejected(url: str, websocket: bool) -> None:
    with pytest.raises(ProtocolError):
        relay_origin(url, websocket=websocket, local=True)


async def test_setup_rejects_expiry_wrong_origin_and_wrong_key_lengths(peer: Peer) -> None:
    with pytest.raises(PairingRequired):
        parse_setup(peer.setup_link(expiresAt=expiry(-1)), peer.origin, local=True)
    with pytest.raises(ProtocolError):
        parse_setup(peer.setup_link(), "https://another.example", local=True)
    with pytest.raises(ProtocolError):
        parse_setup(peer.setup_link(e2eeKey="abcd"), peer.origin, local=True)


@pytest.mark.parametrize(
    "raw", [b'{"id":1,"id":2}', b'{"value":NaN}', b'"' + b"a" * (1024 * 1024) + b'"']
)
def test_ambiguous_nonfinite_and_oversized_json_is_rejected(raw: bytes) -> None:
    with pytest.raises(ProtocolError):
        loads(raw)


def test_credential_store_rejects_shared_permissions_and_symlinks(tmp_path: Path) -> None:
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir(mode=0o755)
    with pytest.raises(GadgetError, match="0700"):
        with StateStore(unsafe):
            pass
    private = tmp_path / "private"
    with StateStore(private) as store:
        store.save({"secret": "synthetic"})
    (private / "state.json").chmod(0o644)
    with StateStore(private) as store:
        with pytest.raises(GadgetError, match="owner-only"):
            store.load()
    link = tmp_path / "link"
    link.symlink_to(private, target_is_directory=True)
    with pytest.raises(GadgetError, match="0700"):
        with StateStore(link):
            pass
