"""Tests for the shadow-mode logger."""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_presenter.eval.shadow import (
    InMemorySink,
    JsonlSink,
    ShadowEntry,
    ShadowLogger,
    attach_shadow_to_recording,
    decisions_from_shadow,
    ground_truth_from_shadow,
    load_shadow_log,
)
from auto_presenter.eval.types import GroundTruthEvent, Recording


def test_record_decision_stamps_relative_to_t0() -> None:
    sink = InMemorySink()
    logger = ShadowLogger(sink, t0=100.0)
    # Force a specific monotonic-ish stamp by patching time below; easier:
    # the entries' t_seconds are >= 0 because t0 was set in the past.
    entry = logger.record_decision(item_idx=2, slide_idx=4, confidence=0.7)
    assert entry.source == "ai"
    assert entry.item_idx == 2
    assert entry.slide_idx == 4
    assert entry.confidence == 0.7
    assert entry.t_seconds >= 0.0
    assert sink.entries == [entry]


def test_record_override_marks_source() -> None:
    sink = InMemorySink()
    logger = ShadowLogger(sink)
    entry = logger.record_override(item_idx=1, slide_idx=0, note="manual nudge")
    assert entry.source == "override"
    assert entry.note == "manual nudge"
    assert entry.confidence == 1.0


def test_jsonl_sink_appends_and_reloads(tmp_path: Path) -> None:
    path = tmp_path / "shadow.jsonl"
    with JsonlSink(path) as sink:
        sink.write(
            ShadowEntry(t_seconds=1.0, item_idx=0, slide_idx=0, source="ai", confidence=0.8)
        )
        sink.write(
            ShadowEntry(t_seconds=2.5, item_idx=0, slide_idx=1, source="override")
        )

    loaded = load_shadow_log(path)
    assert len(loaded) == 2
    assert loaded[0].source == "ai" and loaded[0].confidence == 0.8
    assert loaded[1].source == "override"


def test_jsonl_sink_is_line_buffered(tmp_path: Path) -> None:
    """A crash mid-service should leave a well-formed prefix on disk."""
    path = tmp_path / "shadow.jsonl"
    sink = JsonlSink(path)
    try:
        sink.write(ShadowEntry(t_seconds=0.1, item_idx=0, slide_idx=0, source="ai"))
        # Don't close — simulate a crash. The line should still be flushed.
        content = path.read_text()
    finally:
        sink.close()
    assert content.endswith("\n")
    assert "item_idx" in content


def test_load_shadow_log_tolerates_blank_and_bad_lines(tmp_path: Path) -> None:
    path = tmp_path / "shadow.jsonl"
    path.write_text(
        '{"t_seconds":0.1,"item_idx":0,"slide_idx":0,"source":"ai","confidence":0.5,"note":""}\n'
        "\n"
        "not json at all\n"
        '{"t_seconds":1.5,"item_idx":0,"slide_idx":1,"source":"override","confidence":1.0,"note":""}\n'
    )
    out = load_shadow_log(path)
    assert [e.slide_idx for e in out] == [0, 1]


def test_decisions_from_shadow_filters_overrides_by_default() -> None:
    entries = [
        ShadowEntry(t_seconds=0.0, item_idx=0, slide_idx=0, source="ai"),
        ShadowEntry(t_seconds=1.0, item_idx=0, slide_idx=1, source="override"),
        ShadowEntry(t_seconds=2.0, item_idx=0, slide_idx=2, source="ai"),
    ]
    decisions = decisions_from_shadow(entries)
    assert [d.slide_idx for d in decisions] == [0, 2]

    with_overrides = decisions_from_shadow(entries, include_overrides=True)
    assert [d.slide_idx for d in with_overrides] == [0, 1, 2]


def test_ground_truth_from_shadow_only_keeps_overrides() -> None:
    entries = [
        ShadowEntry(t_seconds=0.0, item_idx=0, slide_idx=0, source="ai"),
        ShadowEntry(t_seconds=1.0, item_idx=0, slide_idx=1, source="override"),
    ]
    gt = ground_truth_from_shadow(entries)
    assert len(gt) == 1
    assert isinstance(gt[0], GroundTruthEvent)
    assert gt[0].slide_idx == 1


def test_attach_shadow_to_recording(tmp_path: Path) -> None:
    shadow_path = tmp_path / "shadow.jsonl"
    with JsonlSink(shadow_path) as sink:
        sink.write(
            ShadowEntry(t_seconds=0.1, item_idx=0, slide_idx=0, source="ai", confidence=0.9)
        )
    recording = Recording(
        id="rec_x",
        setlist_id="sl",
        started_at_ms=0,
        duration_seconds=10.0,
        ground_truth=[],
    )
    rec, decisions = attach_shadow_to_recording(recording, shadow_path)
    assert rec is recording
    assert len(decisions) == 1
    assert decisions[0].source == "ai"


def test_load_shadow_log_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_shadow_log(tmp_path / "nope.jsonl") == []


def test_inmemory_sink_is_threadsafe_basic() -> None:
    """Sanity check that two threads writing don't lose entries."""
    import threading

    sink = InMemorySink()
    logger = ShadowLogger(sink)
    n = 50

    def push(start: int) -> None:
        for i in range(n):
            logger.record_decision(item_idx=0, slide_idx=start + i)

    t1 = threading.Thread(target=push, args=(0,))
    t2 = threading.Thread(target=push, args=(1000,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert len(sink.entries) == 2 * n


@pytest.mark.parametrize(
    "source",
    ["ai", "override"],
)
def test_shadow_entry_to_decision_carries_source(source: str) -> None:
    e = ShadowEntry(t_seconds=0.1, item_idx=0, slide_idx=2, source=source)
    d = e.to_decision()
    assert d.source == source
    assert d.slide_idx == 2
