"""Which library file is open.

Each library is its own SQLite file (docs/roadmap/LIBRARIES_AND_SYNC.md). The
app passes this computer's own library (`--db-path`), and records the one
that's open in that file's settings as a path relative to the app's folder
(`src-tauri/src/libraries.rs`). Reading it each time, rather than being told,
keeps an engine that restarted mid-week on the right library.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

OPEN_KEY = "open_library"
_ORG_FILE = re.compile(r"orgs/(?!this-computer/)[A-Za-z0-9_-]+/library\.db")


def open_library_path(home: Path | None) -> Path | None:
    """The open library's file; `home` when none is recorded, the record
    can't be read, or its file has gone."""
    if home is None:
        return None
    try:
        con = sqlite3.connect(f"{home.resolve().as_uri()}?mode=ro", uri=True, timeout=1.0)
        try:
            row = con.execute("SELECT value FROM settings WHERE key = ?", (OPEN_KEY,)).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return home
    try:
        rel = json.loads(row[0]) if row else ""
    except (TypeError, ValueError):
        return home
    # Only ever `orgs/<id>/library.db` (as `libraries.rs` writes it): an
    # absolute path, `..` or a drive letter opens this computer's library.
    if not isinstance(rel, str) or not _ORG_FILE.fullmatch(rel):
        return home
    path = home.parent / rel
    return path if path.exists() else home
