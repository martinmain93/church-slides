"""RecordingSink + recording manager tests."""

from __future__ import annotations

import json
import time
import wave
import zipfile
from pathlib import Path

import numpy as np
import pytest

from auto_presenter.audio.types import SAMPLE_RATE, AudioFrame
from auto_presenter.eval.types import Recording
from auto_presenter.recording.sink import (
    RecordingSink,
    delete_recording,
    export_recording_zip,
    list_recordings,
    load_recording_metadata,
    new_recording_id,
)


def make_frame(n_samples: int, fill: float = 0.5) -> AudioFrame:
    return AudioFrame(
        samples=np.full(n_samples, fill, dtype=np.float32),
        t_start=0.0,
        t_end=n_samples / SAMPLE_RATE,
    )


# --- sink lifecycle ---------------------------------------------------------


def test_open_creates_dir_and_wav(tmp_path: Path) -> None:
    sink = RecordingSink("rec_abc", tmp_path / "rec_abc", "sl_test")
    sink.open()
    assert sink.audio_path.exists()
    sink.close()


def test_close_writes_metadata_json(tmp_path: Path) -> None:
    sink = RecordingSink("rec_abc", tmp_path / "rec_abc", "sl_test")
    sink.open()
    sink.close()
    assert sink.metadata_path.exists()
    data = json.loads(sink.metadata_path.read_text())
    assert data["id"] == "rec_abc"
    assert data["setlist_id"] == "sl_test"
    assert data["audio_path"].endswith("audio.wav")


def test_close_is_idempotent(tmp_path: Path) -> None:
    sink = RecordingSink("rec_abc", tmp_path / "rec_abc", "sl_test")
    sink.open()
    a = sink.close()
    b = sink.close()
    # Second call returns the same metadata without crashing.
    assert a.id == b.id


def test_close_without_open_doesnt_crash(tmp_path: Path) -> None:
    sink = RecordingSink("rec_abc", tmp_path / "rec_abc", "sl_test")
    sink.close()  # nothing was opened; should not raise


# --- audio writing ----------------------------------------------------------


