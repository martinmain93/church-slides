"""What an in-service look-back learns from the performance, slide by slide.

Every `TICK_S` of audio the look-back (`learning.lookback.align`) aligns the
words that have *arrived* so far. A pass through a slide becomes reference
material only when it is done (the band has moved on, `SETTLE_S` ago) and the
look-back is sure of it: confidence >= `MIN_CONFIDENCE`, it starts at the
line's first words, and it heard most of them (the gates `learning.profile`
applies to timing). From it:

* rhythm: where each word fell, in the song's own median word spacing (a beat,
  as `learning.profile` does), up to the next line's first word;
* chords: the offline chord analysis over the slide's time, per cell; a cell
  whose chord is not clear (< `CHORD_MIN`) is left unknown.

A slide's material is set once, from its first sure pass; until then it may
borrow a tune-mate's (`tune_groups`), word places stretched to its own length.
Its own sure pass replaces a borrowed one; nothing replaces its own.
"""
from __future__ import annotations

import numpy as np

from auto_presenter.follower import score as sc
from auto_presenter.follower.sheet import CELL
from auto_presenter.follower.spec import NONE, SegmentSpec
from auto_presenter.learning.lookback import HeardWord, SongModel, align

TICK_S = 5.0
#: With pooling: also give an identical copy the learned slide's material
#: (its length too, though copies sit before different things).
POOL_COPY = __import__("os").environ.get("POOL_COPY", "1") == "1"
#: For this long after a slide is learned its material follows the look-back's
#: current view of the same pass (recomputed every tick, forgotten if the pass
#: stops qualifying); then it is frozen. A pass's hindsight keeps improving for
#: a while: Since Your Love's slide 3 ended at a pass that looked sure at 80 s
#: and read 0.51 once later words came in. 0: frozen at once.
#: A slide sung twice in a row lasts until another slide's sure pass (see above).
REPEAT_SPAN = __import__("os").environ.get("REPEAT_SPAN", "0") == "1"  # tried: 0.221 -> 0.227
REVISE_S = float(__import__("os").environ.get("REVISE_S", "30"))
#: Pool the look-back's confidence over identical-text slides (see perf_schedule).
POOL_IDENTICAL = __import__("os").environ.get("POOL_IDENTICAL", "0") == "1"  # tried: 0.208 -> 0.223, more wrong passes
SETTLE_S = 2.0
MIN_CONFIDENCE = 0.7
MAX_FIRST_WORD = 1
MIN_HEARD = 0.6
#: A line's length runs to the next line's first word, an instrumental break
#: included (the follower should wait through it as a lead sheet makes it);
#: only a gap longer than this is something else (talk, another song).
MAX_GAP_S = float(__import__("os").environ.get("MAX_GAP_S", "60"))
CHORD_MIN = float(__import__("os").environ.get("CHORD_MIN", "0.35"))  # 2: learn no chords
FPS = 10.0


def _onsets(p, n: int) -> np.ndarray | None:
    seen: dict[int, float] = {}
    for j, t in p.onsets:
        if 0 <= j < n and j not in seen:
            seen[j] = t
    if len(seen) < 2:
        return None
    js = np.array(sorted(seen))
    ts = np.maximum.accumulate(np.array([seen[j] for j in js]))
    return np.interp(np.arange(n), js, ts)


#: "words": each word where it was heard; "length": only the line's length.
RHYTHM = __import__("os").environ.get("RHYTHM", "words")
#: Cells per second of learned material; None: the song's median word spacing is a beat.
LEARN_CPS = float(__import__("os").environ["LEARN_CPS"]) if __import__("os").environ.get("LEARN_CPS") else None


#: "fit": a line's start (and the next line's) from a robust straight line
#: through its words' (place, time), not from its first heard word.
BOUNDS = __import__("os").environ.get("BOUNDS", "fit")


