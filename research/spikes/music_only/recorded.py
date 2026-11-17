"""A reference made from a recorded service: the no-lead-sheet future.

A past performance plus its operator's slide changes becomes the reference.
Each cue interval is a segment of 0.1 s cells; what a cell expects is that
performance's own vocal-pitch salience, chord probabilities and transcript
words at that moment. The operator's slide sequence is the order prior.
Tempo is a ratio against the reference (0.75-1.33x).

It implements the same interface as `segref.SegmentReference`, so
`joint.follow` runs on it unchanged.

Emissions compare like with like (live features against stored features)
rather than against notation:
* melody — cosine between the live pitch-class salience and the stored one,
  as a log ratio against the live frame's average similarity to all cells;
* chords — the probability that the two chord distributions agree (their
  dot product), against chance;
* words — phonetic match of a heard word to the words the reference
  performance sang within ±1 s of the cell.
"""
from __future__ import annotations

import functools

import numpy as np

import joint
from auto_presenter.index.phonetic import phonetic_match_score

CELL_S = 0.1


class RecordedReference(joint.Reference):
    def __init__(self, cues: list[tuple[float, float, int]], pcs: np.ndarray, chords: np.ndarray,
                 words: list[tuple[float, str]], fps: float = 10.0, ratios=(0.75, 1.33), n_rates: int = 9,
                 rho: float = 0.2, p_repeat: float = 0.0):
        """`cues`: (t0, t1, slide) intervals of the reference performance;
        `pcs`/`chords`: its features at `fps`; `words`: (time, text)."""
        cells_pcs, cells_ch, slide_of, starts, ends, seg_slides = [], [], [], [], [], []
        cell_words: list[tuple[str, int]] = []
        for t0, t1, sl in cues:
            a, b = int(t0 * fps), max(int(t0 * fps) + 1, int(t1 * fps))
            b = min(b, len(pcs))
            if b <= a:
                continue
            off = len(cells_pcs)
            starts.append(off)
            seg_slides.append(sl)
            cells_pcs.extend(pcs[a:b])
            cells_ch.extend(chords[a:b])
            slide_of.extend([sl] * (b - a))
            ends.append(len(cells_pcs) - 1)
            for t, w in words:
                if t0 <= t < t1:
                    cell_words.append((w, off + int((t - t0) * fps)))
        self.P = len(cells_pcs)
        self.ref_pcs = np.array(cells_pcs, np.float32)
        self.ref_ch = np.array(cells_ch, np.float32)
        self.slide_of = slide_of
        self.jump_targets = np.array(starts)
        self.end_cells = np.array(ends)
        self.seg_of = np.zeros(self.P, int)
        for i, (a, b) in enumerate(zip(starts, ends)):
            self.seg_of[a : b + 1] = i
        self.seg_slides = seg_slides
        # order prior: what the operator did next, plus "the same slide's music again"
        S = len(starts)
        succ = np.zeros((S, S))
        for i in range(S):
            succ[i, (i + 1) % S] = 1.0
        self.succ = (1 - p_repeat) * succ + p_repeat * np.eye(S)
        self.rates = np.geomspace(*ratios, n_rates) * fps / CELL_S / fps  # cells per second
        self.words = cell_words
        self.rho = rho

    def melody(self, pcs):
        a = pcs / (np.linalg.norm(pcs, axis=1, keepdims=True) + 1e-6)
        b = self.ref_pcs / (np.linalg.norm(self.ref_pcs, axis=1, keepdims=True) + 1e-6)
        sim = a @ b.T  # T x P
        voiced = (pcs.max(axis=1) >= 0.3)[:, None] & (self.ref_pcs.max(axis=1) >= 0.3)[None, :]
        x = np.log(0.05 + np.clip(sim, 0, 1)) - np.log(0.05 + np.clip(sim, 0, 1).mean(axis=1, keepdims=True))
        return joint.robust(np.where(voiced, x, 0.0), self.rho).astype(np.float32)

    def chords(self, probs):
        agree = probs @ self.ref_ch.T  # T x P: P(same chord)
        return joint.robust(np.log(1e-3 + agree) - np.log(1e-3 + 1 / 25), self.rho).astype(np.float32)

    @functools.lru_cache(maxsize=4096)
    def _word_vec(self, text: str) -> np.ndarray:
        sim = np.zeros(self.P, np.float32)
        for w, c in self.words:
            v = phonetic_match_score(text, w)
            if v > 0:
                lo, hi = max(0, c - 10), min(self.P, c + 11)
                np.maximum(sim[lo:hi], v, out=sim[lo:hi])
        return sim

    def word(self, text):
        t = "".join(ch for ch in text.lower() if ch.isalpha() or ch == "'")
        if not t:
            return None
        sim = self._word_vec(t)
        return (np.log(0.1 + sim) - np.log(0.1 + sim.mean())).astype(np.float32)
