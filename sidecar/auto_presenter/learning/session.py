"""The learning observer for one audio session.

Fed from what the session already produces, never asked for anything:

* `on_frame_time` (capture thread): the audio clock; one float store.
* `on_words` (event loop): the transcript's final words, as the matcher got
  them, each filed under the song on screen when it was *sung* (words arrive
  ~3 s late; a song change in between must not drag them across).
* `on_slide_shown` (event loop): every slide the screen showed and who put it
  there (`manual`, `override`, `voice`, `ai`).

A background thread wakes every `LOOKBACK_EVERY_S`, aligns each song that
heard new words (`lookback.align`), and writes the song's performance row,
replacing its draft. `stop` writes the final rows. A song the screen only
passed through (fewer than `MIN_WORDS` heard) is not a performance.
"""

from __future__ import annotations

import bisect
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field

import numpy as np

from auto_presenter.follower import score as sc
from auto_presenter.learning import labels
from auto_presenter.learning.lookback import HeardWord, SongModel, align
from auto_presenter.learning.store import Store
from auto_presenter.learning.summary import PassRecord, PerformanceSummary, slide_key
from auto_presenter.protocol import SetlistContent

log = logging.getLogger(__name__)

#: Wall seconds between background look-backs.
LOOKBACK_EVERY_S = 15.0
#: Fewer heard words than this and the song was passed through, not sung.
MIN_WORDS = 20


@dataclass
class _Perf:
    item_idx: int
    song_id: str
    slides: list[list[str]]
    model: SongModel
    id: str = field(default_factory=lambda: f"perf_{uuid.uuid4().hex[:16]}")
    started_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    words: list[HeardWord] = field(default_factory=list)
    moves: list[tuple[float, int, str]] = field(default_factory=list)
    dirty: bool = False
    summary: PerformanceSummary | None = None
    lookback_ms: float = 0.0


def identical_canon(slides: list[list[str]]):
    """slide -> its lowest identical-text copy."""
    first: dict[str, int] = {}
    out: dict[int, int] = {}
    for si, sw in enumerate(slides):
        k = " ".join(sc.norm(w) for w in sw)
        out[si] = first.setdefault(k, si)
    return lambda s: out.get(s, s)


