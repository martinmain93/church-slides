"""A/B harness — run two pipelines on the same recordings and compare.

A "pipeline" here is just a function `Recording -> list[Decision]`. Phase 3
ships the comparator infrastructure; the real pipelines (naive matcher,
particle filter, etc.) plug in at Phases 5+.

Bootstrap CIs (1000 resamples by default) make per-recording metric
improvements falsifiable rather than vibes-based.
"""

from __future__ import annotations

import random
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from auto_presenter.eval.metrics import (
    anticipation_error_ms,
    override_rate,
    recovery_time_ms,
    wrong_slide_rate,
)
from auto_presenter.eval.types import Decision, MetricResult, Recording

Pipeline = Callable[[Recording], list[Decision]]


@dataclass
class PipelineRun:
    """A pipeline's metrics summary for a single recording."""

    recording_id: str
    decisions: list[Decision]
    anticipation_error_ms: list[float]
    wrong_slide_rate: float
    recovery_time_ms: list[float | None]
    override_rate: float


def evaluate(
    recordings: Sequence[Recording],
    pipeline: Pipeline,
    *,
    n_songs_in_recording: Callable[[Recording], int] = lambda r: max(
        1,
        len({(g.item_idx,) for g in r.ground_truth}),
    ),
    forgive_unpredictable: bool = True,
) -> list[PipelineRun]:
    """Run `pipeline` against every recording, returning per-recording results.

    `forgive_unpredictable` is threaded straight through to
    `wrong_slide_rate` — see its docstring for why the off switch is worth
    keeping. On (the default) the band's unpredictable jumps get a short
    recovery window and slides carrying identical words are treated as the
    same display; off reproduces the unforgiving numbers every earlier
    baseline in this repo was measured with. The two travel together here
    deliberately: this is the harness's "score it the way an operator would
    see it" switch, and callers wanting them apart can use
    `wrong_slide_rate` directly.
    """
    runs: list[PipelineRun] = []
    for rec in recordings:
        decisions = pipeline(rec)
        runs.append(
            PipelineRun(
                recording_id=rec.id,
                decisions=decisions,
                anticipation_error_ms=anticipation_error_ms(decisions, rec.ground_truth),
                wrong_slide_rate=wrong_slide_rate(
                    decisions,
                    rec.ground_truth,
                    rec.deviation_markers or None,
                    forgive_unpredictable=forgive_unpredictable,
                    # The recording's own snapshot, so slides carrying the
                    # same words are not scored as different displays.
                    setlist_content=rec.setlist_content,
                    forgive_identical_slides=forgive_unpredictable,
                    # Strict mode reproduces the pre-2026-09-04 number
                    # exactly, blank cues included, so one flag restores
                    # comparability with every earlier baseline.
                    score_blank_cues=not forgive_unpredictable,
                ),
                recovery_time_ms=recovery_time_ms(
                    decisions, rec.ground_truth, rec.deviation_markers
                ),
                override_rate=override_rate(decisions, n_songs_in_recording(rec)),
            )
        )
    return runs


@dataclass
class ComparisonReport:
    """A/B summary for one metric."""

    metric: MetricResult
    a_value: float
    b_value: float
    delta: float  # b - a
    bootstrap_ci_delta: tuple[float, float] | None


def compare(
    a_runs: Sequence[PipelineRun],
    b_runs: Sequence[PipelineRun],
    *,
    bootstrap_resamples: int = 1000,
    rng_seed: int = 42,
) -> dict[str, ComparisonReport]:
    """Aggregate two sets of runs into a per-metric A/B report."""
    a_by_id = {r.recording_id: r for r in a_runs}
    b_by_id = {r.recording_id: r for r in b_runs}
    common_ids = sorted(set(a_by_id) & set(b_by_id))

    out: dict[str, ComparisonReport] = {}

    # Per-event metrics (anticipation_error, recovery_time): aggregate by
    # median across all matched events from all recordings.
    out["anticipation_error_ms_median"] = _bootstrap_event_metric(
        common_ids,
        a_by_id,
        b_by_id,
        getter=lambda r: r.anticipation_error_ms,
        agg=lambda xs: statistics.median(xs) if xs else 0.0,
        name="anticipation_error_ms_median",
        unit="ms",
        bootstrap_resamples=bootstrap_resamples,
        rng_seed=rng_seed,
    )

    out["recovery_time_ms_median"] = _bootstrap_event_metric(
        common_ids,
        a_by_id,
        b_by_id,
        getter=lambda r: [x for x in r.recovery_time_ms if x is not None],
        agg=lambda xs: statistics.median(xs) if xs else 0.0,
        name="recovery_time_ms_median",
        unit="ms",
        bootstrap_resamples=bootstrap_resamples,
        rng_seed=rng_seed,
    )

    # Per-recording scalar metrics: bootstrap on the per-recording values.
    out["wrong_slide_rate_mean"] = _bootstrap_scalar_metric(
        common_ids,
        a_by_id,
        b_by_id,
        getter=lambda r: r.wrong_slide_rate,
        name="wrong_slide_rate_mean",
        unit="fraction",
        bootstrap_resamples=bootstrap_resamples,
        rng_seed=rng_seed,
    )

    out["override_rate_mean"] = _bootstrap_scalar_metric(
        common_ids,
        a_by_id,
        b_by_id,
        getter=lambda r: r.override_rate,
        name="override_rate_mean",
        unit="overrides_per_song",
        bootstrap_resamples=bootstrap_resamples,
        rng_seed=rng_seed,
    )

    return out


