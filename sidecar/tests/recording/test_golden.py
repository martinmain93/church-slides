"""Golden-annotation + audio helper tests."""

from __future__ import annotations

import base64
import wave
from pathlib import Path

import numpy as np

from auto_presenter.eval.replay import load_recording
from auto_presenter.eval.types import GroundTruthEvent, Recording
from auto_presenter.recording.golden import (
    effective_ground_truth,
    golden_path,
    load_audio_b64,
    load_golden,
    save_golden,
)


def _write_recording(rec_dir: Path, ground_truth: list[GroundTruthEvent]) -> Recording:
    rec_dir.mkdir(parents=True, exist_ok=True)
    rec = Recording(
        id=rec_dir.name,
        setlist_id="sl_test",
        started_at_ms=1000,
        duration_seconds=3.0,
        ground_truth=ground_truth,
    )
    (rec_dir / "recording.json").write_text(rec.model_dump_json(indent=2))
    return rec


def _write_wav(rec_dir: Path, *, seconds: float = 1.0, sample_rate: int = 16000) -> None:
    rec_dir.mkdir(parents=True, exist_ok=True)
    n = int(seconds * sample_rate)
    samples = (np.zeros(n, dtype=np.int16))
    with wave.open(str(rec_dir / "audio.wav"), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(samples.tobytes())


# --- save / load roundtrip --------------------------------------------------


def test_save_golden_roundtrip_and_sorts(tmp_path: Path) -> None:
    rec_dir = tmp_path / "rec_x"
    rec_dir.mkdir()
    events = [
        GroundTruthEvent(t_seconds=5.0, item_idx=0, slide_idx=2),
        GroundTruthEvent(t_seconds=1.0, item_idx=0, slide_idx=1),
    ]
    saved = save_golden(rec_dir, events, recording_id="rec_x", notes="hi")

    assert golden_path(rec_dir).exists()
    assert saved.recording_id == "rec_x"
    assert saved.notes == "hi"
    # Sorted chronologically on write.
    assert [e.t_seconds for e in saved.ground_truth] == [1.0, 5.0]

    loaded = load_golden(rec_dir)
    assert loaded is not None
    assert [e.slide_idx for e in loaded.ground_truth] == [1, 2]


def test_load_golden_absent_returns_none(tmp_path: Path) -> None:
    assert load_golden(tmp_path / "nope") is None


def test_effective_ground_truth_prefers_golden(tmp_path: Path) -> None:
    rec_dir = tmp_path / "rec_y"
    raw = [GroundTruthEvent(t_seconds=2.0, item_idx=0, slide_idx=0)]
    rec = _write_recording(rec_dir, raw)

    # No golden yet -> recording's own.
    assert effective_ground_truth(rec_dir, rec) == raw

    golden_events = [GroundTruthEvent(t_seconds=9.0, item_idx=1, slide_idx=3)]
    save_golden(rec_dir, golden_events)
    eff = effective_ground_truth(rec_dir, rec)
    assert [e.t_seconds for e in eff] == [9.0]
    assert [e.item_idx for e in eff] == [1]


# --- audio base64 -----------------------------------------------------------


def test_load_audio_b64_reports_duration_and_rate(tmp_path: Path) -> None:
    rec_dir = tmp_path / "rec_a"
    _write_wav(rec_dir, seconds=2.0, sample_rate=16000)

    b64, sample_rate, duration = load_audio_b64(rec_dir)
    assert sample_rate == 16000
    assert abs(duration - 2.0) < 1e-6
    # base64 decodes back to the original WAV bytes.
    assert base64.b64decode(b64) == (rec_dir / "audio.wav").read_bytes()


def test_load_audio_b64_missing_raises(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(FileNotFoundError):
        load_audio_b64(tmp_path / "empty")


# --- replay loader overlays golden ------------------------------------------


def test_load_recording_overlays_golden(tmp_path: Path) -> None:
    rec_dir = tmp_path / "rec_z"
    _write_recording(
        rec_dir, [GroundTruthEvent(t_seconds=2.0, item_idx=0, slide_idx=0)]
    )

    # Without golden -> raw ground truth.
    raw = load_recording(rec_dir)
    assert [e.t_seconds for e in raw.ground_truth] == [2.0]

    save_golden(
        rec_dir,
        [
            GroundTruthEvent(t_seconds=4.5, item_idx=2, slide_idx=1),
            GroundTruthEvent(t_seconds=8.0, item_idx=2, slide_idx=2),
        ],
    )
    overlaid = load_recording(rec_dir)
    assert [e.t_seconds for e in overlaid.ground_truth] == [4.5, 8.0]
    assert [e.item_idx for e in overlaid.ground_truth] == [2, 2]
    # recording.json itself is untouched.
    on_disk = Recording.model_validate_json(
        (rec_dir / "recording.json").read_text()
    )
    assert [e.t_seconds for e in on_disk.ground_truth] == [2.0]


# --- approval flag ----------------------------------------------------------


def test_save_golden_defaults_to_unapproved(tmp_path: Path) -> None:
    rec_dir = tmp_path / "rec_draft"
    rec_dir.mkdir()
    saved = save_golden(rec_dir, [], recording_id="rec_draft")
    assert saved.approved is False
    loaded = load_golden(rec_dir)
    assert loaded is not None and loaded.approved is False


def test_save_golden_records_human_signoff(tmp_path: Path) -> None:
    rec_dir = tmp_path / "rec_signed"
    rec_dir.mkdir()
    save_golden(rec_dir, [], recording_id="rec_signed", approved=True)
    loaded = load_golden(rec_dir)
    assert loaded is not None and loaded.approved is True


def test_approval_can_be_withdrawn(tmp_path: Path) -> None:
    rec_dir = tmp_path / "rec_undo"
    rec_dir.mkdir()
    save_golden(rec_dir, [], recording_id="rec_undo", approved=True)
    save_golden(rec_dir, [], recording_id="rec_undo", approved=False)
    loaded = load_golden(rec_dir)
    assert loaded is not None and loaded.approved is False


def test_pre_approval_golden_files_load_as_unapproved(tmp_path: Path) -> None:
    """Annotations written before `approved` existed must still parse."""
    rec_dir = tmp_path / "rec_legacy"
    rec_dir.mkdir()
    golden_path(rec_dir).write_text(
        '{"recording_id": "rec_legacy", "ground_truth": [], '
        '"notes": "", "edited_at_ms": 1}'
    )
    loaded = load_golden(rec_dir)
    assert loaded is not None and loaded.approved is False
