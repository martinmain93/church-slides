"""A/B harness tests."""

from __future__ import annotations

from auto_presenter.eval.compare import (
    compare,
    evaluate,
    render_report,
)
from auto_presenter.eval.types import Decision, GroundTruthEvent, Recording


def make_recording(rid: str) -> Recording:
    return Recording(
        id=rid,
        setlist_id="sl",
        started_at_ms=0,
        duration_seconds=20.0,
        ground_truth=[
            GroundTruthEvent(t_seconds=0, item_idx=0, slide_idx=0),
            GroundTruthEvent(t_seconds=10, item_idx=0, slide_idx=1),
            GroundTruthEvent(t_seconds=20, item_idx=0, slide_idx=2),
        ],
    )


def perfect_pipeline(rec: Recording) -> list[Decision]:
    return [
        Decision(t_seconds=g.t_seconds, item_idx=g.item_idx, slide_idx=g.slide_idx, source="ai")
        for g in rec.ground_truth
    ]


def late_500ms_pipeline(rec: Recording) -> list[Decision]:
    return [
        Decision(
            t_seconds=g.t_seconds + 0.5,
            item_idx=g.item_idx,
            slide_idx=g.slide_idx,
            source="ai",
        )
        for g in rec.ground_truth
    ]


def test_evaluate_runs_pipeline_against_each_recording():
    recordings = [make_recording("a"), make_recording("b")]
    runs = evaluate(recordings, perfect_pipeline)
    assert len(runs) == 2
    assert runs[0].recording_id == "a"
    assert runs[1].recording_id == "b"
    assert all(r.wrong_slide_rate == 0.0 for r in runs)


def test_compare_identical_pipelines_has_zero_delta():
    recordings = [make_recording("a"), make_recording("b")]
    a_runs = evaluate(recordings, perfect_pipeline)
    b_runs = evaluate(recordings, perfect_pipeline)
    report = compare(a_runs, b_runs, bootstrap_resamples=200)
    for r in report.values():
        assert r.delta == 0.0


def test_compare_late_pipeline_shows_positive_anticipation_delta():
    recordings = [make_recording("a"), make_recording("b"), make_recording("c")]
    a_runs = evaluate(recordings, perfect_pipeline)
    b_runs = evaluate(recordings, late_500ms_pipeline)
    report = compare(a_runs, b_runs, bootstrap_resamples=200)
    # Late pipeline = 500ms anticipation_error vs 0ms for perfect.
    assert report["anticipation_error_ms_median"].delta == 500.0


def test_compare_late_pipeline_shows_higher_wrong_slide_rate():
    recordings = [make_recording("a"), make_recording("b"), make_recording("c")]
    a_runs = evaluate(recordings, perfect_pipeline)
    b_runs = evaluate(recordings, late_500ms_pipeline)
    report = compare(a_runs, b_runs, bootstrap_resamples=200)
    # Late pipeline misses 500ms per slide change → small but positive delta.
    assert report["wrong_slide_rate_mean"].delta > 0


def test_render_report_produces_markdown_table():
    recordings = [make_recording("a")]
    runs = evaluate(recordings, perfect_pipeline)
    report = compare(runs, runs, bootstrap_resamples=50)
    text = render_report(report)
    assert "| Metric |" in text
    assert "anticipation_error_ms_median" in text
    assert "wrong_slide_rate_mean" in text
