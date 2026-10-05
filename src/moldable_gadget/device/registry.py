"""Private device enrollment; every read checks current approval/revocation.

Short-lived file locks allow the running bridge and its local management CLI to
share this store without an unauthenticated administration network endpoint.
"""

from __future__ import annotations

import hmac
import time
from pathlib import Path

from ..errors import GadgetError, PairingRequired
from ..protocol import Object, object_value, string
from ..storage import StateStore
from .wire import decode_b64, standard_b64

MAX_DEVICES = 32
PENDING_SECONDS = 300


class DeviceRegistry:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    @staticmethod
    def _load(store: StateStore) -> Object:
        try:
            return store.load()
        except PairingRequired:
            return {}

    def lookup(self, identifier: str) -> Object | None:
        with StateStore(self.directory) as store:
            value = self._load(store).get(identifier)
            return object_value(value) if value is not None else None

    def pending(self, identifier: str, key: bytes, name: str) -> Object:
        with StateStore(self.directory) as store:
            state = self._load(store)
            existing = state.get(identifier)
            if existing is not None:
                record = object_value(existing)
                if not hmac.compare_digest(decode_b64(record.get("key"), 32), key):
                    raise GadgetError("Device key changed.")
                return record
            if len(state) >= MAX_DEVICES:
                raise GadgetError("Device registry is full; remove unused pending devices.")
            record: Object = {
                "key": standard_b64(key),
                "name": name[:80],
                "status": "pending",
                "expires": int(time.time()) + PENDING_SECONDS,
            }
            state[identifier] = record
            store.save(state)
            return record

    def approve(self, identifier: str) -> None:
        with StateStore(self.directory) as store:
            state = self._load(store)
            record = object_value(state.get(identifier))
            if record.get("status") != "pending" or float(record.get("expires", 0)) < time.time():
                raise GadgetError(
                    "Pending request expired. Forget this device, then reconnect to enroll."
                )
            record["status"] = "approved"
            store.save(state)

    def revoke(self, identifier: str) -> None:
        with StateStore(self.directory) as store:
            state = self._load(store)
            record = object_value(state.get(identifier))
            record["status"] = "revoked"
            store.save(state)

    def forget(self, identifier: str) -> None:
        with StateStore(self.directory) as store:
            state = self._load(store)
            state.pop(identifier, None)
            store.save(state)

    def list(self) -> list[Object]:
        with StateStore(self.directory) as store:
            return [
                {
                    "id": identifier,
                    "name": string(object_value(value).get("name")),
                    "status": string(object_value(value).get("status")),
                }
                for identifier, value in self._load(store).items()
            ]
