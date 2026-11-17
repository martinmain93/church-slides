"""Chord changes and downbeats as a *timing* side channel for slide switches.

Spike, 2026-09-08. Nothing here is wired into the sidecar; it imports from
`auto_presenter` read-only. numpy only — no new dependency, no licence question.

The question, in the project's own terms (see Plan/MUSIC_FINGERPRINTING.md and
the goal note "the side channel is for SPEED, not identity"): harmony already
failed to *identify* sections. But a metrical grid is the one signal in this
project that is **causally ahead of the audio** — once tempo and phase are
known, the next downbeat is a time in the future. If slide changes land on
downbeats, the grid tells us *when* to fire something the words already justify.

Stages, each verifiable in isolation via `--verify`:

    audio -> onset envelope -> tempo -> beat grid (DP)      timing
    audio -> log-freq chroma -> chord Viterbi (24 triads)   harmony

NOT IMPLEMENTED: downbeat estimation. An earlier version of this docstring
listed "beats + chord changes -> downbeat phase" as a third stage; it was never
written. Everything metrical measured in this spike is beat-level only, so any
statement here about downbeats comes from the annotated corpora
(spike_metre_corpus.py), never from our audio.

Run:
    uv run --project sidecar python research/spikes/spike_chord_downbeat.py --verify
    uv run --project sidecar python research/spikes/spike_chord_downbeat.py --grid
"""
from __future__ import annotations

import json
import os
import sys
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "sidecar"))

SR = 16000

# Onset front end: 16 ms hop. Beat phase is the quantity of interest and a
# 32 ms hop would put half the measurement error in the analysis grid itself.
ON_NFFT = 1024
ON_HOP = 256
ON_FPS = SR / ON_HOP            # 62.5

# Harmony front end: 256 ms window. Chords move at ~1/s, and a long window is
# what resolves the low notes that decide a triad's root.
CH_NFFT = 4096
CH_HOP = 512
CH_FPS = SR / CH_HOP            # 31.25

RECORDINGS = Path(
    os.path.expanduser("~/Library/Application Support/com.churchslides.app/recordings")
)


# --------------------------------------------------------------------------
# audio
# --------------------------------------------------------------------------

def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        assert w.getframerate() == SR, f"expected {SR} Hz, got {w.getframerate()}"
        assert w.getsampwidth() == 2
        raw = w.readframes(w.getnframes())
    a = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if w.getnchannels() == 2:
        a = a.reshape(-1, 2).mean(axis=1)
    return a


