"""The sidecar's own record of past performances: `learning.db`, beside the
library.

Kept out of the app's `library.db` deliberately. The sidecar writes it while
a service runs, and owning the file means no write contention with the app,
no migration in the app for a format that will change, and an eval or dev
session pointed at a throwaway library writes to a throwaway store too.
Rows are anchored to lyric text (`summary.slide_key`), so a deleted or
edited song leaves rows that simply stop binding.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from auto_presenter.learning.summary import PerformanceSummary

log = logging.getLogger(__name__)

#: Most performances of one song read back (newest first); older ones have
#: decayed to nothing anyway.
MAX_ROWS = 200

_SCHEMA = """
CREATE TABLE IF NOT EXISTS performances (
  id            TEXT PRIMARY KEY,
  song_id       TEXT NOT NULL,
  started_at_ms INTEGER NOT NULL,
  provenance    TEXT NOT NULL,
  weight        REAL NOT NULL,
  final         INTEGER NOT NULL,
  data_json     TEXT NOT NULL,
  updated_at_ms INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS performances_song ON performances(song_id, started_at_ms);
"""


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.Lock()
        with self._connect() as con:
            con.executescript(_SCHEMA)

    @classmethod
    def beside(cls, library_db: Path | str | None) -> Store | None:
        """The store next to the library, or None without one (tests, browser dev)."""
        if not library_db:
            return None
        try:
            return cls(Path(library_db).parent / "learning.db")
        except sqlite3.Error as exc:
            log.warning("learning store unavailable: %s", exc)
            return None

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path, timeout=5.0)
        try:
            with con:  # commits, or rolls back on error
                yield con
        finally:
            con.close()

    def save(self, s: PerformanceSummary) -> None:
        with self._lock, self._connect() as con:
            con.execute(
                "INSERT INTO performances VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET weight = excluded.weight, final = excluded.final, "
                "data_json = excluded.data_json, updated_at_ms = excluded.updated_at_ms",
                (
                    s.id,
                    s.song_id,
                    s.started_at_ms,
                    s.provenance,
                    s.weight,
                    int(s.final),
                    s.to_json(),
                    int(time.time() * 1000),
                ),
            )

    def load(self, song_id: str, limit: int = MAX_ROWS) -> list[PerformanceSummary]:
        """A song's performances, newest first; unreadable rows skipped."""
        with self._lock, self._connect() as con:
            rows = con.execute(
                "SELECT data_json FROM performances WHERE song_id = ? "
                "ORDER BY started_at_ms DESC LIMIT ?",
                (song_id, limit),
            ).fetchall()
        out = [PerformanceSummary.from_json(r[0]) for r in rows]
        return [s for s in out if s is not None]

    def delete(self, performance_id: str) -> None:
        with self._lock, self._connect() as con:
            con.execute("DELETE FROM performances WHERE id = ?", (performance_id,))

    def counts(self) -> dict[str, int]:
        """song id -> stored performances (debug panel)."""
        with self._lock, self._connect() as con:
            return dict(con.execute("SELECT song_id, COUNT(*) FROM performances GROUP BY song_id"))
