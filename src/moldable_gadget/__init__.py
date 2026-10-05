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
from .runtime import Action, GadgetRuntime, Sensor
from .storage import StateStore, default_state_directory

__all__ = [
    "GadgetClient",
    "GadgetRuntime",
    "Action",
    "Sensor",
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
