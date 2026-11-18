"""The engine reads the same library the app has open."""

import json
import sqlite3
from pathlib import Path

from auto_presenter.library_file import OPEN_KEY, open_library_path
from auto_presenter.server_state import _state


def _home(tmp_path: Path, open_rel: str | None) -> Path:
    home = tmp_path / "library.db"
    con = sqlite3.connect(home)
    con.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL)")
    if open_rel is not None:
        con.execute("INSERT INTO settings VALUES (?, ?, 0)", (OPEN_KEY, json.dumps(open_rel)))
    con.commit()
    con.close()
    return home


def test_home_when_nothing_recorded(tmp_path: Path) -> None:
    home = _home(tmp_path, None)
    assert open_library_path(home) == home
    assert open_library_path(None) is None


def test_follows_the_recorded_library(tmp_path: Path) -> None:
    org = tmp_path / "orgs" / "org-1" / "library.db"
    org.parent.mkdir(parents=True)
    org.touch()
    home = _home(tmp_path, "orgs/org-1/library.db")
    assert open_library_path(home) == org

    previous = _state.home_db_path
    try:
        _state.library_db_path = home
        assert _state.library_db_path == org
    finally:
        _state.home_db_path = previous


def test_missing_or_escaping_paths_fall_back_home(tmp_path: Path) -> None:
    assert open_library_path(_home(tmp_path, "orgs/gone/library.db")) == tmp_path / "library.db"
    (tmp_path / "library.db").unlink()
    assert open_library_path(_home(tmp_path, "../elsewhere.db")) == tmp_path / "library.db"
    # Not a library file at all: still home.
    bogus = tmp_path / "nothing.db"
    assert open_library_path(bogus) == bogus
