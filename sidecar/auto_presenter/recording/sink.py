"""`RecordingSink` — write audio frames + ground-truth slide changes to disk.

The sink is owned by the server's audio session. While capturing:

- Every `AudioFrame` from the capture is appended to `audio.wav` (16-bit
  PCM, mono, 16 kHz — matches `AudioFrame`'s contract).
- Every `record_slide_change()` call (driven by WS messages from the
  UI) appends a `GroundTruthEvent` with a timestamp anchored at the
  sink's `t0`.
- On `close()`, the WAV is finalized and `recording.json` is written
  alongside it. The returned `Recording` is what the eval harness in
  Phase 5+ consumes.

We deliberately write the WAV incrementally so a crash mid-service
doesn't lose everything — `audio.wav` is well-formed PCM up to the
last `write_frame()` call. The JSON only lands at close time, but that's
recoverable: the audio plus the duration is enough to re-annotate
ground truth post-hoc.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import wave
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

import numpy as np

from auto_presenter.audio.types import SAMPLE_RATE, AudioFrame
from auto_presenter.eval.types import (
    GroundTruthEvent,
    Recording,
    SessionConfig,
    SetlistItemSnapshot,
)

log = logging.getLogger(__name__)

#: The live transcript a recording keeps beside `recording.json`.
LIVE_TRANSCRIPT = "live_transcript.json"


SlideChangeSource = Literal["manual", "override", "ai", "voice"]


class RecordingSink:
    """Thread-safe sink for one recording session.

    `write_frame()` may be called from the audio worker thread; the
    other methods are called from the asyncio loop in response to WS
    messages. The internal lock keeps the WAV writer and ground-truth
    list consistent under that concurrency.
    """

    def __init__(
        self,
        recording_id: str,
        output_dir: Path,
        setlist_id: str,
        *,
        sample_rate: int = SAMPLE_RATE,
        setlist_content: list[SetlistItemSnapshot] | None = None,
        config: SessionConfig | None = None,
    ) -> None:
        self.recording_id = recording_id
        self.output_dir = Path(output_dir)
        self.setlist_id = setlist_id
        self.sample_rate = sample_rate
        self.started_at_ms = int(time.time() * 1000)
        self._t0 = time.monotonic()
        self._ground_truth: list[GroundTruthEvent] = []
        #: The transcript's final words as the matcher got them, so a past
        #: service can be inspected with exactly what the live look-back
        #: heard (`live_transcript.json`, beside `recording.json`).
        self._words: list[dict] = []
        self._setlist_content: list[SetlistItemSnapshot] = list(setlist_content or [])
        # What produced this trace. See `SessionConfig` — a recording that
        # cannot say how it was configured cannot settle why it behaved the
        # way it did.
        self._config = config
        self._wav: wave.Wave_write | None = None
        self._frames_written = 0
        self._lock = threading.Lock()
        self._closed = False

    # ----------------------------------------------------------- lifecycle

    def open(self) -> None:
        """Create the output directory and open the WAV file for writing."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        wav = wave.open(str(self.audio_path), "wb")
        wav.setnchannels(1)
        wav.setsampwidth(2)  # 16-bit PCM
        wav.setframerate(self.sample_rate)
        with self._lock:
            self._wav = wav
        log.info(
            "recording %s started: setlist=%s dir=%s",
            self.recording_id,
            self.setlist_id,
            self.output_dir,
        )

    def close(self) -> Recording:
        """Finalize the WAV, write `recording.json`, return the Recording.

        If `open()` was never called, returns an empty Recording without
        touching the filesystem — that's the right behavior for sessions
        that abort before any audio arrived.
        """
        with self._lock:
            if self._closed:
                # Already closed — return whatever we wrote.
                return self._build_recording()
            never_opened = self._wav is None and not self.output_dir.exists()
            if self._wav is not None:
                try:
                    self._wav.close()
                except Exception as exc:  # pragma: no cover - defensive
                    log.warning("error closing WAV writer: %s", exc)
                self._wav = None
            self._closed = True
            recording = self._build_recording()

        if never_opened:
            return recording

        self.metadata_path.write_text(recording.model_dump_json(indent=2))
        if self._words:
            try:
                (self.output_dir / LIVE_TRANSCRIPT).write_text(
                    json.dumps({"source": "live", "words": self._words})
                )
            except OSError as exc:  # pragma: no cover - defensive
                log.warning("live transcript not written: %s", exc)
        log.info(
            "recording %s closed: duration=%.1fs frames=%d ground_truth_events=%d",
            self.recording_id,
            recording.duration_seconds,
            self._frames_written,
            len(recording.ground_truth),
        )
        return recording

    # ----------------------------------------------------------- write paths

    def write_frame(self, frame: AudioFrame) -> None:
        """Append one audio frame to the WAV. Safe to call from audio thread."""
        # Convert float32 [-1, 1] → int16 PCM.
        clipped = np.clip(frame.samples, -1.0, 1.0)
        int16 = (clipped * 32767).astype(np.int16)
        with self._lock:
            if self._closed or self._wav is None:
                return
            self._wav.writeframes(int16.tobytes())
            self._frames_written += frame.n_samples

    def record_words(self, words) -> None:
        """Final transcript words (`asr.types.Word`), as the matcher got them."""
        with self._lock:
            if self._closed:
                return
            for w in words:
                if w.t_start is None:
                    continue
                self._words.append(
                    {
                        "text": w.text,
                        "t_start": round(float(w.t_start), 3),
                        "t_end": round(float(w.t_end if w.t_end is not None else w.t_start), 3),
                    }
                )

    def extend_setlist(self, items: list[SetlistItemSnapshot]) -> bool:
        """Songs added to the end mid-service (a spoken request, the operator
        appending one): add them to the saved setlist, so the slide changes
        that land on them can be replayed. Anything else — a reorder, a
        removal — would renumber earlier events, so the start-of-service
        setlist stands. True if it grew."""
        with self._lock:
            have = self._setlist_content
            if len(items) <= len(have):
                return False
            if [(i.item_idx, i.song_id) for i in items[: len(have)]] != [
                (i.item_idx, i.song_id) for i in have
            ]:
                return False
            self._setlist_content = have + list(items[len(have) :])
            return True

    def record_slide_change(
        self,
        item_idx: int,
        slide_idx: int,
        source: SlideChangeSource = "manual",
    ) -> None:
        """Append a ground-truth slide-change event.

        `source` is recorded, not just accepted. It used to be dropped on the
        floor, which left every live display log able to say the screen
        changed and unable to say what changed it.
        """
        t = time.monotonic() - self._t0
        with self._lock:
            if self._closed:
                return
            self._ground_truth.append(
                GroundTruthEvent(
                    t_seconds=t,
                    item_idx=item_idx,
                    slide_idx=slide_idx,
                    source=source,
                )
            )
        log.debug(
            "recording %s slide change: item=%d slide=%d (%s) at t=%.2fs",
            self.recording_id,
            item_idx,
            slide_idx,
            source,
            t,
        )

    # ----------------------------------------------------------- accessors

    @property
    def audio_path(self) -> Path:
        return self.output_dir / "audio.wav"

    @property
    def metadata_path(self) -> Path:
        return self.output_dir / "recording.json"

    @property
    def frames_written(self) -> int:
        return self._frames_written

    @property
    def is_open(self) -> bool:
        return not self._closed and self._wav is not None

    @property
    def ground_truth(self) -> Iterable[GroundTruthEvent]:
        return list(self._ground_truth)

    # ----------------------------------------------------------- internals

    def _duration_seconds(self) -> float:
        if self.sample_rate <= 0:
            return 0.0
        return self._frames_written / self.sample_rate

    def _build_recording(self) -> Recording:
        return Recording(
            id=self.recording_id,
            setlist_id=self.setlist_id,
            started_at_ms=self.started_at_ms,
            duration_seconds=self._duration_seconds(),
            ground_truth=list(self._ground_truth),
            audio_path=str(self.audio_path),
            setlist_content=list(self._setlist_content),
            config=self._config,
        )


