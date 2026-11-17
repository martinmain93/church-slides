"""The lead sheet as a `joint.Reference`.

Cells are eighth notes of the unrolled sheet (follow.CELL). Expectations:
the sung note and the chord in force (reference.Reference), and each sheet
word at the cell of its first syllable. A heard word supports the cells
where a phonetically similar sheet word starts, with a little slack either
side because Whisper's word starts run early and singers place words loosely.
"""
from __future__ import annotations

import functools

import numpy as np

import follow as fl
import joint
import reference as rf
import score as sc
from auto_presenter.index.phonetic import phonetic_match_score

EPS_MEL = 0.02


class LeadSheetReference(joint.Reference):
    def __init__(self, s: sc.Score, slides, key_shift: int = 0, bpm=(40, 130), n_rates: int = 16,
                 word_slack=(2, 1), rho: float = 0.2):
        self.s = s
        m = fl.build(s, slides, key_shift=key_shift)
        self.model = m
        self.P = m.P
        self.rates = np.geomspace(*bpm, n_rates) / 60.0 / fl.CELL  # cells per second
        self.jump_targets = m.targets
        self.end_cells = m.end_cells
        self.midi, self.chord = m.midi, m.chord
        self.rho = rho
        # sheet words at their first syllable's cell
        self.words = [(w.text, min(self.P - 1, int(w.onset // fl.CELL))) for w in s.words]
        self.slack = word_slack

    def melody(self, pcs):
        tab = np.log(EPS_MEL + pcs) - np.log(EPS_MEL + pcs.mean(axis=1, keepdims=True))
        tab = joint.robust(tab, self.rho)
        has = self.midi >= 0
        return np.where(has[None, :], tab[:, np.where(has, self.midi % 12, 0)], 0.0).astype(np.float32)

    def chords(self, probs):
        tab = np.log(1e-3 + probs) - np.log(1e-3 + 1 / 25)
        tab = joint.robust(tab, self.rho)
        has = self.chord >= 0
        return np.where(has[None, :], tab[:, np.where(has, self.chord, 0)], 0.0).astype(np.float32)

    @functools.lru_cache(maxsize=4096)
    def _word_vec(self, text: str) -> np.ndarray:
        sim = np.zeros(self.P, np.float32)
        before, after = self.slack
        for w, c in self.words:
            v = phonetic_match_score(text, w)
            if v <= 0:
                continue
            lo, hi = max(0, c - after), min(self.P, c + before + 1)  # heard early -> sung at later cells
            np.maximum(sim[lo:hi], v, out=sim[lo:hi])
        return sim

    def word(self, text: str):
        t = "".join(ch for ch in text.lower() if ch.isalpha() or ch == "'")
        if not t:
            return None
        sim = self._word_vec(t)
        eps = 0.1
        return (np.log(eps + sim) - np.log(eps + sim.mean())).astype(np.float32)
