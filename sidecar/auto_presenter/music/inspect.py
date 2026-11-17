"""One recording, analysed for the model inspector's music panel.

Cached beside the audio once Beat This and Basic Pitch both returned, so
reopening the inspector does not pay for the models again. A bump of
`CACHE_VERSION` throws the cache away.
"""

from __future__ import annotations

import json
import logging
import threading
import wave
from pathlib import Path

import numpy as np

from auto_presenter.music.basic_pitch import PitchRun
from auto_presenter.music.beat_this import iter_transcribe
from auto_presenter.music.layers import (
    BANDS,
    ChordRun,
    band_onsets,
    pulse_from_beats,
    pulse_track,
)
from auto_presenter.music.rhythm import describe_rhythm

log = logging.getLogger(__name__)

# How much audio to finish before publishing the next picture. The models
# are not restarted at this boundary: each one keeps the windows it has
# already run, on the same grid a single full-file pass would use.
CHUNK_S = 30.0

CACHE_VERSION = 3
CACHE_NAME = "music_layers.json"
# A service set fits. A multi-hour recording does not: loading it is already
# several GB in this process, and the client stops waiting at three minutes.
MAX_INSPECT_SECONDS = 45 * 60



def _full_backends(payload: dict) -> bool:
    """Only a result from the full models is worth caching."""
    return payload["beat_backend"] == "beat_this" and payload["pitch_backend"] == "basic_pitch"

def wav_duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as wf:
        rate = wf.getframerate() or 1
        return wf.getnframes() / rate


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        ch = wf.getnchannels()
        sw = wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())
    if sr != 16000:
        raise ValueError(f"{path.name} is {sr} Hz; recordings are 16 kHz")
    if sw == 2:
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sw == 4:
        samples = np.frombuffer(raw, dtype=np.float32).copy()
    else:
        raise ValueError(f"unsupported sample width {sw}")
    if ch > 1:
        samples = samples.reshape(-1, ch).mean(axis=1)
    return np.ascontiguousarray(samples, dtype=np.float32)


def _payload(
    audio: np.ndarray,
    beats: list[float],
    downbeats: list[float],
    beat_backend: str,
    pitch_points: list,
    pitch_notes: list,
    pitch_backend: str,
    chords,
    chord_backend: str,
    onsets: dict[str, list[float]],
    *,
    partial: bool,
    through: float,
    source: str = "mix",
    stage: str = "",
    version: int | None = None,
    duration_seconds: float | None = None,
    bands: tuple | None = None,
) -> dict:
    pulse = pulse_from_beats(beats) if len(beats) >= 3 else (
        [] if partial else pulse_track(audio)
    )
    rhythm, rhythm_bars = describe_rhythm(beats, downbeats, onsets)
    rows = BANDS if bands is None else bands
    dur = len(audio) / 16000 if duration_seconds is None else duration_seconds
    return {
        "version": CACHE_VERSION if version is None else version,
        "source": source,
        "stage": stage,
        "duration_seconds": round(dur, 3),
        "beats": beats,
        "downbeats": downbeats,
        "beat_backend": beat_backend,
        "chords": [
            {"name": c.name, "t_start": c.t_start, "t_end": c.t_end} for c in chords
        ],
        "chord_backend": chord_backend,
        "pulse": [
            {"t_seconds": p.t_seconds, "bpm": p.bpm, "lock": p.lock} for p in pulse
        ],
        "onsets": [
            {"band": name, "hz_lo": lo, "hz_hi": hi, "times": onsets.get(name, [])}
            for name, lo, hi in rows
        ],
        "pitch": [
            {"t_seconds": p.t_seconds, "midi": p.midi, "confidence": p.confidence}
            for p in pitch_points
        ],
        "notes": [
            {
                "t_start": n.t_start,
                "t_end": n.t_end,
                "midi": n.midi,
                "confidence": n.confidence,
            }
            for n in pitch_notes
        ],
        "pitch_backend": pitch_backend,
        "rhythm": [
            {
                "band": e.band,
                "t": e.t,
                "phase": e.phase,
                "kind": e.kind,
                "error_ms": e.error_ms,
                "bar_index": e.bar_index,
                "show": e.show,
            }
            for e in rhythm
        ],
        "rhythm_bars": [
            {
                "band": b.band,
                "bar_index": b.bar_index,
                "t_start": b.t_start,
                "t_end": b.t_end,
                "pattern": b.pattern,
                "stable": b.stable,
                "onset_count": b.onset_count,
                "on_grid": b.on_grid,
            }
            for b in rhythm_bars
        ],
        "partial": partial,
        "through_seconds": round(through, 3),
    }


