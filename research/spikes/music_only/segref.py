"""A reference built from the library's slides: one segment per slide.

The lead-sheet reference (`lead.py`) followed the sheet's own roadmap with
the sheet's own words. Two things wrong with that, both measured:

* a hymn sheet prints one verse; the band sings five, so verses 2-5 had
  verse 1's words in the reference (I Surrender All: words-only 0.566
  wrong-slide against production's 0.209);
* chorus 1 and chorus 3 were separate stretches of the roadmap carrying the
  same music, so position and slide had to be untangled at read-out.

Here each library slide becomes a segment: *its own* words, with the music
and timing of its line taken from wherever the sheet has that line. Segments
run in slide order, so at a segment's end the natural next step is the next
slide, and a jump may land on any slide's start. Every cell belongs to
exactly one slide.

This is also the shape a recorded reference will have (`recorded.py`): a
past service is a sequence of slides, each with its timed words, pitch and
chords, and the operator's slide changes are the segment boundaries. The
follower cannot tell the two apart.
"""
from __future__ import annotations

import functools
from difflib import SequenceMatcher

import numpy as np

import follow as fl
import joint
import lead
import score as sc
from auto_presenter.index.phonetic import phonetic_match_score


def order_prior(seg_slides: list[int], sequences: list[list[int]], n_slides: int,
                rep: dict[int, int] | None = None, sung: list[int] | None = None,
                p_repeat: float = 0.0) -> np.ndarray:
    """S x S: from each segment, where the band goes next. Half library order
    (each sung slide -> the next sung slide, and the last back to the first),
    half the observed sequences' transitions; a segment with no observed
    successor keeps library order only.

    `rep` maps every slide to the segment that represents it: slides with
    identical words share one segment, and that segment's successors are the
    union of what follows each copy — nothing can tell the copies apart
    except what is sung next."""
    rep = rep or {sl: sl for sl in seg_slides}
    S = len(seg_slides)
    idx = {sl: i for i, sl in enumerate(seg_slides)}
    seg = lambda sl: idx.get(rep.get(sl, sl))
    lib = np.zeros((S, S))
    order = sung or seg_slides
    for a, b in zip(order, order[1:] + order[:1]):
        if seg(a) is not None and seg(b) is not None and seg(a) != seg(b):
            lib[seg(a), seg(b)] += 1
    for i in range(S):
        if lib[i].sum() == 0:
            lib[i, (i + 1) % S] = 1
    lib /= lib.sum(axis=1, keepdims=True)
    obs = np.zeros((S, S))
    for seq in sequences:
        for a, b in zip(seq, seq[1:]):
            if seg(a) is not None and seg(b) is not None and seg(a) != seg(b):
                obs[seg(a), seg(b)] += 1
    out = lib.copy()
    for i in range(S):
        if obs[i].sum() > 0:
            out[i] = 0.5 * lib[i] + 0.5 * obs[i] / obs[i].sum()
    # bands repeat and vamp lines (Reckless Love holds one slide 28 s against
    # 8 s of written music): the same slide's music again is a transition of
    # its own, never learnable from one sequence because the count varies
    return (1 - p_repeat) * out + p_repeat * np.eye(S)


