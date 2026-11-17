"""The library as vectors: one per song, one per passage, kept current.

A song's score for a query is half its whole-song similarity and half its
best passage's: whole songs read the overall theme ("Good Friday" 0.80 vs
0.10 for passages alone), the best passage catches a song whose one chorus
is the answer, and names the slide shown as "why it matched".

The index lives in memory and on disk (`search/` beside `library.db`), keyed by the
model and each song's text, so a restart re-embeds nothing and an edited
song is re-embedded on the next search. The first build for a library runs
in the background (~13 s for 326 songs on an M-series Mac); searches during
it report `indexing` and the UI shows keyword results meanwhile.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np

from auto_presenter.library_search.embedder import MODEL_ID, Embedder, get_embedder

log = logging.getLogger(__name__)

Status = Literal["ready", "indexing", "unavailable"]

#: Changes this many songs or fewer are embedded inline on a search (~5 ms a
#: passage); more go to the background so the search answers straight away.
INLINE_UPDATE_SONGS = 20
#: A background build embeds this many songs at a time, and between batches
#: waits for a running service to end: the live engine gets the CPU first.
BACKGROUND_BATCH_SONGS = 16
BUSY_POLL_S = 2.0

_TAG_LINE = re.compile(r"^\[.*\]$")
_BLOCK_SPLIT = re.compile(r"\n\s*\n")
_NON_WORD = re.compile(r"\W+")


def passages_of(body: str) -> list[str]:
    """A song's distinct passages: its blank-line separated blocks, without
    section tags, lines joined with " / ". A chorus sung four times is one
    passage — repeats would otherwise outvote the verses."""
    out: list[str] = []
    seen: set[str] = set()
    for block in _BLOCK_SPLIT.split(body or ""):
        lines = [ln.strip() for ln in block.splitlines()]
        lines = [ln for ln in lines if ln and not _TAG_LINE.match(ln)]
        if not lines:
            continue
        text = " / ".join(lines)
        key = _NON_WORD.sub(" ", text.lower()).strip()
        if key and key not in seen:
            seen.add(key)
            out.append(text)
    return out


def _song_hash(title: str, body: str) -> str:
    return hashlib.sha1(f"{MODEL_ID}\0{title}\0{body}".encode()).hexdigest()


@dataclass
class _Song:
    id: str
    hash: str
    passages: list[str]
    vec: np.ndarray  # (384,)
    passage_vecs: np.ndarray  # (n, 384)


@dataclass
class Hit:
    song_id: str
    score: float
    passage: str


class LibraryIndex:
    def __init__(
        self,
        db_path: Path,
        cache_dir: Path,
        embedder: Embedder | None = None,
        busy: Callable[[], bool] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.cache_dir = Path(cache_dir)
        self._embedder = embedder
        #: True while a service is running: background builds pause for it.
        self._busy = busy or (lambda: False)
        self._songs: dict[str, _Song] = {}
        self._loaded = False
        self._building: threading.Thread | None = None
        self._lock = threading.Lock()  # guards _songs and _building
        self._matrix: tuple | None = None  # cached stacked arrays, rebuilt on change

    # ------------------------------------------------------------ public

    def search(self, texts: list[str], limit: int = 50) -> tuple[Status, list[Hit]]:
        """Songs ranked against `texts` (averaged), best first."""
        emb = self._get_embedder()
        if emb is None:
            return "unavailable", []
        texts = [t.strip() for t in texts if t and t.strip()]
        if not texts:
            return "ready", []
        if not self.refresh():
            return "indexing", []
        with self._lock:
            matrix = self._stacked()
        if matrix is None:
            return "ready", []
        ids, song_vecs, pas_vecs, offsets, pas_text = matrix
        q = emb.queries(texts)  # (k, 384)
        whole = (song_vecs @ q.T).mean(1)  # (songs,)
        per_passage = (pas_vecs @ q.T).mean(1)  # (passages,)
        best = np.maximum.reduceat(per_passage, offsets[:-1])
        score = 0.5 * whole + 0.5 * best
        order = np.argsort(-score)[:limit]
        hits = []
        for i in order:
            lo, hi = offsets[i], offsets[i + 1]
            p = lo + int(np.argmax(per_passage[lo:hi]))
            hits.append(Hit(song_id=ids[i], score=float(score[i]), passage=pas_text[p]))
        return "ready", hits

    def refresh(self) -> bool:
        """Bring the index up to date with the library. True when it can be
        searched now; False while a first build (or a large update) runs in
        the background."""
        emb = self._get_embedder()
        if emb is None:
            return False
        with self._lock:
            if self._building is not None and self._building.is_alive():
                return self._loaded and bool(self._songs)
            if not self._loaded:
                self._load_cache()
                self._loaded = True
        rows = self._read_library()
        if rows is None:
            # Unreadable is not empty: keep what's indexed rather than read
            # it as every song deleted (and re-embed the lot next time).
            return self._loaded and bool(self._songs)
        changed = self._diff(rows)
        removed = set(self._songs) - {r[0] for r in rows}
        if not changed and not removed:
            return True
        if len(changed) <= INLINE_UPDATE_SONGS and self._songs:
            self._apply(emb, changed, removed)
            return True
        # First build, or a big import: answer from what's there (if
        # anything) and catch up in the background.
        with self._lock:
            if self._building is None or not self._building.is_alive():
                self._building = threading.Thread(
                    target=self._apply,
                    args=(emb, changed, removed, True),
                    name="library-index",
                    daemon=True,
                )
                self._building.start()
            return bool(self._songs)

    def wait(self, timeout: float | None = None) -> None:
        """Block until a background build finishes (tests, eval scripts)."""
        t = self._building
        if t is not None:
            t.join(timeout)

    # ------------------------------------------------------------ internals

    def _get_embedder(self) -> Embedder | None:
        if self._embedder is None:
            self._embedder = get_embedder()
        return self._embedder

    def _read_library(self) -> list[tuple[str, str, str]] | None:
        """Every song's (id, title, body); None when the library can't be read."""
        if not self.db_path.exists():
            return []
        try:
            con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            log.warning("library search: could not open %s: %s", self.db_path, exc)
            return None
        try:
            return [
                (str(i), str(t or ""), str(b or ""))
                for i, t, b in con.execute("SELECT id, title, body_text FROM songs")
            ]
        except sqlite3.Error as exc:
            log.warning("library search: query failed: %s", exc)
            return None
        finally:
            con.close()

    def _diff(self, rows: list[tuple[str, str, str]]) -> list[tuple[str, str, list[str]]]:
        changed = []
        for song_id, title, body in rows:
            h = _song_hash(title, body)
            cur = self._songs.get(song_id)
            if cur is None or cur.hash != h:
                changed.append((song_id, h, passages_of(body) or [title]))
        return changed

    def _apply(self, emb: Embedder, changed, removed, background: bool = False) -> None:
        try:
            new: dict[str, _Song] = {}
            step = BACKGROUND_BATCH_SONGS if background else max(1, len(changed))
            for start in range(0, len(changed), step):
                while background and self._busy():
                    time.sleep(BUSY_POLL_S)
                batch = changed[start : start + step]
                # Every passage of the batch in one batched pass, then each
                # song's whole text.
                flat = [p for _, _, ps in batch for p in ps]
                pv = emb.passages(flat)
                wv = emb.passages([" / ".join(ps) for _, _, ps in batch])
                k = 0
                for (song_id, h, ps), v in zip(batch, wv, strict=True):
                    new[song_id] = _Song(song_id, h, ps, v, pv[k : k + len(ps)])
                    k += len(ps)
            with self._lock:
                for song_id in removed:
                    self._songs.pop(song_id, None)
                self._songs.update(new)
                self._matrix = None
            log.info(
                "library search: embedded %d songs (%d passages), removed %d",
                len(changed),
                sum(len(ps) for _, _, ps in changed),
                len(removed),
            )
            self._save_cache()
        except Exception:
            log.exception("library search: indexing failed")

    def _stacked(self):
        if self._matrix is None and self._songs:
            songs = list(self._songs.values())
            ids = [s.id for s in songs]
            song_vecs = np.vstack([s.vec for s in songs]).astype(np.float32)
            pas_vecs = np.vstack([s.passage_vecs for s in songs]).astype(np.float32)
            lengths = [len(s.passages) for s in songs]
            offsets = np.concatenate([[0], np.cumsum(lengths)]).astype(np.int64)
            pas_text = [p for s in songs for p in s.passages]
            self._matrix = (ids, song_vecs, pas_vecs, offsets, pas_text)
        return self._matrix

    # ------------------------------------------------------------ disk cache

    @property
    def _meta_path(self) -> Path:
        return self.cache_dir / "library_index.json"

    @property
    def _vec_path(self) -> Path:
        return self.cache_dir / "library_index.npz"

    def _load_cache(self) -> None:
        try:
            meta = json.loads(self._meta_path.read_text())
            if meta.get("model") != MODEL_ID:
                return
            data = np.load(self._vec_path)
            song_vecs, pas_vecs = (
                data["songs"].astype(np.float32),
                data["passages"].astype(np.float32),
            )
            k = 0
            for i, s in enumerate(meta["songs"]):
                n = len(s["passages"])
                self._songs[s["id"]] = _Song(
                    s["id"], s["hash"], s["passages"], song_vecs[i], pas_vecs[k : k + n]
                )
                k += n
            log.info("library search: loaded %d songs from cache", len(self._songs))
        except FileNotFoundError:
            pass
        except Exception:
            log.warning("library search: ignoring unreadable cache", exc_info=True)
            self._songs = {}

    def _save_cache(self) -> None:
        with self._lock:
            songs = list(self._songs.values())
        if not songs:
            return
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            meta = {
                "model": MODEL_ID,
                "songs": [{"id": s.id, "hash": s.hash, "passages": s.passages} for s in songs],
            }
            tmp_vec = self._vec_path.with_suffix(".tmp.npz")
            np.savez(
                tmp_vec,
                songs=np.vstack([s.vec for s in songs]).astype(np.float16),
                passages=np.vstack([s.passage_vecs for s in songs]).astype(np.float16),
            )
            tmp_meta = self._meta_path.with_suffix(".tmp")
            tmp_meta.write_text(json.dumps(meta))
            tmp_vec.replace(self._vec_path)
            tmp_meta.replace(self._meta_path)
        except OSError:
            log.warning("library search: could not save the index cache", exc_info=True)


_INDEX: LibraryIndex | None = None
_INDEX_LOCK = threading.Lock()


def library_index(
    db_path: Path | None, busy: Callable[[], bool] | None = None
) -> LibraryIndex | None:
    """The engine's one index over `library.db`."""
    global _INDEX
    if db_path is None:
        return None
    with _INDEX_LOCK:
        if _INDEX is None or _INDEX.db_path != Path(db_path):
            # Beside the library it indexes, so a test or dev library never
            # shares (or clobbers) the real one's cache.
            _INDEX = LibraryIndex(Path(db_path), Path(db_path).parent / "search", busy=busy)
        return _INDEX