class _BeatPump:
    """Beat This in its own process, publishing each finished chunk."""

    def __init__(self, wav: Path):
        self._wav = wav
        self._lock = threading.Lock()
        self._state = {
            "beats": [],
            "downbeats": [],
            "through": 0.0,
            "backend": "",
            "done": False,
        }
        self._thread: threading.Thread | None = None
        self._stop = False

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        backend = "failed"
        try:
            for msg in iter_transcribe(self._wav):
                if self._stop:
                    break
                err = msg.get("error")
                if err:
                    backend = str(err)
                    break
                backend = "beat_this"
                with self._lock:
                    self._state = {
                        "beats": [float(t) for t in msg.get("beats", [])],
                        "downbeats": [float(t) for t in msg.get("downbeats", [])],
                        "through": float(msg.get("through", 0)),
                        "backend": backend,
                        "done": not msg.get("partial", False),
                    }
                if not msg.get("partial", False):
                    return
        except Exception:
            log.exception("beat this stream failed")
            backend = "failed"
        with self._lock:
            done = dict(self._state)
            done["backend"] = backend
            done["done"] = True
            self._state = done

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._state)

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def close(self) -> None:
        self._stop = True


class _FixedBeats:
    """A beat grid already computed on the mix, reused by the stem pass."""

    def __init__(self, beats: list[float], downbeats: list[float]) -> None:
        self._state = {
            "beats": [float(t) for t in beats],
            "downbeats": [float(t) for t in downbeats],
            "through": 1e9,
            "backend": "beat_this",
            "done": True,
        }

    def start(self) -> None:
        return None

    def snapshot(self) -> dict:
        return dict(self._state)

    def join(self, timeout: float | None = None) -> None:
        return None

    def close(self) -> None:
        return None


def _upto(times: list[float], through: float, final: bool) -> list[float]:
    if final:
        return times
    return [t for t in times if t <= through + 1e-6]


def _cached(cache: Path, version: int = CACHE_VERSION) -> dict | None:
    if not cache.exists():
        return None
    try:
        cached = json.loads(cache.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if (
        isinstance(cached, dict)
        and cached.get("version") == version
        and cached.get("beat_backend") == "beat_this"
        and cached.get("pitch_backend") == "basic_pitch"
    ):
        cached["partial"] = False
        cached["through_seconds"] = cached.get("duration_seconds", 0)
        return cached
    return None


def _require_wav(recording_dir: Path) -> Path:
    wav = recording_dir / "audio.wav"
    if not wav.exists():
        raise FileNotFoundError(f"no audio.wav in {recording_dir}")
    seconds = wav_duration_seconds(wav)
    if seconds > MAX_INSPECT_SECONDS:
        minutes = MAX_INSPECT_SECONDS // 60
        raise ValueError(
            f"recording is {seconds / 60:.0f} min; music analysis stops at {minutes} min"
        )
    return wav


def iter_analyse(recording_dir: Path):
    """Yield a growing prefix of the analysis, then one complete result.

    Onsets are measured on the whole file first — that pass is cheap, and
    the peak threshold depends on the whole envelope, so slicing it would
    change which ticks survive. Pitch and chords then advance in 30 s steps
    along the same windows a one-shot run uses. Beats arrive from Beat This's
    own chunking, in a side process, and are included as far as that process
    has committed.
    """
    wav = _require_wav(recording_dir)
    cache = recording_dir / CACHE_NAME
    cached = _cached(cache)
    if cached is not None:
        yield cached
        return

    audio = load_wav(wav)
    yield from _iter_layers(
        audio,
        pitch_audio=audio,
        chord_audio=audio,
        beat_wav=wav,
        onsets_all=band_onsets(audio),
        cache=cache,
        source="mix",
    )


# Stem pictures are a different file from the mix cache, so opening Stems
# does not throw away a mix that is already drawn.
STEM_CACHE_NAME = "music_stems.json"
STEM_CACHE_VERSION = 3


def iter_analyse_stems(recording_dir: Path):
    """Separate the mix, then run chords and pitch on the stems.

    Beats and downbeats stay on the full mix. Beat This drops most of the
    beats, and the bar, when it only hears the drum stem. Chords listen to
    the harmonic leftover and pitch listens to the vocals.
    """
    from auto_presenter.music.layers import signal_onsets
    from auto_presenter.music.realtime_stems import (
        STEM_DIR,
        STEMS,
        RealtimeStems,
        StemUnavailable,
        read_stem_wavs,
        write_stem_wavs,
    )

    wav = _require_wav(recording_dir)
    cache = recording_dir / STEM_CACHE_NAME
    cached = _cached(cache, STEM_CACHE_VERSION)
    if cached is not None:
        yield cached
        return

    mix_cached = _cached(recording_dir / CACHE_NAME)
    legacy = _older_stem_cache(cache)
    if legacy is not None and mix_cached and mix_cached.get("beats"):
        upgraded = _with_mix_beats(legacy, mix_cached)
        cache.write_text(json.dumps(upgraded))
        yield upgraded
        return

    audio = load_wav(wav)
    duration = len(audio) / 16000 if len(audio) else 0.0
    stem_dir = recording_dir / STEM_DIR
    loaded = read_stem_wavs(stem_dir)
    if loaded is None:
        session = RealtimeStems()
        try:
            session.start()
        except StemUnavailable as exc:
            yield {
                "duration_seconds": round(duration, 3),
                "source": "stems",
                "stage": "",
                "partial": False,
                "through_seconds": 0,
                "error": str(exc),
            }
            return
        buckets: dict[str, list[np.ndarray]] = {name: [] for name in STEMS}
        try:
            step = 16000 * 2
            for i in range(0, len(audio), step):
                new = session.push(audio[i : i + step])
                for name, samples in new.items():
                    if len(samples):
                        buckets[name].append(samples)
                through = session.through_seconds
                if any(len(samples) for samples in new.values()):
                    yield _stem_progress(duration, through)
            tail = session.finish()
            for name, samples in tail.items():
                if len(samples):
                    buckets[name].append(samples)
        finally:
            session.close()
        loaded = {
            name: np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)
            for name, parts in buckets.items()
        }
        write_stem_wavs(stem_dir, loaded)
    bands = tuple((name, 0.0, 0.0) for name in STEMS)
    onsets = {name: signal_onsets(loaded[name]) for name in STEMS}
    mix_beats = list(mix_cached.get("beats") or []) if mix_cached else []
    beat_grid = (
        (mix_beats, list(mix_cached.get("downbeats") or [])) if mix_beats else None
    )
    yield from _iter_layers(
        loaded["drums"],
        pitch_audio=loaded["vocals"],
        chord_audio=loaded["other"],
        beat_wav=wav,
        beat_grid=beat_grid,
        onsets_all=onsets,
        cache=cache,
        source="stems",
        duration_seconds=duration,
        bands=bands,
        version=STEM_CACHE_VERSION,
    )