def _stft_mag(audio: np.ndarray, n_fft: int, hop: int) -> np.ndarray:
    """(frames, bins) magnitude. Plain framing — no centring, so frame i starts
    at sample i*hop and its *centre* is at (i*hop + n_fft/2)/SR seconds."""
    n = 1 + max(0, (len(audio) - n_fft) // hop)
    win = np.hanning(n_fft).astype(np.float32)
    idx = np.arange(n_fft)[None, :] + (np.arange(n) * hop)[:, None]
    frames = audio[idx] * win
    return np.abs(np.fft.rfft(frames, axis=1))


def frame_times(n_frames: int, n_fft: int, hop: int) -> np.ndarray:
    return (np.arange(n_frames) * hop + n_fft / 2.0) / SR


# --------------------------------------------------------------------------
# onset strength
# --------------------------------------------------------------------------

def onset_envelope(audio: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Half-wave-rectified log-magnitude spectral flux, per Dixon 2006.

    Returns (envelope, times). Log first: a drum hit over a loud band is a
    small *linear* change and a large *ratio*, and worship mixes are loud
    throughout.
    """
    mag = _stft_mag(audio, ON_NFFT, ON_HOP)
    logm = np.log1p(1000.0 * mag)
    diff = np.diff(logm, axis=0, prepend=logm[:1])
    env = np.maximum(diff, 0.0).sum(axis=1)
    # Remove slow drift so a loud chorus does not outweigh a quiet verse.
    env = env - _moving_median(env, int(ON_FPS * 2.0))
    env = np.maximum(env, 0.0)
    return env, frame_times(len(env), ON_NFFT, ON_HOP)


def _moving_median(x: np.ndarray, w: int) -> np.ndarray:
    if w < 3:
        return np.zeros_like(x)
    pad = w // 2
    xp = np.pad(x, pad, mode="edge")
    out = np.empty_like(x)
    # Coarse: median of a strided subsample, adequate for a baseline estimate.
    for i in range(0, len(x), 8):
        out[i:i + 8] = np.median(xp[i:i + w])
    return out


# --------------------------------------------------------------------------
# tempo
# --------------------------------------------------------------------------

TEMPO_MIN, TEMPO_MAX = 50.0, 190.0
TEMPO_PRIOR_BPM = 100.0
TEMPO_PRIOR_OCTAVES = 1.0


def estimate_tempo(env: np.ndarray, fps: float = ON_FPS) -> tuple[float, np.ndarray]:
    """Global tempo (BPM) by autocorrelation with a log-normal prior.

    The prior is what stops the octave error that dominates untutored tempo
    estimation — a 70 BPM ballad autocorrelates just as well at 140.
    """
    x = env - env.mean()
    ac = np.correlate(x, x, mode="full")[len(x) - 1:]
    ac /= max(ac[0], 1e-9)
    lags = np.arange(len(ac)) / fps
    lo, hi = 60.0 / TEMPO_MAX, 60.0 / TEMPO_MIN
    ok = (lags >= lo) & (lags <= hi)
    bpm = np.full(len(ac), np.nan)
    bpm[ok] = 60.0 / lags[ok]
    prior = np.zeros(len(ac))
    prior[ok] = np.exp(-0.5 * (np.log2(bpm[ok] / TEMPO_PRIOR_BPM) / TEMPO_PRIOR_OCTAVES) ** 2)
    scored = ac * prior
    scored[~ok] = -np.inf
    return float(60.0 / lags[int(np.argmax(scored))]), scored


# --------------------------------------------------------------------------
# beat grid (Ellis 2007 dynamic programming)
# --------------------------------------------------------------------------

def track_beats(env: np.ndarray, bpm: float, fps: float = ON_FPS,
                tightness: float = 100.0) -> np.ndarray:
    """Beat frame indices maximising onset strength + tempo regularity.

        C[t] = O[t] + max_tau ( C[tau] - tightness * log(period / (t-tau))^2 )

    A global optimum over the whole recording, so a few bars of ambiguity are
    resolved by the bars either side rather than by a local peak.
    """
    period = 60.0 / bpm * fps
    o = env / max(env.std(), 1e-9)
    n = len(o)
    lo = max(1, int(period * 0.5))
    hi = min(n - 1, int(period * 2.0))
    offsets = np.arange(lo, hi + 1)
    penalty = -tightness * np.log(period / offsets) ** 2

    cum = np.full(n, -np.inf)
    back = np.full(n, -1, dtype=int)
    cum[0] = o[0]
    for t in range(1, n):
        a = t - offsets
        valid = a >= 0
        if not valid.any():
            cum[t] = o[t]
            back[t] = -1
            continue
        cand = cum[a[valid]] + penalty[valid]
        j = int(np.argmax(cand))
        cum[t] = o[t] + cand[j]
        back[t] = a[valid][j]

    # Start from a strong late peak rather than the global argmax of cum,
    # which trivially sits at the end of the recording.
    tail = cum[-int(period * 4):]
    end = n - int(period * 4) + int(np.argmax(tail))
    beats = []
    t = end
    while t >= 0:
        beats.append(t)
        t = back[t]
    return np.array(sorted(beats))


def beat_times(beat_frames: np.ndarray) -> np.ndarray:
    return (beat_frames * ON_HOP + ON_NFFT / 2.0) / SR


# --------------------------------------------------------------------------
# chroma + chords
# --------------------------------------------------------------------------

PITCH_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _log_freq_weights(n_fft: int = CH_NFFT) -> np.ndarray:
    """(12, bins) — energy of each pitch class, summed over octaves A1..B6,
    each note collected from a +/- 50 cent band around its centre."""
    freqs = np.fft.rfftfreq(n_fft, 1.0 / SR)
    w = np.zeros((12, len(freqs)), dtype=np.float32)
    for midi in range(33, 96):                       # A1 .. B6
        f0 = 440.0 * 2 ** ((midi - 69) / 12.0)
        band = (freqs > f0 * 2 ** (-0.5 / 12)) & (freqs < f0 * 2 ** (0.5 / 12))
        if band.any():
            # Flatten the spectral tilt so bass notes do not swamp the triad.
            w[midi % 12, band] += 1.0 / band.sum()
    return w


_CH_W = _log_freq_weights()


def chromagram(audio: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mag = _stft_mag(audio, CH_NFFT, CH_HOP)
    ch = (mag @ _CH_W.T).astype(np.float32)
    ch = ch / np.maximum(np.linalg.norm(ch, axis=1, keepdims=True), 1e-9)
    return ch, frame_times(len(ch), CH_NFFT, CH_HOP)


def _chord_templates() -> tuple[np.ndarray, list[str]]:
    """24 triads. Bass note weighted double — the root is what names a chord,
    and a plain binary mask cannot tell C major from A minor's upper voices."""
    rows, names = [], []
    for root in range(12):
        for third, suffix in ((4, ""), (3, "m")):
            v = np.zeros(12, dtype=np.float32)
            v[root] = 2.0
            v[(root + third) % 12] = 1.0
            v[(root + 7) % 12] = 1.0
            rows.append(v / np.linalg.norm(v))
            names.append(PITCH_NAMES[root] + suffix)
    return np.array(rows), names


CHORD_TEMPLATES, CHORD_NAMES = _chord_templates()


def chord_sequence(chroma: np.ndarray, self_bonus: float = 0.30) -> np.ndarray:
    """Viterbi over 24 triads. `self_bonus` is a duration prior: chords last
    about a bar, so an unsmoothed argmax flickers on every passing note."""
    emis = chroma @ CHORD_TEMPLATES.T
    n, k = emis.shape
    cum = emis[0].copy()
    back = np.zeros((n, k), dtype=np.int16)
    for t in range(1, n):
        best_prev = int(np.argmax(cum))
        stay = cum + self_bonus
        move = np.full(k, cum[best_prev])
        take_stay = stay > move
        back[t] = np.where(take_stay, np.arange(k), best_prev)
        cum = np.where(take_stay, stay, move) + emis[t]
    path = np.zeros(n, dtype=np.int16)
    path[-1] = int(np.argmax(cum))
    for t in range(n - 1, 0, -1):
        path[t - 1] = back[t, path[t]]
    return path


def chord_change_times(path: np.ndarray, times: np.ndarray) -> np.ndarray:
    idx = np.flatnonzero(np.diff(path) != 0) + 1
    return times[idx]


# --------------------------------------------------------------------------
# recordings
# --------------------------------------------------------------------------

@dataclass
class Rec:
    rid: str
    dir: Path
    audio: np.ndarray
    cues: list[dict]
    words: list[dict]
    setlist: list[dict]


def approved_ids() -> list[str]:
    out = []
    for d in sorted(RECORDINGS.glob("rec_*")):
        g = d / "golden.json"
        if g.exists() and json.loads(g.read_text()).get("approved"):
            out.append(d.name)
    return out


def load_rec(rid: str) -> Rec:
    d = RECORDINGS / rid
    golden = json.loads((d / "golden.json").read_text())
    rj = json.loads((d / "recording.json").read_text())
    tj = json.loads((d / "transcript.json").read_text()) if (d / "transcript.json").exists() else {}
    return Rec(
        rid=rid,
        dir=d,
        audio=load_wav(d / "audio.wav"),
        cues=golden.get("ground_truth", []),
        words=tj.get("words", []),
        setlist=rj.get("setlist_content", []),
    )


# --------------------------------------------------------------------------
# verification — every stage against a signal whose answer is known
# --------------------------------------------------------------------------

def _click_track(bpm: float, secs: float, jitter: float = 0.0,
                 seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    a = rng.normal(0, 0.002, int(secs * SR)).astype(np.float32)
    period = 60.0 / bpm
    ts = []
    t = 0.5
    while t < secs - 0.5:
        tt = t + (rng.normal(0, jitter) if jitter else 0.0)
        i = int(tt * SR)
        env = np.exp(-np.arange(1200) / 120.0)
        a[i:i + 1200] += (rng.normal(0, 1, 1200) * env).astype(np.float32) * 0.5
        ts.append(tt)
        t += period
    return a, np.array(ts)


def _drum_loop(bpm: float, secs: float, jitter: float = 0.0,
               seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """A rigid but *dense* metrical signal: kick, snare, eighth hats, chord pad.

    A bare click track is the wrong control for anything scored as a ratio of
    onset strength, because its envelope is zero between clicks and both the
    observed and null terms collapse toward zero. This has continuous content
    the way a band does, while still being exactly metronomic.
    """
    rng = np.random.default_rng(seed)
    n = int(secs * SR)
    a = rng.normal(0, 0.003, n).astype(np.float32)
    beat = 60.0 / bpm
    t = np.arange(n) / SR
    # Sustained pad, so there is energy everywhere and the envelope is not sparse.
    for f in (110.0, 164.81, 220.0):
        a += (0.05 * np.sin(2 * np.pi * f * t)).astype(np.float32)

    def hit(at: float, kind: str) -> None:
        i = int(at * SR)
        if i < 0 or i >= n - 2400:
            return
        k = np.arange(2400)
        if kind == "kick":
            env = np.exp(-k / 300.0)
            sig = np.sin(2 * np.pi * 60 * k / SR * np.exp(-k / 800.0)) * env * 0.9
        elif kind == "snare":
            env = np.exp(-k / 400.0)
            sig = rng.normal(0, 1, 2400) * env * 0.5
        else:
            env = np.exp(-k / 90.0)
            sig = rng.normal(0, 1, 2400) * env * 0.18
        a[i:i + 2400] += sig.astype(np.float32)

    beats = []
    b = 1.0
    idx = 0
    while b < secs - 1.0:
        j = rng.normal(0, jitter) if jitter else 0.0
        hit(b + j, "kick" if idx % 4 in (0, 2) else "snare")
        hit(b + j + beat / 2, "hat")
        beats.append(b + j)
        b += beat
        idx += 1
    a /= max(float(np.abs(a).max()), 1e-9)
    return (a * 0.7).astype(np.float32), np.array(beats)


def _tone(freqs: list[float], secs: float, sr: int = SR) -> np.ndarray:
    t = np.arange(int(secs * sr)) / sr
    out = np.zeros_like(t)
    for f in freqs:
        for h, amp in ((1, 1.0), (2, 0.5), (3, 0.3)):
            out += amp * np.sin(2 * np.pi * f * h * t)
    return (out / np.abs(out).max() * 0.5).astype(np.float32)


def _midi(name: str, octave: int = 3) -> float:
    return 440.0 * 2 ** ((PITCH_NAMES.index(name) + 12 * (octave + 1) - 69) / 12.0)


def verify() -> None:
    ok = True

    def check(label: str, cond: bool, detail: str) -> None:
        nonlocal ok
        ok = ok and cond
        print(f"  {'PASS' if cond else 'FAIL'}  {label:44s} {detail}")

    print("onset envelope")
    a, ts = _click_track(120.0, 20.0)
    env, et = onset_envelope(a)
    peaks = et[env > env.max() * 0.3]
    err = [min(abs(peaks - t)) for t in ts]
    check("peaks land on the clicks", float(np.median(err)) < 0.05,
          f"median |err| {np.median(err) * 1000:.0f} ms over {len(ts)} clicks")

    print("tempo")
    for bpm in (75.0, 100.0, 140.0):
        a, _ = _click_track(bpm, 40.0)
        env, _ = onset_envelope(a)
        est, _ = estimate_tempo(env)
        check(f"recovers {bpm:.0f} BPM", abs(est - bpm) < 2.0, f"got {est:.1f}")
    # The octave trap the prior exists to avoid.
    a, _ = _click_track(70.0, 40.0)
    env, _ = onset_envelope(a)
    est, _ = estimate_tempo(env)
    check("no octave error at 70 BPM", abs(est - 70.0) < 3.0, f"got {est:.1f}")

    print("beat grid")
    a, ts = _click_track(96.0, 40.0, jitter=0.015, seed=3)
    env, _ = onset_envelope(a)
    est, _ = estimate_tempo(env)
    bt = beat_times(track_beats(env, est))
    err = [min(abs(bt - t)) for t in ts]
    check("beats track a jittered click train",
          float(np.median(err)) < 0.06 and abs(len(bt) - len(ts)) <= 2,
          f"median |err| {np.median(err) * 1000:.0f} ms, {len(bt)} beats vs {len(ts)} clicks")

    print("chroma")
    ch, _ = chromagram(_tone([_midi("A", 4)], 3.0))
    top = int(np.argmax(ch.mean(axis=0)))
    check("A440 reads as A", PITCH_NAMES[top] == "A", f"got {PITCH_NAMES[top]}")
    ch, _ = chromagram(_tone([_midi("C"), _midi("E"), _midi("G")], 3.0))
    order = np.argsort(ch.mean(axis=0))[::-1][:3]
    check("C major triad reads C/E/G",
          set(PITCH_NAMES[i] for i in order) == {"C", "E", "G"},
          "/".join(PITCH_NAMES[i] for i in order))

    print("chord decoding")
    prog = [("C", ["C", "E", "G"]), ("G", ["G", "B", "D"]),
            ("Am", ["A", "C", "E"]), ("F", ["F", "A", "C"])]
    audio = np.concatenate([_tone([_midi(n) for n in notes], 2.0) for _, notes in prog])
    ch, ct = chromagram(audio)
    path = chord_sequence(ch)
    got = [CHORD_NAMES[int(np.bincount(path[(ct >= i * 2 + 0.4) &
                                            (ct < i * 2 + 1.8)]).argmax())]
           for i in range(4)]
    check("recovers C-G-Am-F", got == [p[0] for p in prog], " ".join(got))
    changes = chord_change_times(path, ct)
    near = [min(abs(changes - b)) if len(changes) else 9 for b in (2.0, 4.0, 6.0)]
    check("change times land on the boundaries", max(near) < 0.35,
          f"max |err| {max(near) * 1000:.0f} ms, {len(changes)} changes for 3 real")

    print()
    print("VERIFY:", "all stages behave as claimed" if ok else "SOMETHING IS WRONG")
    if not ok:
        sys.exit(1)


# --------------------------------------------------------------------------
# circular statistics
# --------------------------------------------------------------------------

def beat_phase(events: np.ndarray, beats: np.ndarray) -> np.ndarray:
    """Where each event falls between the beats either side of it, in [0, 1).

    Computed against the *local* beat pair rather than a global period, so a
    band that drifts does not smear the measurement.
    """
    out = []
    for t in events:
        i = int(np.searchsorted(beats, t)) - 1
        if i < 0 or i + 1 >= len(beats):
            continue
        span = beats[i + 1] - beats[i]
        if span <= 0:
            continue
        out.append(((t - beats[i]) / span) % 1.0)
    return np.array(out)


def rayleigh(phases: np.ndarray) -> tuple[float, float]:
    """(R, p) for the null that phases are uniform on the circle.

    R is the resultant length: 0 is perfectly spread, 1 perfectly aligned.
    """
    n = len(phases)
    if n < 3:
        return 0.0, 1.0
    z = np.exp(2j * np.pi * phases)
    r = float(abs(z.mean()))
    # Zar's approximation, good for n > 10 and what every circular-stats
    # package uses; the raw exp(-nR^2) is optimistic at small n. The statistic
    # is the resultant *length* n*r, not the concentration n*r^2 — getting
    # that wrong reported p = 0.95 for a sample whose true p was 1e-7.
    big_r = n * r
    p = float(np.exp(np.sqrt(1 + 4 * n + 4 * (n * n - big_r * big_r)) - (1 + 2 * n)))
    return r, min(1.0, p)


def rayleigh_harmonic(phases: np.ndarray, k: int) -> tuple[float, float]:
    """Rayleigh at the k-th harmonic of beat phase.

    Sung words land on eighths and sixteenths as readily as on the beat, and
    those sit at phases {0, 0.5} or {0, .25, .5, .75} — distributions whose
    first-harmonic resultant is *zero*. Testing k=1 alone is blind to exactly
    the structure we are looking for.
    """
    return rayleigh((phases * k) % 1.0)


def circ_mean(phases: np.ndarray) -> float:
    return float(np.angle(np.exp(2j * np.pi * phases).mean()) / (2 * np.pi) % 1.0)


def tempo_drift(env: np.ndarray, bpm: float, window_s: float = 30.0) -> list[float]:
    n = int(window_s * ON_FPS)
    out = []
    for i in range(0, len(env) - n, n):
        out.append(estimate_tempo(env[i:i + n])[0])
    return out


# --------------------------------------------------------------------------
# --grid : does a beat grid exist in this audio at all?
# --------------------------------------------------------------------------

def cmd_grid(ids: list[str]) -> None:
    print("Beat grid, validated against Whisper word onsets — an independent")
    print("signal (cross-attention on lyrics, not the DSP onset envelope).")
    print()
    hdr = f"{'recording':28s} {'BPM':>6s} {'drift':>7s} {'beats':>6s} {'words':>6s} {'R':>6s} {'p':>9s} {'mean φ':>7s}"
    print(hdr)
    print("-" * len(hdr))
    allw = []
    for rid in ids:
        rec = load_rec(rid)
        env, _ = onset_envelope(rec.audio)
        bpm, _ = estimate_tempo(env)
        bt = beat_times(track_beats(env, bpm))
        drift = tempo_drift(env, bpm)
        spread = (max(drift) - min(drift)) if drift else 0.0
        onsets = np.array([w["start"] for w in rec.words], dtype=float)
        ph = beat_phase(onsets, bt)
        r, p = rayleigh(ph)
        allw.append(ph)
        print(f"{rid:28s} {bpm:6.1f} {spread:6.1f}B {len(bt):6d} {len(ph):6d} "
              f"{r:6.3f} {p:9.2e} {circ_mean(ph):7.3f}")
    pooled = np.concatenate(allw)
    r, p = rayleigh(pooled)
    print("-" * len(hdr))
    print(f"{'POOLED':28s} {'':6s} {'':7s} {'':6s} {len(pooled):6d} {r:6.3f} {p:9.2e} "
          f"{circ_mean(pooled):7.3f}")
    print()
    print("Null model: the same statistic on times drawn uniformly over each")
    print("recording, 200 draws, matched n.")
    rng = np.random.default_rng(0)
    nulls = []
    for _ in range(200):
        fake = np.sort(rng.uniform(pooled.min(), 1.0, len(pooled)))
        nulls.append(rayleigh(fake % 1.0)[0])
    print(f"  random phases: R = {np.mean(nulls):.3f} "
          f"(95th pct {np.percentile(nulls, 95):.3f}), observed R = {r:.3f}")
    print()
    print("Histogram of word onsets by beat phase (0 = on the beat):")
    hist, _ = np.histogram(pooled, bins=16, range=(0, 1))
    for i, c in enumerate(hist):
        bar = "#" * int(60 * c / max(hist.max(), 1))
        print(f"  {i / 16:4.2f} {bar} {c}")


# --------------------------------------------------------------------------
# --quantized : is there a metrical grid, independent of any beat tracker?
# --------------------------------------------------------------------------

def comb_search(times: np.ndarray, lo: float, hi: float,
                n: int = 900) -> tuple[float, float, np.ndarray, np.ndarray]:
    """Find the period T that best explains `times` as multiples of T.

    R(T) = |mean(exp(2*pi*i*t/T))| — the resultant of the times taken modulo T.
    This asks the metrical question *without* a beat tracker: no onset
    envelope, no DSP, nothing that could be blamed for a negative result.
    """
    periods = np.linspace(lo, hi, n)
    z = np.exp(2j * np.pi * times[:, None] / periods[None, :])
    r = np.abs(z.mean(axis=0))
    i = int(np.argmax(r))
    return float(periods[i]), float(r[i]), periods, r


def comb_null(n_events: int, span: tuple[float, float], lo: float, hi: float,
              draws: int = 300, seed: int = 0) -> np.ndarray:
    """Max R over the same period range for uniformly random times.

    Maximising over 900 candidate periods inflates R on its own; without this
    the statistic would look impressive on noise.
    """
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(draws):
        t = rng.uniform(span[0], span[1], n_events)
        out.append(comb_search(t, lo, hi)[1])
    return np.array(out)


def cmd_quantized(ids: list[str]) -> None:
    """RETAINED BUT RETRACTED — this test failed its control.

    Run on Beatles section boundaries, where 94% are known to sit on an
    annotated downbeat, it recovers the true bar period only 5% of the time:
    sections are 8-16 bars apart, so a comb search over 0.8-6.0 s has many
    spurious periods that explain a dozen events as well as the real one. The
    negative it reports on our goldens is therefore not evidence of absence.
    See `power` in Plan/CHORD_DOWNBEAT.md. Kept so the trap is not re-set.
    """
    print("RETRACTED: this test fails its own control — see the docstring.")
    print()
    print("A metrical grid, sought directly in event times — no beat tracker,")
    print("no onset envelope. R is the resultant of times taken modulo T; the")
    print("null is the same search on uniformly random times, which controls")
    print("for the inflation from maximising over 900 candidate periods.")
    print()
    hdr = (f"{'recording':26s} | {'word onsets':>26s} | {'slide cues':>26s} | ratio")
    print(hdr)
    print(f"{'':26s} | {'n':>4s} {'beat T':>7s} {'R':>5s} {'null':>5s} | "
          f"{'n':>4s} {'bar T':>7s} {'R':>5s} {'null':>5s} |")
    print("-" * len(hdr))
    rows = []
    for rid in ids:
        rec = load_rec(rid)
        onsets = np.array([w["start"] for w in rec.words], dtype=float)
        cues = np.array([c["t_seconds"] for c in rec.cues], dtype=float)
        if len(onsets) < 30 or len(cues) < 5:
            continue
        span_w = (onsets.min(), onsets.max())
        tw, rw, _, _ = comb_search(onsets, 0.30, 1.30)
        nw = comb_null(len(onsets), span_w, 0.30, 1.30)
        span_c = (cues.min(), cues.max())
        tc, rc, _, _ = comb_search(cues, 0.80, 6.00)
        nc = comb_null(len(cues), span_c, 0.80, 6.00)
        ratio = tc / tw
        rows.append((rid, tw, rw, nw, tc, rc, nc, ratio))
        print(f"{rid:26s} | {len(onsets):4d} {tw:6.3f}s {rw:5.3f} "
              f"{np.percentile(nw, 95):5.3f} | {len(cues):4d} {tc:6.3f}s {rc:5.3f} "
              f"{np.percentile(nc, 95):5.3f} | {ratio:5.2f}")
    print()
    print("`beat T` should be a plausible beat (0.3-1.3 s = 46-200 BPM).")
    print("`ratio` is the slide-cue period divided by the word-onset period: if")
    print("slide changes fall on bar lines, this should sit near a small whole")
    print("number (2, 4, 8) rather than anywhere in between.")
    if rows:
        rr = np.array([r[7] for r in rows])
        near = np.abs(rr - np.round(rr))
        print(f"\ndistance from the nearest whole number: median {np.median(near):.2f} "
              f"(0.25 would be chance)")


def cmd_chords(ids: list[str]) -> None:
    """Harmonic rhythm against slide rhythm on our own audio.

    A chord change can only trigger a slide switch if the two happen at
    comparable rates. This measures that on worship material with the decoder
    verified above, independently of the Beatles annotation figure.
    """
    print("Chord changes are decoded by the 24-triad Viterbi verified under")
    print("--verify. `precision` is the share of chord changes falling within")
    print("1 s of a real slide cue, i.e. what a chord-change trigger would give.")
    print()
    hdr = (f"{'recording':26s} {'chord chg/min':>14s} {'slide chg/min':>14s} "
           f"{'ratio':>7s} {'precision':>10s}")
    print(hdr)
    print("-" * len(hdr))
    rat, prec = [], []
    for rid in ids:
        rec = load_rec(rid)
        ch, ct = chromagram(rec.audio)
        changes = chord_change_times(chord_sequence(ch), ct)
        cues = np.array([c["t_seconds"] for c in rec.cues], dtype=float)
        if len(changes) < 5 or len(cues) < 5:
            continue
        mins = (ct[-1] - ct[0]) / 60.0
        d = np.abs(changes[:, None] - cues[None, :]).min(axis=1)
        p = float((d < 1.0).mean())
        r = len(changes) / len(cues)
        rat.append(r)
        prec.append(p)
        print(f"{rid:26s} {len(changes) / mins:14.1f} {len(cues) / mins:14.1f} "
              f"{r:7.1f} {p:9.1%}")
    print("-" * len(hdr))
    print(f"{'mean':26s} {'':14s} {'':14s} {np.mean(rat):7.1f} {np.mean(prec):9.1%}")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--grid", action="store_true")
    ap.add_argument("--quantized", action="store_true")
    ap.add_argument("--chords", action="store_true")
    ap.add_argument("--set", default="", help="comma-separated recording ids")
    args = ap.parse_args()
    ids = args.set.split(",") if args.set else approved_ids()
    if args.verify:
        verify()
    if args.grid:
        cmd_grid(ids)
    if args.quantized:
        cmd_quantized(ids)
    if args.chords:
        cmd_chords(ids)
