"""A song's lead sheet as the follower's reference: one segment per library slide
(and, with counted loops, further segments for the passes of a section the
band repeats: `LOOPS`).

Ported from the music-only spike (`segref.py`, `lead.py`, `stage1.song_spans`,
`gt.prune_spans`, `run_joint.roadmap_sequence`). Each library slide becomes a
segment of cells (eighth notes) carrying *its own* words, placed on the
rhythm of the line the sheet sings it to, with that line's notes and chords.
A segment runs until the next line's first sung word in the sheet's roadmap
(`extend_to_next`): that is where an operator changes slide, and stopping at
the last word plus a bar made a correct tempo reach the end of the segment
during the gap between lines.

At a segment's end the band moves on by the *order prior*: half library
order, half the sheet's roadmap.

**The intro cell.** Cell 0 is the song's leading blank slide: the band
playing before anyone sings. It holds (it does not advance with the tempo)
and leaks into the first sung line slowly, so an instrumental intro does not
walk the follower through the first slide; the first line's evidence pulls
the belief out of it.

The follower sees only the `Reference` interface (cells, tempo range,
segments, order prior, one scoring function per source). A reference built
from a recorded service — the operator's own slide changes as segments —
implements the same interface (see the spike's `recorded.py`).
"""

from __future__ import annotations

import itertools
import os
from dataclasses import dataclass
from difflib import SequenceMatcher

import numpy as np

from auto_presenter.follower import score as sc
from auto_presenter.follower import sheet
from auto_presenter.follower.sheet import CELL
from auto_presenter.follower.spec import ReferenceSpec, SegmentSpec
from auto_presenter.follower.talk import lyric_pairs
from auto_presenter.index.phonetic import phonetic_match_score

#: Scored evidence at the intro: the mean log-probability per token a window
#: with no line in it reaches. Scores are otherwise only compared with each
#: other, so before anyone sang the least-bad line (-4.2 to -5 on King Of My
#: Heart's intro) pulled the follower off the blank. Sung lines score -0.6 to
#: -1.5. Measured flat from -2.5 to -3.5 (4 goldens 0.167 -> 0.162).
SCORED_NULL = -3.0
#: How scorer evidence is formed (`SegmentReference.scored`): "legacy" — a
#: fixed temperature (`tokens`) and a fixed robustness floor (`rho`);
#: "entropy" — the calibrated temperature `ENTROPY_BETA` and a floor set per
#: step by how unmistakable the step is: the normalised entropy H of its
#: posterior over slides gives floor = ENTROPY_EPS_MIN ** (1 - H), so a step
#: sure of one slide (H ~ 0) can push the others down by up to
#: log(ENTROPY_EPS_MIN) and an uncertain one (H ~ 1) by nothing.
#: Calibrated on 1282 scorer steps of the 8 live traces against the band's
#: slide (research/spikes/music_only/scorer_calibration.py): mean log-prob per
#: token x 2 is calibrated (log-loss 0.92 vs 1.64 at the legacy x7; a 0.9+
#: posterior is right 95% of the time), and steps with entropy < 0.1 are right
#: 96% of the time against ~30% above 0.7.
SCORED_MODE = "legacy"
ENTROPY_BETA = 2.0
ENTROPY_EPS_MIN = 0.01
#: Scorer steps count by how clearly they heard a line (`reliability`).
RELIABILITY_FLOOR = -4.5
RELIABILITY_FULL = -2.5


def robust(x: np.ndarray, rho: float) -> np.ndarray:
    """log(rho + (1-rho) e^x): a source that is confidently wrong can lower a
    state by at most log(rho) per observation."""
    if rho <= 0:
        return x
    return np.log(rho + (1 - rho) * np.exp(np.minimum(x, 30)))


def prune_spans(
    s: sc.Score, spans: dict[int, list[sc.Span]], contained: set[int] | frozenset[int] = frozenset()
) -> dict[int, list[sc.Span]]:
    """Drop a span that begins inside another slide's span when the slide has
    a span that does not: a line also sung straddling two other slides is not
    where that slide is. For a slide `contained` in another, drop such spans
    even when nothing else is left: its words are then sung only as part of
    that other slide. Beginning at the same word counts when the other
    span is longer: Build My Life's "worthy of every breath … we live for
    you" (slide 2) is also the start of slide 4's "… we live for you, we live
    for you", and crediting both put a 2 into the order where the band sings
    4. Identical spans (identical slides) are not inside each other."""
    out = {}
    for si, sps in spans.items():

        def inside(sp, si=si):
            return any(
                o.w0 < sp.w0 <= o.w1 or (o.w0 == sp.w0 and o.w1 > sp.w1)
                for sj, os_ in spans.items()
                if sj != si
                for o in os_
            )

        clean = [sp for sp in sps if not inside(sp)]
        out[si] = clean if (clean or si in contained) else sps
    return out


