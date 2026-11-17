"""The lead sheet as a performance: unrolled form, words, and slide spans.

Stage 1 of docs/research/MUSIC_ONLY_MATCHING.md. Reads a `score.json` written by
`research/spikes/spike_leadsheet_omr.py` and

* unrolls repeats, numbered endings, D.S. and To Coda into the order the
  sheet asks for, choosing each pass's lyric row;
* joins syllables into words (the sheet's own hyphens decide);
* binds each library slide to the spans of that performance whose words it
  carries;
* groups slides whose music is identical — the ceiling for any matcher that
  hears only music.

Positions are in quarter notes. `written` positions are (bar number, beat in
bar) and identify the music; `played` positions count from the start of the
unrolled performance.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path


@dataclass
class PlayedBar:
    bar: int  # written bar number, 1-based
    row: int  # lyric row sung on this pass
    start: float  # played position of the downbeat, quarter notes


@dataclass
class Word:
    text: str  # normalised
    played: int  # index into the unrolled bars
    onset: float  # played position, quarter notes
    bar: int
    beat: float


@dataclass
class Score:
    path: Path
    bars: list[dict]
    meter: float
    key_fifths: int
    played: list[PlayedBar] = field(default_factory=list)
    words: list[Word] = field(default_factory=list)

    def bar(self, number: int) -> dict:
        return self.bars[number - 1]

    @property
    def length(self) -> float:
        return self.played[-1].start + self.meter if self.played else 0.0


def load(path: Path) -> Score:
    data = json.loads(Path(path).read_text())
    bars = data["bars"]
    meter = bars[0].get("meter", 4.0)
    s = Score(Path(path), bars, meter, data["key_fifths"])
    s.played = unroll(bars)
    t = 0.0
    for pb in s.played:
        pb.start = t
        t += _bar_len(bars[pb.bar - 1], meter)
    s.words = words_of(s)
    return s


def _bar_len(bar: dict, meter: float) -> float:
    return bar.get("pickup") or meter


# ---------------------------------------------------------------- unrolling


def _volta_groups(bars: list[dict]) -> list[list[tuple[list[int], int, int]]]:
    """Numbered endings as groups of (numbers, first bar idx, last bar idx).

    An ending runs to its repeat sign or D.S. if it has one before the next
    ending starts; consecutive endings form one group."""
    groups: list[list[tuple[list[int], int, int]]] = []
    i = 0
    n = len(bars)
    while i < n:
        if not bars[i]["voltas"]:
            i += 1
            continue
        group = []
        while i < n and bars[i]["voltas"]:
            start = i
            j = i
            while j < n:
                if bars[j]["end_repeat"] or bars[j]["ds"]:
                    break
                if j + 1 < n and bars[j + 1]["voltas"]:
                    break
                if j > i and bars[j].get("section"):
                    j -= 1
                    break
                # the last ending of a group has no terminator: one bar
                if j + 1 >= n or not any(bars[k]["voltas"] for k in range(j + 1, min(n, j + 4))):
                    break
                j += 1
            group.append((bars[start]["voltas"], start, j))
            i = j + 1
            if not (i < n and bars[i]["voltas"]):
                break
        groups.append(group)
    return groups


def unroll(bars: list[dict], max_bars: int = 2000) -> list[PlayedBar]:
    groups = _volta_groups(bars)
    group_at = {g[0][1]: g for g in groups}
    group_count: dict[int, int] = {}
    repeat_taken: set[int] = set()
    visits: dict[int, int] = {}
    segno = next((i for i, b in enumerate(bars) if b["segno"]), 0)
    coda = [i for i, b in enumerate(bars) if b.get("coda")]
    ds_taken = False
    out: list[PlayedBar] = []
    i = 0
    stop_after: int | None = None  # last bar of the chosen ending, if its own terminator ends it

    def repeat_start(idx: int) -> int:
        for k in range(idx, -1, -1):
            if bars[k]["start_repeat"]:
                return k
            if ds_taken and k == segno:
                return k
        return 0

    while i < len(bars) and len(out) < max_bars:
        if i in group_at:
            g = group_at[i]
            c = group_count.get(i, 0) + 1
            group_count[i] = c
            choice = next((e for e in g if c in e[0]), None)
            if choice is None:
                choice = g[-1] if c > max(max(e[0]) for e in g) else None
            if choice is None:
                i = g[-1][2] + 1
                continue
            i = choice[1]
            stop_after = choice[2]
        b = bars[i]
        rows = 1 + max([int(k) for n in b["notes"] for k in n["syllables"]] or [0])
        v = visits.get(i, 0)
        visits[i] = v + 1
        out.append(PlayedBar(b["number"], min(v, rows - 1), 0.0))
        nxt = i + 1
        if b["end_repeat"] and (i not in repeat_taken or (stop_after is not None and i <= stop_after)):
            # inside a chosen ending, the repeat sign always applies
            if i not in repeat_taken or stop_after == i:
                repeat_taken.add(i)
                nxt = repeat_start(i)
        if b["ds"] and not ds_taken:
            ds_taken = True
            nxt = segno
        if ds_taken and b.get("to_coda") and coda:
            later = [k for k in coda if k > i]
            if later:
                nxt = later[-1]
        if stop_after is not None and i >= stop_after:
            stop_after = None
        i = nxt
    return out


# ---------------------------------------------------------------- words


def norm(t: str) -> str:
    t = t.lower().replace("’", "'")
    return re.sub(r"[^a-z']", "", t)


def line(bar: dict, row: int) -> list[dict]:
    """The notes sung on one lyric row of a bar, in time order.

    Row 0 is the main voice. A later row follows the cue voice where the bar
    has one, and the main voice wherever the cue voice is silent."""
    v1 = [n for n in bar["notes"] if n["voice"] == 1]
    v2 = [n for n in bar["notes"] if n["voice"] == 2 and n["midi"] is not None]
    if row == 0 or not v2:
        return sorted(v1, key=lambda n: n["onset"])
    lo = min(n["onset"] for n in v2)
    hi = max(n["onset"] + n["dur"] for n in v2)
    keep = [n for n in v1 if not (lo <= n["onset"] < hi)]
    return sorted(keep + v2, key=lambda n: n["onset"])


def words_of(s: Score) -> list[Word]:
    out: list[Word] = []
    pending = ""
    pend_at = None
    for pi, pb in enumerate(s.played):
        for n in line(s.bar(pb.bar), pb.row):
            syl = n["syllables"].get(str(pb.row))
            if syl is None:
                continue
            if pend_at is None:
                pend_at = (pi, pb.start + n["onset"], pb.bar, n["onset"])
            pending += syl
            if not n["hyphen_after"].get(str(pb.row), False):
                w = norm(pending)
                if w:
                    out.append(Word(w, *pend_at))
                pending, pend_at = "", None
    return out


# ---------------------------------------------------------------- slides


@dataclass
class Span:
    slide: int
    w0: int  # first word index in Score.words
    w1: int  # last word index, inclusive
    start: float  # played position
    end: float  # played position of the last word's onset
    score: float


def bind_slides(s: Score, slides: list[list[str]], min_ratio: float = 0.93) -> dict[int, list[Span]]:
    """Every place in the performance each slide's words are sung.

    Compared as letters with the spaces removed, so the sheet's syllable
    splits ("heav en") and the library's words ("heaven") agree, while one
    changed word ("beautiful" / "wonderful") is enough to tell passes apart.
    Windows of one word more or fewer than the slide are tried too."""
    stream = [w.text for w in s.words]
    out: dict[int, list[Span]] = {}
    for si, sw in enumerate(slides):
        target = "".join(norm(w) for w in sw)
        if not target:
            continue
        n = len([w for w in sw if norm(w)])
        cands: list[Span] = []
        for size in (n - 1, n, n + 1):
            if size < 1:
                continue
            for i in range(0, len(stream) - size + 1):
                r = SequenceMatcher(None, target, "".join(stream[i : i + size])).ratio()
                if r >= min_ratio:
                    cands.append(Span(si, i, i + size - 1, s.words[i].onset, s.words[i + size - 1].onset, r))
        # keep the best of any overlapping candidates
        cands.sort(key=lambda sp: -sp.score)
        chosen: list[Span] = []
        for sp in cands:
            if all(sp.w1 < c.w0 or sp.w0 > c.w1 for c in chosen):
                chosen.append(sp)
        out[si] = sorted(chosen, key=lambda sp: sp.w0)
    return out


def bar_signature(bar: dict) -> tuple:
    # ties are not heard as such: a held note and a re-struck one sound alike
    # to every feature here, so they do not make a bar different music
    notes = tuple((n["onset"], n["dur"], n["midi"]) for n in line(bar, 0))
    chords = tuple((c["beat"], c["name"]) for c in bar["chords"])
    return notes + ("|",) + chords


def canonical_bars(s: Score) -> dict[int, int]:
    """Written bar number -> the lowest bar number with identical music."""
    first: dict[tuple, int] = {}
    out = {}
    for b in s.bars:
        out[b["number"]] = first.setdefault(bar_signature(b), b["number"])
    return out


def position_of(s: Score, played: float) -> tuple[int, int, float]:
    """Played position -> (played bar index, written bar, beat in bar)."""
    for i in range(len(s.played) - 1, -1, -1):
        if s.played[i].start <= played + 1e-9:
            return i, s.played[i].bar, played - s.played[i].start
    return 0, s.played[0].bar, 0.0


def span_end(s: Score, sp: Span) -> float:
    """A slide's music runs until the word after its last one is sung, or
    one bar past its last word if the next word is further off (an
    instrumental follows, and that is not this slide's music)."""
    nxt = s.words[sp.w1 + 1].onset if sp.w1 + 1 < len(s.words) else s.length
    return min(nxt, s.words[sp.w1].onset + s.meter)


def coverage(s: Score, sp: Span, step: float = 0.5) -> set[tuple[int, float]]:
    """The canonical (bar, beat) grid points a slide's span covers."""
    canon = canonical_bars(s)
    out = set()
    t = sp.start
    end = span_end(s, sp)
    while t < end - 1e-9:
        _, bar, beat = position_of(s, t)
        out.add((canon[bar], round(beat / step) * step))
        t += step
    return out


def notes_between(s: Score, start: float, end: float) -> list[tuple[float, float, int | None, bool]]:
    """Sung (played onset, duration, midi, tied) in [start, end), following
    each pass's lyric row."""
    out = []
    for pb in s.played:
        bar = s.bar(pb.bar)
        if pb.start + _bar_len(bar, s.meter) <= start or pb.start >= end:
            continue
        for n in line(bar, pb.row):
            t = pb.start + n["onset"]
            if start <= t < end:
                out.append((t, n["dur"], n["midi"], n["tied_from_prev"]))
    return out


def span_signature(s: Score, sp: Span, words_after: int = 1) -> tuple:
    """What the music of a slide is: its notes from the first word to the
    word after its last, positions relative to the first word. Two slides
    with the same signature cannot be told apart by anything that hears only
    the music."""
    end = s.words[sp.w1 + words_after].onset if sp.w1 + words_after < len(s.words) else s.length
    notes = notes_between(s, sp.start, end)
    return tuple((round(t - sp.start, 3), round(d, 3), m, tie) for t, d, m, tie in notes)
