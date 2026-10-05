"""Durable at-most-once action admission, including uncertain crash outcomes."""

from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path

from .errors import GadgetError
from .protocol import Object, dumps, loads, object_value


class CommandJournal:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        parent = path.parent.lstat()
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.getuid()
            or parent.st_mode & 0o077
        ):
            raise GadgetError("Command journal needs a private, owner-only directory.")
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
                or info.st_nlink != 1
            ):
                raise GadgetError("Command journal must be a private regular file.")
        finally:
            os.close(descriptor)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("""CREATE TABLE IF NOT EXISTS commands (
            id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, status TEXT NOT NULL,
            result TEXT, reported INTEGER NOT NULL DEFAULT 0
        )""")
        # An interrupted process may have changed hardware before persisting its
        # receipt. Never call that handler again merely because it restarted.
        with self.connection:
            self.connection.execute(
                "UPDATE commands SET status='unknown', result=?, reported=0 WHERE status='running'",
                (dumps({"reason": "Process stopped before a durable action outcome."}),),
            )

    def begin(self, identifier: str, fingerprint: str) -> bool:
        with self.connection:
            existing = self.connection.execute(
                "SELECT fingerprint FROM commands WHERE id=?", (identifier,)
            ).fetchone()
            if existing:
                if existing[0] != fingerprint:
                    raise GadgetError("Command ID was reused with different content.")
                self.connection.execute(
                    "UPDATE commands SET reported=0 WHERE id=? AND status!='running'", (identifier,)
                )
                return False
            self.connection.execute(
                "INSERT INTO commands (id,fingerprint,status) VALUES (?,?,'running')",
                (identifier, fingerprint),
            )
            return True

    def finish(self, identifier: str, status: str, result: Object) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE commands SET status=?, result=?, reported=0 WHERE id=?",
                (status, dumps(result), identifier),
            )

    def pending_results(self) -> list[Object]:
        return [
            {"commandId": identifier, "status": status, "result": object_value(loads(result))}
            for identifier, status, result in self.connection.execute(
                "SELECT id,status,result FROM commands WHERE reported=0 AND status!='running' "
                "ORDER BY rowid LIMIT 16"
            )
        ]

    def reported(self, identifier: str) -> None:
        with self.connection:
            self.connection.execute("UPDATE commands SET reported=1 WHERE id=?", (identifier,))

    def close(self) -> None:
        self.connection.close()