def contained_slides(slides: list[list[str]]) -> set[int]:
    """Slides whose words all appear, in order, inside another (longer) slide.

    Nothing But The Blood's slide 5 is "nothing but the blood of Jesus", the
    tag that also ends slides 1, 2 and 4. The sheet prints the tag only inside
    those verses, so the slide's only places in the sheet lay over the second
    half of each — and the follower flipped to it half-way through every verse
    (the largest share of the "a human never would" errors, 2026-10-08). The
    containing slide already shows those words; the contained one gets no
    place of its own."""
    norm = [" " + " ".join(w for w in (sc.norm(t) for t in sw) if w) + " " for sw in slides]
    out = set()
    for a, wa in enumerate(norm):
        if not wa.strip():
            continue
        for b, wb in enumerate(norm):
            if a != b and len(wb) > len(wa) and wa in wb:
                out.add(a)
                break
    return out


def song_spans(
    s: sc.Score, slides: list[list[str]], sections: list[str]
) -> dict[int, list[sc.Span]]:
    """Where each slide is sung in the sheet, pruned; a slide the sheet does
    not print (a hymn prints one verse, the band sings five) takes the spans of
    a bound slide of the same section type, closest in word count. A slide
    contained in another (`contained_slides`) keeps only places of its own."""
    inside = contained_slides(slides)
    spans = prune_spans(s, sc.bind_slides(s, slides), contained=inside)
    kind = [sec.split(":")[0] if sec else "" for sec in sections]
    for si, sw in enumerate(slides):
        if spans.get(si) or not sw or si >= len(kind) or si in inside:
            continue
        same = [sj for sj in spans if spans[sj] and sj < len(kind) and kind[sj] == kind[si]]
        if same:
            best = min(same, key=lambda sj: abs(len(slides[sj]) - len(sw)))
            spans[si] = list(spans[best])
    return spans


def roadmap_sequence(spans: dict[int, list[sc.Span]]) -> list[int]:
    """The slide order the sheet's roadmap sings: every span, by its place in
    the unrolled sheet.

    A line several identical slides carry (a chorus line sung twice, as slides
    3 and 4) is credited pass by pass: to the copy that continues library order
    from the slide before it, else to the next copy in turn. Crediting every
    pass to the lowest-numbered copy, as the spike did, turned King Of My
    Heart's "3, 4, 5" into "3, 3, 5" and Reckless Love's "7, 8, 9" into
    "7, 5, 9" — an order prior sending half the belief out of the chorus one
    line early, so the follower waited for words at every section change."""
    by_span: dict[tuple[int, int], tuple[float, list[int]]] = {}
    for si, sps in spans.items():
        for sp in sps:
            key = (sp.w0, sp.w1)
            start, owners = by_span.get(key, (sp.start, []))
            by_span[key] = (start, sorted({*owners, si}))
    out: list[int] = []
    turn: dict[tuple[int, ...], int] = {}
    for _start, owners in sorted(by_span.values()):
        if len(owners) == 1:
            out.append(owners[0])
            continue
        group = tuple(owners)
        prev = out[-1] if out else None
        if prev is not None and prev + 1 in owners:
            pick = prev + 1
        else:
            k = turn.get(group, 0)
            pick = owners[k % len(owners)]
        turn[group] = (owners.index(pick) + 1) % len(owners)
        out.append(pick)
    return out


#: Share of the order prior taken from the sheet's roadmap (the rest is
#: library order) wherever the roadmap says where a segment goes next. The
#: roadmap carries the repeats a library's slide list leaves out; at an even
#: split King Of My Heart's chorus return (library says 7, roadmap 3) was a
#: coin toss. Measured 0.5 -> 0.85: 4 goldens 0.162 -> 0.149 offline, live
#: traces 0.176 -> 0.165; flat above 0.85.
ROADMAP_WEIGHT = 0.85


#: Learned orders (`ReferenceSpec.learned`): the base prior (library order
#: and any sheet roadmap) counts as this many observed transitions from each
#: line — a lead sheet weighs about as much as three services.
LEARNED_PRIOR_COUNT = 3.0
#: The learned share of a line's successor never exceeds this, so library
#: order (and a novel arrangement) can always still be followed.
LEARNED_CAP = 0.9


#: From the song's last slide, the share of the order prior that repeats it;
#: the rest is spread over the other sung slides. Was "the last back to the
#: first", which on the ground truth (75 recordings) is the rarest thing a
#: band does after its last slide: another song 379, an earlier slide 286, the
#: first slide 13 (a vamp on the last slide shows no change at all). Through
#: Since Your Love's three-minute closing vamp the follower wrapped to the
#: first line every ~10 s and the evidence brought it back. None: the old
#: wrap.
LAST_REPEAT: float | None = 0.5


