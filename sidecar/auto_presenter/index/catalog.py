"""Read the SQLite song library the Tauri shell owns.

The sidecar does not write this database. Tauri runs migrations and owns
every INSERT. We open read-only, without `immutable=1`, because a song added
minutes earlier can still sit only in `library.db-wal` — `bootstrap_golden`
already paid for that lesson.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from auto_presenter.index.fingerprint import CatalogSong, catalog_song_from_body

log = logging.getLogger(__name__)


def load_catalog(db_path: Path | str | None) -> list[CatalogSong]:
    """Every song in `library.db`, expanded to matcher-shaped slides.

    Missing / unreadable databases return an empty list: off-setlist
    recovery then no-ops, which is the safe failure. We never raise into
    the audio session for a feature that is supposed to be optional.
    """
    if not db_path:
        return []
    path = Path(db_path)
    if not path.exists():
        log.info("library catalog: %s does not exist yet", path)
        return []
    try:
        # URI so `mode=ro` is honoured; no `immutable=1` (WAL).
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        log.warning("library catalog: could not open %s: %s", path, exc)
        return []
    try:
        con.execute("PRAGMA query_only = ON")
        try:
            rows = con.execute(
                "SELECT id, title, body_text, slide_order FROM songs"
            ).fetchall()
        except sqlite3.OperationalError:
            # Very old fixtures without the slide_order migration.
            rows = [
                (i, t, b, None)
                for i, t, b in con.execute(
                    "SELECT id, title, body_text FROM songs"
                ).fetchall()
            ]
    except sqlite3.Error as exc:
        log.warning("library catalog: query failed: %s", exc)
        return []
    finally:
        con.close()

    songs: list[CatalogSong] = []
    for song_id, title, body, slide_order in rows:
        try:
            songs.append(
                catalog_song_from_body(
                    str(song_id),
                    str(title or ""),
                    str(body or ""),
                    str(slide_order) if slide_order else None,
                )
            )
        except Exception:
            log.exception("library catalog: skipped song %s", song_id)
    return songs