def _fit(p) -> tuple[float, float] | None:
    """(time of word 0, seconds per word) from a pass's heard words: the
    median of pairwise slopes (Theil-Sen), then the median intercept."""
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


#: "beats": learned material in musical time, like a lead sheet: every word at
#: its place on the beat grid (Beat This, offline), a line as many beats long
#: as the band took; "seconds": seconds at one rate per song.
UNIT = __import__("os").environ.get("UNIT", "beats")


#: A beat track with at least this share of gaps over twice the median
#: (missed beats) is not measured in: the song keeps its library slides.
#: Build My Life's (9%) halved its learned lines. None: always learn.
BEAT_GATE = float(__import__("os").environ.get("BEAT_GATE", "0.08")) or None
#: Clean the beat grid before measuring in it (see `clean_beats`).
CLEAN_BEATS = __import__("os").environ.get("CLEAN_BEATS", "1") == "1"


def clean_beats(beats: np.ndarray, half_window: int = 8) -> np.ndarray:
    """A steadier grid from a beat tracker's beats: a gap of 1.5x or more the
    local beat (the median of the neighbouring gaps) gets evenly spaced beats
    put back, and a beat closer than half the local beat to the last kept one
    is dropped. Build My Life's track missed beats (9% of gaps over twice the
    median), which halved those lines' learned length."""
    b = np.asarray(beats, float)
    if len(b) < 4:
        return b
    gaps = np.diff(b)
    local = np.array([np.median(gaps[max(0, i - half_window): i + half_window + 1]) for i in range(len(gaps))])
    out = [b[0]]
    for i, g in enumerate(gaps):
        nxt = b[i + 1]
        if nxt - out[-1] < 0.5 * local[i]:
            continue  # a doubled beat
        n = int(round((nxt - out[-1]) / local[i]))
        if n >= 2 and (nxt - out[-1]) >= 1.5 * local[i]:
            out.extend(out[-1] + (nxt - out[-1]) * k / n for k in range(1, n))
        out.append(nxt)
    return np.asarray(out)


def _beat_segment(slide, words, on, end, beats, chords) -> SegmentSpec:
    idx = np.arange(len(beats), dtype=float)
    span = beats[-1] - beats[0]
    per = span / max(1, len(beats) - 1)

    def b_of(t):  # fractional beat index, linear past the ends
        return np.interp(t, beats, idx, left=np.nan, right=np.nan) if beats[0] <= t <= beats[-1] else (t - beats[0]) / per

    def t_of(b):
        return float(np.interp(b, idx, beats)) if 0 <= b <= idx[-1] else beats[0] + b * per

    b0 = b_of(on[0])
    cells = max(len(words) + 1, int(round((b_of(end) - b0) / CELL)) + 1)
    offs = [min(cells - 1, max(0, int(round((b_of(t) - b0) / CELL)))) for t in on]
    offs = list(np.maximum.accumulate(offs))
    chord = np.full(cells, NONE, np.int16)
    for c in range(cells):
        a, z = int(t_of(b0 + c * CELL) * FPS), int(t_of(b0 + (c + 1) * CELL) * FPS) + 1
        if 0 <= a < len(chords):
            pr = chords[a:max(a + 1, min(z, len(chords)))].mean(axis=0)
            k = int(np.argmax(pr[:24]))
            if pr[k] >= CHORD_MIN:
                chord[c] = k
    return SegmentSpec(slide=slide, cells=cells, words=[(w, int(o)) for w, o in zip(words, offs)],
                       midi=np.full(cells, NONE, np.int16), chord=chord)