#: Chord evidence for a cell with no chord: the mean over cells with one
#: (`SegmentReference.chords`), not 0. A reference learned during the
#: service has chords for some slides only; at 0, King Of My Heart's slide 6
#: (none learned yet) lost the belief to slides 1, 2 and 5 (learned, a
#: similar tune) while the band sang it. 8 live traces, wrong / perceived:
#: lead sheet 0.131 / 0.157 -> 0.130 / 0.156; library + tunes learned in the
#: service 0.205 / 0.253 -> 0.206 / 0.249, their ideal 0.164 / 0.198 ->
#: 0.156 / 0.179; library slides alone have no chords (unchanged). False: 0.
CHORDLESS_AT_MEAN = True
#: Counted loops. A section the band repeats (a run of identical slides, a
#: tag closing a slide, a slide heard sung twice in a row: `ReferenceSpec.
#: loops`) is unrolled into up to `LOOP_MAX` passes, each its own segment,
#: so the follower knows how many times it has been sung; at the end of pass
#: k the band leaves with `loop_leave(k)`: rarely on an odd count, more on 2
#: and 6, mostly on 4, nearly always on 8. Before, a run of four identical
#: slides was exactly four passes (King Of My Heart's chorus sung a fifth
#: time jumped to the next section) and a tag repeated with a fixed 0.5 at
#: every end. False: off.
#: Replayed on the 8 live traces (wrong / perceived / 5x jump seconds):
#: library slides 0.219/0.254/24.1 -> 0.204/0.216/8.2 (King Of My Heart
#: 0.261 -> 0.117, its 18 s of jumps gone); tunes learned in the service
#: 0.179/0.230/34.9 -> 0.170/0.209/26.5; nothing worse beyond 0.002.
#: Marking slides the look-back hears sung twice in a row made it worse
#: (0.170 -> 0.178); not built. Real time, 4 recordings, library slides:
#: 0.222 -> 0.176. Set per session from the `counted_loops` tunable
#: (`server_follower.build_session_followers`); AUTO_PRESENTER_LOOPS=0/1
#: overrides both, for the eval harness.
LOOPS_ENV = os.environ.get("AUTO_PRESENTER_LOOPS")
LOOPS = LOOPS_ENV != "0"
LOOP_MAX = 12
#: Heard words whose match vectors a reference keeps (`_word_vec`).
WORD_VEC_CACHE = 512
#: After a section's first pass, the chance it is sung again: a tag closing
#: a slide (most verses sing it once), a slide heard looped before.
LOOP_TAG_AGAIN = 0.5
LOOP_MARKED_AGAIN = 0.5
#: Inside a written run of identical slides: the band follows the writing.
LOOP_WRITTEN_LEAVE = 0.05
#: After a written run's last pass, the chance the band moves on as written
#: (at least; the count's own rhythm from the next pass). With the rhythm
#: alone a pair sung as written showed the next line 3 s late, a run of 3
#: 4 s late.
LOOP_WRITTEN_END_LEAVE = 0.7
#: The last pass (`LOOP_MAX`): the chance a further pass is still this line,
#: so a long closing vamp doesn't leave for an earlier slide.
LOOP_LAST_STAY = 0.5


def loop_leave(k: int) -> float:
    """The chance the band leaves a looped section after its k-th pass."""
    if k >= LOOP_MAX:
        return 1.0
    if k % 8 == 0:
        return 0.85
    if k % 4 == 0:
        return 0.6
    if k % 2 == 0:
        return 0.3
    return 0.05


#: None: the mean; else this quantile of the cells with a chord (a chordless
#: slide assumed to fit about as well as a good match does).
CHORDLESS_QUANTILE: float | None = None


