"""End-to-end: record → save → load → score with metrics.

Exercises the chain PHASE_3.md calls out as the integration test for
the eval harness — recording with ground-truth captured at the source,
loaded back from disk, replayed through a pipeline, and scored.

The recording is built with `RecordingSink` (the real implementation
used by the server), the file is loaded with `load_recording()`, and
the metrics pipeline (`evaluate` + `compare`) is the same one the CLI
uses.
"""

from __future__ import annotations

from pathlib import Path

from auto_presenter.eval.compare import compare, evaluate
from auto_presenter.eval.metrics import (
    anticipation_error_ms,
    override_rate,
    wrong_slide_rate,
)
from auto_presenter.eval.replay import load_recording
from auto_presenter.eval.types import Decision, GroundTruthEvent, Recording


def test_recording_roundtrips_and_oracle_scores_perfectly(make_recording_session):
    recording = make_recording_session(
        audio_seconds=1.0,
        slide_changes=[(0, 0), (0, 1), (1, 0)],
    )
    assert Path(recording.audio_path).exists(), "RecordingSink should have written audio.wav"
    assert len(recording.ground_truth) == 3

    # The metadata file the eval harness loads is the one the sink wrote
    # next to the WAV — reload it via the public API.
    loaded = load_recording(Path(recording.audio_path).parent)
    assert loaded.id == recording.id
    assert len(loaded.ground_truth) == 3

    # Oracle pipeline: emits exactly the ground truth as decisions →
    # every metric should be at its perfect value.
    def oracle(rec):
        return [
            Decision(
                t_seconds=g.t_seconds,
                item_idx=g.item_idx,
                slide_idx=g.slide_idx,
            )
            for g in rec.ground_truth
        ]

    runs = evaluate([loaded], oracle)
    assert len(runs) == 1
    run = runs[0]

    # Every decision should be matched to its ground-truth event with
    # zero offset.
    assert run.anticipation_error_ms == [0.0, 0.0, 0.0]
    assert run.wrong_slide_rate == 0.0
    assert run.override_rate == 0.0


def test_stub_pipeline_is_worse_than_oracle(make_recording_session):
    recording = make_recording_session(
        audio_seconds=1.0,
        slide_changes=[(0, 0), (0, 1), (0, 2), (0, 3)],
    )

    def stub(_rec):
        return []

    def oracle(rec):
        return [
            Decision(
                t_seconds=g.t_seconds,
                item_idx=g.item_idx,
                slide_idx=g.slide_idx,
            )
            for g in rec.ground_truth
        ]

    a_runs = evaluate([recording], stub)
    b_runs = evaluate([recording], oracle)

    # Stub: every metric is the "no decisions" floor.
    assert a_runs[0].anticipation_error_ms == []
    assert a_runs[0].wrong_slide_rate >= 0.0
    # Oracle: matches every event.
    assert b_runs[0].anticipation_error_ms == [0.0, 0.0, 0.0, 0.0]

    # Compare returns the A/B delta so reports are diffable in CI.
    reports = compare(a_runs, b_runs, bootstrap_resamples=64, rng_seed=7)
    assert "wrong_slide_rate_mean" in reports
    assert reports["wrong_slide_rate_mean"].a_value >= reports["wrong_slide_rate_mean"].b_value


def test_late_decision_yields_positive_anticipation_error():
    """Sanity check on the metric semantics: late decisions => positive ms.

    Built from a hand-authored `Recording` rather than the fixture sink
    so that the ground-truth events are spaced out across the recording
    rather than stamped microseconds apart — this is what lets
    `wrong_slide_rate` distinguish the in-window correct/wrong intervals.
    """
    recording = Recording(
        id="rec_late_decisions",
        setlist_id="sl_test",
        started_at_ms=0,
        duration_seconds=10.0,
        ground_truth=[
            GroundTruthEvent(t_seconds=0.0, item_idx=0, slide_idx=0),
            GroundTruthEvent(t_seconds=2.0, item_idx=0, slide_idx=1),
            GroundTruthEvent(t_seconds=5.0, item_idx=0, slide_idx=2),
        ],
    )

    # Push every decision 200 ms after the ground-truth event.
    def late(rec):
        return [
            Decision(
                t_seconds=g.t_seconds + 0.200,
                item_idx=g.item_idx,
                slide_idx=g.slide_idx,
            )
            for g in rec.ground_truth
        ]

    errs = anticipation_error_ms(late(recording), recording.ground_truth)
    assert len(errs) == 3
    assert all(195.0 <= e <= 205.0 for e in errs), errs

    # Overall wrong-slide rate is small but non-zero: each decision is
    # 200 ms behind ground truth over a 5 s window.
    wsr = wrong_slide_rate(late(recording), recording.ground_truth)
    assert 0.0 < wsr < 0.2, wsr

    # Override rate respects the source field.
    assert override_rate(late(recording), n_songs=1) == 0.0
