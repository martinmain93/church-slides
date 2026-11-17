"""Scoring a predicted sheet position against the ground truth.

Positions are compared as *music*, not as slide ids: a predicted position
counts as the same as any other played position with identical music (a
repeat of the same bar, at the same beat). So chorus 1 and chorus 3 are one
answer, as docs/research/MUSIC_ONLY_MATCHING.md agreed.

* `pos_err`: beats between the prediction and the truth, taking the nearest
  musically identical copy of the predicted position.
* `slide_ok`: the true slide is one of the slides whose music covers the
  predicted position — what a music-only system could put on screen.
"""

from __future__ import annotations

import numpy as np

import follow as fl
import score as sc


class Scorer:
    def __init__(self, m: fl.Model, slides: list[list[str]], spans: dict[int, list[sc.Span]]):
        s = m.s
        self.m = m
        canon = sc.canonical_bars(s)
        # canonical id per cell: (canonical bar, cell offset within bar)
        bar_cells = np.round(np.array([pb.start for pb in s.played]) / fl.CELL).astype(int)
        bar_of = np.searchsorted(bar_cells, np.arange(m.P), side="right") - 1
        keys = [(canon[s.played[b].bar], c - bar_cells[b]) for c, b in enumerate(bar_of)]
        groups: dict[tuple, list[int]] = {}
        for c, k in enumerate(keys):
            groups.setdefault(k, []).append(c)
        self.equiv = [np.array(groups[k]) for k in keys]
        # which slides cover each cell
        self.cover: list[set[int]] = [set() for _ in range(m.P)]
        for si, sps in spans.items():
            for sp in sps:
                a = int(sp.start // fl.CELL)
                b = int(np.ceil(sc.span_end(s, sp) / fl.CELL))
                for c in range(max(0, a), min(m.P, b)):
                    self.cover[c].add(si)
        self.cover_eq = [set().union(*(self.cover[e] for e in self.equiv[c])) for c in range(m.P)]
        self.lyric_free = np.array([not self.cover_eq[c] for c in range(m.P)])

    def errors(self, pred_cell: np.ndarray, gt_pos: np.ndarray) -> np.ndarray:
        """Beats from each prediction (nearest identical copy) to the truth."""
        out = np.full(len(pred_cell), np.nan)
        g = gt_pos / fl.CELL
        for i, (c, gc) in enumerate(zip(pred_cell, g)):
            if np.isfinite(gc) and c >= 0:
                out[i] = np.min(np.abs(self.equiv[c] - gc)) * fl.CELL
        return out

    def report(self, pred_cell: np.ndarray, tl: dict, label: str) -> dict:
        slide, pos = tl["slide"], tl["pos"]
        sung = (slide > 0) & np.isfinite(pos)
        err = self.errors(pred_cell, pos)
        ok_slide = np.array([slide[i] in self.cover_eq[c] for i, c in enumerate(pred_cell)])
        inst = slide == 0
        r = {
            "slide": ok_slide[slide > 0].mean(),
            "pos1": np.mean(err[sung] <= 1.0),
            "pos2": np.mean(err[sung] <= 2.0),
            "bar": np.mean(err[sung] <= 4.0),
            "med_err": float(np.median(err[sung & (err <= 4.0)])) if np.any(sung & (err <= 4.0)) else np.nan,
            "inst": self.lyric_free[pred_cell[inst]].mean() if inst.any() else np.nan,
        }
        print(f"  {label:34s} slide {r['slide']:5.1%}  pos<=1 beat {r['pos1']:5.1%}  <=2 {r['pos2']:5.1%}  <=1 bar {r['bar']:5.1%}"
              f"  (median err when <=1 bar {r['med_err']:.2f} beats)  instrumental->no-lyric {r['inst']:5.1%}")
        return r

    def chance(self, tl: dict, n: int = 20, seed: int = 0) -> dict:
        rng = np.random.default_rng(seed)
        rs = []
        for _ in range(n):
            pred = rng.integers(0, self.m.P, len(tl["t"]))
            slide, pos = tl["slide"], tl["pos"]
            sung = (slide > 0) & np.isfinite(pos)
            err = self.errors(pred, pos)
            rs.append((np.mean([slide[i] in self.cover_eq[c] for i, c in enumerate(pred) if slide[i] > 0]),
                       np.mean(err[sung] <= 1.0), np.mean(err[sung] <= 4.0)))
        a = np.array(rs).mean(axis=0)
        print(f"  {'chance (uniform random position)':34s} slide {a[0]:5.1%}  pos<=1 beat {a[1]:5.1%}  <=1 bar {a[2]:5.1%}")
        return {"slide": a[0], "pos1": a[1], "bar": a[2]}
