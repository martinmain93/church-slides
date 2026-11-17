"""Tracker state types.

`Particle` is a single hypothesis: which slide it thinks the band is
on, how far through it (0..1), and a latent tempo (`rate`). The
tracker's `BeliefState` is a snapshot of its whole belief at a moment in
time; the forward filter (`tracker.forward`) reports its dense posterior
in this shape, a weighted point per cell. The names date from the Phase 6
particle filter (research/dead_ends/particle_filter).

These are mutable dataclasses for the simple reason that the predict
step rewrites every particle's position/rate in place; a frozen
dataclass would force a per-update reallocation of ~500 records which
the per-token budget can't afford.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass


@dataclass
class Particle:
    """One hypothesis about where in the song the band is right now."""

    item_idx: int
    slide_idx: int
    position: float
    rate: float
    weight: float = 1.0


@dataclass
class BeliefState:
    """Snapshot of the particle cloud + summary statistics.

    `t_seconds` is the wall-clock-relative timestamp of the most recent
    update. `committed_slide_idx` is set by the director when a
    decision is broadcast; the tracker itself just exposes the mode.
    """

    particles: list[Particle]
    t_seconds: float = 0.0
    committed_slide_idx: int | None = None
    #: Summed marginal of every slide that would display the same words as the
    #: top one — i.e. how sure we are of what to *show*, rather than of which
    #: index to call it.
    #:
    #: These differ whenever a song writes a repeated section as several
    #: identical slides, and then the per-slide marginal is bounded near 1/N
    #: however certain the tracker actually is. Measured on King of My Heart,
    #: whose chorus is four identical slides: top marginal 0.34 against pooled
    #: 0.79, with a 0.40 commit threshold and a 0.50 found threshold. The
    #: tracker was permanently lost and the screen frozen for two minutes while
    #: the cloud was confidently in the right place.
    #:
    #: Falls back to the top marginal when the filter has no equivalence
    #: information, so a `BeliefState` built by hand behaves as before.
    display_marginal: float | None = None
    #: Share of all mass on the top song, over every slide of it. `None` when
    #: the tracker does not report one; see `ForwardBelief.song_share`.
    song_share: float | None = None

    @property
    def item_idx(self) -> int:
        """Mode item index across the cloud. Defaults to first particle."""
        if not self.particles:
            return 0
        return self._mode_key(lambda p: p.item_idx)

    @property
    def commit_confidence(self) -> float:
        """Confidence in *what to display* — pooled over identical slides."""
        if self.display_marginal is not None:
            return self.display_marginal
        return self.top_hypothesis[2]

    @property
    def top_hypothesis(self) -> tuple[int, int, float]:
        """Best `(item_idx, slide_idx, marginal)` across the cloud.

        Marginal = summed weight of every particle on that slide,
        normalized to total weight. Phase 6's director uses this as
        the candidate to commit when its marginal exceeds threshold.
        """
        return _top_n(self.particles, 1)[0]

    @property
    def top_3(self) -> list[tuple[int, int, float]]:
        """Top three `(item_idx, slide_idx, marginal)` hypotheses by weight."""
        return _top_n(self.particles, 3)

    @property
    def ess(self) -> float:
        """Effective sample size — see `resample.effective_sample_size`."""
        if not self.particles:
            return 0.0
        total = sum(p.weight for p in self.particles)
        if total <= 0:
            return 0.0
        return 1.0 / sum((p.weight / total) ** 2 for p in self.particles)

    @property
    def mode_rate(self) -> float:
        """Weighted-mean words-per-second across the top hypothesis particles."""
        if not self.particles:
            return 0.0
        item, slide, _ = self.top_hypothesis
        weighted: list[tuple[float, float]] = [
            (p.rate, p.weight)
            for p in self.particles
            if p.item_idx == item and p.slide_idx == slide
        ]
        if not weighted:
            return 0.0
        total_weight = sum(w for _, w in weighted)
        if total_weight <= 0:
            return 0.0
        return sum(r * w for r, w in weighted) / total_weight

    def top_position_spread(self) -> list[tuple[float, float]]:
        """`(position, weight)` for every state on the top hypothesis's slide.

        The posterior's opinion about *when this slide ends*, before it is
        averaged away. `mode_position` is the mean of exactly these numbers,
        and a mean cannot distinguish "the band is halfway through, confidently"
        from "they are either nearly done or barely started" — which are
        opposite instructions for a scheduler deciding whether to commit the
        next slide.

        Empty when there is no belief, so callers can fall back to the point
        estimate rather than special-casing.
        """
        if not self.particles:
            return []
        item, slide, _ = self.top_hypothesis
        return [
            (p.position, p.weight)
            for p in self.particles
            if p.item_idx == item and p.slide_idx == slide and p.weight > 0.0
        ]

    @property
    def mode_position(self) -> float:
        """Weighted-mean position within the top-hypothesis slide."""
        if not self.particles:
            return 0.0
        item, slide, _ = self.top_hypothesis
        weighted: list[tuple[float, float]] = [
            (p.position, p.weight)
            for p in self.particles
            if p.item_idx == item and p.slide_idx == slide
        ]
        if not weighted:
            return 0.0
        total_weight = sum(w for _, w in weighted)
        if total_weight <= 0:
            return 0.0
        return sum(r * w for r, w in weighted) / total_weight

    def _mode_key(self, key) -> int:
        c: Counter = Counter()
        for p in self.particles:
            c[key(p)] += p.weight
        return c.most_common(1)[0][0]


def _top_n(particles: list[Particle], n: int) -> list[tuple[int, int, float]]:
    if not particles:
        return [(0, 0, 0.0)] * n
    total = sum(p.weight for p in particles) or 1.0
    buckets: dict[tuple[int, int], float] = {}
    for p in particles:
        key = (p.item_idx, p.slide_idx)
        buckets[key] = buckets.get(key, 0.0) + p.weight
    ranked = sorted(buckets.items(), key=lambda kv: kv[1], reverse=True)
    return [
        (item, slide, min(1.0, weight / total))
        for (item, slide), weight in ranked[:n]
    ] + [(0, 0, 0.0)] * max(0, n - len(ranked))