def _segment(slide, words, on, end, spacing, chords, beats=None) -> SegmentSpec:
    if UNIT == "beats" and beats is not None:
        return _beat_segment(slide, words, on, end, beats, chords)
    cps = LEARN_CPS or (1.0 / CELL) / spacing  # cells per second
    t0 = on[0]
    cells = max(len(words) + 1, int(round((end - t0) * cps)) + 1)
    if RHYTHM == "length":
        # trust the line's length, not each word's time: the words evenly
        # over the sung part, as the library has them
        sung = (on[-1] - t0) * cps
        offs = np.minimum(np.round(np.linspace(0, sung, len(words))).astype(int), cells - 1)
    else:
        offs = np.minimum(np.round((on - t0) * cps).astype(int), cells - 1)
    chord = np.full(cells, NONE, np.int16)
    for c in range(cells):
        a, b = int((t0 + c / cps) * FPS), int((t0 + (c + 1) / cps) * FPS) + 1
        if 0 <= a < len(chords):
            pr = chords[a:min(b, len(chords))].mean(axis=0)
            k = int(np.argmax(pr[:24]))
            if pr[k] >= CHORD_MIN:
                chord[c] = k
    return SegmentSpec(slide=slide, cells=cells, words=[(w, int(o)) for w, o in zip(words, offs)],
                       midi=np.full(cells, NONE, np.int16), chord=chord)


def _borrow(src: SegmentSpec, slide: int, words: list[str]) -> SegmentSpec:
    """A tune-mate's segment, its word places stretched to `words`."""
    so = np.array([o for _w, o in src.words], float)
    pos = np.interp(np.linspace(0, len(so) - 1, len(words)), np.arange(len(so)), so)
    offs = np.minimum(np.round(pos).astype(int), src.cells - 1)
    return SegmentSpec(slide=slide, cells=src.cells, words=[(w, int(o)) for w, o in zip(words, offs)],
                       midi=src.midi.copy(), chord=src.chord.copy())


#: Where learned rhythm comes from: "forced" (the slide's lyrics forced onto
#: its audio, `forced_align`, the live scorer's own DTW timing) or "words"
#: (the free transcript's word times, as the look-back hears them).
RHYTHM_SRC = __import__("os").environ.get("RHYTHM_SRC", "forced")
MAX_LINE_S = 28.0


def _forced(ctx, p, nxt_pass, words, nxt_words, prev_words=()):
    """(word starts, the next line's first word) for pass `p`, by forced
    alignment of the previous line's last words + this line + the next
    line's first words. The DTW path starts at the window's first frame, so
    whatever leads in (an instrumental, the window's margin) is absorbed by
    the first word aligned: without a prefix that was this line's first word,
    which then started ~1.5 s early (every learned line had word 1 six or more
    cells after word 0)."""
    import forced_align as FA

    name, audio = ctx[0], ctx[1]
    f, g = _fit(p), _fit(nxt_pass)
    if f is None:
        return None
    pre = list(prev_words)[-2:] if PREFIX else []
    t0 = f[0] - (3.0 if pre else 1.5)
    nxt_t = g[0] if g is not None else nxt_pass.t_start
    line_end = f[0] + f[1] * len(words) + 2.0
    # the next line's first words close the window when they are near; after a
    # long break the line is aligned alone and ends where the next line starts
    near = nxt_t + 1.5 - t0 <= MAX_LINE_S and nxt_t - line_end <= 4.0
    t1 = nxt_t + 1.5 if near else line_end
    if not 0 < t1 - t0 <= MAX_LINE_S:
        return None
    tail = nxt_words[:2] if near else []
    got = FA.aligned(name, audio, p.slide, pre + words + tail, round(t0, 1), round(t1, 1))
    if got is None:
        return None
    got = got[len(pre):]
    on = np.maximum.accumulate(np.array(got[: len(words)]))
    if not pre and len(on) >= 3:
        # nothing in front to absorb the lead-in: word 0 from the spacing after it
        on[0] = max(on[0], on[1] - (on[2] - on[1]))
    stop = got[len(words)] if tail and len(got) > len(words) else nxt_t
    return on, max(stop, on[-1] + 0.1)


#: Align with the previous line's last words in front (see `_forced`).
PREFIX = __import__("os").environ.get("PREFIX", "1") == "1"


