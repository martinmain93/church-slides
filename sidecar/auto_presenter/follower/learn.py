"""Learning a song's music during the service, for songs with no lead sheet.

A follower on library slides alone knows no rhythm and no chords. A look-back
running a little behind live (no hurry, so it can afford what the live path
cannot) listens to each slide as the band sings it and writes what it heard
into the same reference the live follower uses:

* which slide was sung when: `learning.lookback.align` over the words heard
  so far, forward and backward; a pass is used only when it is done and the
  look-back is sure of it (`MIN_CONFIDENCE`, from the line's first words,
  most of them heard);
* where each word fell: the slide's lyrics forced onto that stretch of audio
  with the live scorer's own DTW timing (`Scorer.word_starts`), the previous
  line's last words in front so the lead-in is not counted as the first word;
* in musical time: places on the beat grid (Beat This, cleaned), a line as
  many beats long as the band took to reach the next sure line, an
  instrumental break included;
* the chords the session's analyser heard over it, per cell, in the band's key.

What it learns about one slide it also lends, until they are heard, to the
slides likely sung to the same tune: by their text's metre (`metre_groups`),
as a hymn's verses share theirs. A slide's material follows the look-back's
current view of its pass for `REVISE_S`, then freezes: hindsight on the same
pass refines it, a later stray word cannot replace it.

Replayed on the 8 live traces (research/spikes/music_only/tune_share_replay.py,
library follower, wrong / perceived): library alone 0.219 / 0.254; learned
with metre grouping 0.189 / 0.229; with the lead sheets' own tune groups (the
ceiling of grouping) 0.177 / 0.213.
"""

from __future__ import annotations

import collections
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from auto_presenter.follower import score as sc
from auto_presenter.follower.sheet import CELL
from auto_presenter.follower.spec import NONE, SegmentSpec
from auto_presenter.learning.lookback import HeardWord, SongModel, align

log = logging.getLogger(__name__)

#: Seconds of audio between look-backs.
TICK_S = 5.0
#: A pass is used only once it ended at least this long ago.
SETTLE_S = 2.0
MIN_CONFIDENCE = 0.7
MAX_FIRST_WORD = 1
MIN_HEARD = 0.6
#: A pass's words must be the lyric word they are placed on (or one either
#: side) at least this often. The look-back places every heard word on some
#: lyric word, talk included, and its confidence only compares this song's
#: slides with each other: a children's talk before Awesome God ("I'm not a
#: man I'm a man") passed as its chorus at confidence 0.7-0.8, matching 0% of
#: its words; sung passes match 26%+ (5th percentile, 8 live traces).
#: Swept 0.2-0.8 (replay, wrong / perceived): 0.2 0.181/0.231, 0.3
#: 0.179/0.230, 0.4 0.187/0.237, then regressions (Reckless Love 0.236 ->
#: 0.297 at 0.5, 0.357 at 0.8 with new jumps): a song learned in patches
#: does worse than one learned whole or not at all.
MIN_LEXICAL = 0.3
#: A learned line with more than this share of its words on the same cell as
#: the word before is a collapsed alignment, not a rhythm (median 0.07 on
#: sound passes; the bad ones 0.5-0.8).
MAX_COLLAPSED = 0.3
#: A line runs to the next sure line, an instrumental break included, unless
#: the gap is longer than this (talk, another song).
MAX_GAP_S = 60.0
#: Longest stretch forced alignment is asked to place (a Whisper window).
MAX_LINE_S = 28.0
#: A cell's chord is learned only when the analyser was this sure of it.
CHORD_MIN = 0.35
#: Seconds a learned slide follows the look-back's current view of its pass.
REVISE_S = 30.0
#: A beat track with this share of its gaps over twice the median (missed
#: beats) is not measured in: the song keeps its library slides.
BEAT_GATE = 0.08
#: A song is learned once two usable grids agree on its tempo within this
#: share; one whose tempo then moves by more (the tracker switching between
#: half and double time) is not learned at all: it forgets what it learned
#: and keeps its library slides.
#: Over growing stretches of the 7 single-song recordings, WABN's grid read
#: 62 then 120-125 bpm and the medley's 115 then 65; the rest held within 6%.
#: Those two are the songs learning made worse: WABN replayed on one
#: consistent grid 0.198 -> 0.243-0.254 and live 0.249 -> 0.295.
TEMPO_TOL = 0.15
#: Chord frames per second (`LiveFollowers` pushes one per 0.1 s).
CHORD_FPS = 10.0