def order_prior(
    seg_slides: list[int],
    sequences: list[list[int]],
    sung: list[int],
    learned: list[tuple[list[int], float]] = (),
) -> np.ndarray:
    """S x S: from each segment, where the band goes next.

    The base: library order (each sung slide to the next, the last back to
    the first), mixed `ROADMAP_WEIGHT` with the `sequences`' transitions
    (a lead sheet's roadmap) wherever they say where a segment goes next.

    Then `learned` (sequence, weight) pairs from past services: from a line
    with n weighted observed transitions, the learned successors take
    n / (n + LEARNED_PRIOR_COUNT) of the row, at most `LEARNED_CAP`."""
    n_seg = len(seg_slides)
    idx = {sl: i for i, sl in enumerate(seg_slides)}
    lib = np.zeros((n_seg, n_seg))
    # Only slides with a segment: a tag slide (`spec.tag_slides`) is covered
    # but has none, and as the song's last slide it hid the real last one
    # from the rule below, which then went to the first slide every time
    # (13 of 361 library songs; a line sung five times jumped to slide 1
    # four times, 2026-11-13).
    order = [s for s in (sung or seg_slides) if s in idx] or seg_slides
    for a, b in zip(order, order[1:] + order[:1], strict=True):
        if a in idx and b in idx and idx[a] != idx[b]:
            lib[idx[a], idx[b]] += 1
    for i in range(n_seg):
        if lib[i].sum() == 0:
            lib[i, (i + 1) % n_seg] = 1
    if LAST_REPEAT is not None and n_seg > 1 and order and order[-1] in idx:
        # after the last slide: the band repeats it (a closing vamp) or goes
        # back to an earlier one (a chorus); the first only as one of those
        last = idx[order[-1]]
        lib[last] = (1 - LAST_REPEAT) / (n_seg - 1)
        lib[last, last] = LAST_REPEAT
    lib /= lib.sum(axis=1, keepdims=True)

    def counts(seqs: list[tuple[list[int], float]]) -> np.ndarray:
        obs = np.zeros((n_seg, n_seg))
        for seq, w in seqs:
            for a, b in itertools.pairwise(seq):
                if a in idx and b in idx and idx[a] != idx[b]:
                    obs[idx[a], idx[b]] += w
        return obs

    obs = counts([(seq, 1.0) for seq in sequences])
    out = lib.copy()
    for i in range(n_seg):
        if obs[i].sum() > 0:
            out[i] = (1 - ROADMAP_WEIGHT) * lib[i] + ROADMAP_WEIGHT * obs[i] / obs[i].sum()
    if learned:
        seen = counts(list(learned))
        for i in range(n_seg):
            n = seen[i].sum()
            if n > 0:
                share = min(LEARNED_CAP, n / (n + LEARNED_PRIOR_COUNT))
                out[i] = (1 - share) * out[i] + share * seen[i] / n
    return out


@dataclass(frozen=True)
class Coverage:
    """How much of the song the sheet can follow."""

    bound: int  # sung slides with a place in the sheet
    sung: int  # slides with words

    @property
    def share(self) -> float:
        return self.bound / self.sung if self.sung else 0.0