class LearningSession:
    def __init__(
        self,
        content: SetlistContent,
        store: Store | None,
        provenance: str = "live",
        start: tuple[int, int | None] | None = None,
    ):
        self.store = store
        self.provenance = provenance
        self.audio_now = 0.0
        self._lock = threading.Lock()
        #: one look-back at a time: the final rows must not be overwritten by
        #: a background pass still saving its draft
        self._tick_lock = threading.Lock()
        self._items = {i.item_idx: i for i in content.items if not i.spoken}
        self._perfs: dict[int, _Perf] = {}
        #: (audio time, item, slide, source), in time order.
        self._timeline: list[tuple[float, int, int | None, str]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="learning-lookback", daemon=True)
        self.passes_run = 0
        if start is not None:
            self.on_slide_shown(start[0], start[1], "start", t=0.0)

    # ------------------------------------------------------------ feeds

    def start(self) -> None:
        self._thread.start()

    def on_frame_time(self, t_end: float) -> None:
        self.audio_now = float(t_end)

    def on_slide_shown(
        self, item_idx: int, slide_idx: int | None, source: str, t: float | None = None
    ) -> None:
        t = self.audio_now if t is None else t
        with self._lock:
            self._timeline.append((t, item_idx, slide_idx, source))
            perf = self._perf(item_idx)
            if perf is not None and slide_idx is not None:
                perf.moves.append((t, slide_idx, source))
                perf.dirty = True

    def on_words(self, words) -> None:
        """`asr.types.Word`s from a final transcript event."""
        with self._lock:
            times = [e[0] for e in self._timeline]
            for w in words:
                if w.t_start is None or not sc.norm(w.text):
                    continue
                i = bisect.bisect_right(times, float(w.t_start)) - 1
                if i < 0:
                    continue
                perf = self._perf(self._timeline[i][1])
                if perf is not None:
                    perf.words.append(HeardWord(float(w.t_start), w.text))
                    perf.dirty = True

    def update_content(self, content: SetlistContent) -> None:
        """A setlist changed mid-service: songs whose slides changed are
        re-modelled; their words so far are kept."""
        with self._lock:
            old_song = {idx: it.song_id for idx, it in self._items.items()}
            self._items = {i.item_idx: i for i in content.items if not i.spoken}
            # the timeline names items by place: a reorder moves a song to
            # another place, so words heard late for it still find it. A song
            # that stayed put keeps its place; the others take, in order, a
            # place holding the same song that nobody kept (a song in the
            # setlist twice: each copy its own place)
            new_song = {idx: it.song_id for idx, it in self._items.items()}
            stayed = {idx for idx, sid in old_song.items() if new_song.get(idx) == sid}
            taken = set(stayed)  # new places already claimed
            moved: dict[int, int] = {}
            for idx, sid in sorted(old_song.items()):
                if idx in stayed:
                    continue
                to = next(
                    (j for j in sorted(new_song) if new_song[j] == sid and j not in taken), None
                )
                if to is not None:
                    moved[idx] = to
                    taken.add(to)
            if moved:
                self._timeline = [(t, moved.get(i, i), s, src) for t, i, s, src in self._timeline]
                # all at once: one at a time, a song moved onto a place not
                # yet moved out of overwrote the one waiting there
                perfs = {}
                for idx, perf in self._perfs.items():
                    to = moved.get(idx, idx)
                    perf.item_idx = to
                    perfs[to] = perf
                self._perfs = perfs
            for idx, perf in list(self._perfs.items()):
                item = self._items.get(idx)
                if item is None or item.song_id != perf.song_id:
                    del self._perfs[idx]  # the item now holds another song
                    continue
                slides = [list(s) for s in item.slides]
                if slides != perf.slides:
                    perf.slides, perf.model, perf.dirty = slides, SongModel(slides), True

    def _perf(self, item_idx: int) -> _Perf | None:
        perf = self._perfs.get(item_idx)
        if perf is None:
            item = self._items.get(item_idx)
            if item is None:
                return None
            slides = [list(s) for s in item.slides]
            perf = _Perf(item_idx, item.song_id, slides, SongModel(slides))
            self._perfs[item_idx] = perf
        return perf

    # ------------------------------------------------------------ look-back

    def _run(self) -> None:
        while not self._stop.wait(LOOKBACK_EVERY_S):
            try:
                self.tick()
            except Exception:
                log.exception("learning look-back failed; trying again next time")

    def tick(self, final: bool = False) -> None:
        """Align every song with new words and save its row."""
        with self._tick_lock:
            self._tick(final)

    def _tick(self, final: bool) -> None:
        with self._lock:
            todo = [p for p in self._perfs.values() if p.dirty or final]
            work = [(p, list(p.words), list(p.moves)) for p in todo]
            for p in todo:
                p.dirty = False
        for perf, words, moves in work:
            if len(words) < MIN_WORDS:
                continue
            started = time.perf_counter()
            summary = self._summarize(perf, words, moves, final)
            perf.lookback_ms = (time.perf_counter() - started) * 1000
            perf.summary = summary
            self.passes_run += 1
            if self.store is not None:
                try:
                    self.store.save(summary)
                except Exception:
                    log.exception("could not save performance %s", summary.id)

    def stop(self) -> None:
        """Final rows for every song; safe to call once the session stopped,
        and more than once (only the first writes)."""
        if self._stop.is_set():
            return
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5.0)
        self.tick(final=True)

    def _summarize(
        self, perf: _Perf, words: list[HeardWord], moves: list[tuple[float, int, str]], final: bool
    ) -> PerformanceSummary:
        al = align(perf.model, words)
        t0 = words[0].t
        keys = [slide_key(sw) for sw in perf.slides]
        canon = identical_canon(perf.slides)
        cleaned = labels.clean_moves(moves)
        led = labels.led_by(cleaned)
        agree = labels.agreement(al.passes, cleaned, canon)
        n = sum(p.n_words for p in al.passes)
        mean_conf = sum(p.confidence * p.n_words for p in al.passes) / n if n else 0.0
        return PerformanceSummary(
            id=perf.id,
            song_id=perf.song_id,
            started_at_ms=perf.started_at_ms,
            provenance=self.provenance,
            led_by=led,
            weight=labels.performance_weight(self.provenance, led, agree, mean_conf),
            passes=[
                PassRecord(
                    slide=p.slide,
                    key=keys[p.slide],
                    t_start=round(p.t_start - t0, 2),
                    t_end=round(p.t_end - t0, 2),
                    onsets=[(j, round(t - p.t_start, 2)) for j, t in p.onsets],
                    confidence=round(p.confidence, 3),
                )
                for p in al.passes
            ],
            operator=[(round(t - t0, 2), s, src) for t, s, src in cleaned],
            agreement=None if agree is None else round(agree, 3),
            slide_keys=keys,
            heard_words=len(words),
            final=final,
            notes={"mean_confidence": round(float(mean_conf), 3)},
        )

    # ------------------------------------------------------------ debug panel

    def items(self) -> list:
        """The setlist as this session now has it."""
        with self._lock:
            return list(self._items.values())

    def performance(self, item_idx: int) -> tuple[PerformanceSummary | None, int, float]:
        """This session's latest look-back of a song: (summary, heard words, ms)."""
        with self._lock:
            p = self._perfs.get(item_idx)
            return (None, 0, 0.0) if p is None else (p.summary, len(p.words), p.lookback_ms)

    def snapshot(self) -> dict:
        with self._lock:
            perfs = list(self._perfs.values())
        out = []
        for p in perfs:
            s = p.summary
            out.append(
                {
                    "item_idx": p.item_idx,
                    "song_id": p.song_id,
                    "heard_words": len(p.words),
                    "moves": len(p.moves),
                    "lookback_ms": round(p.lookback_ms, 1),
                    "summary": None
                    if s is None
                    else {
                        "led_by": s.led_by,
                        "weight": s.weight,
                        "agreement": s.agreement,
                        "mean_confidence": s.notes.get("mean_confidence"),
                        "passes": [[q.slide, q.t_start, q.confidence] for q in s.passes],
                        "operator": [[t, sl, src] for t, sl, src in s.operator],
                        "final": s.final,
                    },
                }
            )
        return {
            "provenance": self.provenance,
            "audio_now": round(self.audio_now, 1),
            "passes_run": self.passes_run,
            "songs": out,
            "median_lookback_ms": float(np.median([p.lookback_ms for p in perfs]))
            if perfs
            else 0.0,
        }
