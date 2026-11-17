"""Matcher output types.

`MatcherDecision` is what the matcher emits when it commits to a slide.
`BeliefState` is the per-slide score snapshot the debug panel renders.

Both are intentionally pure data — no behavior — so Phase 6's particle
filter can produce richer `BeliefState`s (top-3 hypotheses with
marginals) without changing the matcher protocol.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MatcherDecision:
    """The matcher's commitment to a particular slide at a moment in time.

    `confidence` is in `[0, 1]`: the tracker's marginal on the slide.
    """

    item_idx: int
    slide_idx: int
    t_seconds: float
    confidence: float


@dataclass(frozen=True)
class SlideBelief:
    """One slide's score in the current belief snapshot."""

    item_idx: int
    slide_idx: int
    score: float


@dataclass(frozen=True)
class BeliefState:
    """Snapshot of the matcher's per-slide scores at a point in time.

    `slides` lists one entry per slide of the *current* item only — this
    is what the debug panel renders as a bar chart. `committed_slide_idx`
    is the slide the matcher most recently committed to (may be None
    before the first decision in this item).
    """

    item_idx: int
    committed_slide_idx: int | None
    slides: tuple[SlideBelief, ...] = field(default_factory=tuple)
    t_seconds: float = 0.0
