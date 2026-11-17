"""Read-only load of Tauri's `library.db`."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from auto_presenter.index.catalog import load_catalog


def test_missing_path_is_empty_not_an_error():
    assert load_catalog(None) == []
    assert load_catalog(Path("/no/such/library.db")) == []


def test_reads_songs_without_immutable_flag(tmp_path: Path):
    db = tmp_path / "library.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE songs (id TEXT, title TEXT, body_text TEXT, slide_order TEXT)"
    )
    con.execute(
        "INSERT INTO songs VALUES (?, ?, ?, ?)",
        ("s1", "Agnus Dei", "[chorus]\nHoly holy\n", None),
    )
    con.commit()
    con.close()
    songs = load_catalog(db)
    assert len(songs) == 1
    assert songs[0].song_id == "s1"
    assert songs[0].title == "Agnus Dei"
    assert any(words for words in songs[0].slides)
