"""Library search index: passages, incremental refresh, the disk cache, and
(when the model is installed) that it actually finds songs by meaning."""

from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from auto_presenter.library_search import index as index_mod
from auto_presenter.library_search.embedder import Embedder, default_model_dir
from auto_presenter.library_search.index import LibraryIndex, passages_of


class FakeEmbedder:
    """Deterministic vectors from word hashes: shared words -> similar vectors.
    Counts what it was asked to embed so refresh behaviour is observable."""

    def __init__(self) -> None:
        self.embedded: list[str] = []

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(384, dtype=np.float32)
        for w in text.lower().replace("/", " ").split():
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 384] += 1
        return v / max(np.linalg.norm(v), 1e-9)

    def queries(self, texts):
        return np.vstack([self._vec(t) for t in texts])

    def passages(self, texts):
        self.embedded.extend(texts)
        return np.vstack([self._vec(t) for t in texts]) if texts else np.zeros((0, 384), np.float32)


def make_db(path: Path, songs: dict[str, tuple[str, str]]) -> None:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE IF NOT EXISTS songs (id TEXT PRIMARY KEY, title TEXT, body_text TEXT)"
    )
    con.execute("DELETE FROM songs")
    con.executemany(
        "INSERT INTO songs VALUES (?, ?, ?)", [(k, t, b) for k, (t, b) in songs.items()]
    )
    con.commit()
    con.close()


# Public-domain hymn texts, so the meaning test has real lyrics to read.
SONGS = {
    "blood": (
        "Nothing But The Blood",
        "[v1]\nWhat can wash away my sin\nNothing but the blood of Jesus\n"
        "What can make me whole again\nNothing but the blood of Jesus\n\n"
        "[chorus]\nOh precious is the flow\nThat makes me white as snow\n"
        "No other fount I know\nNothing but the blood of Jesus",
    ),
    "xmas": (
        "Silent Night",
        "[v1]\nSilent night holy night\nAll is calm all is bright\n"
        "Round yon virgin mother and child\nHoly infant so tender and mild\n\n"
        "[v2]\nSilent night holy night\nShepherds quake at the sight\n"
        "Glories stream from heaven afar\nHeavenly hosts sing alleluia\nChrist the Saviour is born",
    ),
    "well": (
        "It Is Well With My Soul",
        "[v1]\nWhen peace like a river attendeth my way\nWhen sorrows like sea billows roll\n"
        "Whatever my lot Thou hast taught me to say\nIt is well it is well with my soul\n\n"
        "[v2]\nThough Satan should buffet though trials should come\n"
        "Let this blest assurance control",
    ),
}


def built(tmp_path: Path, songs=SONGS, emb=None) -> tuple[LibraryIndex, FakeEmbedder]:
    db = tmp_path / "library.db"
    make_db(db, songs)
    emb = emb or FakeEmbedder()
    idx = LibraryIndex(db, tmp_path / "search", embedder=emb)
    idx.refresh()
    idx.wait()
    return idx, emb


def test_passages_drop_tags_and_repeats():
    body = "[verse 1]\nA line\nB line\n\n[chorus]\nSing it\n\n[chorus]\nSing it\n\n[verse 2]\n\n"
    assert passages_of(body) == ["A line / B line", "Sing it"]


def test_first_build_runs_in_the_background_then_answers(tmp_path):
    idx, _ = built(tmp_path)
    status, hits = idx.search(["blood of Jesus"])
    assert status == "ready"
    assert hits[0].song_id == "blood"
    assert "blood of Jesus" in hits[0].passage


def test_an_edited_song_is_re_embedded_and_nothing_else(tmp_path):
    idx, emb = built(tmp_path)
    emb.embedded.clear()
    songs = dict(
        SONGS, well=("It Is Well", "[v1]\nYou part the waves so I can walk upon the shore")
    )
    make_db(tmp_path / "library.db", songs)
    status, hits = idx.search(["part the waves"])
    assert status == "ready" and hits[0].song_id == "well"
    # one passage + one whole-song text, for the one changed song
    assert emb.embedded == ["You part the waves so I can walk upon the shore"] * 2


def test_a_deleted_song_leaves_the_results(tmp_path):
    idx, _ = built(tmp_path)
    make_db(tmp_path / "library.db", {k: v for k, v in SONGS.items() if k != "xmas"})
    _, hits = idx.search(["silent night"])
    assert "xmas" not in {h.song_id for h in hits}


def test_an_unreadable_library_keeps_the_index(tmp_path, monkeypatch):
    """A read that fails (a WAL library opened read-only with its side files
    gone) is not every song deleted: the index stays, and nothing is
    re-embedded when the library can be read again."""
    idx, emb = built(tmp_path)
    emb.embedded.clear()
    monkeypatch.setattr(idx, "_read_library", lambda: None)
    status, hits = idx.search(["silent night"])
    assert status == "ready" and hits[0].song_id == "xmas"
    monkeypatch.undo()
    idx.search(["silent night"])
    assert emb.embedded == []


def test_a_restart_loads_the_cache_and_embeds_nothing(tmp_path):
    built(tmp_path)
    emb = FakeEmbedder()
    again = LibraryIndex(tmp_path / "library.db", tmp_path / "search", embedder=emb)
    status, hits = again.search(["silent night"])
    assert status == "ready" and hits[0].song_id == "xmas"
    assert emb.embedded == []


def test_a_cache_from_another_model_is_ignored(tmp_path, monkeypatch):
    built(tmp_path)
    monkeypatch.setattr(index_mod, "MODEL_ID", "some-other-model/9")
    emb = FakeEmbedder()
    again = LibraryIndex(tmp_path / "library.db", tmp_path / "search", embedder=emb)
    again.refresh()
    again.wait()
    assert len(emb.embedded) > 0


def test_no_model_means_unavailable_not_an_error(tmp_path, monkeypatch):
    make_db(tmp_path / "library.db", SONGS)
    monkeypatch.setattr(index_mod, "get_embedder", lambda: None)
    idx = LibraryIndex(tmp_path / "library.db", tmp_path / "search")
    assert idx.search(["anything"]) == ("unavailable", [])


MODEL = default_model_dir()
needs_model = pytest.mark.skipif(
    not (MODEL / "model.onnx").is_file(),
    reason="search model not installed (scripts/setup_search_model.py)",
)


@needs_model
def test_the_real_model_finds_songs_by_meaning(tmp_path):
    idx, _ = built(tmp_path, emb=Embedder(MODEL))
    # Few or no words shared with the lyrics: this is meaning, not keywords.
    # Occasions arrive as the UI's occasion phrases (src/lib/smartSearch).
    _, hits = idx.search(["born in a manger in Bethlehem", "angels sing glory to the newborn King"])
    assert hits[0].song_id == "xmas"
    _, hits = idx.search(["peace in times of grief"])
    assert hits[0].song_id == "well"
    _, hits = idx.search(["forgiveness and cleansing"])
    assert hits[0].song_id == "blood"


def test_a_background_build_waits_while_a_service_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(index_mod, "BUSY_POLL_S", 0.01)
    make_db(tmp_path / "library.db", SONGS)
    service_running = threading.Event()
    service_running.set()
    emb = FakeEmbedder()
    idx = LibraryIndex(
        tmp_path / "library.db", tmp_path / "search", embedder=emb, busy=service_running.is_set
    )
    assert idx.search(["blood"]) == ("indexing", [])
    time.sleep(0.1)
    assert emb.embedded == []  # nothing embedded while the service runs
    service_running.clear()
    idx.wait(5)
    status, hits = idx.search(["blood"])
    assert status == "ready" and hits[0].song_id == "blood"
