"""A song's past performances, aggregated into what the follower can use.

Aggregated on load, never stored: decay, weights and the rules below can
change without touching a row, one bad service can be deleted, and the cost
is small (200 performances of a song load and aggregate in ~9 ms).

What it yields, both anchored to the song's *current* slides (a row's slides
are re-bound by their text; passes of slides that no longer exist are
dropped, and break the order there rather than invent a transition):

* **order** — each line change the look-back is sure of (both lines, see
  `labels.MIN_CHANGE_CONFIDENCE`), weighted by how sure, for
  `reference.order_prior`;
* **word timing** — per line, the median place of each word after the line's
  first, and the median distance to the next line's first word. Measured in
  *words*, not seconds: each performance's times are divided by its own
  median seconds per word, so the shape of a line is learned and its speed is
  not (bands vary week to week: Build My Life's line 6 took 15.4 s in one
  service and 21.1 s in another). Speed is the follower's tempo state's job.
"""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field, replace

import numpy as np

from auto_presenter.follower import score as sc
from auto_presenter.follower.sheet import CELL
from auto_presenter.follower.spec import (
    LIBRARY_BEATS_PER_WORD,
    LIBRARY_GAP_BEATS,
    NONE,
    ReferenceSpec,
    SegmentSpec,
)
from auto_presenter.learning.labels import CONFIDENT, MIN_CHANGE_CONFIDENCE
from auto_presenter.learning.summary import PerformanceSummary, slide_key

#: A performance counts half as much this many days later.
HALF_LIFE_DAYS = 182.0
#: A word of learned timing (one median word-spacing) is a beat, the
#: library reference's scale, so learned and unlearned lines agree.
CELLS_PER_WORD = LIBRARY_BEATS_PER_WORD / CELL
#: A pass times its line only if it heard the line from (near) its start and
#: most of its words.
TIMING_MAX_FIRST_WORD = 1
TIMING_MIN_HEARD = 0.6
#: Learned line timing is blended with the library's word-a-beat: the library
#: counts as this many performances (as a lead sheet does for order), so one
#: noisy service moves a line only part of the way. Replayed (2026-10-08):
#: Build My Life learned from its other service read 0.188 unblended, 0.173
#: at 1, 0.154 at 3, against 0.150 for the library alone; in-fit gains shrink
#: (tuning 0.222 -> 0.266) but grow back as services agree.
TIMING_PRIOR_COUNT = 3.0
#: Gaps longer than this between a line and the next are a break (an
#: instrumental, talk), not the line's length.
MAX_LINE_GAP_S = 8.0


@dataclass
class LineTiming:
    #: Median place after the line's first word, per word of the slide, in
    #: median word-spacings of the performance.
    onsets: list[float]
    #: Median distance from the line's first word to the next line's, same unit.
    length: float
    passes: int
    #: Performances that timed this line.
    rows: int = 0


#: The operator "made" a learned line change if they showed the same move
#: within this long of the look-back hearing it.
OPERATOR_MATCH_S = 6.0


@dataclass
class TransitionEvidence:
    """One learned line change, and where it came from (debug panel)."""

    from_slide: int
    to_slide: int
    performance_id: str
    started_at_ms: int
    led_by: str
    #: The performance's weight after decay: what this change counts for.
    weight: float
    #: Seconds into the performance the new line was heard.
    t: float
    #: The look-back's confidence in the line left and the line entered.
    conf_from: float
    conf_to: float
    #: Whether the operator showed the same move then; None if the operator
    #: moved nothing in that performance.
    operator_agrees: bool | None


@dataclass
class LearnedSong:
    rows: int = 0
    #: Rows that count (live, bound, weight > 0).
    used: int = 0
    #: Sum of decayed weights.
    n_eff: float = 0.0
    sequences: list[tuple[list[int], float]] = field(default_factory=list)
    timing: dict[int, LineTiming] = field(default_factory=dict)
    #: Passes dropped because their slide no longer exists.
    unbound: int = 0
    #: Median seconds per word over the performances (the band's usual speed).
    seconds_per_word: float | None = None
    #: Every line change counted in `sequences`, with its source.
    evidence: list[TransitionEvidence] = field(default_factory=list)


