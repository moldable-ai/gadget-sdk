"""Stable error categories for device applications."""


class GadgetError(Exception):
    """Base error; messages never include credentials or pairing payloads."""


class ProtocolError(GadgetError):
    """Malformed or unauthenticated peer data. Do not retry blindly."""


class PairingRequired(GadgetError):
    """The saved pairing is absent, revoked, or no longer recoverable."""


class ConnectionLost(GadgetError):
    """Delivery may be uncertain. Reuse the mutation ID when retrying."""


class RemoteError(GadgetError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class ReplayRequired(GadgetError):
    """Recover from the last host-issued cursor before consuming more events."""