def dominant_beat(beats: np.ndarray) -> float:
    """The song's beat period: the median gap, folded by octaves into 0.5-1.2 s."""
    g = float(np.median(np.diff(beats)))
    while g < 0.5:
        g *= 2
    while g > 1.2:
        g /= 2
    return g


#: item -> slide -> (t_start, t_end, confidence) of the pass it was last learned from
PASSES: dict = {}
#: item -> slide -> lexical match of the pass last considered for it
LEX: dict = {}
#: Mark a slide looped once the look-back is sure the band sang it twice in a
#: row (`ReferenceSpec.loops`). item -> [(time marked, slide)]
LOOP_MARKS = __import__("os").environ.get("LOOP_MARKS", "0") == "1"
MARKS: dict = {}
_env = __import__("os").environ.get
#: Root-cause switches (2026-10-10 diagnosis; off when unset):
#: a pass must account for this share of the words heard during it (talk
#: before a song brushed "our god" and passed for a chorus)
PREC_MIN = float(_env("PREC_MIN", "0")) or None
#: reject a learned line with more than this share of its words on one cell
#: (forced alignment collapsed)
COLLAPSE_MAX = float(_env("COLLAPSE_MAX", "0")) or None
#: a line ends at most this many word gaps after its last word: one pass's
#: instrumental or vamp is not the line's length
GAP_K = float(_env("GAP_K", "0")) or None
#: a pass must have this share of its words match the lyric word they are
#: placed on (or a neighbour): the look-back places every heard word on some
#: lyric word, talk included, so its confidence never says whether the song
#: is being sung
LEX_MIN = float(_env("LEX_MIN", "0")) or None


def lexical_match(p, lyric: list[str], text_at: dict) -> float:
    """Share of pass `p`'s words that are the lyric word they are placed on, or
    one either side."""
    hits = 0
    for j, t in p.onsets:
        w = text_at.get(round(t, 3))
        if w and w in lyric[max(0, j - 1): j + 2]:
            hits += 1
    return hits / max(1, len(p.onsets))