def _older_stem_cache(cache: Path) -> dict | None:
    """A finished stem picture from before beats were taken off the drum stem."""
    if not cache.exists():
        return None
    try:
        data = json.loads(cache.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("version") == STEM_CACHE_VERSION:
        return None
    if data.get("source") != "stems" or data.get("pitch_backend") != "basic_pitch":
        return None
    if not data.get("onsets"):
        return None
    return data


def _with_mix_beats(stem: dict, mix: dict) -> dict:
    """Keep the stem detectors and replace the beat grid with the mix's."""
    beats = [float(t) for t in mix.get("beats") or []]
    downbeats = [float(t) for t in mix.get("downbeats") or []]
    onsets = {
        str(row["band"]): [float(t) for t in row.get("times") or []]
        for row in stem.get("onsets") or []
    }
    rhythm, bars = describe_rhythm(beats, downbeats, onsets)
    pulse = pulse_from_beats(beats) if len(beats) >= 3 else []
    out = dict(stem)
    out["version"] = STEM_CACHE_VERSION
    out["beats"] = beats
    out["downbeats"] = downbeats
    out["beat_backend"] = "beat_this"
    out["pulse"] = [
        {"t_seconds": p.t_seconds, "bpm": p.bpm, "lock": p.lock} for p in pulse
    ]
    out["rhythm"] = [
        {
            "band": e.band,
            "t": e.t,
            "phase": e.phase,
            "kind": e.kind,
            "error_ms": e.error_ms,
            "bar_index": e.bar_index,
            "show": e.show,
        }
        for e in rhythm
    ]
    out["rhythm_bars"] = [
        {
            "band": b.band,
            "bar_index": b.bar_index,
            "t_start": b.t_start,
            "t_end": b.t_end,
            "pattern": b.pattern,
            "stable": b.stable,
            "onset_count": b.onset_count,
            "on_grid": b.on_grid,
        }
        for b in bars
    ]
    out["partial"] = False
    out["stage"] = ""
    out["through_seconds"] = out.get("duration_seconds", 0)
    return out


def _stem_progress(duration: float, through: float) -> dict:
    from auto_presenter.music.realtime_stems import STEMS

    return {
        "duration_seconds": round(duration, 3),
        "source": "stems",
        "stage": "separating",
        "partial": True,
        "through_seconds": round(through, 3),
        "onsets": [
            {"band": name, "hz_lo": 0.0, "hz_hi": 0.0, "times": []} for name in STEMS
        ],
    }


def _iter_layers(
    audio: np.ndarray,
    *,
    pitch_audio: np.ndarray,
    chord_audio: np.ndarray,
    beat_wav: Path,
    onsets_all: dict[str, list[float]],
    cache: Path,
    source: str,
    duration_seconds: float | None = None,
    bands: tuple | None = None,
    version: int | None = None,
    beat_grid: tuple[list[float], list[float]] | None = None,
):
    duration = len(audio) / 16000 if duration_seconds is None else duration_seconds
    pitch = PitchRun(pitch_audio)
    chords = ChordRun(chord_audio)
    # The stem path passes the mix's grid when that picture is already
    # cached. Otherwise Beat This runs on the mix, never on a stem.
    beats: _BeatPump | _FixedBeats
    if beat_grid is not None:
        beats = _FixedBeats(beat_grid[0], beat_grid[1])
    else:
        beats = _BeatPump(beat_wav)
    beats.start()
    try:
        t = 0.0
        while True:
            t = min(duration, t + CHUNK_S)
            final_slice = t >= duration - 1e-6
            pitch.advance_to(duration if final_slice else t)
            chords.advance_to(duration if final_slice else t)
            snap = beats.snapshot()
            local = duration if final_slice else min(pitch.committed, chords.committed, t)
            beat_done = bool(snap["done"])
            final = final_slice and beat_done
            beat_times = list(snap["beats"])
            down_times = list(snap["downbeats"])
            if not beat_done:
                beat_times = _upto(beat_times, float(snap["through"]), False)
                down_times = _upto(down_times, float(snap["through"]), False)
            shown_onsets = {
                name: _upto(times, local, final) for name, times in onsets_all.items()
            }
            payload = _payload(
                audio,
                beat_times,
                down_times,
                str(snap["backend"]),
                _upto_points(pitch.points, local, final),
                _upto_notes(pitch.notes, local, final),
                pitch.backend,
                chords.spans,
                chords.backend,
                shown_onsets,
                partial=not final,
                through=duration if final else local,
                source=source,
                stage="" if final else "analysing",
                version=version,
                duration_seconds=duration,
                bands=bands,
            )
            if final and _full_backends(payload):
                cache.write_text(json.dumps(payload))
            yield payload
            if final_slice and beat_done:
                break
            if final_slice:
                # Pitch and chords are done. Keep publishing the beat prefix
                # until the tracker itself finishes.
                while not beats.snapshot()["done"]:
                    beats.join(timeout=5)
                    snap = beats.snapshot()
                    if snap["done"]:
                        break
                    waiting = _payload(
                        audio,
                        _upto(list(snap["beats"]), float(snap["through"]), False),
                        _upto(list(snap["downbeats"]), float(snap["through"]), False),
                        str(snap["backend"]),
                        pitch.points,
                        pitch.notes,
                        pitch.backend,
                        chords.spans,
                        chords.backend,
                        onsets_all,
                        partial=True,
                        through=min(duration, float(snap["through"])),
                        source=source,
                        stage="analysing",
                        version=version,
                        duration_seconds=duration,
                        bands=bands,
                    )
                    yield waiting
                snap = beats.snapshot()
                beat_times = list(snap["beats"])
                if not beat_times and snap["backend"] != "beat_this":
                    # No grid arrived. The onset pulse is the fallback, and it
                    # is only computed once, for the finished file.
                    pass
                payload = _payload(
                    audio,
                    beat_times,
                    list(snap["downbeats"]),
                    str(snap["backend"]),
                    pitch.points,
                    pitch.notes,
                    pitch.backend,
                    chords.spans,
                    chords.backend,
                    onsets_all,
                    partial=False,
                    through=duration,
                    source=source,
                    stage="",
                    version=version,
                    duration_seconds=duration,
                    bands=bands,
                )
                if _full_backends(payload):
                    cache.write_text(json.dumps(payload))
                yield payload
                break
    finally:
        beats.close()


def _upto_points(points: list, through: float, final: bool) -> list:
    if final:
        return points
    return [p for p in points if p.t_seconds <= through + 1e-6]


def _upto_notes(notes: list, through: float, final: bool) -> list:
    if final:
        return notes
    return [n for n in notes if n.t_start <= through + 1e-6]


def analyse_recording(recording_dir: Path) -> dict:
    """Analyse `audio.wav`, using a sibling cache when it is still current."""
    last = None
    for payload in iter_analyse(recording_dir):
        last = payload
    if last is None:
        raise RuntimeError(f"no analysis for {recording_dir}")
    return last