def _bootstrap_event_metric(
    ids: list[str],
    a_by_id: dict[str, PipelineRun],
    b_by_id: dict[str, PipelineRun],
    *,
    getter: Callable[[PipelineRun], list[float]],
    agg: Callable[[list[float]], float],
    name: str,
    unit: str,
    bootstrap_resamples: int,
    rng_seed: int,
) -> ComparisonReport:
    a_pool: list[float] = []
    b_pool: list[float] = []
    for rid in ids:
        a_pool.extend(getter(a_by_id[rid]))
        b_pool.extend(getter(b_by_id[rid]))
    a_value = agg(a_pool)
    b_value = agg(b_pool)
    ci = _bootstrap_delta_ci(
        a_pool, b_pool, agg=agg, n=bootstrap_resamples, seed=rng_seed
    )
    return ComparisonReport(
        metric=MetricResult(name=name, value=b_value, unit=unit, n=len(b_pool)),
        a_value=a_value,
        b_value=b_value,
        delta=b_value - a_value,
        bootstrap_ci_delta=ci,
    )


def _bootstrap_scalar_metric(
    ids: list[str],
    a_by_id: dict[str, PipelineRun],
    b_by_id: dict[str, PipelineRun],
    *,
    getter: Callable[[PipelineRun], float],
    name: str,
    unit: str,
    bootstrap_resamples: int,
    rng_seed: int,
) -> ComparisonReport:
    a_values = [getter(a_by_id[rid]) for rid in ids]
    b_values = [getter(b_by_id[rid]) for rid in ids]
    a_value = statistics.fmean(a_values) if a_values else 0.0
    b_value = statistics.fmean(b_values) if b_values else 0.0
    ci = _bootstrap_delta_ci(
        a_values,
        b_values,
        agg=lambda xs: statistics.fmean(xs) if xs else 0.0,
        n=bootstrap_resamples,
        seed=rng_seed,
    )
    return ComparisonReport(
        metric=MetricResult(name=name, value=b_value, unit=unit, n=len(b_values)),
        a_value=a_value,
        b_value=b_value,
        delta=b_value - a_value,
        bootstrap_ci_delta=ci,
    )


def _bootstrap_delta_ci(
    a_pool: list[float],
    b_pool: list[float],
    *,
    agg: Callable[[list[float]], float],
    n: int,
    seed: int,
    alpha: float = 0.05,
) -> tuple[float, float] | None:
    if not a_pool or not b_pool:
        return None
    rng = random.Random(seed)
    deltas: list[float] = []
    a_size = len(a_pool)
    b_size = len(b_pool)
    for _ in range(n):
        a_sample = [a_pool[rng.randrange(a_size)] for _ in range(a_size)]
        b_sample = [b_pool[rng.randrange(b_size)] for _ in range(b_size)]
        deltas.append(agg(b_sample) - agg(a_sample))
    deltas.sort()
    lo = deltas[int(n * (alpha / 2))]
    hi = deltas[int(n * (1 - alpha / 2)) - 1]
    return (lo, hi)


def render_report(reports: dict[str, ComparisonReport]) -> str:
    """Render an A/B report as a Markdown table for CI diffs / commit messages."""
    lines = [
        "| Metric | A | B | Δ (B − A) | 95% CI of Δ |",
        "|---|---:|---:|---:|---|",
    ]
    for name in sorted(reports):
        r = reports[name]
        ci = (
            f"[{r.bootstrap_ci_delta[0]:+.3f}, {r.bootstrap_ci_delta[1]:+.3f}]"
            if r.bootstrap_ci_delta is not None
            else "—"
        )
        lines.append(
            f"| {name} | {r.a_value:.3f} | {r.b_value:.3f} | {r.delta:+.3f} | {ci} |"
        )
    return "\n".join(lines)
