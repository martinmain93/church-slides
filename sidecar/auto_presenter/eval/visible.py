"""Visible error": is the line being sung on the screen?

`wrong_slide_rate` compares the screen to the operator's slide index. That
charges us for things a congregation never sees — the band singing a line
that is on both the operator's slide and ours, an operator who never showed
the refrain the band sang, a cue clicked two seconds late — and it has no
opinion at all about whether the words being sung were readable.

This measures the second thing directly, from the audio's own word timing
(an offline Whisper transcript with word timestamps):

1. **Place sung words in the lyrics.** A transcript word is *placed* when an
   n-gram of consecutive transcript words containing it (default 3 words,
   at least one of them not a stop word) occurs contiguously in some slide's
   text. Its *home* is every `(item, slide)` carrying that n-gram — so a line
   printed on two slides, or on two identical slide copies, is at home on
   both, and a garbled or ad-libbed stretch places nothing.
2. **Score only placed singing.** Each placed word covers
   `[start, min(end, start + max_word_s)]` of the timeline. At each such
   moment the screen is *visible* when it shows one of the active words'
   homes. Everything else (instrumentals, talking, unplaceable ASR noise) is
   not scored here; `coverage` says how much of the timeline was.

Transcription noise only *removes* moments from scoring (a wrong word breaks
the n-gram); it can add a wrong placement only when Whisper hallucinates
three consecutive lyric words, which is what the n-gram length buys. The
same placement checks a golden (`ground_truth`) against the singing, which is
how golden mistakes are found (`research/spikes/music_only/visible_error.py audit`).

Pure functions, no I/O, like `metrics`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from itertools import pairwise

from auto_presenter.eval.types import Decision, GroundTruthEvent, SetlistItemSnapshot

Slide = tuple[int, int]

#: Words too common to anchor a placement on their own.
STOP_WORDS = frozenset(
    "a an and are as at be but by for from he his i i'm im in is it its me my "
    "of oh on our so that the their there this to us was we were what when "
    "who will with you your yeah ooh whoa ah".split()
)


def normalize_word(word: str) -> str:
    """Lowercase, smart quotes folded, everything but [a-z0-9] removed.

    Apostrophes are dropped on both sides (lyrics "don't", Whisper "dont" or
    "don't" → "dont"), so contractions compare equal whichever way either
    source wrote them.
    """
    w = word.lower().replace("\u2019", "'").replace("\u2018", "'")
    return re.sub(r"[^a-z0-9]+", "", w)


def slide_tokens(words: Iterable[str]) -> list[str]:
    """A slide's words split the way Whisper's are: hyphens/dashes break words."""
    out: list[str] = []
    for w in words:
        for part in re.split("[-\u2013\u2014/]+", w):
            n = normalize_word(part)
            if n:
                out.append(n)
    return out


def _edit_le1(a: str, b: str) -> bool:
    """Levenshtein distance <= 1."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        return sum(x != y for x, y in zip(a, b, strict=True)) == 1
    if la > lb:
        a, b, la, lb = b, a, lb, la
    i = 0
    while i < la and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1 :]


def words_match(a: str, b: str) -> bool:
    """Same word, allowing one edit on words of 5+ letters (sung mis-hearings)."""
    if a == b:
        return True
    return len(a) >= 5 and len(b) >= 5 and _edit_le1(a, b)


@dataclass(frozen=True)
class SungWord:
    """One transcript word placed in the lyrics."""

    start: float
    end: float
    word: str
    homes: frozenset[Slide]


@dataclass(frozen=True)
class Placement:
    """Every transcript word, and the subset that could be placed."""

    words: list[tuple[float, float, str]]
    placed: list[SungWord]

    @property
    def placed_share(self) -> float:
        return len(self.placed) / len(self.words) if self.words else 0.0


def place_sung_words(
    transcript: Sequence[tuple[float, float, str]],
    setlist_content: Sequence[SetlistItemSnapshot],
    *,
    ngram: int = 3,
    items: Iterable[int] | None = None,
) -> Placement:
    """Place transcript words `(start, end, word)` in the setlist's slides.

    `items` restricts the songs searched (default: every song in the setlist).
    """
    allowed = set(items) if items is not None else None
    slides: list[tuple[Slide, list[str]]] = []
    for item in setlist_content:
        if allowed is not None and item.item_idx not in allowed:
            continue
        for s, words in enumerate(item.slides):
            toks = slide_tokens(words)
            if toks:
                slides.append(((item.item_idx, s), toks))
    words = [(float(a), float(b), normalize_word(w)) for a, b, w in transcript]
    words = [x for x in words if x[2]]
    toks = [w for _a, _b, w in words]
    homes: list[set[Slide]] = [set() for _ in toks]
    for i in range(len(toks) - ngram + 1):
        gram = toks[i : i + ngram]
        if all(g in STOP_WORDS for g in gram):
            continue
        hit: set[Slide] = set()
        for key, st in slides:
            for j in range(len(st) - ngram + 1):
                if all(words_match(gram[k], st[j + k]) for k in range(ngram)):
                    hit.add(key)
                    break
        if hit:
            for k in range(ngram):
                homes[i + k] |= hit
    placed = [
        SungWord(start=a, end=b, word=w, homes=frozenset(h))
        for (a, b, w), h in zip(words, homes, strict=True)
        if h
    ]
    return Placement(words=words, placed=placed)


