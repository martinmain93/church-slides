"""Is the band following the expected arrangement right now?

Anticipation projects the follower's belief forward by the expected slide
order. That works while the band sings what the arrangement says. When the
band deviates (sings a chorus more times than written, goes back to a
chorus, holds a line far longer than the music does), the projection guesses
from the order prior at every fork, and the guess is wrong exactly where the
band is improvising.

So a state flag, `spontaneous`, judged only on what the follower has
*heard*: the belief about where the band was `heard_s` ago, with that
moment's words already in (`joint.follow.marg_heard`), never on the
projected present. Judging projected transitions would let the follower's
own guesses clear the flag.

Events, at the level of slide *groups* (slides with identical words are one
group, since nothing can tell those copies apart except order):

* the heard group changes a -> b: expected if the order prior sends some
  slide of a to some slide of b (`succ >= expect_p`). Unexpected sets the
  flag; expected clears it when a ends a section.
* a pass: the heard position wraps from the end of the group's line back
  to its start. One more pass than the arrangement writes consecutively is a
  repeat the band added (set). A written pass clears nothing: going round
  the same line is the fork in doubt.
* overstay: the heard group lasts longer than `overstay` x its line's
  expected duration at the current tempo, plus `overstay_s`. A held ending,
  an unwritten instrumental, or a vamp the position cannot see (set).

While spontaneous, the tracker is the follower's gate (`joint.follow(gate=)`):
in the stretch the follower has not heard yet, and in the lookahead, mass that
reaches the end of a *section* (a cluster, `clusters.py`) waits there instead
of following the order prior. Inside a section it still moves on — line 2 of
a chorus follows line 1 whatever the band is doing. Only a fork taken the
written way (an expected move out of a section's end) clears the flag; the
next line of the same section was never in doubt, so it says nothing.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Event:
    wall: float
    kind: str  # "expected", "unexpected", "added pass", "overstay"
    detail: str
    spontaneous: bool


@dataclass
class SpontaneityTracker:
    seg_of: np.ndarray  # cell -> segment
    slide_of: np.ndarray  # cell -> slide
    starts: np.ndarray  # segment -> first cell
    ends: np.ndarray  # segment -> last cell
    succ: np.ndarray  # S x S order prior
    group_of_slide: dict[int, int]  # slide -> group id (lowest identical slide)
    run_len: dict[int, int]  # group -> most consecutive copies the arrangement writes
    cluster_end: dict[int, bool] | None = None  # group -> it ends a section (clusters.py); None: every group does
    expect_p: float = 0.2
    overstay: float = 1.5
    overstay_s: float = 2.0
    debounce: int = 2  # read-outs a new heard group must hold before it counts
    min_share: float = 0.0  # an unexpected move counts only when the heard belief gives the new group this share
    clear_any: bool = False  # any expected move clears (True), or only one out of a section's end
    spontaneous: bool = False
    events: list[Event] = field(default_factory=list)

    def __post_init__(self):
        self.seg_group = np.array([self.group_of_slide[int(self.slide_of[c])] for c in self.starts])
        self.cell_group = self.seg_group[self.seg_of]
        self.seg_len = (self.ends - self.starts + 1).astype(float)
        G = self.seg_group
        self.expected: set[tuple[int, int]] = set()
        for a in range(len(G)):
            for b in range(len(G)):
                if G[a] != G[b] and self.succ[a, b] >= self.expect_p:
                    self.expected.add((int(G[a]), int(G[b])))
        self.group = None
        self.candidate, self.cand_n = None, 0
        self.entered = 0.0
        self.passes = 0
        self.last_frac = None

    def _ends(self, g: int) -> bool:
        return True if self.cluster_end is None else self.cluster_end.get(g, True)

    def _mark(self, wall, kind, detail, value):
        if value != self.spontaneous or kind != "expected":
            self.events.append(Event(wall, kind, detail, value))
        self.spontaneous = value

    def update(self, wall: float, heard: np.ndarray, rate_cells_s: float) -> bool:
        """One read-out: the heard marginal over cells, the tempo. Returns the flag."""
        gm = np.bincount(self.cell_group, weights=heard, minlength=self.cell_group.max() + 1)
        g = int(np.argmax(gm))
        share = gm[g] / max(1e-12, gm.sum())
        if g != self.group:
            self.candidate, self.cand_n = (g, self.cand_n + 1) if g == self.candidate else (g, 1)
            if self.cand_n >= self.debounce:
                if self.group is not None:
                    ok = (self.group, g) in self.expected
                    if not ok:
                        # a follower unsure where the band is looks just like a band off the
                        # arrangement; only a confident unexpected move is the band's doing
                        if share >= self.min_share:
                            self._mark(wall, "unexpected", f"group {self.group} -> {g} ({share:.2f})", True)
                    elif self.clear_any or self._ends(self.group):
                        # only a fork taken the written way says the band is back on the arrangement;
                        # the next line of the same section was never in doubt
                        self._mark(wall, "expected", f"group {self.group} -> {g}", False)
                self.group, self.entered, self.passes, self.last_frac = g, wall, 1, None
            return self.spontaneous
        self.candidate, self.cand_n = None, 0
        # where in the group's line: the best cell among the group's cells
        cells = np.where(self.cell_group == g)[0]
        c = int(cells[np.argmax(heard[cells])])
        s = self.seg_of[c]
        frac = (c - self.starts[s]) / self.seg_len[s]
        if self.last_frac is not None and self.last_frac >= 0.7 and frac <= 0.3:
            self.passes += 1
            self.entered = wall
            if self.passes > self.run_len.get(g, 1):
                self._mark(wall, "added pass", f"group {g} pass {self.passes} of {self.run_len.get(g, 1)} written", True)
            # a written pass clears nothing: going round the same line again is
            # the very fork in doubt; only moving on to another section is
        self.last_frac = frac
        expect_s = self.seg_len[s] / max(1e-6, rate_cells_s)
        if not self.spontaneous and wall - self.entered > self.overstay * expect_s + self.overstay_s:
            self._mark(wall, "overstay", f"group {g} held {wall - self.entered:.0f} s vs {expect_s:.0f} s", True)
        return self.spontaneous


def groups(n_slides: int, identical: dict[int, set[int]]) -> dict[int, int]:
    return {sl: min({sl} | set(identical.get(sl, ()))) for sl in range(n_slides)}


def run_lengths(sequence: list[int], group_of: dict[int, int]) -> dict[int, int]:
    """Most consecutive copies of each group the arrangement writes."""
    out: dict[int, int] = {}
    prev, n = None, 0
    for sl in sequence:
        g = group_of.get(sl, sl)
        n = n + 1 if g == prev else 1
        prev = g
        out[g] = max(out.get(g, 0), n)
    return out
