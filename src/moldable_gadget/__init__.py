"""Build trusted devices that talk to a Moldable desktop."""

from .client import GadgetClient
from .errors import (
    ConnectionLost,
    GadgetError,
    PairingRequired,
    ProtocolError,
    RemoteError,
    ReplayRequired,
)
from .pairing import pair
from .storage import StateStore, default_state_directory

__all__ = [
    "GadgetClient",
    "GadgetError",
    "ConnectionLost",
    "PairingRequired",
    "ProtocolError",
    "RemoteError",
    "ReplayRequired",
    "StateStore",
    "default_state_directory",
    "pair",
]