def rebind(row: PerformanceSummary, slides: list[list[str]]) -> dict[int, int]:
    """Row slide index -> current slide index, by text; unbound ones absent."""
    keys = [slide_key(sw) for sw in slides]
    where: dict[str, list[int]] = {}
    for i, k in enumerate(keys):
        if k:
            where.setdefault(k, []).append(i)
    out: dict[int, int] = {}
    for old, k in enumerate(row.slide_keys):
        if not k:
            continue
        if old < len(keys) and keys[old] == k:
            out[old] = old
        elif where.get(k):
            out[old] = min(where[k], key=lambda i: abs(i - old))
    return out


#: Rows of one song started this close together are one service, the session
#: stopped and started again (two services in a morning are further apart).
SAME_SERVICE_MS = 30 * 60 * 1000


def one_per_service(rows: list[PerformanceSummary]) -> list[PerformanceSummary]:
    """The fullest row of each service: restarting a session mid-service made
    a second row for the same singing, counted twice."""
    out: list[PerformanceSummary] = []
    group_start = None
    # only rows that count compete: an injected or zero-weight row must not
    # push out the live one and then be dropped itself
    usable = [r for r in rows if r.provenance == "live" and r.weight > 0]
    for row in sorted(usable, key=lambda r: r.started_at_ms):
        # measured from the group's first row, not the one that replaced it
        if out and group_start is not None and row.started_at_ms - group_start < SAME_SERVICE_MS:
            if _heard(row) > _heard(out[-1]):
                out[-1] = row
            continue
        out.append(row)
        group_start = row.started_at_ms
    return out


def _heard(row: PerformanceSummary) -> int:
    return sum(len(p.onsets) for p in row.passes)


def aggregate(
    rows: list[PerformanceSummary], slides: list[list[str]], now_ms: int | None = None
) -> LearnedSong:
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    song = LearnedSong(rows=len(rows))
    per_word: dict[int, dict[int, list[float]]] = {}
    lengths: dict[int, list[float]] = {}
    counts: dict[int, int] = {}
    timed_rows: dict[int, set[int]] = {}
    speeds: list[float] = []
    n_words = {si: len([w for w in (sc.norm(x) for x in sw) if w]) for si, sw in enumerate(slides)}
    for row in one_per_service(rows):
        if row.provenance != "live" or row.weight <= 0:
            continue
        age_days = max(0.0, (now_ms - row.started_at_ms) / 86_400_000)
        w = row.weight * 0.5 ** (age_days / HALF_LIFE_DAYS)
        bind = rebind(row, slides)
        if not bind:
            continue
        song.used += 1
        song.n_eff += w
        spw = _row_spacing(row.passes)
        if spw:
            speeds.append(spw)
        #: the last pass counted: (slide, confidence); a pass that does not
        #: count breaks the chain rather than bridging it with a change nobody heard
        prev: tuple[int, float] | None = None
        op_moves = _operator_moves(row, bind)
        for i, p in enumerate(row.passes):
            cur = bind.get(p.slide)
            if cur is None:
                song.unbound += 1
            if cur is None or p.confidence < CONFIDENT:
                prev = None
                continue
            if prev is not None and prev[0] != cur:
                both = prev[1] * p.confidence  # P(both lines right)
                if both >= MIN_CHANGE_CONFIDENCE:
                    song.sequences.append(([prev[0], cur], w * both))
                    song.evidence.append(
                        TransitionEvidence(
                            from_slide=prev[0],
                            to_slide=cur,
                            performance_id=row.id,
                            started_at_ms=row.started_at_ms,
                            led_by=row.led_by,
                            weight=round(w * both, 3),
                            t=p.t_start,
                            conf_from=prev[1],
                            conf_to=p.confidence,
                            operator_agrees=_operator_made(op_moves, prev[0], cur, p.t_start),
                        )
                    )
            prev = (cur, p.confidence)
            # timing
            if spw and p.onsets and p.onsets[0][0] <= TIMING_MAX_FIRST_WORD:
                heard = len({j for j, _ in p.onsets})
                if heard >= TIMING_MIN_HEARD * max(1, n_words.get(cur, 0)):
                    lead = p.onsets[0][0]  # a missed first word: shift by one
                    shift = lead * _spacing(p.onsets)
                    bucket = per_word.setdefault(cur, {})
                    for j, dt in p.onsets:
                        bucket.setdefault(j, []).append(((dt + shift) / spw, p.confidence))
                    counts[cur] = counts.get(cur, 0) + 1
                    timed_rows.setdefault(cur, set()).add(id(row))
                    nxt = row.passes[i + 1] if i + 1 < len(row.passes) else None
                    if nxt is not None and nxt.confidence >= CONFIDENT:
                        gap = nxt.t_start - p.t_start + shift
                        if 0 < gap - (p.t_end - p.t_start) <= MAX_LINE_GAP_S:
                            lengths.setdefault(cur, []).append((gap / spw, p.confidence))
    for si, bucket in per_word.items():
        n = n_words.get(si, 0)
        if not n:
            continue
        known = sorted((j, _weighted_median(v)) for j, v in bucket.items() if j < n)
        js, ts = [k for k, _ in known], [t for _, t in known]
        onsets = list(np.maximum.accumulate(np.interp(range(n), js, ts)))
        if len(js) == 1 or js[-1] < n - 1:
            step = _spacing(known) or 1.0
            last_j = js[-1]
            for j in range(last_j + 1, n):
                onsets[j] = ts[-1] + (j - last_j) * step
        length = (
            _weighted_median(lengths[si])
            if lengths.get(si)
            else onsets[-1] + LIBRARY_GAP_BEATS / LIBRARY_BEATS_PER_WORD
        )
        song.timing[si] = LineTiming(
            onsets=[round(float(x), 3) for x in onsets],
            length=round(max(length, onsets[-1] + 0.5), 3),
            passes=counts.get(si, 0),
            rows=len(timed_rows.get(si, ())),
        )
    if speeds:
        song.seconds_per_word = round(float(np.median(speeds)), 3)
    return song


