"""Per-band rhythm: which part of the spectrum is pulsing, and at what grain.

Isolated spike. Not wired into the sidecar. The question it exists to answer,
before any fingerprint is built, is whether a human can look at the output and
recognise a kick on the beat, a strum on the eighths, and a shaker on the
sixteenths as three different rows.

Worship songs reuse the same chords in the verse and the chorus, which is why
mix-level chroma could not tell those sections apart at a window a live system
can afford (53% forced-choice at 4 s; docs/research/MUSIC_FINGERPRINTING.md). The
arrangement usually still changes: a kick enters, a guitar starts a pattern, a
shaker doubles. This spike measures that change as a pulse *inside a frequency
band*, not as pitch content of the whole mix.

Run the synthetic check before trusting any recording number. A 16th-note
pattern also autocorrelates at the beat, so the grain is the *finest*
subdivision that locks, not the strongest peak.

    uv run --project sidecar python research/spikes/spike_band_rhythm.py --verify
    uv run --project sidecar python research/spikes/spike_band_rhythm.py --song

`--song` writes a compact JSON summary of one approved golden to stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "sidecar"))

SR = 16000
N_FFT = 2048
HOP = 512  # 32 ms, 31.25 frames/s
FPS = SR / HOP

# Nyquist at 16 kHz is 8 kHz. Hats above that are already gone in these
# recordings (docs/research/MUSIC_MODELS_2026.md); "air" is the band we still have.
BANDS = (
    ("kick", 30.0, 90.0),
    ("bass", 90.0, 250.0),
    ("body", 250.0, 700.0),
    ("strum", 700.0, 2500.0),
    ("air", 2500.0, 7500.0),
)

# Period of each grid mark, in beats, assuming the global tempo is the quarter.
GRAINS = (
    ("bar", 4.0),
    ("half", 2.0),
    ("beat", 1.0),
    ("8th", 0.5),
    ("16th", 0.25),
)

# Finest grain whose autocorr is at least this fraction of the best grain.
FINE_FRACTION = 0.72
# And at least this absolute autocorr, or the band is "unpulsed".
MIN_LOCK = 0.28

# Worship ballads sit near 60 BPM. A floor of 70 forces those songs onto a
# double-time reading and then mis-names every subdivision.
TEMPO_MIN = 50.0
TEMPO_MAX = 180.0


def stft_mag(audio: np.ndarray) -> np.ndarray:
    """Magnitude spectrogram, shape (frames, n_fft/2+1)."""
    x = np.ascontiguousarray(audio, dtype=np.float32)
    if len(x) < N_FFT:
        x = np.pad(x, (0, N_FFT - len(x)))
    n = 1 + (len(x) - N_FFT) // HOP
    frames = np.lib.stride_tricks.as_strided(
        x,
        shape=(n, N_FFT),
        strides=(x.strides[0] * HOP, x.strides[0]),
    )
    window = np.hanning(N_FFT).astype(np.float32)
    return np.abs(np.fft.rfft(frames * window, axis=1)).astype(np.float32)


def _band_mask(freqs: np.ndarray, lo: float, hi: float) -> np.ndarray:
    return (freqs >= lo) & (freqs < hi)


def onset_env(energy: np.ndarray) -> np.ndarray:
    """Half-wave rectified log-energy flux, lightly smoothed."""
    loge = np.log1p(energy.astype(np.float64))
    delta = np.diff(loge, prepend=loge[0])
    delta[delta < 0] = 0.0
    kernel = np.array([0.25, 0.5, 0.25])
    return np.convolve(delta, kernel, mode="same")


def _autocorr(env: np.ndarray) -> np.ndarray:
    x = env - env.mean()
    n = len(x)
    spec = np.fft.rfft(x, n=2 * n)
    ac = np.fft.irfft(spec * np.conj(spec), n=2 * n)[:n]
    if ac[0] <= 1e-12:
        return np.zeros(n)
    return ac / ac[0]


def _lag_bpm(ac: np.ndarray, lag: int, fps: float) -> tuple[float, float]:
    """BPM at `lag`, with a parabolic tweak so 120 BPM is not read as 117."""
    if lag <= 0 or lag >= len(ac):
        return 0.0, 0.0
    if 0 < lag < len(ac) - 1:
        a, b, c = ac[lag - 1], ac[lag], ac[lag + 1]
        denom = a - 2 * b + c
        delta = 0.0 if abs(denom) < 1e-9 else 0.5 * (a - c) / denom
        delta = float(np.clip(delta, -0.5, 0.5))
    else:
        delta = 0.0
    return 60.0 * fps / (lag + delta), float(ac[lag])


def _tempo_lags(ac: np.ndarray, fps: float) -> tuple[int, int] | None:
    min_lag = max(2, int(round(fps * 60.0 / TEMPO_MAX)))
    max_lag = min(len(ac) - 1, int(round(fps * 60.0 / TEMPO_MIN)))
    if max_lag <= min_lag:
        return None
    return min_lag, max_lag


def tempo_bpm(env: np.ndarray, fps: float = FPS) -> tuple[float, float]:
    """Tactus inside the tempo band.

    The raw autocorrelation peak prefers the longer lag: a steady quarter-note
    grid also lines up every two beats, and that half-tempo peak is often
    taller. Walk toward double-time while that lag stays nearly as strong.
    A kick with nothing between the beats stays at the slow tempo, because
    the halfway lag is weak. Returns (bpm, autocorr). A flat envelope
    returns (0, 0).
    """
    ac = _autocorr(env)
    bounds = _tempo_lags(ac, fps)
    if bounds is None:
        return 0.0, 0.0
    min_lag, max_lag = bounds
    lag = int(np.argmax(ac[min_lag:max_lag])) + min_lag
    while True:
        finer = max(min_lag, lag // 2)
        if finer >= lag:
            break
        # Local max near the halfway lag, so we do not sample a trough.
        lo = max(min_lag, finer - 1)
        hi = min(max_lag, finer + 2)
        finer = int(np.argmax(ac[lo:hi])) + lo
        if ac[finer] < 0.8 * ac[lag]:
            break
        lag = finer
    bpm, lock = _lag_bpm(ac, lag, fps)
    return float(bpm), lock


def tempo_octaves(env: np.ndarray, fps: float = FPS) -> list[dict]:
    """Chosen tactus, plus the half- and double-time lags around it.

    A 68 BPM ballad and its 136 BPM eighth-note grid both live in the search
    band. The grain names hang on whichever one was chosen, so the other
    octave's lock has to be visible or the labels look more certain than
    they are.
    """
    bpm, _lock = tempo_bpm(env, fps)
    if bpm <= 0:
        return []
    ac = _autocorr(env)
    bounds = _tempo_lags(ac, fps)
    if bounds is None:
        return []
    min_lag, max_lag = bounds
    lag = int(round(fps * 60.0 / bpm))
    rows = []
    for name, candidate in (("half", lag * 2), ("locked", lag), ("double", max(min_lag, lag // 2))):
        if candidate < min_lag or candidate > max_lag or candidate >= len(ac):
            continue
        cand_bpm, cand_lock = _lag_bpm(ac, candidate, fps)
        rows.append({"which": name, "bpm": round(cand_bpm, 1), "lock": round(cand_lock, 3)})
    return rows


def grain_scores(env: np.ndarray, bpm: float, fps: float = FPS) -> dict[str, float]:
    """Autocorr of the onset envelope at each subdivision of `bpm`."""
    ac = _autocorr(env)
    beat_lag = fps * 60.0 / bpm
    scores: dict[str, float] = {}
    for name, beats in GRAINS:
        lag = int(round(beat_lag * beats))
        if lag < 2 or lag >= len(ac):
            scores[name] = 0.0
            continue
        lo = max(1, lag - 1)
        hi = min(len(ac), lag + 2)
        scores[name] = float(ac[lo:hi].max())
    return scores


def finest_grain(scores: dict[str, float]) -> str:
    """Finest subdivision that locks, not the tallest peak.

    A steady 16th-note shaker also lines up on the beat, so argmax(score)
    often says "beat" for every band. The grain is the shortest period that
    is nearly as strong as the best period. Below MIN_LOCK the band has no
    grid worth naming.
    """
    best = max(scores.values()) if scores else 0.0
    if best < MIN_LOCK:
        return "none"
    threshold = FINE_FRACTION * best
    for name, _beats in reversed(GRAINS):
        if scores.get(name, 0.0) >= threshold:
            return name
    return "none"


def analyse_layers(audio: np.ndarray) -> dict:
    """Band energies, onset envelopes, and a single-clip grain reading.

    `audio` is mono float at `SR`. Short clips (the synthetic check) are
    scored as one window. Callers that want a timeline slice the audio.
    """
    mag = stft_mag(audio)
    freqs = np.fft.rfftfreq(N_FFT, 1.0 / SR)
    energies: dict[str, np.ndarray] = {}
    onsets: dict[str, np.ndarray] = {}
    for name, lo, hi in BANDS:
        energy = mag[:, _band_mask(freqs, lo, hi)].sum(axis=1)
        energies[name] = energy
        onsets[name] = onset_env(energy)

    mixed = np.sum([onsets[name] for name, *_ in BANDS], axis=0)
    bpm, tempo_lock = tempo_bpm(mixed)
    layers = []
    if bpm <= 0:
        for name, lo, hi in BANDS:
            layers.append({
                "band": name,
                "hz": [lo, hi],
                "grain": "none",
                "scores": {g: 0.0 for g, _ in GRAINS},
                "energy": float(energies[name].mean()),
            })
        return {"bpm": 0.0, "tempo_lock": 0.0, "layers": layers, "fps": FPS}

    for name, lo, hi in BANDS:
        scores = grain_scores(onsets[name], bpm)
        layers.append({
            "band": name,
            "hz": [lo, hi],
            "grain": finest_grain(scores),
            "scores": {k: round(v, 3) for k, v in scores.items()},
            "energy": float(energies[name].mean()),
        })
    return {
        "bpm": round(bpm, 1),
        "tempo_lock": round(tempo_lock, 3),
        "layers": layers,
        "fps": FPS,
    }


def _burst(lo: float, hi: float, dur_s: float = 0.03) -> np.ndarray:
    n = int(SR * dur_s)
    noise = np.random.default_rng(0).standard_normal(n).astype(np.float32)
    spec = np.fft.rfft(noise)
    freqs = np.fft.rfftfreq(n, 1.0 / SR)
    spec[(freqs < lo) | (freqs >= hi)] = 0
    y = np.fft.irfft(spec, n=n).astype(np.float32)
    y *= np.linspace(1.0, 0.0, n, dtype=np.float32) ** 2
    peak = float(np.max(np.abs(y))) or 1.0
    return y / peak


def _place(canvas: np.ndarray, burst: np.ndarray, times: np.ndarray) -> None:
    for t in times:
        i = int(t * SR)
        j = min(len(canvas), i + len(burst))
        if i >= len(canvas) or j <= i:
            continue
        canvas[i:j] += burst[: j - i]


def synthetic_mix(bpm: float = 120.0, bars: int = 8) -> np.ndarray:
    """Kick on quarters, strum on eighths, shaker on sixteenths, plus a tone.

    The tone is there so the body band is not empty, and it must *not* be
    labelled as a 16th-note grid — it has no attack pattern.
    """
    beat = 60.0 / bpm
    duration = bars * 4 * beat
    n = int(SR * duration)
    audio = np.zeros(n, dtype=np.float32)
    beats = np.arange(0.0, duration - 0.05, beat)
    _place(audio, _burst(30, 90, 0.05) * 1.0, beats)
    _place(audio, _burst(700, 2500, 0.025) * 0.7, np.arange(0.0, duration - 0.05, beat / 2))
    _place(audio, _burst(2500, 7500, 0.012) * 0.45, np.arange(0.0, duration - 0.05, beat / 4))
    t = np.arange(n, dtype=np.float32) / SR
    # A held pitch in the body band, no percussive envelope.
    audio += 0.15 * np.sin(2 * np.pi * 330.0 * t)
    return audio


def _check_mix(audio: np.ndarray, bpm_range: tuple[float, float], expected: dict[str, str]) -> dict:
    reading = analyse_layers(audio)
    rows = []
    failures = []
    for layer in reading["layers"]:
        want = expected.get(layer["band"])
        ok = want is None or layer["grain"] == want
        rows.append({
            "band": layer["band"],
            "hz": layer["hz"],
            "expected": want or "(no grid required)",
            "grain": layer["grain"],
            "ok": ok,
            "scores": layer["scores"],
        })
        if not ok:
            failures.append(f"{layer['band']}: expected {want}, got {layer['grain']}")
    if not (bpm_range[0] <= reading["bpm"] <= bpm_range[1]):
        failures.append(
            f"tempo {reading['bpm']} BPM, expected {bpm_range[0]:.0f}–{bpm_range[1]:.0f}"
        )
    return {
        "bpm": reading["bpm"],
        "tempo_lock": reading["tempo_lock"],
        "rows": rows,
        "failures": failures,
    }


def verify() -> list[dict]:
    """Grids must survive both a driving mix and a slow kick with gaps."""
    driving = _check_mix(
        synthetic_mix(),
        (118.0, 122.0),
        {"kick": "beat", "strum": "8th", "air": "16th"},
    )
    # A ballad kick, nothing on the offbeats. The half-tempo trap would call
    # this 120 BPM and label the kick as an eighth. It has to stay a beat.
    beat = 60.0 / 60.0
    duration = 8 * 4 * beat
    slow = np.zeros(int(SR * duration), dtype=np.float32)
    _place(slow, _burst(30, 90, 0.05), np.arange(0.0, duration - 0.05, beat))
    ballad = _check_mix(slow, (58.0, 62.0), {"kick": "beat"})
    driving["name"] = "driving mix at 120"
    ballad["name"] = "ballad kick at 60"
    return [driving, ballad]


def _load_wav_mono(path: Path) -> np.ndarray:
    """16-bit or float WAV, downmixed, resampled only if it is already 16 kHz.

    Goldens are 16 kHz mono. Anything else is refused rather than silently
    resampled — upsampling cannot restore the hats these bands are missing.
    """
    import wave

    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        ch = wf.getnchannels()
        sw = wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())
    if sr != SR:
        raise SystemExit(f"{path} is {sr} Hz; this spike expects {SR} Hz goldens")
    if sw == 2:
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sw == 4:
        samples = np.frombuffer(raw, dtype=np.float32)
    else:
        raise SystemExit(f"unsupported sample width {sw}")
    if ch > 1:
        samples = samples.reshape(-1, ch).mean(axis=1)
    return np.ascontiguousarray(samples, dtype=np.float32)


def timeline(audio: np.ndarray, win_s: float = 6.0, hop_s: float = 2.0) -> dict:
    """Grain and pulse strength of each band along the recording.

    The tempo used for the grid is estimated once, from the whole clip's
    onset mixture, so a locally quiet verse is not allowed to invent a
    different BPM and then mis-label its own subdivisions.
    """
    mag = stft_mag(audio)
    freqs = np.fft.rfftfreq(N_FFT, 1.0 / SR)
    onsets = {}
    energy = {}
    for name, lo, hi in BANDS:
        e = mag[:, _band_mask(freqs, lo, hi)].sum(axis=1)
        energy[name] = e
        onsets[name] = onset_env(e)
    mixed = np.sum(list(onsets.values()), axis=0)
    bpm, tempo_lock = tempo_bpm(mixed)
    octaves = tempo_octaves(mixed)
    frame_hop = int(round(hop_s * FPS))
    frame_win = int(round(win_s * FPS))
    n = len(mixed)
    times = []
    series: dict[str, list] = {name: [] for name, *_ in BANDS}
    grains: dict[str, list] = {name: [] for name, *_ in BANDS}
    levels: dict[str, list] = {name: [] for name, *_ in BANDS}
    i = 0
    while i + frame_win <= n:
        t = (i + frame_win / 2) / FPS
        times.append(round(t, 2))
        for name, *_ in BANDS:
            window = onsets[name][i:i + frame_win]
            if bpm <= 0:
                series[name].append(0.0)
                grains[name].append("none")
            else:
                scores = grain_scores(window, bpm)
                series[name].append(round(max(scores.values()), 3))
                grains[name].append(finest_grain(scores))
            levels[name].append(round(float(energy[name][i:i + frame_win].mean()), 5))
        i += frame_hop
    return {
        "bpm": round(bpm, 1),
        "tempo_lock": round(tempo_lock, 3),
        "octaves": octaves,
        "win_s": win_s,
        "hop_s": hop_s,
        "times": times,
        "pulse": series,
        "grain": grains,
        "level": levels,
    }


def onset_times(audio: np.ndarray, t0: float, t1: float) -> dict[str, list[float]]:
    """Peak times of each band's onset envelope inside [t0, t1]."""
    sl = audio[int(t0 * SR):int(t1 * SR)]
    mag = stft_mag(sl)
    freqs = np.fft.rfftfreq(N_FFT, 1.0 / SR)
    out: dict[str, list[float]] = {}
    for name, lo, hi in BANDS:
        env = onset_env(mag[:, _band_mask(freqs, lo, hi)].sum(axis=1))
        if env.max() <= 0:
            out[name] = []
            continue
        thresh = float(np.median(env) + np.std(env))
        times = []
        for i in range(1, len(env) - 1):
            if env[i] >= thresh and env[i] >= env[i - 1] and env[i] > env[i + 1]:
                times.append(round(t0 + i / FPS, 3))
        out[name] = times
    return out


