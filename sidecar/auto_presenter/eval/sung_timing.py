"""When the band actually reaches each golden cue, from the audio's own words.

A golden cue is when the operator *clicked*, and operators click early by
varying amounts. The perceptual scorer needs two moments the click doesn't
give it, for every cue from slide A to slide B:

* `first_word` — the first word of B starts being sung. Before this the band
  is still on A, so showing A is not late however late the click was.
* `last_line` — the last line of A starts being sung. From here on showing B
  is a good anticipatory switch, not an early one.

Both come from an offline transcript with word timestamps, aligned to the
lyrics at the *junction*: the last few words before the moment followed by the
first few after it ("...oh it is my song | let the light of the morning"). A
junction is far more specific than either side alone, which matters because
choruses repeat lines and verses share openings — "let the light of the morning"
starts four slides, the junction with what precedes it starts one.

Where the words can't be aligned (garbled singing, an ad-lib, no transcript)
the moment is *estimated* — `first_word` from the cue plus this recording's
median click-to-word offset, `last_line` by the last line's share of the
slide's words — and flagged, so a caller can say how much was measured.

Line breaks come from the song's text in the library (`slide_lines`): the
recording's snapshot carries each slide's words but not its lines.

Pure functions, no I/O, like `metrics`.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence

from auto_presenter.eval.types import CueTiming, GroundTruthEvent, SetlistItemSnapshot
from auto_presenter.eval.visible import STOP_WORDS, normalize_word, slide_tokens, words_match

Slide = tuple[int, int]

#: Words either side of a junction used to find it.
JUNCTION_WORDS = 6
#: Whisper stretches a word that follows a pause back over the silence before
#: it ("let" 22.4-23.5 s). An onset is taken as no earlier than this before the
#: word's end.
MAX_WORD_S = 0.6
#: Assumed length of a word that was not heard, when backing an onset up to
#: the true first word from the first one that matched.
WORD_S = 0.4
#: Junction alignment scores: a matched word, a wrong one, a junction word not
#: heard, a transcript word inside the junction that isn't in it.
_MATCH, _MISS, _GAP_T, _GAP_H = 2.0, -1.0, -1.0, -0.6
#: How far from the click the first word may be searched for.
SEARCH_BEFORE_S = 4.0
SEARCH_AFTER_S = 6.0


def slide_lines(
    setlist_content: Sequence[SetlistItemSnapshot],
    song_bodies: Mapping[str, str],
) -> dict[Slide, list[int]]:
    """Word count of each line of each slide, from the songs' library text.

    Counted the way `visible.slide_tokens` splits words, so a slide's counts
    sum to its token count.

    A snapshot slide is matched to the library slide carrying exactly its
    words. Slides with no match are left out; callers treat them as one line.
    """
    from auto_presenter.library.parser import parse_body_text, tokenize

    out: dict[Slide, list[int]] = {}
    for item in setlist_content:
        body = song_bodies.get(item.song_id)
        if not body:
            continue
        lines_of: dict[tuple[str, ...], list[int]] = {}
        for s in parse_body_text(body, item.song_id)[0]:
            per_line = (len(slide_tokens(tokenize(ln))) for ln in s.lyrics.split("\n"))
            counts = [n for n in per_line if n]
            lines_of.setdefault(tuple(s.words), counts)
        for idx, words in enumerate(item.slides):
            if words and tuple(words) in lines_of:
                out[(item.item_idx, idx)] = lines_of[tuple(words)]
    return out


def _onset(start: float, end: float) -> float:
    return max(start, end - MAX_WORD_S)


def locate_junction(
    transcript: Sequence[tuple[float, float, str]],
    left: Sequence[str],
    right: Sequence[str],
    lo: float,
    hi: float,
    *,
    latest: bool = False,
) -> float | None:
    """Onset of `right[0]` where `left` + `right` is sung inside [lo, hi].

    A semi-global alignment: the junction's words must be accounted for (a
    missing one costs), transcript words around it are free. Needs at least
    two matched words on each side, one of them not a stop word, or the
    placement is not trusted and None is returned. Either side may be empty
    (a song's first slide has no left), in which case it needs no matches.

    `latest` takes the last of several equally good placements instead of the
    first: a slide the band sings round several times ends on its final pass.
    """
    hyp = [
        (_onset(a, b), b, w)
        for a, b, raw in transcript
        if lo <= a <= hi and (w := normalize_word(raw))
    ]
    target = list(left) + list(right)
    n, m = len(target), len(hyp)
    if not right or m == 0:
        return None
    neg = float("-inf")
    # score[i][j]: target[:i] aligned, ending at hyp[:j]; hyp prefix is free.
    score = [[0.0] * (m + 1)] + [[neg] * (m + 1) for _ in range(n)]
    back: list[list[int]] = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        score[i][0] = score[i - 1][0] + _GAP_T
        back[i][0] = 1
        for j in range(1, m + 1):
            hit = words_match(target[i - 1], hyp[j - 1][2])
            cand = (
                (score[i - 1][j - 1] + (_MATCH if hit else _MISS), 0),
                (score[i - 1][j] + _GAP_T, 1),
                (score[i][j - 1] + _GAP_H, 2),
            )
            score[i][j], back[i][j] = max(cand)
    best = max(score[n])  # hyp suffix is free
    near = [k for k in range(m + 1) if score[n][k] >= best - 0.5]
    j = near[-1] if latest else near[0]
    i = n
    pairs: list[tuple[int, int]] = []
    while i > 0 and j > 0:
        b = back[i][j]
        if b == 0:
            if words_match(target[i - 1], hyp[j - 1][2]):
                pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif b == 1:
            i -= 1
        else:
            j -= 1
    pairs.reverse()
    nl = len(left)
    lp = [(ti, hj) for ti, hj in pairs if ti < nl]
    rp = [(ti, hj) for ti, hj in pairs if ti >= nl]
    if len(rp) < min(2, len(right)) or (left and len(lp) < min(2, nl)):
        return None
    if all(target[ti] in STOP_WORDS for ti, _ in lp + rp):
        return None
    ti, hj = rp[0]
    onset = hyp[hj][0] - (ti - nl) * WORD_S
    if lp:
        onset = max(onset, hyp[lp[-1][1]][1])  # not before the left side ended
    return onset


def cue_timings(
    ground_truth: Sequence[GroundTruthEvent],
    setlist_content: Sequence[SetlistItemSnapshot],
    transcript: Sequence[tuple[float, float, str]] | None,
    lines: Mapping[Slide, Sequence[int]] | None = None,
) -> list[CueTiming]:
    """One `CueTiming` per golden cue, in time order (the first included).

    `transcript` is `(start, end, word)` rows; None gives an all-estimated
    timing, which is the cue itself for `first_word`.
    """
    gt = sorted(ground_truth, key=lambda g: g.t_seconds)
    lines = lines or {}
    toks = {
        (i.item_idx, s): slide_tokens(words)
        for i in setlist_content
        for s, words in enumerate(i.slides)
    }

    def line_counts(slide: Slide) -> list[int]:
        got = list(lines.get(slide) or [])
        n = len(toks.get(slide, []))
        return got if got and sum(got) == n else ([n] if n else [])

    first: list[float | None] = []
    for k, ev in enumerate(gt):
        b = toks.get((ev.item_idx, ev.slide_idx), [])
        a = toks.get((gt[k - 1].item_idx, gt[k - 1].slide_idx), []) if k else []
        if not b or transcript is None:
            first.append(None)
            continue
        lo = max(ev.t_seconds - SEARCH_BEFORE_S, gt[k - 1].t_seconds if k else 0.0)
        hi = ev.t_seconds + SEARCH_AFTER_S
        if k + 1 < len(gt):
            hi = min(hi, gt[k + 1].t_seconds + 1.0)
        first.append(
            locate_junction(
                transcript, a[-JUNCTION_WORDS:], b[:JUNCTION_WORDS], lo - 3.0, hi
            )
        )

    offsets = [f - g.t_seconds for f, g in zip(first, gt, strict=True) if f is not None]
    typical = statistics.median(offsets) if offsets else 0.0
    fw = [
        (f, True) if f is not None else (g.t_seconds + typical, False)
        for f, g in zip(first, gt, strict=True)
    ]

    out: list[CueTiming] = []
    for k, ev in enumerate(gt):
        f, f_ok = fw[k]
        if k == 0:
            out.append(CueTiming(ev.t_seconds, f, f, f_ok, False))
            continue
        prev = (gt[k - 1].item_idx, gt[k - 1].slide_idx)
        a = toks.get(prev, [])
        counts = line_counts(prev)
        start_a = fw[k - 1][0]
        last, l_ok = None, False
        if a and len(counts) > 1:
            cut = sum(counts[:-1])
            if transcript is not None:
                last = locate_junction(
                    transcript,
                    a[max(0, cut - JUNCTION_WORDS) : cut],
                    a[cut : cut + JUNCTION_WORDS],
                    start_a - 1.0,
                    f + 1.0,
                    latest=True,
                )
                l_ok = last is not None and start_a - 1.0 <= last <= f
            if not l_ok:
                share = counts[-1] / sum(counts)
                last = f - share * max(0.0, f - start_a)
        elif a:
            last = start_a  # a one-line slide: all of it is its last line
        else:
            last = f  # previous slide is wordless
        last = min(max(last, min(start_a, f)), f)
        out.append(CueTiming(ev.t_seconds, f, last, f_ok, l_ok))
    return out
