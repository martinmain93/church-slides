"""Resolve a recording or WAV into a FileCapture for live injection."""

from __future__ import annotations

from pathlib import Path

from auto_presenter.audio.capture import FileCapture
from auto_presenter.eval.replay import load_recording
from auto_presenter.server_state import _state

#: What the last successful inject pointed at, so snapshots can name it.
current: dict[str, str | None] = {
    "recording_id": None,
    "wav_path": None,
}


def clear() -> None:
    current["recording_id"] = None
    current["wav_path"] = None


def resolve_wav(*, recording_id: str | None, wav_path: str | None) -> Path:
    if recording_id and wav_path:
        raise ValueError("pass recording_id or wav_path, not both")
    if not recording_id and not wav_path:
        raise ValueError("recording_id or wav_path is required")
    if wav_path:
        path = Path(wav_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"wav not found: {path}")
        return path
    root = _state.recordings_root
    if root is None:
        raise FileNotFoundError("recordings root is not set")
    assert recording_id is not None
    rec_dir = root / recording_id
    if not rec_dir.is_dir():
        raise FileNotFoundError(f"recording not found: {recording_id}")
    recording = load_recording(rec_dir)
    if not recording.audio_path:
        raise FileNotFoundError(f"recording {recording_id} has no audio")
    path = Path(recording.audio_path)
    if not path.is_file():
        raise FileNotFoundError(f"recording wav missing: {path}")
    return path


def make_capture(
    *,
    recording_id: str | None,
    wav_path: str | None,
    realtime: bool,
) -> FileCapture:
    path = resolve_wav(recording_id=recording_id, wav_path=wav_path)
    current["recording_id"] = recording_id
    current["wav_path"] = str(path)
    return FileCapture(path, realtime=realtime)
