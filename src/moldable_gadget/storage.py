"""Owner-only state and a process lock: refresh tokens must have one writer."""

from __future__ import annotations

import fcntl
import os
import stat
import tempfile
from pathlib import Path

from .errors import GadgetError, PairingRequired
from .protocol import Object, dumps, loads, object_value


class StateStore:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory).expanduser().absolute()
        self.path = self.directory / "state.json"
        self._lock: int | None = None

    def __enter__(self) -> StateStore:
        if self._lock is not None:
            raise GadgetError("State store is already open.")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise GadgetError("State directory must be owned by you with mode 0700.")
        fd = os.open(self.directory / "state.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            self._check_file(fd)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, GadgetError) as error:
            os.close(fd)
            raise GadgetError("State is unsafe or in use by another gadget process.") from error
        self._lock = fd
        return self

    def __exit__(self, *_: object) -> None:
        if self._lock is not None:
            os.close(self._lock)
            self._lock = None

    def _require_lock(self) -> None:
        if self._lock is None:
            raise GadgetError("Use StateStore as a context manager.")

    @staticmethod
    def _check_file(fd: int) -> None:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_nlink != 1
        ):
            raise GadgetError("Credential files must be private, owner-only regular files.")

    def load(self) -> Object:
        self._require_lock()
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError as error:
            raise PairingRequired("No pairing saved. Run moldable-gadget pair.") from error
        with os.fdopen(fd, "rb") as stream:
            self._check_file(stream.fileno())
            return object_value(loads(stream.read(1024 * 1024 + 1)))

    def save(self, state: Object) -> None:
        self._require_lock()
        encoded = dumps(state).encode()
        fd, name = tempfile.mkstemp(prefix=".state-", dir=self.directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def forget(self) -> None:
        self._require_lock()
        self.path.unlink(missing_ok=True)


def default_state_directory() -> Path:
    return (
        Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
        / "moldable-gadget"
    )