# ---------------------------------------------------------------- tune groups


def syllables(word: str) -> int:
    """A rough English syllable count: vowel groups, a silent final e dropped."""
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 0
    n = len(re.findall(r"[aeiouy]+", w))
    if w.endswith("e") and not w.endswith(("le", "ee")) and n > 1:
        n -= 1
    return max(1, n)


def metre_groups(slides: list[list[str]], word_ratio: float = 1.25) -> list[set[int]]:
    """Slides likely sung to one tune, from their text: the same number of
    syllables and of words within `word_ratio` (or the same words). The only
    grouping a session has before a slide is sung. Against the lead sheets'
    melodies on the 8 goldens it finds most shared tunes (a hymn's verses all
    of them) but also groups unlike lines of a like length."""
    info = {}
    for si, sw in enumerate(slides):
        ws = [w for w in (sc.norm(x) for x in sw) if w]
        if ws:
            info[si] = (sum(syllables(w) for w in ws), len(ws), " ".join(ws))
    parent = {s: s for s in info}

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    ss = sorted(info)
    for i, a in enumerate(ss):
        for b in ss[i + 1 :]:
            (sa, wa, ta), (sb, wb, tb) = info[a], info[b]
            if ta == tb or (sa == sb and max(wa, wb) / max(1, min(wa, wb)) <= word_ratio):
                parent[find(a)] = find(b)
    out: dict[int, set[int]] = collections.defaultdict(set)
    for s in ss:
        out[find(s)].add(s)
    return list(out.values())


# ---------------------------------------------------------------- beats


def clean_beats(beats: np.ndarray, half_window: int = 8) -> np.ndarray:
    """A steadier grid: a gap of 1.5x or more the local beat gets evenly spaced
    beats put back; a beat closer than half the local beat is dropped."""
    b = np.asarray(beats, float)
    if len(b) < 4:
        return b
    gaps = np.diff(b)
    local = np.array(
        [np.median(gaps[max(0, i - half_window) : i + half_window + 1]) for i in range(len(gaps))]
    )
    out = [b[0]]
    for i in range(len(gaps)):
        nxt = b[i + 1]
        if nxt - out[-1] < 0.5 * local[i]:
            continue
        n = round((nxt - out[-1]) / local[i])
        if n >= 2 and (nxt - out[-1]) >= 1.5 * local[i]:
            out.extend(out[-1] + (nxt - out[-1]) * k / n for k in range(1, n))
        out.append(nxt)
    return np.asarray(out)


def beats_usable(beats: np.ndarray | None) -> bool:
    if beats is None or len(beats) < 8:
        return False
    g = np.diff(np.asarray(beats, float))
    return bool(np.mean(g > 2 * np.median(g)) < BEAT_GATE)


# ---------------------------------------------------------------- one slide


def _fit(p) -> tuple[float, float] | None:
    """(time of word 0, seconds per word) through a pass's words (Theil-Sen)."""
    pts = sorted({j: t for j, t in reversed(p.onsets)}.items())
    if len(pts) < 3:
        return None
    js = np.array([j for j, _ in pts], float)
    ts = np.array([t for _, t in pts], float)
    sl = [(ts[b] - ts[a]) / (js[b] - js[a]) for a in range(len(js)) for b in range(a + 1, len(js))]
    slope = float(np.median(sl))
    if not 0.05 < slope < 3.0:
        return None
    return float(np.median(ts - slope * js)), slope