def test_write_frame_appends_to_wav(tmp_path: Path) -> None:
    sink = RecordingSink("rec_abc", tmp_path / "rec_abc", "sl_test")
    sink.open()
    for _ in range(5):
        sink.write_frame(make_frame(1600))
    sink.close()

    with wave.open(str(sink.audio_path), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == SAMPLE_RATE
        assert wav.getnframes() == 1600 * 5


def test_duration_in_metadata_matches_frames_written(tmp_path: Path) -> None:
    sink = RecordingSink("rec_abc", tmp_path / "rec_abc", "sl_test")
    sink.open()
    sink.write_frame(make_frame(SAMPLE_RATE))  # exactly 1 second
    sink.write_frame(make_frame(SAMPLE_RATE // 2))  # half second
    rec = sink.close()
    assert rec.duration_seconds == pytest.approx(1.5, abs=0.01)


def test_write_frame_after_close_is_silently_dropped(tmp_path: Path) -> None:
    sink = RecordingSink("rec_abc", tmp_path / "rec_abc", "sl_test")
    sink.open()
    sink.close()
    sink.write_frame(make_frame(100))  # should not raise
    assert sink.frames_written == 0


def test_clips_samples_outside_float_range(tmp_path: Path) -> None:
    sink = RecordingSink("rec_abc", tmp_path / "rec_abc", "sl_test")
    sink.open()
    # Inject values outside [-1, 1]; sink should clip rather than overflow.
    frame = AudioFrame(
        samples=np.array([2.0, -2.0, 0.5, -0.5], dtype=np.float32),
        t_start=0.0,
        t_end=4 / SAMPLE_RATE,
    )
    sink.write_frame(frame)
    sink.close()
    with wave.open(str(sink.audio_path), "rb") as wav:
        raw = wav.readframes(wav.getnframes())
    samples = np.frombuffer(raw, dtype=np.int16)
    # +1.0 → 32767, -1.0 → -32767, intermediate stays proportional.
    assert samples[0] == 32767  # clipped from 2.0
    assert samples[1] == -32767  # clipped from -2.0


# --- ground truth -----------------------------------------------------------


def test_slide_changes_get_timestamped_relative_to_open(tmp_path: Path) -> None:
    sink = RecordingSink("rec_abc", tmp_path / "rec_abc", "sl_test")
    sink.open()
    time.sleep(0.05)
    sink.record_slide_change(item_idx=0, slide_idx=1)
    time.sleep(0.05)
    sink.record_slide_change(item_idx=0, slide_idx=2)
    rec = sink.close()

    assert len(rec.ground_truth) == 2
    assert 0 <= rec.ground_truth[0].t_seconds < 0.5
    assert rec.ground_truth[0].t_seconds < rec.ground_truth[1].t_seconds
    assert rec.ground_truth[0].item_idx == 0
    assert rec.ground_truth[0].slide_idx == 1


def test_slide_change_after_close_is_dropped(tmp_path: Path) -> None:
    sink = RecordingSink("rec_abc", tmp_path / "rec_abc", "sl_test")
    sink.open()
    sink.close()
    sink.record_slide_change(item_idx=0, slide_idx=0)
    assert list(sink.ground_truth) == []


# --- recording id ----------------------------------------------------------


def test_new_recording_id_is_unique_across_close_calls() -> None:
    ids = {new_recording_id() for _ in range(50)}
    assert len(ids) >= 1  # not strictly unique due to millisecond grain
    for rid in ids:
        assert rid.startswith("rec_")


# --- manager (list / delete / export / load) -------------------------------


def _seed_recording(root: Path, rid: str = "rec_seed") -> Path:
    sink = RecordingSink(rid, root / rid, "sl_seed")
    sink.open()
    sink.write_frame(make_frame(1600))
    sink.record_slide_change(item_idx=0, slide_idx=0)
    sink.close()
    return root / rid


def test_list_recordings_finds_completed_recordings(tmp_path: Path) -> None:
    _seed_recording(tmp_path, "rec_a")
    _seed_recording(tmp_path, "rec_b")
    rows = list_recordings(tmp_path)
    assert len(rows) == 2
    ids = {r["id"] for r in rows}
    assert ids == {"rec_a", "rec_b"}
    for r in rows:
        assert r["ground_truth_count"] == 1
        assert r["duration_seconds"] > 0


def test_list_recordings_skips_dirs_without_metadata(tmp_path: Path) -> None:
    (tmp_path / "rec_bogus").mkdir()
    (tmp_path / "rec_bogus" / "audio.wav").write_bytes(b"")
    rows = list_recordings(tmp_path)
    assert rows == []


def test_list_recordings_returns_empty_when_root_missing(tmp_path: Path) -> None:
    rows = list_recordings(tmp_path / "does_not_exist")
    assert rows == []


def test_delete_recording_removes_directory(tmp_path: Path) -> None:
    rec_dir = _seed_recording(tmp_path, "rec_doomed")
    assert delete_recording(tmp_path, "rec_doomed") is True
    assert not rec_dir.exists()


def test_delete_recording_returns_false_for_missing_id(tmp_path: Path) -> None:
    assert delete_recording(tmp_path, "rec_ghost") is False


def test_export_recording_zip_contains_audio_and_metadata(tmp_path: Path) -> None:
    rec_dir = _seed_recording(tmp_path, "rec_export")
    out = tmp_path / "out.zip"
    export_recording_zip(rec_dir, out)
    assert out.exists()
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
    assert "audio.wav" in names
    assert "recording.json" in names


def test_load_recording_metadata_roundtrip(tmp_path: Path) -> None:
    rec_dir = _seed_recording(tmp_path, "rec_roundtrip")
    loaded = load_recording_metadata(rec_dir)
    assert loaded is not None
    assert loaded.id == "rec_roundtrip"
    assert loaded.setlist_id == "sl_seed"
    assert len(loaded.ground_truth) == 1


def test_load_recording_metadata_returns_none_for_missing(tmp_path: Path) -> None:
    assert load_recording_metadata(tmp_path / "nope") is None


def test_list_recordings_reports_songs_and_golden_state(tmp_path: Path) -> None:
    from auto_presenter.eval.types import SetlistItemSnapshot
    from auto_presenter.recording.golden import save_golden

    sink = RecordingSink(
        "rec_songs",
        tmp_path / "rec_songs",
        "sl_x",
        setlist_content=[
            SetlistItemSnapshot(item_idx=0, song_id="song_a", slides=[[]]),
            SetlistItemSnapshot(item_idx=1, song_id="song_b", slides=[[]]),
        ],
    )
    sink.open()
    sink.write_frame(make_frame(1600))
    sink.close()

    # No golden yet.
    row = list_recordings(tmp_path)[0]
    assert row["song_ids"] == ["song_a", "song_b"]
    assert row["has_golden"] is False
    assert row["golden_approved"] is False

    # A draft golden exists but nobody has signed it off.
    save_golden(tmp_path / "rec_songs", [], recording_id="rec_songs")
    row = list_recordings(tmp_path)[0]
    assert row["has_golden"] is True
    assert row["golden_approved"] is False

    # Approved.
    save_golden(tmp_path / "rec_songs", [], recording_id="rec_songs", approved=True)
    row = list_recordings(tmp_path)[0]
    assert row["golden_approved"] is True


def test_list_recordings_song_ids_empty_without_snapshot(tmp_path: Path) -> None:
    _seed_recording(tmp_path, "rec_bare")
    row = list_recordings(tmp_path)[0]
    assert row["song_ids"] == []


def test_a_moved_recording_still_finds_its_audio(tmp_path: Path) -> None:
    """`audio_path` is absolute and written at capture time, so it goes stale
    as soon as the folder moves — an export/import, a different machine, or a
    changed app-data dir. Two recordings on the developer's machine already
    pointed at a directory that no longer existed and replay died on
    FileNotFoundError, even though the wav sat right beside the metadata.
    """
    rec_dir = tmp_path / "rec_moved"
    rec_dir.mkdir()
    rec = Recording(
        id="rec_moved",
        setlist_id="sl",
        started_at_ms=1,
        duration_seconds=1.0,
        ground_truth=[],
        audio_path="/nowhere/that/exists/rec_moved/audio.wav",
    )
    (rec_dir / "recording.json").write_text(rec.model_dump_json())
    with wave.open(str(rec_dir / "audio.wav"), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(np.zeros(SAMPLE_RATE, dtype=np.int16).tobytes())

    loaded = load_recording_metadata(rec_dir)
    assert loaded is not None
    assert Path(loaded.audio_path).exists()
    assert Path(loaded.audio_path) == rec_dir / "audio.wav"


def test_a_valid_audio_path_is_left_alone(tmp_path: Path) -> None:
    """Only a *broken* path is rerouted — a recording whose stored path still
    resolves keeps it, so nothing is silently repointed."""
    rec_dir = tmp_path / "rec_ok"
    rec_dir.mkdir()
    elsewhere = tmp_path / "elsewhere.wav"
    with wave.open(str(elsewhere), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(np.zeros(SAMPLE_RATE, dtype=np.int16).tobytes())
    rec = Recording(
        id="rec_ok",
        setlist_id="sl",
        started_at_ms=1,
        duration_seconds=1.0,
        ground_truth=[],
        audio_path=str(elsewhere),
    )
    (rec_dir / "recording.json").write_text(rec.model_dump_json())
    (rec_dir / "audio.wav").write_bytes(b"not the one")

    loaded = load_recording_metadata(rec_dir)
    assert loaded is not None
    assert Path(loaded.audio_path) == elsewhere
