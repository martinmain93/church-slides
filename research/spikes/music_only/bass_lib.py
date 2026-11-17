"""Bass spike: shared helpers (sheet chords -> bass per frame; bass features from audio).

Nothing here is used by the app. See build/spike_music_only/bass/REPORT.md.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cv  # noqa: E402
import gt  # noqa: E402
import multi_replay as mr  # noqa: E402
import score as sc  # noqa: E402
import stage1  # noqa: E402

S = cv.S
OUT = Path(__file__).resolve().parents[3] / "build/spike_music_only/bass"
FPS = 10.0
SR = 16000

#: (recording dir under S, {sheet name: setlist item or None = auto})
CASES = {
    "wabn": ("wabn", {"What A Beautiful Name-lead-D": None}),
    "rl": ("rl", {"Reckless Love-lead-F#": None}),
    "komh": ("komh", {"King Of My Heart-lead-Bb": None}),
    "isa": ("isa", {"I Surrender All-lead-D": None}),
    "medley": ("nbtb", {"The Blood-lead-Eb": 0, "Nothing But The Blood (Plainfield)-lead-G": 1}),
    "since": ("neg_214054_974", {"Since Your Love-lead-B": None}),
    "set": ("set1002", {"Awesome God-lead-Cm": 1, "Build My Life-lead-G": 0, "Doxology-lead-F#": 2,
                        "O Come All Ye Faithful (Adeste Fideles)-lead-G": 3}),
}
FEATS = {"wabn": "wabn", "rl": "rl", "komh": "komh", "isa": "isa", "medley": "nbtb",
         "since": "neg_214054_974", "set": "set1002"}

PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]


def note_pc(s: str) -> int:
    p = PC[s[0]]
    for ch in s[1:]:
        p += 1 if ch == "#" else -1 if ch == "b" else 0
    return p % 12


@dataclass
class Chord:
    name: str
    root: int
    bass: int
    minor: bool
    slash: bool


def parse_chord(name: str) -> Chord | None:
    m = re.match(r"^([A-G][#b]?)([^/]*)(?:/([A-G][#b]?))?$", name.strip())
    if not m:
        return None
    root = note_pc(m.group(1))
    qual = m.group(2)
    minor = qual.startswith("m") and not qual.startswith("maj")
    bass = note_pc(m.group(3)) if m.group(3) else root
    return Chord(name, root, bass, minor, m.group(3) is not None)


def score_for(sheet: str) -> sc.Score:
    p = OUT / "scores" / f"{sheet}.score.json"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(mr.sheet(sheet)))
    return sc.load(p)


def played_chords(s: sc.Score) -> list[tuple[float, float, Chord, int]]:
    """Unrolled chord spans: (start, end played quarter notes, chord, played-bar index).
    A bar with no chord symbol continues the previous chord."""
    out = []
    cur = None
    for pi, pb in enumerate(s.played):
        bar = s.bar(pb.bar)
        blen = s.played[pi + 1].start - pb.start if pi + 1 < len(s.played) else s.meter
        chs = sorted(bar["chords"], key=lambda c: c["beat"])
        for c in chs:
            ch = parse_chord(c["name"])
            if ch is None:
                continue
            t = pb.start + c["beat"]
            if cur is not None:
                out.append((cur[0], t, cur[1], cur[2]))
            cur = (t, ch, pi)
        if not chs and cur is None:
            continue
    if cur is not None:
        out.append((cur[0], s.length, cur[1], cur[2]))
    return [o for o in out if o[1] > o[0]]


@dataclass
class SongGT:
    case: str
    sheet: str
    item: int
    t: np.ndarray        # frame times
    slide: np.ndarray    # GT slide (-1 none, -2 other song)
    pos: np.ndarray      # played position (quarter notes) or NaN
    span: np.ndarray     # index into chords, -1 where unknown
    chords: list         # played_chords(s)
    s: sc.Score


def song_gt(case: str, sheet: str, item: int | None, duration: float) -> SongGT:
    rec = S / CASES[case][0]
    s = score_for(sheet)
    items = stage1.load_items(rec)
    it = stage1.song_item(s, rec) if item is None else item
    slides = items[it]
    cues = gt.build(s, slides, rec, duration, item=it)
    tl = gt.timeline(cues, fps=FPS, duration=duration, s=s)
    chords = played_chords(s)
    starts = np.array([c[0] for c in chords])
    pos = tl["pos"]
    span = np.full(len(pos), -1, np.int64)
    ok = np.isfinite(pos)
    span[ok] = np.clip(np.searchsorted(starts, pos[ok], side="right") - 1, 0, len(chords) - 1)
    return SongGT(case, sheet, it, tl["t"], tl["slide"], pos, span, chords, s)


# ---------------------------------------------------------------- audio features


def load_audio(path: Path) -> np.ndarray:
    x, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if x.ndim > 1:
        x = x.mean(axis=1)
    assert sr == SR, sr
    return x


def spectrogram(x: np.ndarray, n_fft: int, hop: int, causal: bool) -> tuple[np.ndarray, np.ndarray]:
    """|STFT|^2 at frame times. Causal frames END at their time stamp; centred ones are centred."""
    w = np.hanning(n_fft).astype(np.float32)
    n_frames = 1 + (len(x) - n_fft) // hop if len(x) >= n_fft else 0
    idx = np.arange(n_fft)[None, :] + hop * np.arange(n_frames)[:, None]
    out = np.empty((n_frames, n_fft // 2 + 1), np.float32)
    for a in range(0, n_frames, 512):
        fr = x[idx[a:a + 512]] * w
        out[a:a + 512] = np.abs(np.fft.rfft(fr, axis=1)) ** 2
    t = (hop * np.arange(n_frames) + (n_fft if causal else n_fft / 2)) / SR
    return t, out


def semitone_map(spec: np.ndarray, n_fft: int, midi_lo: int, midi_hi: int) -> np.ndarray:
    """Power spectrum -> semitone energies MIDI midi_lo..midi_hi (triangular, 1 semitone wide)."""
    f = np.arange(spec.shape[1]) * SR / n_fft
    with np.errstate(divide="ignore"):
        m = 69 + 12 * np.log2(np.maximum(f, 1e-6) / 440.0)
    mids = np.arange(midi_lo, midi_hi + 1)
    W = np.maximum(0.0, 1.0 - np.abs(m[None, :] - mids[:, None]))  # semis x bins
    W[:, f < 20] = 0
    return spec @ W.T.astype(np.float32)


def bass_salience(semis: np.ndarray, midi_lo: int, f0_lo: int = 28, f0_hi: int = 52,
                  harm=(1.0, 0.8, 0.0, 0.6)) -> np.ndarray:
    """Harmonic-sum salience for bass fundamentals f0_lo..f0_hi from semitone energies.
    Harmonics 1, 2, 4 (octaves) by default; h3 (a 12th up = the fifth) is left out on
    purpose: it would vote for the root when the real note is the fifth's fundamental."""
    offs = [0, 12, 19, 24]
    T = semis.shape[0]
    out = np.zeros((T, f0_hi - f0_lo + 1), np.float32)
    lg = np.log1p(semis / (np.median(semis) + 1e-9))
    for j, m0 in enumerate(range(f0_lo, f0_hi + 1)):
        for w, o in zip(harm, offs):
            k = m0 + o - midi_lo
            if w and 0 <= k < semis.shape[1]:
                out[:, j] += w * lg[:, k]
    return out