def beat_segment(
    slide: int, words: list[str], on: np.ndarray, end: float, beats: np.ndarray, chords
) -> SegmentSpec:
    """A slide in musical time: word places and length on the beat grid,
    the chord heard over each cell (`chords(t0, t1)` -> mean probs or None)."""
    idx = np.arange(len(beats), dtype=float)
    per = (beats[-1] - beats[0]) / max(1, len(beats) - 1)

    def b_of(t: float) -> float:
        if beats[0] <= t <= beats[-1]:
            return float(np.interp(t, beats, idx))
        return (t - beats[0]) / per

    def t_of(b: float) -> float:
        return float(np.interp(b, idx, beats)) if 0 <= b <= idx[-1] else beats[0] + b * per

    b0 = b_of(float(on[0]))
    cells = max(len(words) + 1, round((b_of(end) - b0) / CELL) + 1)
    offs = [min(cells - 1, max(0, round((b_of(float(t)) - b0) / CELL))) for t in on]
    offs = list(np.maximum.accumulate(offs))
    chord = np.full(cells, NONE, np.int16)
    for c in range(cells):
        pr = chords(t_of(b0 + c * CELL), t_of(b0 + (c + 1) * CELL))
        if pr is not None:
            k = int(np.argmax(pr[:24]))
            if pr[k] >= CHORD_MIN:
                chord[c] = k
    return SegmentSpec(
        slide=slide,
        cells=cells,
        words=[(w, int(o)) for w, o in zip(words, offs, strict=True)],
        midi=np.full(cells, NONE, np.int16),
        chord=chord,
    )


def lexical_match(p, lyric: list[str], text_at: dict[float, str]) -> float:
    """Share of pass `p`'s words that are the lyric word they are placed on,
    or one either side."""
    hits = 0
    for j, t in p.onsets:
        w = text_at.get(round(t, 3))
        if w and w in lyric[max(0, j - 1) : j + 2]:
            hits += 1
    return hits / max(1, len(p.onsets))


def collapsed(seg: SegmentSpec) -> bool:
    """Too many of the line's words on the cell of the word before them."""
    offs = np.array([o for _w, o in seg.words])
    return len(offs) > 2 and float(np.mean(np.diff(offs) == 0)) > MAX_COLLAPSED


def borrow(src: SegmentSpec, slide: int, words: list[str]) -> SegmentSpec:
    """A tune-mate's segment, its word places stretched to `words`."""
    so = np.array([o for _w, o in src.words], float)
    pos = np.interp(np.linspace(0, len(so) - 1, len(words)), np.arange(len(so)), so)
    offs = np.minimum(np.round(pos).astype(int), src.cells - 1)
    return SegmentSpec(
        slide=slide,
        cells=src.cells,
        words=[(w, int(o)) for w, o in zip(words, offs, strict=True)],
        midi=src.midi.copy(),
        chord=src.chord.copy(),
    )


# ---------------------------------------------------------------- the learner


#: (audio, words) -> each word's start in seconds into `audio`, or None.
Aligner = Callable[[np.ndarray, list[str]], "list[float] | None"]
#: (audio) -> beat times in seconds, or None.
BeatTracker = Callable[[np.ndarray], "np.ndarray | None"]


@dataclass
class _Song:
    item_idx: int
    slides: list[list[str]]
    norm: dict[int, list[str]]
    model: SongModel
    mates: dict[int, set[int]]
    #: (audio time, arrival time, text)
    words: list[tuple[float, float, str]] = field(default_factory=list)
    #: audio of this song, contiguous from `audio_t0` (16 kHz mono)
    audio: list[np.ndarray] = field(default_factory=list)
    audio_t0: float | None = None
    audio_n: int = 0
    #: (audio time, chord probs)
    chords: list[tuple[float, np.ndarray]] = field(default_factory=list)
    beats: np.ndarray | None = None
    beats_at: float = -1e9
    own: dict[int, SegmentSpec] = field(default_factory=dict)
    born: dict[int, tuple[float, float]] = field(default_factory=dict)
    published: tuple = ()
    #: slide -> (word starts, line end) in seconds, re-measured on each grid
    placed: dict[int, tuple[np.ndarray, float]] = field(default_factory=dict)
    #: the tempo (bpm) of the first usable grid
    tempo0: float | None = None
    #: a second grid agreed with the first (`TEMPO_TOL`)
    confirmed: bool = False
    #: the grid's tempo moved (`TEMPO_TOL`): not learned
    dropped: bool = False
    #: the grid the learned segments are measured on
    measured_on: np.ndarray | None = None