class SegmentReference(joint.Reference):
    def __init__(self, s: sc.Score, slides: list[list[str]], spans: dict, key_shift: int = 0,
                 bpm=(40, 130), n_rates: int = 16, word_slack=(2, 1), rho: float = 0.2,
                 sequences: list[list[int]] | None = None, merge_identical: bool = False,
                 extend_to_next: bool = False, p_repeat: float = 0.0):
        """`sequences`: slide orders known to be sung — the sheet's roadmap,
        or the operator's slide changes from a recorded service. They become
        the order prior at each segment's end, together with library order."""
        base = fl.build(s, slides, key_shift=key_shift)
        self.key_shift = key_shift
        # a slide's music runs until the next slide's first word in the
        # roadmap, so instrumental bars and held endings belong to the slide
        # on screen during them — exactly as a recorded service has it, where
        # the time until the operator's next change is the slide's own
        all_starts = sorted({sp.start for sps in spans.values() for sp in sps})
        midi, chord, seg_of, slide_of, words = [], [], [], [], []
        word_cell: dict[tuple[int, int], int] = {}
        starts, ends = [], []
        # slides with identical words are one segment (the lowest-numbered copy)
        rep: dict[int, int] = {}
        first: dict[tuple, int] = {}
        for si, sw in enumerate(slides):
            key = tuple(w for w in (sc.norm(x) for x in sw) if w)
            if key:
                rep[si] = first.setdefault(key, si) if merge_identical else si
        sung = [si for si, sw in enumerate(slides) if sw and (spans.get(si) or [])]
        for si, sw in enumerate(slides):
            sps = spans.get(si) or []
            if not sw or not sps or rep.get(si) != si:
                continue
            sp = sps[0]
            a = max(0, int(sp.start // fl.CELL))
            nxt = [x for x in all_starts if x > sp.start + 1e-9]
            stop = (nxt[0] if nxt else s.length) if extend_to_next else sc.span_end(s, sp)
            b = min(base.P, max(a + 1, int(np.ceil(stop / fl.CELL))))
            off = len(midi)
            starts.append(off)
            midi.extend(base.midi[a:b])
            chord.extend(base.chord[a:b])
            slide_of.extend([si] * (b - a))
            ends.append(len(midi) - 1)
            # this slide's own words, at the onsets of the sheet's words for the
            # line (matched where they agree; spread over the line where not)
            sheet = s.words[sp.w0 : sp.w1 + 1]
            mine = [w for w in (sc.norm(x) for x in sw) if w]
            pos: list[float | None] = [None] * len(mine)
            for blk in SequenceMatcher(None, mine, [w.text for w in sheet], autojunk=False).get_matching_blocks():
                for q in range(blk.size):
                    pos[blk.a + q] = sheet[blk.b + q].onset
            onsets = [w.onset for w in sheet]
            for j in range(len(mine)):
                if pos[j] is None:
                    # by proportion through the line: an unprinted verse sings
                    # its own words to the printed verse's rhythm
                    k = min(len(onsets) - 1, int(round(j * (len(onsets) - 1) / max(1, len(mine) - 1))))
                    pos[j] = onsets[k] if onsets else sp.start
            for j, (w, p) in enumerate(zip(mine, pos)):
                c = off + min(b - a - 1, max(0, int(p // fl.CELL) - a))
                words.append((w, c))
                word_cell[(si, j)] = c
        self.P = len(midi)
        self.seg_slides = [slide_of[c] for c in starts]  # slide of each segment
        self.seg_of = np.zeros(self.P, int)
        for i, (a, b) in enumerate(zip(starts, ends)):
            self.seg_of[a : b + 1] = i
        self.succ = order_prior(self.seg_slides, sequences or [], len(slides), rep, sung, p_repeat=p_repeat)
        self.rep = rep
        self.midi = np.array(midi)
        self.chord = np.array(chord)
        self.slide_of = slide_of
        self.jump_targets = np.array(starts)
        self.end_cells = np.array(ends)
        self.rates = np.geomspace(*bpm, n_rates) / 60.0 / fl.CELL
        self.words = words
        self.word_cell = word_cell  # (slide, word index) -> cell of that word
        self.slack = word_slack
        self.rho = rho

    melody = lead.LeadSheetReference.melody
    chords = lead.LeadSheetReference.chords

    @functools.lru_cache(maxsize=4096)
    def _word_vec(self, text: str) -> np.ndarray:
        sim = np.zeros(self.P, np.float32)
        before, after = self.slack
        for w, c in self.words:
            v = phonetic_match_score(text, w)
            if v <= 0:
                continue
            lo, hi = max(0, c - after), min(self.P, c + before + 1)
            np.maximum(sim[lo:hi], v, out=sim[lo:hi])
        return sim

    word = lead.LeadSheetReference.word