def fold(sal: np.ndarray, midi_lo: int) -> np.ndarray:
    out = np.zeros((sal.shape[0], 12), np.float32)
    for j in range(sal.shape[1]):
        out[:, (midi_lo + j) % 12] = np.maximum(out[:, (midi_lo + j) % 12], sal[:, j])
    return out


def to_fps(t_src: np.ndarray, X: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Average rows of X whose time stamps fall in each 1/FPS frame (nearest if none)."""
    lo = np.searchsorted(t_src, t - 0.5 / FPS)
    hi = np.searchsorted(t_src, t + 0.5 / FPS)
    cs = np.vstack([np.zeros((1, X.shape[1]), np.float64), np.cumsum(X, axis=0, dtype=np.float64)])
    cnt = (hi - lo)[:, None]
    near = np.clip(np.searchsorted(t_src, t), 0, len(t_src) - 1)
    avg = (cs[np.minimum(hi, len(X))] - cs[np.minimum(lo, len(X))]) / np.maximum(cnt, 1)
    return np.where(cnt > 0, avg, X[near]).astype(np.float32)


def stft_bass_chroma(x: np.ndarray, t: np.ndarray, causal: bool, n_fft: int = 8192, hop: int = 800,
                     band=(28, 64), harmonic: bool = True) -> np.ndarray:
    """T x 12 bass pitch-class evidence from a low-band spectrum.
    band = MIDI range of the spectrum used (28 = E1 41 Hz, 64 = E4 330 Hz)."""
    ts, P = spectrogram(x, n_fft, hop, causal)
    semis = semitone_map(P, n_fft, band[0], band[1])
    if harmonic:
        sal = bass_salience(semis, band[0])
        pcs = fold(sal, 28)
    else:
        lim = 55 - band[0]  # fundamentals only, up to G3
        pcs = fold(np.log1p(semis[:, :lim + 1] / (np.median(semis) + 1e-9)), band[0])
    return to_fps(ts, pcs, t)


def bp_bass_chroma(F, src: str, t: np.ndarray, midi_hi: int = 52, lowest: bool = False, thr: float = 0.3) -> np.ndarray:
    """From cached Basic Pitch contour salience (frames x 264, 3 bins/semitone from MIDI 21):
    pitch-class salience restricted to MIDI 28..midi_hi, or the LOWEST semitone above thr."""
    c = F[f"{src}_pitch"].astype(np.float32)
    pt = F[f"{src}_pitch_t"]
    n_semi = (c.shape[1] - 1) // 3
    semi = np.stack([c[:, max(0, 1 + 3 * k - 1): 1 + 3 * k + 2].max(axis=1) for k in range(n_semi)], axis=1)
    lo, hi = 28 - 21, midi_hi - 21
    sub = semi[:, lo:hi + 1]
    if lowest:
        on = sub >= thr
        first = np.where(on.any(axis=1), on.argmax(axis=1), -1)
        out = np.zeros((len(sub), 12), np.float32)
        r = np.where(first >= 0)[0]
        out[r, (28 + first[r]) % 12] = 1.0
    else:
        out = np.zeros((len(sub), 12), np.float32)
        for j in range(sub.shape[1]):
            k = (28 + j) % 12
            out[:, k] = np.maximum(out[:, k], sub[:, j])
    return to_fps(pt.astype(np.float64), out, t)


def btc_root_chroma(F, src: str, t: np.ndarray) -> np.ndarray:
    """BTC's chord distribution folded onto its ROOT pitch class: 'the bass is the root'."""
    ch = F[f"{src}_chord"].astype(np.float32)
    ct = F[f"{src}_chord_t"]
    ci = np.clip(np.searchsorted(ct, t), 0, len(ct) - 1)
    p = ch[ci]
    out = np.zeros((len(t), 12), np.float32)
    for k in range(24):
        out[:, k // 2] += p[:, k]
    return out


def btc_chord_frames(F, src: str, t: np.ndarray) -> np.ndarray:
    ch = F[f"{src}_chord"].astype(np.float32)
    ct = F[f"{src}_chord_t"]
    return ch[np.clip(np.searchsorted(ct, t), 0, len(ct) - 1)]


def key_shift(g: SongGT, chords25: np.ndarray) -> int:
    """Semitones the band is ABOVE the sheet: the shift maximising BTC's probability
    of the sheet's own chord (root + major/minor) over the aligned frames."""
    ok = g.span >= 0
    best, arg = -1.0, 0
    for k in range(12):
        idx = [2 * ((g.chords[sp][2].root + k) % 12) + int(g.chords[sp][2].minor) for sp in g.span[ok]]
        v = float(np.mean(chords25[ok][np.arange(ok.sum()), idx]))
        if v > best:
            best, arg = v, k
    return arg