# --------------------------------------------------------------------- utility


def new_recording_id() -> str:
    """Timestamp-based recording id. Sortable, human-readable, unique enough."""
    ts = time.strftime("%Y%m%d-%H%M%S")
    # Add a millisecond suffix so two recordings started within the same
    # second don't collide.
    ms = int((time.time() % 1) * 1000)
    return f"rec_{ts}_{ms:03d}"


def export_recording_zip(recording_dir: Path, output_path: Path) -> Path:
    """Zip up a recording directory for export. Returns the zip path."""
    import zipfile

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(recording_dir.iterdir()):
            if path.is_file():
                zf.write(path, arcname=path.name)
    return output_path


def list_recordings(recordings_root: Path) -> list[dict]:
    """List recording metadata for every recording in `recordings_root`.

    Returns lightweight dicts — the UI uses this for the list view. Full
    Recording objects can be loaded via `load_recording()`.

    `song_ids` comes from the recording's own setlist snapshot; resolving
    them to titles is the UI's job, since the song library lives behind
    Tauri rather than in the sidecar. `golden_approved` reflects a human
    signing off the timeline in the editor — a `golden.json` on disk is
    not enough, as machine-estimated ones exist too.
    """
    if not recordings_root.exists():
        return []
    out: list[dict] = []
    for entry in sorted(recordings_root.iterdir(), reverse=True):
        meta_path = entry / "recording.json"
        if not meta_path.exists():
            continue
        try:
            data = json.loads(meta_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("skipping unreadable recording at %s: %s", entry, exc)
            continue
        song_ids = [
            item.get("song_id", "")
            for item in (data.get("setlist_content") or [])
            if item.get("song_id")
        ]
        golden = _read_golden_summary(entry)
        out.append(
            {
                "id": data.get("id", entry.name),
                "setlist_id": data.get("setlist_id", ""),
                "started_at_ms": data.get("started_at_ms", 0),
                "duration_seconds": data.get("duration_seconds", 0.0),
                "ground_truth_count": len(data.get("ground_truth", [])),
                "directory": str(entry),
                "song_ids": song_ids,
                "has_golden": golden is not None,
                "golden_approved": bool(golden and golden.get("approved")),
            }
        )
    return out


def _read_golden_summary(recording_dir: Path) -> dict | None:
    """Read `golden.json` as a raw dict, or None if absent/unreadable."""
    path = recording_dir / "golden.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("skipping unreadable golden annotation at %s: %s", path, exc)
        return None


def delete_recording(recordings_root: Path, recording_id: str) -> bool:
    """Delete a recording directory by ID. Returns True if anything was removed."""
    import shutil

    root = recordings_root.resolve()
    target = (root / recording_id).resolve()
    if target.parent != root or not target.is_dir():
        return False  # only ever a folder directly inside the recordings folder
    shutil.rmtree(target)
    return True


def load_recording_metadata(recording_dir: Path) -> Recording | None:
    """Read `recording.json` from a recording directory."""
    meta_path = recording_dir / "recording.json"
    if not meta_path.exists():
        return None
    try:
        recording = Recording.model_validate_json(meta_path.read_text())
    except Exception as exc:
        log.warning("failed to parse recording metadata at %s: %s", meta_path, exc)
        return None
    # `audio_path` is written as an absolute path at capture time, so it stops
    # resolving the moment the folder moves — an export/import, a different
    # machine, or a changed app-data dir. The wav sits beside the metadata
    # regardless, so prefer the sibling whenever the stored path is gone;
    # two of the recordings on this machine already point at a directory that
    # no longer exists, and replay failed with FileNotFoundError.
    sibling = recording_dir / "audio.wav"
    if not Path(recording.audio_path or "").exists() and sibling.exists():
        recording.audio_path = str(sibling)
    return recording
