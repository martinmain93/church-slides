"""Recording load/save + replay helpers.

The data-format layer (save/load/iter_ground_truth) has been here since
Phase 3 Slice 1. Audio-frame replay was deferred to Phase 4 because it
needed `AudioFrame` + `FileCapture` — those exist now, so the `Replayer`
wrapper below makes recordings interchangeable with `LiveCapture` for any
pipeline that consumes a frame stream.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from auto_presenter.audio.capture import FileCapture
from auto_presenter.audio.types import DEFAULT_FRAME_LENGTH, AudioFrame
from auto_presenter.eval.types import GoldenAnnotation, GroundTruthEvent, Recording


def save_recording(recording: Recording, path: Path) -> None:
    """Write the recording's metadata to a JSON file.

    The audio WAV (if any) is referenced by `recording.audio_path` and is
    not duplicated by this call.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(recording.model_dump_json(indent=2))


def load_recording(path: Path) -> Recording:
    """Inverse of `save_recording`.

    Accepts either a path directly to a `recording.json` file or to the
    recording directory that contains one. The latter is what
    `RecordingSink` writes during a live service.

    If a human-corrected `golden.json` sits beside the recording, its
    `ground_truth` is overlaid onto the loaded Recording — so every
    eval/baseline replay automatically uses the corrected timeline while the
    raw operator capture in `recording.json` stays untouched.
    """
    p = Path(path)
    if p.is_dir():
        p = p / "recording.json"
    recording = Recording.model_validate(json.loads(p.read_text()))

    # `audio_path` is stored absolute, so a recording that moves — or was
    # captured against a directory that has since been cleaned — points at a
    # file that no longer exists. The audio always sits beside the manifest, so
    # prefer the sibling when the recorded path is stale. Without this the
    # goldens are only replayable on the machine that captured them, which is
    # not a property ground truth should have.
    stored = recording.audio_path
    if stored is None or not Path(stored).exists():
        sibling = p.parent / "audio.wav"
        if sibling.exists():
            recording = recording.model_copy(update={"audio_path": str(sibling)})

    golden_file = p.parent / "golden.json"
    if golden_file.exists():
        try:
            golden = GoldenAnnotation.model_validate_json(golden_file.read_text())
            recording = recording.model_copy(
                update={"ground_truth": list(golden.ground_truth)}
            )
        except Exception:  # pragma: no cover - defensive; fall back to raw
            pass
    return recording


def iter_ground_truth(recording: Recording) -> Iterator[GroundTruthEvent]:
    """Yield ground-truth events in chronological order."""
    yield from sorted(recording.ground_truth, key=lambda g: g.t_seconds)


class Replayer:
    """Stream a `Recording`'s audio + ground truth.

    The audio side is a thin wrapper over `FileCapture` so anything that
    accepts an `AudioCapture` also accepts a `Replayer`. `realtime=True`
    paces frames at wall-clock speed (matching what a live mic would
    produce). `realtime=False` runs as fast as possible — that's the
    right mode for batch eval where you want N recordings processed in
    seconds, not in their native duration.
    """

    def __init__(
        self,
        recording: Recording,
        *,
        realtime: bool = False,
        frame_length: int = DEFAULT_FRAME_LENGTH,
    ) -> None:
        if recording.audio_path is None:
            raise ValueError(
                f"recording {recording.id!r} has no audio_path; "
                "replay-only-ground-truth mode is not supported here"
            )
        self.recording = recording
        self.realtime = realtime
        self.frame_length = frame_length

    def audio_frames(self) -> Iterator[AudioFrame]:
        """Pump the recording's WAV into the standard frame stream."""
        if self.recording.audio_path is None:
            raise ValueError("recording has no audio_path")
        capture = FileCapture(
            Path(self.recording.audio_path),
            realtime=self.realtime,
            frame_length=self.frame_length,
        )
        return capture.stream()

    def ground_truth(self) -> Iterator[GroundTruthEvent]:
        return iter_ground_truth(self.recording)

    # Alias so callers can write `replayer.stream()` matching the
    # `AudioCapture` protocol shape.
    def stream(self) -> Iterator[AudioFrame]:
        return self.audio_frames()