@dataclass
class VisibleResult:
    """Outcome of `visible_error`; times in seconds."""

    span: float  # golden first..last cue (same window as wrong_slide_rate)
    scored: float  # placed singing inside the span, minus excused time
    hidden: float  # of `scored`, time the sung line was not on screen
    hidden_windows: list[tuple[float, float]] = field(default_factory=list)

    @property
    def rate(self) -> float:
        return self.hidden / self.scored if self.scored > 0 else 0.0

    @property
    def coverage(self) -> float:
        return self.scored / self.span if self.span > 0 else 0.0


def screen_at(
    decisions: Sequence[Decision] | Sequence[GroundTruthEvent],
) -> Callable[[float], Slide | None]:
    """`t -> (item, slide) | None` for a piecewise-constant decision list."""
    seq = sorted(((d.t_seconds, (d.item_idx, d.slide_idx)) for d in decisions), key=lambda x: x[0])
    times = [t for t, _ in seq]
    import bisect

    def at(t: float) -> Slide | None:
        k = bisect.bisect_right(times, t) - 1
        return seq[k][1] if k >= 0 else None

    return at


def sung_samples(
    placement: Placement,
    lo: float,
    hi: float,
    *,
    step: float = 0.05,
    max_word_s: float = 1.0,
    min_word_s: float = 0.2,
    tolerance_s: float = 0.0,
    bridge_s: float = 0.0,
) -> list[tuple[float, frozenset[Slide]]]:
    """`(t, homes)` every `step` s inside placed singing.

    `homes` are those of every placed word active at `t` — or, with
    `tolerance_s`, active anywhere within `t ± tolerance_s`, so a screen that
    changes up to that long before or after the line does is not charged
    (the moments scored stay the same). `bridge_s` also scores gaps of up to
    that long between consecutive placed words (held notes, breaths inside a
    line), where either neighbour's slide counts as visible.
    """
    import math

    buckets: dict[int, set[Slide]] = {}
    wide: dict[int, set[Slide]] = {}
    for w in placement.placed:
        a = w.start
        b = max(min(w.end, w.start + max_word_s), w.start + min_word_s)
        for k in range(math.ceil(max(a, lo) / step), math.ceil(min(b, hi) / step)):
            buckets.setdefault(k, set()).update(w.homes)
        if tolerance_s > 0:
            k0, k1 = math.ceil((a - tolerance_s) / step), math.ceil((b + tolerance_s) / step)
            for k in range(k0, k1):
                wide.setdefault(k, set()).update(w.homes)
    src = wide if tolerance_s > 0 else buckets
    if bridge_s > 0:
        # Inside a line (held notes, breaths) the sung words are those either
        # side: fill short gaps between placed words with both words' homes.
        ws = sorted(placement.placed, key=lambda w: w.start)
        for w0, w1 in pairwise(ws):
            a = max(min(w0.end, w0.start + max_word_s), w0.start + min_word_s)
            if 0 < w1.start - a <= bridge_s:
                for k in range(math.ceil(max(a, lo) / step), math.ceil(min(w1.start, hi) / step)):
                    if k not in buckets:
                        src.setdefault(k, set()).update(w0.homes | w1.homes)
                        buckets.setdefault(k, set())
    return [(k * step, frozenset(src[k])) for k in sorted(buckets)]


def visible_error(
    decisions: Sequence[Decision] | Sequence[GroundTruthEvent],
    ground_truth: Sequence[GroundTruthEvent],
    placement: Placement,
    *,
    excused: Sequence[tuple[float, float]] = (),
    step: float = 0.05,
    max_word_s: float = 1.0,
    tolerance_s: float = 0.0,
    bridge_s: float = 0.0,
) -> VisibleResult:
    """Share of placed-singing time whose line was not on the screen.

    Scored over the same window as `wrong_slide_rate` (first to last golden
    cue) so the two compare; `excused` windows (e.g. the post-deviation grace)
    are removed from both numerator and denominator. `decisions` can be a
    golden, which is how the golden audit asks whether the operator showed the
    singing. `tolerance_s` forgives a screen that changes up to that long
    before or after the singing does; `bridge_s` scores short gaps inside a
    line (see `sung_samples`).
    """
    gt = sorted(ground_truth, key=lambda g: g.t_seconds)
    lo, hi = gt[0].t_seconds, gt[-1].t_seconds
    at = screen_at(decisions)
    scored = hidden = 0.0
    windows: list[tuple[float, float]] = []
    for t, homes in sung_samples(
        placement,
        lo,
        hi,
        step=step,
        max_word_s=max_word_s,
        tolerance_s=tolerance_s,
        bridge_s=bridge_s,
    ):
        if any(a <= t < b for a, b in excused):
            continue
        scored += step
        if at(t) not in homes:
            hidden += step
            if windows and t - windows[-1][1] <= step * 1.5:
                windows[-1] = (windows[-1][0], t + step)
            else:
                windows.append((t, t + step))
    return VisibleResult(span=hi - lo, scored=scored, hidden=hidden, hidden_windows=windows)


def merge_windows(windows: Sequence[tuple[float, float]], gap: float) -> list[tuple[float, float]]:
    """Join windows separated by less than `gap` seconds."""
    out: list[tuple[float, float]] = []
    for a, b in sorted(windows):
        if out and a - out[-1][1] < gap:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out