def sheet_spec(s: sc.Score, slides: list[list[str]], sections: list[str]) -> ReferenceSpec:
    """A lead sheet as a `ReferenceSpec`: each slide placed at its first span
    in the unrolled sheet, carrying the sheet's melody and chords and its own
    words on the rhythm of the line the sheet sings it to; the roadmap as the
    one observed order."""
    spans = song_spans(s, slides, sections)
    midi_sheet, chord_sheet = sheet.cell_tables(s)
    all_starts = sorted({sp.start for sps in spans.values() for sp in sps})
    segments: list[SegmentSpec] = []
    # a contained slide is shown by its container: covered, not missing
    inside = contained_slides(slides)
    covered = [si for si, sw in enumerate(slides) if sw and (spans.get(si) or si in inside)]
    for si, sw in enumerate(slides):
        sps = spans.get(si) or []
        if not sw or not sps:
            continue
        sp = sps[0]
        a = max(0, int(sp.start // CELL))
        nxt = [x for x in all_starts if x > sp.start + 1e-9]
        stop = nxt[0] if nxt else s.length
        b = min(len(midi_sheet), max(a + 1, int(np.ceil(stop / CELL))))
        # this slide's own words, at the onsets of the sheet's words for the
        # line (matched where they agree; spread over the line where not)
        sheet_words = s.words[sp.w0 : sp.w1 + 1]
        mine = [w for w in (sc.norm(x) for x in sw) if w]
        pos: list[float | None] = [None] * len(mine)
        for blk in SequenceMatcher(
            None, mine, [w.text for w in sheet_words], autojunk=False
        ).get_matching_blocks():
            for q in range(blk.size):
                pos[blk.a + q] = sheet_words[blk.b + q].onset
        onsets = [w.onset for w in sheet_words]
        for j in range(len(mine)):
            if pos[j] is None:
                # by proportion through the line: an unprinted verse sings
                # its own words to the printed verse's rhythm
                k = min(len(onsets) - 1, round(j * (len(onsets) - 1) / max(1, len(mine) - 1)))
                pos[j] = onsets[k] if onsets else sp.start
        words = [
            (w, min(b - a - 1, max(0, int(p // CELL) - a))) for w, p in zip(mine, pos, strict=True)
        ]
        segments.append(
            SegmentSpec(
                slide=si,
                cells=b - a,
                words=words,
                midi=np.asarray(midi_sheet[a:b], np.int16),
                chord=np.asarray(chord_sheet[a:b], np.int16),
            )
        )
    roadmap = roadmap_sequence(spans)
    placed = {g.slide for g in segments}
    first = next((sl for sl in roadmap if sl in placed), covered[0] if covered else None)
    return ReferenceSpec(
        source="lead_sheet",
        segments=segments,
        covered=covered,
        sequences=[roadmap],
        first_slide=first,
        spans=spans,
    )


class SegmentReference:
    """One segment per placed slide, plus the intro cell (cell 0); with
    `LOOPS`, after them a segment per further pass of each looped section
    (`copy_of` maps its cells to the ones they repeat).

    Built from a `ReferenceSpec` (`follower.spec`), or from a lead sheet's
    `Score`, which goes through `sheet_spec`. Nothing here knows which."""

    #: Reference interface: cells, tempo states (cells per second), segments.
    P: int
    rates: np.ndarray

    def __init__(
        self,
        source: sc.Score | ReferenceSpec,
        slides: list[list[str]],
        sections: list[str] | None = None,
        *,
        bpm: tuple[float, float] = (40, 130),
        n_rates: int = 16,
        word_slack: tuple[int, int] = (2, 1),
        rho: float = 0.2,
        blank_slide: int | None = 0,
    ):
        if isinstance(source, ReferenceSpec):
            spec, self.s = source, None
        else:
            spec, self.s = sheet_spec(source, slides, sections or []), source
        self.spec = spec
        self.source = spec.source
        self.spans = spec.spans or {}
        #: The lyrics' word pairs, for telling talk from singing (`follower.talk`).
        self.lyric_pairs = lyric_pairs(slides)
        #: Each slide's normalised words, and its section label ("chorus:1"),
        #: for the next-line check (`OnlineFollower.next_line_query`).
        self.slide_words = [[w for w in (sc.norm(x) for x in sw) if w] for sw in slides]
        self.sections = list(sections or [])
        # cell 0: the intro (the item's leading blank slide)
        midi, chord, seg_of, slide_of = (
            [-1],
            [-1],
            [0],
            [blank_slide if blank_slide is not None else -1],
        )
        starts, ends = [0], [0]
        words: list[tuple[str, int]] = []
        word_cell: dict[tuple[int, int], int] = {}
        inside = contained_slides(slides)
        for g in spec.segments:
            off = len(midi)
            seg = len(starts)
            starts.append(off)
            midi.extend(g.midi.tolist())
            chord.extend(g.chord.tolist())
            slide_of.extend([g.slide] * g.cells)
            seg_of.extend([seg] * g.cells)
            ends.append(len(midi) - 1)
            for j, (w, c) in enumerate(g.words):
                words.append((w, off + c))
                word_cell[(g.slide, j)] = off + c
        # A slide ending with another slide's words (Nothing But The Blood's
        # verses end with its tag, "nothing but the blood of Jesus") can be
        # tagged: the band repeats that ending. segment -> the cell where its
        # tag starts.
        self.tag_restart: dict[int, int] = {}
        normed = {si: [w for w in (sc.norm(x) for x in sw) if w] for si, sw in enumerate(slides)}
        for si, mw in normed.items():
            for t in inside:
                tw = normed[t]
                j = len(mw) - len(tw)
                if t != si and 0 < j and mw[j:] == tw and (si, j) in word_cell:
                    c = word_cell[(si, j)]
                    self.tag_restart[int(seg_of[c])] = c
        n_base = len(starts)
        #: loop pass segment -> the section's unit segment / all its passes
        self._loop_exit: dict[int, int] = {}
        self._loop_chain: dict[int, frozenset[int]] = {}
        base_succ = self._base_succ(spec, [int(slide_of[c]) for c in starts], starts)
        #: (slide, word) -> every further cell carrying it (loop passes)
        self.word_cells_more: list[tuple[tuple[int, int], int]] = []
        #: cell -> the cell it copies (a loop pass), itself otherwise
        copy_of = list(range(len(midi)))
        loop_rows: dict[int, tuple[float, int | None]] = {}
        if LOOPS:
            loops = self._find_loops(spec, slides, starts, slide_of, n_base)
            for unit_seg, first_cell, written, again in loops:
                # passes 1..written are already there (the written run, or the
                # slide itself): add passes written+1..LOOP_MAX copying the unit
                a, b = first_cell, ends[unit_seg]
                chain = list(range(unit_seg - written + 1, unit_seg + 1))
                for _k in range(written + 1, LOOP_MAX + 1):
                    off = len(midi)
                    seg = len(starts)
                    starts.append(off)
                    for c in range(a, b + 1):
                        midi.append(midi[c])
                        chord.append(chord[c])
                        slide_of.append(slide_of[c])
                        seg_of.append(seg)
                        copy_of.append(c)
                    ends.append(len(midi) - 1)
                    for w, c in [x for x in words if a <= x[1] <= b]:
                        words.append((w, off + (c - a)))
                    for sw, c in word_cell.items():
                        if a <= c <= b:
                            self.word_cells_more.append((sw, off + (c - a)))
                    chain.append(seg)
                # pass k (1-based) is chain[k-1]; its end: again or leave
                for k, g in enumerate(chain, start=1):
                    if k == len(chain):
                        # the last pass: the band may still be vamping;
                        # sung again, it stays on this line
                        loop_rows[g] = (1.0 - LOOP_LAST_STAY, g)
                    elif k < written:
                        loop_rows[g] = (LOOP_WRITTEN_LEAVE, chain[k])
                    elif k == written and written > 1:
                        # the written run sung as written: mostly on to the
                        # next line; the count's rhythm only once past it
                        loop_rows[g] = (max(LOOP_WRITTEN_END_LEAVE, loop_leave(k)), chain[k])
                    elif k == 1:
                        loop_rows[g] = (1.0 - again, chain[k])
                    else:
                        loop_rows[g] = (loop_leave(k), chain[k])
                for g in chain:
                    self._loop_exit[g] = unit_seg
                    self._loop_chain[g] = frozenset(chain)
                self.tag_restart.pop(unit_seg, None)
        self.P = len(midi)
        self.seg_of = np.asarray(seg_of)
        self.copy_of = np.asarray(copy_of)
        #: cells that are not a loop's further pass (written cells)
        self.written_cell = self.copy_of == np.arange(len(copy_of))
        self.slide_of = np.asarray(slide_of)
        self.jump_targets = np.asarray(starts)
        self.end_cells = np.asarray(ends)
        self.seg_slides = [int(slide_of[c]) for c in starts]
        self.intro_cell = 0
        # the order prior over the sung segments; the intro's own row is
        # replaced by the transition (it only ever leaves to the first line)
        n_seg = len(starts)
        succ = np.zeros((n_seg, n_seg))
        succ[:n_base, :n_base] = base_succ
        for g, (leave, nxt) in loop_rows.items():
            # where the section leads: its unit segment's own (base) row
            # a written member leaves the way its own row says (a roadmap or
            # past services may know); a further pass, the way the unit's does
            own = g if g < n_base else self._loop_exit[g]
            exit_row = np.zeros(n_seg)
            exit_row[:n_base] = base_succ[own]
            for x in self._loop_chain[g]:
                exit_row[x] = 0.0  # leaving the loop goes elsewhere
            if exit_row.sum() <= 0:
                exit_row[:n_base] = base_succ[self._loop_exit[g]]
                for x in self._loop_chain[g]:
                    exit_row[x] = 0.0
            if exit_row.sum() <= 0:
                exit_row[:n_base] = base_succ[self._loop_exit[g]]
            exit_row /= exit_row.sum()
            succ[g] = leave * exit_row
            if nxt is not None:
                succ[g, nxt] += 1.0 - leave
        # the intro leaves to the first line the band sings
        seg_index = {sl: i for i, sl in enumerate(self.seg_slides[:n_base]) if i > 0}
        first_slide = spec.first_slide
        if first_slide is None and len(self.seg_slides) > 1:
            first_slide = self.seg_slides[1]
        self.first_segment = seg_index.get(first_slide, 0) if first_slide is not None else 0
        succ[0, self.first_segment] = 1.0
        self.succ = succ
        self.midi_sheet = np.asarray(midi, np.int16)
        self.chord_sheet = np.asarray(chord, np.int16)
        #: Whether any cell has a chord to compare heard chords with.
        self.has_chords = bool((self.chord_sheet >= 0).any())
        # Which cells are sung: a segment from its first word to the end of
        # its last note (rests inside a line count as sung: a breath is not a
        # pause); the cells after, up to the next line's first word, are the
        # sheet's own gap. The intro is not sung.
        self.sung = np.zeros(self.P, bool)
        for g in range(1, n_base):
            a, b = int(starts[g]), int(ends[g])
            notes = np.where(self.midi_sheet[a : b + 1] >= 0)[0]
            last = a + (int(notes[-1]) if len(notes) else 0)
            for _sw, c in (x for x in word_cell.items() if self.seg_of[x[1]] == g):
                last = max(last, c)
            self.sung[a : last + 1] = True
        self.sung = self.sung | self.sung[self.copy_of]
        self.key_shift = 0
        self.rates = np.geomspace(*bpm, n_rates) / 60.0 / CELL
        self.words = words
        self.word_cell = word_cell
        self.slack = word_slack
        self.rho = rho
        #: Scored evidence: the intro cell's fixed score (see `SCORED_NULL`);
        #: None leaves it neutral like any unscored cell.
        self.null_score: float | None = SCORED_NULL
        #: slide -> its identical-text representative (set by the follower),
        #: so identical slides are one outcome in `step_entropy`.
        self.canon: dict[int, int] = {}
        n_sung = sum(1 for sw in slides if sw)
        self.coverage = Coverage(bound=len(spec.covered), sung=n_sung)

    @staticmethod
    def _base_succ(spec: ReferenceSpec, seg_slides: list[int], starts: list[int]) -> np.ndarray:
        """The order prior over the written segments (the intro's row empty)."""
        n_seg = len(starts)
        succ = np.zeros((n_seg, n_seg))
        succ[1:, 1:] = order_prior(seg_slides[1:], spec.sequences, spec.covered, spec.learned)
        return succ

    def _find_loops(self, spec, slides, starts, slide_of, n_base):
        """(unit segment, its first looped cell, passes written, chance of a
        second pass) for each section the band may loop (`LOOPS`)."""
        norm = [" ".join(w for w in (sc.norm(x) for x in sw) if w) for sw in slides]
        text = [
            norm[slide_of[starts[g]]] if g > 0 and slide_of[starts[g]] >= 0 else ""
            for g in range(n_base)
        ]
        out, used = [], set()
        g = 1
        while g < n_base:
            h = g
            while h + 1 < n_base and text[h + 1] and text[h + 1] == text[g]:
                h += 1
            if h > g and text[g]:
                # a written run of identical slides: one line sung h-g+1 times
                out.append((h, starts[h], h - g + 1, 0.0))
                used.update(range(g, h + 1))
            g = h + 1
        for seg, cell in sorted(self.tag_restart.items()):
            if seg not in used and seg < n_base:
                out.append((seg, cell, 1, LOOP_TAG_AGAIN))
                used.add(seg)
        first_seg = {}
        for g in range(1, n_base):
            first_seg.setdefault(int(slide_of[starts[g]]), g)
        for sl in spec.loops:
            seg = first_seg.get(int(sl))
            if seg is not None and seg not in used:
                out.append((seg, starts[seg], 1, LOOP_MARKED_AGAIN))
                used.add(seg)
        return out

    # ---------------------------------------------------------------- key

    def set_key(self, key_shift: int) -> None:
        self.key_shift = int(key_shift) % 12

    @property
    def chord(self) -> np.ndarray:
        return sheet.shift_chords(self.chord_sheet, self.key_shift)

    @property
    def midi(self) -> np.ndarray:
        return sheet.shift_midi(self.midi_sheet, self.key_shift)

    # ---------------------------------------------------------------- scoring

    def chords(self, probs: np.ndarray) -> np.ndarray:
        """Frames of BTC probabilities (T x 25) -> log-likelihood ratios (T x P)."""
        probs = np.atleast_2d(probs)
        tab = np.log(1e-3 + probs) - np.log(1e-3 + 1 / 25)
        tab = robust(tab, self.rho)
        ch = self.chord
        has = ch >= 0
        out = np.where(has[None, :], tab[:, np.where(has, ch, 0)], 0.0)
        if CHORDLESS_AT_MEAN and has.any() and not has.all():
            # a cell with no chord is neither evidence for its slide nor
            # against it: the mean of the cells that have one, as an unscored
            # span is (`scored`); 0 favoured slides with chords over those
            # without whenever the band played chords like theirs
            if CHORDLESS_QUANTILE is None:
                out[:, ~has] = out[:, has].mean(axis=1, keepdims=True)
            else:
                out[:, ~has] = np.quantile(out[:, has], CHORDLESS_QUANTILE, axis=1)[:, None]
        return out.astype(np.float32)

    def melody(self, pcs: np.ndarray, eps: float = 0.02) -> np.ndarray:
        """Frames of pitch-class salience (T x 12) -> log-likelihood ratios (T x P)."""
        pcs = np.atleast_2d(pcs)
        tab = np.log(eps + pcs) - np.log(eps + pcs.mean(axis=1, keepdims=True))
        tab = robust(tab, self.rho)
        m = self.midi
        has = m >= 0
        return np.where(has[None, :], tab[:, np.where(has, m % 12, 0)], 0.0).astype(np.float32)

    def scored(
        self, span_score: dict[tuple[int, int], float], tokens: float = 7.0
    ) -> np.ndarray | None:
        """One scorer step -> log-likelihood ratio over cells.

        `span_score[(slide, j)]` is the mean log-probability per token of the
        span of that slide's words ending at word j. Each score lands on the
        cell of the span's last word and holds until the slide's next word;
        a cell with no scored span gets the scored mean (neutral after
        centring — not being asked about is not evidence against a slide).
        Scaled by a typical span's token count and made robust.

        One-word spans (a line's first word) are left out: a single token's
        mean log-probability beats any real span on a window with no clear
        lyric, and its timing then lands on the window's start. When one of
        them is the best score, the window heard no line at all and the step
        is no evidence (measured: 14-20% of steps; dropping them took the
        4 goldens 0.186 -> 0.167)."""
        if not span_score:
            return None
        if max(span_score, key=span_score.__getitem__)[1] == 0:
            return None
        v = np.full(self.P, np.nan)
        for sw, c in [*self.word_cell.items(), *self.word_cells_more]:
            if sw[1] > 0 and sw in span_score:
                v[c] = span_score[sw]
        if np.all(np.isnan(v)):
            return None
        seg = self.seg_of
        last = np.nan
        for c in range(self.P):
            if c > 0 and seg[c] != seg[c - 1]:
                last = np.nan
            if not np.isnan(v[c]):
                last = v[c]
            v[c] = last
        v = np.where(np.isnan(v), np.nanmean(v), v)
        if self.null_score is not None:
            v[self.intro_cell] = self.null_score
        if SCORED_MODE == "entropy":
            floor = ENTROPY_EPS_MIN ** (1.0 - self.step_entropy(span_score))
            e = np.maximum(ENTROPY_BETA * (v - v.max()), np.log(floor))
            return e.astype(np.float32)
        e = tokens * (v - v.mean())
        if SCORED_MODE == "legacy_entropy":
            # the legacy evidence, with only its robustness floor set per step
            rho = ENTROPY_EPS_MIN ** (1.0 - self.step_entropy(span_score))
            return robust(e, rho).astype(np.float32)
        return robust(e, self.rho).astype(np.float32)

    def step_entropy(self, span_score: dict[tuple[int, int], float]) -> float:
        """Normalised entropy (0..1) of one scorer step's posterior over slides.

        Spans of two or more words, p ∝ exp(ENTROPY_BETA * score); a span
        carried by several slides shares its probability among them, and
        identical-text slides (`canon`) are one outcome. 0: one slide, beyond
        doubt; 1: no idea."""
        keys = [k for k in span_score if k[1] > 0]
        if not keys:
            return 1.0
        # the same span text carried by several slides appears once per slide,
        # with the same score: one outcome, its probability shared among them
        by_text: dict[float, list[int]] = {}
        for k in keys:
            by_text.setdefault(span_score[k], []).append(self.canon.get(k[0], k[0]))
        sc = np.array(list(by_text))
        p = np.exp(ENTROPY_BETA * (sc - sc.max()))
        p /= p.sum()
        post: dict[int, float] = {}
        for pk, slides in zip(p, by_text.values(), strict=True):
            owners = set(slides)
            for slide in owners:
                post[slide] = post.get(slide, 0.0) + float(pk) / len(owners)
        q = np.array([x for x in post.values() if x > 0])
        q = q / q.sum()
        n = len(post)
        if n < 2:
            return 0.0
        return float(-(q * np.log(q)).sum() / np.log(n))

    def reliability(self, span_score: dict[tuple[int, int], float]) -> float:
        """How much a scorer step should count, 0..1, by how clearly it heard
        a line: its best span's score between `RELIABILITY_FLOOR` (noise:
        nothing) and `RELIABILITY_FULL` (a clear line: everything). Scores are
        otherwise only compared with each other, so a window Whisper can't
        make out (Awesome God: median best -5.7) pushed as hard as a clear
        one, and on the live set the follower did better with no words at
        all (0.509 against 0.551). Replayed over the 8 live traces: the live
        set 0.551 -> 0.481 (production 0.524), held-out 0.330 -> 0.300."""
        best = max((v for (_si, j), v in span_score.items() if j > 0), default=None)
        if best is None:
            return 0.0
        span = RELIABILITY_FULL - RELIABILITY_FLOOR
        return float(np.clip((best - RELIABILITY_FLOOR) / span, 0.0, 1.0))

    def heard(self, span_score: dict[tuple[int, int], float]) -> bool:
        """Did this scorer step hear a line of this song? Its best span of two
        or more words reaches the null level (`SCORED_NULL`)."""
        level = SCORED_NULL if self.null_score is None else self.null_score
        return any(v >= level for (_si, j), v in span_score.items() if j > 0)

    def _word_vec(self, text: str) -> np.ndarray:
        """Per reference and bounded: a class-wide cache kept every rebuilt
        reference alive, and through a sermon (the last song still current)
        filled with its vocabulary, ~50 MB of dense vectors."""
        cache = self.__dict__.setdefault("_word_vecs", {})
        hit = cache.get(text)
        if hit is None:
            hit = self._word_vec_uncached(text)
            if len(cache) >= WORD_VEC_CACHE:
                cache.pop(next(iter(cache)))  # the oldest
            cache[text] = hit
        return hit

    def _word_vec_uncached(self, text: str) -> np.ndarray:
        sim = np.zeros(self.P, np.float32)
        before, after = self.slack
        for w, c in self.words:
            v = phonetic_match_score(text, w)
            if v <= 0:
                continue
            lo, hi = (
                max(0, c - after),
                min(self.P, c + before + 1),
            )  # heard early -> sung at later cells
            np.maximum(sim[lo:hi], v, out=sim[lo:hi])
        return sim

    def word(self, text: str) -> np.ndarray | None:
        """One heard word -> log-likelihood ratio over cells, or None for no letters."""
        t = "".join(ch for ch in text.lower() if ch.isalpha() or ch == "'")
        if not t:
            return None
        sim = self._word_vec(t)
        eps = 0.1
        vec = (np.log(eps + sim) - np.log(eps + sim.mean())).astype(np.float32)
        # Talk before a song is not evidence that the song has started: a word
        # matching nothing leaves the intro where it is (and so ahead of every
        # line it does not match), while a word matching a line still pulls
        # the belief there. Measured live: a welcome over the intro moved the
        # screen off the blank 32 s before the band sang.
        vec[self.intro_cell] = 0.0
        return vec
