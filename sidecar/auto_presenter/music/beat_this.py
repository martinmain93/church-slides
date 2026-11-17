"""Beat This! as a subprocess, so torch never enters the sidecar.

The venv is created by hand (see scripts/run_beat_this.py) under the app
data directory. When it is absent the inspector still draws onsets, pulse
and chords, and the beat row says the model is not installed.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from pathlib import Path

from auto_presenter.config import app_data_dir

log = logging.getLogger(__name__)


def python_path() -> Path | None:
    override = os.environ.get("AUTO_PRESENTER_BEAT_THIS_PYTHON")
    if override and Path(override).exists():
        return Path(override)
    candidate = app_data_dir() / "models" / "beat-this-venv" / "bin" / "python"
    if candidate.exists():
        return candidate
    return None


def script_path() -> Path:
    # sidecar/auto_presenter/music/beat_this.py -> repo/scripts/run_beat_this.py
    return Path(__file__).resolve().parents[3] / "scripts" / "run_beat_this.py"


def iter_transcribe(
    wav_path: Path,
    threads: int | None = None,
    background: bool = False,
    timeout_s: float | None = None,
):
    """Yield JSON objects from `run_beat_this.py --stream`.

    `threads` caps torch's CPU threads; `background` runs it on the CPU at
    a lower priority, so it gives way to the session's own audio work.

    Each object is the beat prefix that is safe to draw. The last one has
    `partial` false. Failures yield one object with `error` set.
    """
    py = python_path()
    script = script_path()
    if py is None:
        yield {"partial": False, "beats": [], "downbeats": [], "through": 0, "error": "missing"}
        return
    if not script.exists():
        yield {
            "partial": False,
            "beats": [],
            "downbeats": [],
            "through": 0,
            "error": "script_missing",
        }
        return
    env = None
    if background:
        # off the GPU: the session's Whisper and span scorer queue there
        env = dict(os.environ, BEAT_THIS_DEVICE="cpu")
    if threads:
        env = dict(env or os.environ, OMP_NUM_THREADS=str(threads), MKL_NUM_THREADS=str(threads))
    # `nice` rather than a preexec_fn, which is unsafe in a threaded process
    lower = ["nice", "-n", "10"] if background else []
    proc = subprocess.Popen(
        [*lower, str(py), str(script), "--stream", str(wav_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=env,
    )
    assert proc.stdout is not None
    # a hung tracker must not hold its reader forever
    watchdog = threading.Timer(timeout_s, proc.kill) if timeout_s else None
    if watchdog is not None:
        watchdog.daemon = True
        watchdog.start()
    try:
        saw_final = False
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            saw_final = not msg.get("partial", False)
            yield msg
            if saw_final:
                break
        code = proc.wait(timeout=30)
        if code != 0 and not saw_final:
            yield {
                "partial": False,
                "beats": [],
                "downbeats": [],
                "through": 0,
                "error": "failed",
            }
    finally:
        if watchdog is not None:
            watchdog.cancel()
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def transcribe(wav_path: Path, timeout_s: float = 180.0) -> tuple[list[float], list[float], str]:
    """Beat times, downbeat times, and a status string.

    Status is `beat_this` on success, otherwise a short reason.
    """
    py = python_path()
    script = script_path()
    if py is None:
        return [], [], "missing"
    if not script.exists():
        return [], [], "script_missing"
    try:
        proc = subprocess.run(
            [str(py), str(script), str(wav_path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return [], [], "timeout"
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "beat this failed").strip().splitlines()
        log.warning("beat this failed: %s", err[-1] if err else proc.returncode)
        return [], [], "failed"
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return [], [], "bad_output"
    beats = [float(t) for t in payload.get("beats", [])]
    downbeats = [float(t) for t in payload.get("downbeats", [])]
    return beats, downbeats, "beat_this"


#: Torch threads for a beat run during a service (`beats_of`).
SERVICE_THREADS = 2
#: A beat run during a service gives up after this long (a hung tracker).
SERVICE_TIMEOUT_S = 120.0


def beats_of(samples, sample_rate: int = 16000):
    """Beat times (seconds) of mono float audio, through Beat This, or None
    when it is not installed or fails. Writes a temporary WAV for the
    subprocess. Runs during a service: few threads, low priority."""
    import tempfile
    import wave

    import numpy as np

    if python_path() is None or not script_path().exists():
        return None
    pcm = (np.clip(np.asarray(samples, np.float32), -1, 1) * 32767).astype(np.int16)
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "song.wav"
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm.tobytes())
        final = None
        for msg in iter_transcribe(
            path, threads=SERVICE_THREADS, background=True, timeout_s=SERVICE_TIMEOUT_S
        ):
            if not msg.get("partial", False):
                final = msg
        if final is None or final.get("error"):
            return None
        return np.asarray(final.get("beats", []), float)
