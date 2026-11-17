"""Golden-annotation + audio helpers for the Golden Recording Editor.

A "golden" annotation is a human-corrected ground-truth timeline for a
recording. It lives in a sibling `golden.json` next to `recording.json` so
the raw operator capture is preserved. When a golden file exists, the eval
loader (`eval.replay.load_recording`) prefers its `ground_truth`.

This module also serves the captured audio to the UI as base64 — the sidecar
is WebSocket-only, so the editor pulls the WAV bytes over the wire and decodes
them client-side for playback + waveform rendering.
"""

from __future__ import annotations

import base64
import logging
import time
import wave
from pathlib import Path

from auto_presenter.eval.types import GoldenAnnotation, GroundTruthEvent, Recording

log = logging.getLogger(__name__)


def golden_path(recording_dir: Path) -> Path:
    """Path of the golden annotation file for a recording directory."""
    return Path(recording_dir) / "golden.json"


def audio_path(recording_dir: Path) -> Path:
    return Path(recording_dir) / "audio.wav"


def load_golden(recording_dir: Path) -> GoldenAnnotation | None:
    """Read `golden.json` from a recording directory, or None if absent."""
    path = golden_path(recording_dir)
    if not path.exists():
        return None
    try:
        return GoldenAnnotation.model_validate_json(path.read_text())
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("failed to parse golden annotation at %s: %s", path, exc)
        return None


def save_golden(
    recording_dir: Path,
    ground_truth: list[GroundTruthEvent],
    *,
    recording_id: str | None = None,
    notes: str = "",
    approved: bool = False,
) -> GoldenAnnotation:
    """Write a golden annotation, sorted chronologically. Returns it."""
    recording_dir = Path(recording_dir)
    recording_dir.mkdir(parents=True, exist_ok=True)
    annotation = GoldenAnnotation(
        recording_id=recording_id or recording_dir.name,
        ground_truth=sorted(ground_truth, key=lambda g: g.t_seconds),
        notes=notes,
        edited_at_ms=int(time.time() * 1000),
        approved=approved,
    )
    golden_path(recording_dir).write_text(annotation.model_dump_json(indent=2))
    log.info(
        "golden annotation saved: %s (%d events, approved=%s)",
        recording_dir.name,
        len(annotation.ground_truth),
        approved,
    )
    return annotation


def effective_ground_truth(
    recording_dir: Path, recording: Recording
) -> list[GroundTruthEvent]:
    """Golden ground truth if a golden file exists, else the recording's own."""
    golden = load_golden(recording_dir)
    if golden is not None:
        return list(golden.ground_truth)
    return list(recording.ground_truth)


def load_audio_b64(
    recording_dir: Path, path: Path | None = None
) -> tuple[str, int, float]:
    """Read a WAV and return (base64 bytes, sample_rate, duration_seconds).

    `path` defaults to the recording's own `audio.wav`. A stem solo passes
    the stem file instead. Raises FileNotFoundError if the WAV is missing.
    """
    path = audio_path(recording_dir) if path is None else path
    if not path.exists():
        raise FileNotFoundError(f"no audio at {path.name}")
    raw = path.read_bytes()
    with wave.open(str(path), "rb") as wav:
        sample_rate = wav.getframerate()
        n_frames = wav.getnframes()
        duration = n_frames / sample_rate if sample_rate else 0.0
    return base64.b64encode(raw).decode("ascii"), sample_rate, duration
