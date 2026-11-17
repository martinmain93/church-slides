"""Cross-component: shadow log → eval pipeline metrics.

When Phase 5+ lights up the live shadow mode, the operator's real
session produces a `shadow.jsonl` log alongside the recording. The eval
harness consumes that log via `decisions_from_shadow`; the
`research/eval/eval_pipeline.py` CLI registers a `shadow:<path>` pipeline
that returns those decisions directly.

This test validates that chain end-to-end: write a shadow log via the
real `ShadowLogger`/`JsonlSink`, load it back, score it against the
recording, and verify the metrics make sense.
"""

from __future__ import annotations

from pathlib import Path

from auto_presenter.eval.compare import evaluate
from auto_presenter.eval.shadow import (
    JsonlSink,
    ShadowEntry,
    ShadowLogger,
    decisions_from_shadow,
    load_shadow_log,
)


def test_shadow_log_drives_evaluate_pipeline(tmp_path: Path, make_recording_session):
    recording = make_recording_session(
        audio_seconds=2.0,
        slide_changes=[(0, 0), (0, 1), (0, 2)],
    )

    # Write a shadow log that mirrors the ground truth but is 300 ms late
    # on every decision — exactly the kind of trace a slow matcher would
    # produce in shadow mode.
    shadow_path = tmp_path / "shadow.jsonl"
    with JsonlSink(shadow_path) as sink:
        for g in recording.ground_truth:
            sink.write(
                ShadowEntry(
                    t_seconds=g.t_seconds + 0.300,
                    item_idx=g.item_idx,
                    slide_idx=g.slide_idx,
                    source="ai",
                    confidence=0.8,
                )
            )

    decisions = decisions_from_shadow(load_shadow_log(shadow_path))
    assert len(decisions) == 3

    def shadow_pipeline(_rec):
        return list(decisions)

    runs = evaluate([recording], shadow_pipeline)
    assert len(runs) == 1
    run = runs[0]

    # Every decision is 300 ms late; anticipation_error_ms should reflect that.
    assert len(run.anticipation_error_ms) == 3
    for err in run.anticipation_error_ms:
        assert 295.0 <= err <= 305.0, err


def test_inmemory_shadow_logger_to_decisions(make_recording_session):
    """Confirms ShadowLogger entries flow directly into the eval shape."""
    recording = make_recording_session(audio_seconds=1.0)
    from auto_presenter.eval.shadow import InMemorySink

    sink = InMemorySink()
    logger = ShadowLogger(sink, t0=0.0)
    logger.record_decision(item_idx=0, slide_idx=0, confidence=0.9)
    logger.record_decision(item_idx=0, slide_idx=1, confidence=0.4)
    logger.record_override(item_idx=0, slide_idx=2)

    decisions = decisions_from_shadow(sink.entries)
    # Only the two ai decisions, not the override.
    assert [d.slide_idx for d in decisions] == [0, 1]
    assert all(d.source == "ai" for d in decisions)

    # And the recording is unaffected — shadow mode never writes ground truth.
    assert recording.ground_truth == []