def perf_schedule(rows, item, slides, groups, mode, chords, end, ctx=None):
    global LEARN_CPS
    if ctx is not None and len(ctx) > 2 and ctx[2] is not None and BEAT_GATE is not None:
        g = np.diff(np.asarray(ctx[2], float))
        if len(g) and np.mean(g > 2 * np.median(g)) >= BEAT_GATE:
            return []  # the beat track misses beats: learn nothing, keep the library's slides
    if ctx is not None and len(ctx) > 2 and ctx[2] is not None:
        if UNIT == "beatrate":
            # seconds at one steady rate per song: its dominant beat is a beat
            LEARN_CPS = 1.0 / (CELL * dominant_beat(ctx[2]))
            ctx = (ctx[0], ctx[1], None)
        elif CLEAN_BEATS:
            ctx = (ctx[0], ctx[1], clean_beats(ctx[2]))
    """[(audio time, {slide: SegmentSpec})] as the look-back learns."""
    norm = {si: [w for w in (sc.norm(x) for x in sw) if w] for si, sw in enumerate(slides)}
    words = sorted((r["arr"], r["t"], r["text"]) for r in rows if r["e"] == "word" and r.get("item") == item)
    text_at = {round(tw, 3): sc.norm(txt) for _arr, tw, txt in words}
    MARKS[item] = []
    model = SongModel(slides)
    same_text: dict[int, set[int]] = {}
    for a, wa in norm.items():
        if wa:
            same_text[a] = {b for b, wb in norm.items() if wb == wa}
    group_of: dict[int, set[int]] = {}
    for g in groups:  # a slide may sit in several (direct-match groups): its mates are their union
        for s_ in g:
            group_of.setdefault(s_, set()).update(g)
    own: dict[int, SegmentSpec] = {}
    #: slide -> (time learned, its pass's start): material still settling
    born: dict[int, tuple[float, float]] = {}
    out, last = [], None
    t = TICK_S
    while t <= end + TICK_S:
        heard = [HeardWord(tw, txt) for arr, tw, txt in words if arr <= t]
        if len(heard) >= 8:
            al = align(model, heard)
            ps = al.passes
            if POOL_IDENTICAL:
                # confidence in the *text*: a chorus written as four identical
                # slides splits the look-back's posterior four ways (King Of My
                # Heart's slides 7-10 never reached 0.7 though it was sure)
                col = {sl: k for k, sl in enumerate(al.slides)}
                wt = np.array([w.t for w in al.words])
                for q in ps:
                    grp = [col[x] for x in same_text.get(q.slide, {q.slide}) if x in col]
                    sel = (wt >= q.t_start - 1e-6) & (wt <= q.t_end + 1e-6)
                    if sel.any() and grp:
                        q.confidence = float(al.posterior[sel][:, grp].sum(axis=1).mean())
            gaps = [b - a for p in ps if p.confidence >= MIN_CONFIDENCE
                    for (_j, a), (_k, b) in zip(p.onsets, p.onsets[1:]) if 0 < b - a < 3]
            spacing = float(np.median(gaps)) if len(gaps) >= 5 else None
            # where a line ends: the next pass the look-back is sure of, not a
            # stray one (talk over an instrumental, mapped to some line)
            sure = [k for k, q in enumerate(ps) if q.confidence >= MIN_CONFIDENCE and q.n_words >= 3]
            for i, p in enumerate(ps[:-1]):
                n = len(norm.get(p.slide, ()))
                settling = p.slide in born and t - born[p.slide][0] < REVISE_S and abs(p.t_start - born[p.slide][1]) < 3.0
                if (spacing is None or (p.slide in own and not settling) or not n or p.t_end > t - SETTLE_S
                        or p.confidence < MIN_CONFIDENCE or min(j for j, _ in p.onsets) > MAX_FIRST_WORD
                        or len({j for j, _ in p.onsets}) < MIN_HEARD * n):
                    continue
                nk = next((k for k in sure if k > i), None)
                if nk is None:
                    continue
                q = ps[nk]
                if REPEAT_SPAN and q.slide == p.slide:
                    # the band sang the slide again ("x2"): it lasts until the
                    # next sure pass of another slide, its repeat included
                    nk2 = next((k for k in sure if k > nk and ps[k].slide != p.slide), None)
                    if nk2 is None:
                        continue
                    q = ps[nk2]
                on = _onsets(p, n)
                if on is None:
                    continue
                nxt = q.t_start
                if RHYTHM_SRC == "forced" and ctx is not None:
                    prev = norm.get(ps[max(k for k in sure if k < i)].slide, []) if any(k < i for k in sure) else []
                    got = _forced(ctx, p, q, norm[p.slide], norm.get(q.slide, []), prev)
                    if got is None:
                        continue
                    on, stop = got
                    if stop - on[-1] > MAX_GAP_S:
                        stop = on[-1] + 2 * spacing
                elif BOUNDS == "fit":
                    f, g = _fit(p), _fit(q)
                    if f is None:
                        continue
                    start, per = f
                    on = np.clip(on, start, None)
                    on[0] = start
                    if g is not None:
                        nxt = g[0] - min(j for j, _ in q.onsets) * 0  # the next line's word 0
                    last_word = start + per * (n - 1)
                    stop = nxt if 0 < nxt - last_word <= MAX_GAP_S else last_word + 2 * per
                    on = np.maximum.accumulate(np.minimum(on, stop))
                else:
                    stop = nxt if nxt - on[-1] <= MAX_GAP_S else on[-1] + 2 * spacing
                lex = lexical_match(p, norm[p.slide], text_at)
                LEX.setdefault(item, {})[p.slide] = lex
                if LEX_MIN is not None and lex < LEX_MIN:
                    continue
                if PREC_MIN is not None:
                    inside = sum(1 for _a, tw, _x in words if p.t_start - 1e-6 <= tw <= p.t_end + 1e-6)
                    if p.n_words < PREC_MIN * max(1, inside):
                        continue
                if GAP_K is not None:
                    stop = min(stop, on[-1] + GAP_K * spacing)
                seg = _segment(p.slide, norm[p.slide], on, stop, spacing, chords,
                               ctx[2] if ctx is not None and len(ctx) > 2 else None)
                if COLLAPSE_MAX is not None and len(seg.words) > 2:
                    offs = np.array([o for _w, o in seg.words])
                    if float(np.mean(np.diff(offs) == 0)) > COLLAPSE_MAX:
                        continue
                if p.slide not in born:
                    born[p.slide] = (t, p.t_start)
                PASSES.setdefault(item, {})[p.slide] = (p.t_start, p.t_end, p.confidence, lex)
                own[p.slide] = seg
        if len(heard) >= 8 and LOOP_MARKS:
            marked = {sl for _tm, sl in MARKS.get(item, [])}
            for p, q in zip(ps, ps[1:]):
                if (p.slide == q.slide and p.slide not in marked and q.t_end <= t - SETTLE_S
                        and min(p.confidence, q.confidence) >= MIN_CONFIDENCE
                        and min(p.n_words, q.n_words) >= 3
                        and min(lexical_match(p, norm[p.slide], text_at),
                                lexical_match(q, norm[q.slide], text_at)) >= (LEX_MIN or 0.0)):
                    MARKS.setdefault(item, []).append((t, p.slide))
                    marked.add(p.slide)
        if len(heard) >= 8 and REVISE_S > 0:
            for sl, (tb, ts) in list(born.items()):
                if t - tb >= REVISE_S or sl not in own:
                    continue
                q = next((x for x in ps if x.slide == sl and abs(x.t_start - ts) < 3.0), None)
                if q is None or q.confidence < MIN_CONFIDENCE:
                    # the look-back changed its mind about this pass: forget it
                    del own[sl], born[sl]
        if POOL_IDENTICAL and POOL_COPY:
            for sl in list(own):
                for mate in same_text.get(sl, ()):
                    own.setdefault(mate, own[sl])  # an identical copy is the same line
        known = dict(own)
        if mode != "naive":
            for s, seg in own.items():
                for mate in group_of.get(s, ()):
                    if mate not in known and norm.get(mate):
                        known[mate] = _borrow(seg, mate, norm[mate])
        sig = (tuple(sorted((k, id(v)) for k, v in own.items())) + tuple(sorted(known))
               + tuple(sl for _tm, sl in MARKS.get(item, [])))
        if sig != last:
            out.append((t, known))
            last = sig
        t += TICK_S
    return out


def lookback_order(rows, item, slides, end):
    """[(audio time, learned sequences)]: a move a -> b between two passes the
    look-back is sure of (consecutive among the sure ones, both settled),
    weight 1 per sighting. The same gates as material."""
    words = sorted((r["arr"], r["t"], r["text"]) for r in rows if r["e"] == "word" and r.get("item") == item)
    model = SongModel(slides)
    out, last = [], None
    t = TICK_S
    while t <= end + TICK_S:
        heard = [HeardWord(tw, txt) for arr, tw, txt in words if arr <= t]
        if len(heard) >= 8:
            ps = align(model, heard).passes
            sure = [q for q in ps if q.confidence >= MIN_CONFIDENCE and q.n_words >= 3 and q.t_end <= t - SETTLE_S]
            seqs = [([a.slide, b.slide], 1.0) for a, b in zip(sure, sure[1:]) if a.slide != b.slide]
            sig = tuple(tuple(q) for q, _w in seqs)
            if sig != last:
                out.append((t, seqs))
                last = sig
        t += TICK_S
    return out