SR = 16000
#: Most audio kept per song (trimmed `TRIM_STEP_S` at a time).
MAX_AUDIO_S = 12 * 60.0
TRIM_STEP_S = 30.0
#: Seconds of new audio between beat-tracker runs over a song: at least
#: this, and at least `BEATS_EVERY_SHARE` of the song so far. A run tracks
#: the whole song again (on the CPU, so the GPU stays the session's: 13 s of
#: CPU at 4 min of audio, 24 s at 8), so a fixed interval would cost more
#: and more; a slide placed past the grid's end is measured at its tempo
#: and re-measured on the next grid.
BEATS_EVERY_S = 30.0
BEATS_EVERY_SHARE = 1 / 3


class ServiceLearner:
    """Learns the songs it is given, from what the session hears. Not
    thread-safe: only the learner's thread touches it (`LiveFollowers` feeds
    it through a queue that thread drains, then ticks it, unlocked; the slow
    work, alignment and beats, goes through the callables). `alive` is
    checked before each slow call, so a stopped session's learner stops."""

    def __init__(
        self,
        songs: dict[int, list[list[str]]],
        aligner: Aligner,
        beat_tracker: BeatTracker,
    ) -> None:
        self.aligner = aligner
        self.beat_tracker = beat_tracker
        self.alive: Callable[[], bool] = lambda: True
        self.songs: dict[int, _Song] = {}
        for item, slides in songs.items():
            norm = {si: [w for w in (sc.norm(x) for x in sw) if w] for si, sw in enumerate(slides)}
            mates: dict[int, set[int]] = {}
            for g in metre_groups(slides):
                for s in g:
                    mates.setdefault(s, set()).update(g)
            self.songs[item] = _Song(item, slides, norm, SongModel(slides), mates)
        self.stats = collections.Counter()

    # ------------------------------------------------------------ feeding

    def adopt(self, item: int, song: _Song) -> None:
        """Take over what another learner learned of the same song (the
        setlist was edited mid-service; `item` may be its new place)."""
        if item in self.songs and self.songs[item].slides == song.slides:
            song.item_idx = item
            song.published = ()  # publish again to the new followers
            self.songs[item] = song

    def on_words(self, item: int, words: list[tuple[float, float, str]]) -> None:
        s = self.songs.get(item)
        if s is not None:
            s.words.extend(words)

    def on_audio(self, item: int, t_start: float, samples: np.ndarray) -> None:
        s = self.songs.get(item)
        if s is None:
            return
        if s.audio_t0 is None or abs(t_start - (s.audio_t0 + s.audio_n / SR)) > 0.5:
            # a gap (the song left and came back): keep the latest stretch only
            s.audio, s.audio_t0, s.audio_n = [], t_start, 0
        s.audio.append(np.asarray(samples, np.float32))
        s.audio_n += len(samples)
        excess = s.audio_n - int(MAX_AUDIO_S * SR)
        if excess > TRIM_STEP_S * SR:  # in steps: not a 46 MB copy every frame
            # trim the oldest samples, not whole chunks: `_audio` merges the
            # song into one chunk, which popping would have dropped entirely
            whole = np.concatenate(s.audio) if len(s.audio) > 1 else s.audio[0]
            s.audio = [whole[excess:]]
            s.audio_n -= excess
            s.audio_t0 += excess / SR
            # what fell out of the audio is no use to the look-back either
            s.chords = [x for x in s.chords if x[0] >= s.audio_t0]
            s.words = [x for x in s.words if x[0] >= s.audio_t0 - MAX_GAP_S]

    def on_chords(self, item: int, t: float, probs: np.ndarray) -> None:
        s = self.songs.get(item)
        if s is not None:
            s.chords.append((t, np.asarray(probs, np.float32)))

    # ------------------------------------------------------------ learning

    def _audio(self, s: _Song, t0: float, t1: float) -> np.ndarray | None:
        if s.audio_t0 is None:
            return None
        whole = np.concatenate(s.audio) if len(s.audio) > 1 else s.audio[0]
        if len(s.audio) > 1:
            s.audio = [whole]
        a = int((t0 - s.audio_t0) * SR)
        b = int((t1 - s.audio_t0) * SR)
        if a < 0 or b > len(whole) or b - a < SR:
            return None
        return whole[a:b]

    def _chords_between(self, s: _Song, t0: float, t1: float):
        sel = [p for t, p in s.chords if t0 <= t < t1]
        return np.mean(sel, axis=0) if sel else None

    def tick(self, item: int, now: float) -> dict[int, SegmentSpec] | None:
        """One look-back over `item`; the slides it now knows (own and lent),
        or None when nothing changed."""
        s = self.songs.get(item)
        if s is None:
            return None
        self._refresh_beats(s, now)
        if s.dropped:
            if not s.own and not s.published:
                return None
            s.own.clear()
            s.born.clear()
            s.placed.clear()
            s.published = ()
            return {}  # back to the library slides
        beats = s.beats
        if beats is None or not s.confirmed:
            return None
        if s.measured_on is not beats:
            # a new grid: every learned slide in its beats
            s.measured_on = beats
            for sl, (on, stop) in list(s.placed.items()):
                seg = self._segment(s, sl, on, stop, beats)
                if collapsed(seg):
                    del s.placed[sl]
                    s.own.pop(sl, None)
                    s.born.pop(sl, None)
                else:
                    s.own[sl] = seg
        heard = [HeardWord(t, txt) for t, arr, txt in s.words if arr <= now]
        text_at = {round(t, 3): sc.norm(txt) for t, arr, txt in s.words if arr <= now}
        if len(heard) < 8:
            return None
        al = align(s.model, heard)
        ps = al.passes
        sure = [k for k, q in enumerate(ps) if q.confidence >= MIN_CONFIDENCE and q.n_words >= 3]
        for i, p in enumerate(ps[:-1]):
            n = len(s.norm.get(p.slide, ()))
            settling = (
                p.slide in s.born
                and now - s.born[p.slide][0] < REVISE_S
                and abs(p.t_start - s.born[p.slide][1]) < 3.0
            )
            if (
                (p.slide in s.own and not settling)
                or not n
                or p.t_end > now - SETTLE_S
                or p.confidence < MIN_CONFIDENCE
                or min(j for j, _ in p.onsets) > MAX_FIRST_WORD
                or len({j for j, _ in p.onsets}) < MIN_HEARD * n
                or lexical_match(p, s.norm[p.slide], text_at) < MIN_LEXICAL
            ):
                continue
            nk = next((k for k in sure if k > i), None)
            if nk is None:
                continue
            prev = [k for k in sure if k < i]
            got = self._place(s, p, ps[nk], s.norm.get(ps[prev[-1]].slide, []) if prev else [])
            if got is None:
                continue
            on, stop = got
            seg = self._segment(s, p.slide, on, stop, beats)
            if collapsed(seg):
                self.stats["collapsed alignments"] += 1
                continue
            s.placed[p.slide] = (on, stop)
            if p.slide not in s.born:
                s.born[p.slide] = (now, p.t_start)
                self.stats["slides learned"] += 1
            s.own[p.slide] = seg
        for sl, (tb, ts) in list(s.born.items()):
            if now - tb >= REVISE_S or sl not in s.own:
                continue
            q = next((x for x in ps if x.slide == sl and abs(x.t_start - ts) < 3.0), None)
            if q is None or q.confidence < MIN_CONFIDENCE:
                del s.own[sl], s.born[sl], s.placed[sl]
                self.stats["slides forgotten"] += 1
        known = dict(s.own)
        for sl, seg in s.own.items():
            for mate in s.mates.get(sl, ()):
                if mate not in known and s.norm.get(mate):
                    known[mate] = borrow(seg, mate, s.norm[mate])
        sig = tuple(sorted((k, v.cells, tuple(v.chord[:4].tolist())) for k, v in known.items()))
        if sig == s.published:
            return None
        s.published = sig
        return known

    def _segment(self, s: _Song, slide: int, on, stop: float, beats) -> SegmentSpec:
        return beat_segment(
            slide, s.norm[slide], on, stop, beats, lambda a, b: self._chords_between(s, a, b)
        )

    def _place(self, s: _Song, p, q, prev_words):
        """(word starts, the line's end) for pass `p`, by forced alignment."""
        f, g = _fit(p), _fit(q)
        if f is None:
            return None
        words = s.norm[p.slide]
        nxt_words = s.norm.get(q.slide, [])
        pre = list(prev_words)[-2:]
        t0 = f[0] - (3.0 if pre else 1.5)
        nxt_t = g[0] if g is not None else q.t_start
        line_end = f[0] + f[1] * len(words) + 2.0
        near = nxt_t + 1.5 - t0 <= MAX_LINE_S and nxt_t - line_end <= 4.0
        t1 = nxt_t + 1.5 if near else line_end
        if not 0 < t1 - t0 <= MAX_LINE_S:
            return None
        audio = self._audio(s, t0, t1)
        if audio is None:
            return None
        tail = nxt_words[:2] if near else []
        if not self.alive():
            return None
        got = self.aligner(audio, pre + words + tail)
        self.stats["alignments"] += 1
        if got is None:
            return None
        got = [t0 + x for x in got][len(pre) :]
        on = np.maximum.accumulate(np.array(got[: len(words)]))
        if not pre and len(on) >= 3:
            on[0] = max(on[0], on[1] - (on[2] - on[1]))
        stop = got[len(words)] if tail and len(got) > len(words) else nxt_t
        stop = max(stop, on[-1] + 0.1)
        if stop - on[-1] > MAX_GAP_S:
            stop = on[-1] + 2 * f[1]
        return on, stop

    def _refresh_beats(self, s: _Song, now: float) -> None:
        if (
            s.audio_t0 is None
            or s.dropped
            or now - s.beats_at < max(BEATS_EVERY_S, BEATS_EVERY_SHARE * s.audio_n / SR)
            or s.audio_n < BEATS_EVERY_S * SR  # too little to find a tempo in
        ):
            return
        s.beats_at = now
        whole = self._audio(s, s.audio_t0, s.audio_t0 + s.audio_n / SR)
        if whole is None:
            return
        started = time.monotonic()
        if not self.alive():
            return
        got = self.beat_tracker(whole)
        self.stats["beat runs"] += 1
        log.info(
            "item %d: beats over %.0f s of audio in %.1f s (%s)",
            s.item_idx,
            len(whole) / SR,
            time.monotonic() - started,
            "usable" if got is not None and beats_usable(np.asarray(got, float)) else "not usable",
        )
        # gated on the tracker's own grid: cleaning fills the very gaps the
        # gate looks for
        if got is None or not beats_usable(np.asarray(got, float)):
            s.beats = None
            return
        bpm = 60.0 / float(np.median(np.diff(np.asarray(got, float))))
        if s.tempo0 is None:
            s.tempo0 = bpm
        elif abs(bpm / s.tempo0 - 1.0) <= TEMPO_TOL:
            s.confirmed = True
        else:
            if not s.dropped:
                log.info(
                    "item %d: beat tempo moved %.0f -> %.0f bpm; not learned this service",
                    s.item_idx,
                    s.tempo0,
                    bpm,
                )
                self.stats["songs dropped"] += 1
            s.dropped = True
        s.beats = clean_beats(np.asarray(got, float) + s.audio_t0)
