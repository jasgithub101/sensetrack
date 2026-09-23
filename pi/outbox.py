"""Local store-and-forward queue.

Every event is written here first, then sent. Nothing is deleted until the
backend confirms receipt, so moving between networks, a sleeping laptop, or a
dropped connection costs delay rather than data.
"""

import json
import logging
import sqlite3
from pathlib import Path
from typing import Iterator

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    payload  TEXT NOT NULL,
    image    BLOB,
    attempts INTEGER NOT NULL DEFAULT 0,
    created  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class Outbox:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.execute(_SCHEMA)
        self.conn.commit()

    def add(self, payload: dict, image: bytes | None) -> int:
        cursor = self.conn.execute(
            "INSERT INTO outbox (payload, image) VALUES (?, ?)",
            (json.dumps(payload), image),
        )
        self.conn.commit()
        return cursor.lastrowid

    def pending(self, limit: int = 50) -> Iterator[tuple[int, dict, bytes | None]]:
        """Oldest first, so replayed events keep their original order."""
        rows = self.conn.execute(
            "SELECT id, payload, image FROM outbox ORDER BY id ASC LIMIT ?", (limit,)
        ).fetchall()
        for row_id, payload, image in rows:
            yield row_id, json.loads(payload), image

    def mark_sent(self, row_id: int) -> None:
        self.conn.execute("DELETE FROM outbox WHERE id = ?", (row_id,))
        self.conn.commit()

    def mark_failed(self, row_id: int) -> None:
        self.conn.execute(
            "UPDATE outbox SET attempts = attempts + 1 WHERE id = ?", (row_id,)
        )
        self.conn.commit()

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]

    def close(self) -> None:
        self.conn.close()