def _operator_moves(
    row: PerformanceSummary, bind: dict[int, int]
) -> list[tuple[float, int]] | None:
    """The operator's cleaned moves (time, current slide), or None if they
    moved nothing (an AI-led performance has no opinion to agree with)."""
    from auto_presenter.learning.labels import OPERATOR_SOURCES

    if not any(src in OPERATOR_SOURCES for _t, _s, src in row.operator):
        return None
    return [(t, bind[s]) for t, s, _src in row.operator if s in bind]


def _operator_made(moves, a: int, b: int, t: float) -> bool | None:
    if moves is None:
        return None
    return any(
        moves[k - 1][1] == a and moves[k][1] == b and abs(moves[k][0] - t) <= OPERATOR_MATCH_S
        for k in range(1, len(moves))
    )


def _weighted_median(pairs: list[tuple[float, float]]) -> float:
    """The median of (value, weight) pairs: each pass counts by how sure the
    look-back was of it."""
    pairs = sorted(pairs)
    total = sum(w for _v, w in pairs)
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= total / 2:
            return float(v)
    return float(pairs[-1][0])


def _row_spacing(passes) -> float:
    """A performance's median seconds per word, over its confident passes."""
    steps = [_spacing(p.onsets) for p in passes if p.confidence >= CONFIDENT and len(p.onsets) > 2]
    steps = [x for x in steps if x > 0]
    return float(np.median(steps)) if steps else 0.0


def _spacing(onsets) -> float:
    """Median seconds per word along a pass's (index, time) pairs."""
    steps = [
        (t1 - t0) / (j1 - j0)
        for (j0, t0), (j1, t1) in itertools.pairwise(onsets)
        if j1 > j0 and t1 > t0
    ]
    return float(np.median(steps)) if steps else 0.0


def learned_spec(base: ReferenceSpec, song: LearnedSong) -> ReferenceSpec:
    """`base` (a lead sheet's or the library's) with what the services taught.

    Order: always, on top of the base's own. Word timing: only where the base
    has none of its own (the library's word-a-beat), since a lead sheet's
    rhythm is in beats and carries melody and chords on the same cells."""
    if not song.used:
        return base
    segments = base.segments
    if base.source == "library":
        segments = [_timed(g, song.timing.get(g.slide)) for g in base.segments]
    return replace(
        base,
        source=f"{base.source}+learned",
        segments=segments,
        learned=[*base.learned, *song.sequences],
    )


def _timed(g: SegmentSpec, timing: LineTiming | None) -> SegmentSpec:
    if timing is None or len(timing.onsets) != len(g.words):
        return g
    share = timing.rows / (timing.rows + TIMING_PRIOR_COUNT)
    cells = max(2, round((1 - share) * g.cells + share * timing.length * CELLS_PER_WORD))
    words = [
        (w, min(cells - 1, round((1 - share) * c + share * t * CELLS_PER_WORD)))
        for (w, c), t in zip(g.words, timing.onsets, strict=True)
    ]
    return SegmentSpec(
        slide=g.slide,
        cells=cells,
        words=words,
        midi=np.full(cells, NONE, np.int16),
        chord=np.full(cells, NONE, np.int16),
    )