def song_summary(recording_id: str) -> dict:
    root = Path.home() / "Library/Application Support/com.churchslides.app/recordings"
    wav = root / recording_id / "audio.wav"
    audio = _load_wav_mono(wav)
    # Cap the timeline at 3 minutes so the whiteboard stays readable. The
    # opening is where a worship arrangement usually goes sparse-to-full.
    clip = audio[: int(SR * 180)]
    track = timeline(clip)
    # Detail window: the 12 s where kick pulse rises the most. That is the
    # moment a human should be able to see a new grid appear.
    kick = np.array(track["pulse"]["kick"], dtype=np.float64)
    if len(kick) >= 2:
        rise_at = int(np.argmax(np.diff(kick))) + 1
        centre = track["times"][rise_at]
    else:
        centre = 30.0
    t0 = max(0.0, centre - 6.0)
    t1 = t0 + 12.0
    return {
        "recording_id": recording_id,
        "song": "What A Beautiful Name",
        "duration_s": round(len(audio) / SR, 1),
        "shown_s": round(len(clip) / SR, 1),
        "global_bpm": track["bpm"],
        "tempo_lock": track["tempo_lock"],
        "octaves": track["octaves"],
        "times": track["times"],
        "pulse": track["pulse"],
        "grain": track["grain"],
        "detail": {
            "t0": round(t0, 2),
            "t1": round(t1, 2),
            "why": "12 s centred on the largest rise in kick-band pulse strength",
            "onsets": onset_times(clip, t0, t1),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="synthetic grid check")
    parser.add_argument(
        "--song",
        nargs="?",
        const="rec_20260818-214759_057",
        help="timeline JSON for a golden (default: What A Beautiful Name)",
    )
    args = parser.parse_args()
    if not args.verify and args.song is None:
        parser.error("pass --verify, --song, or both")

    if args.verify:
        failed = False
        for report in verify():
            print(f"{report['name']}: tempo {report['bpm']} BPM (lock {report['tempo_lock']})")
            for row in report["rows"]:
                mark = "ok" if row["ok"] else "FAIL"
                print(f"  {mark:4} {row['band']:6} expected {row['expected']:22} got {row['grain']:5}  {row['scores']}")
            for failure in report["failures"]:
                print("FAIL", failure, file=sys.stderr)
                failed = True
        if failed:
            sys.exit(1)
        print("verify passed")

    if args.song is not None:
        summary = song_summary(args.song)
        out = Path("/tmp/spike_band_rhythm.json")
        out.write_text(json.dumps(summary))
        grains_at_end = {
            band: summary["grain"][band][-1] if summary["grain"][band] else "none"
            for band in summary["grain"]
        }
        print(
            f"song {summary['song']}  {summary['global_bpm']} BPM  "
            f"detail {summary['detail']['t0']}–{summary['detail']['t1']}s  "
            f"last grains {grains_at_end}"
        )
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
