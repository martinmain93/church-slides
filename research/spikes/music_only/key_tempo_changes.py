"""Do key and tempo changes mark song changes?

    uv run --project sidecar python research/spikes/music_only/key_tempo_changes.py

Sheet-free signals from the audio alone:
* key: BTC chord probabilities (cached mix features) against diatonic
  templates for the 12 major keys (a relative minor shares its chords, so
  it's the same class here), over a trailing window; a change is a new key
  that holds for `HOLD_S`;
* tempo: the moving pulse (`music.layers.pulse_track`); a change is a move
  of more than `TEMPO_JUMP` (octave errors folded) that holds for `HOLD_S`.

For the two multi-song recordings: which song changes had a key/tempo change
near them (-20..+30 s). For every recording: changes far from any song
change, per minute (false alarms; real in-song key lifts count here too).
"""
from __future__ import annotations

import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import features as ft  # noqa: E402
import multi_replay as mr  # noqa: E402
from auto_presenter.eval.replay import load_recording  # noqa: E402
from auto_presenter.music.btc import MAJMIN_LABELS  # noqa: E402
from auto_presenter.music.layers import pulse_track  # noqa: E402

PITCHES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
WIN_S, STEP_S, HOLD_S, TEMPO_JUMP = 30.0, 5.0, 15.0, 0.08


def key_templates() -> np.ndarray:
    """12 x 25: weight of each BTC class in each major key."""
    t = np.zeros((12, 25))
    for k in range(12):
        for deg, minor, w in ((0, False, 1.0), (5, False, 1.0), (7, False, 1.0), (2, True, 0.7), (4, True, 0.5), (9, True, 0.8)):
            name = PITCHES[(k + deg) % 12] + ("m" if minor else "")
            t[k, MAJMIN_LABELS.index(name)] = w
    return t


def stable_changes(times, values, same, hold):
    """Times where the value moves to a new one that then holds for `hold` s."""
    out, cur, cand, since = [], None, None, None
    for t, v in zip(times, values, strict=True):
        if cur is None:
            cur = v
            continue
        if same(v, cur):
            cand = None
            continue
        if cand is None or not same(v, cand):
            cand, since = v, t
        elif t - since >= hold:
            out.append(since)
            cur, cand = cand, None
    return out


def keys_over_time(F):
    ct, ch = F["mix_chord_t"], F["mix_chord"].astype(np.float32)
    T = key_templates()
    times = np.arange(WIN_S, float(F["duration"]), STEP_S)
    est = []
    for t in times:
        m = (ct > t - WIN_S) & (ct <= t)
        est.append(int(np.argmax(T @ ch[m].sum(axis=0))) if m.any() else -1)
    return times, est


def tempo_over_time(rdir: Path):
    with wave.open(str(rdir / "audio.wav")) as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
        if w.getnchannels() > 1:
            x = x.reshape(-1, w.getnchannels()).mean(axis=1)
    if sr != 16000:
        x = np.interp(np.arange(0, len(x), sr / 16000), np.arange(len(x)), x).astype(np.float32)
    pts = pulse_track(x)
    return [p.t_seconds for p in pts], [p.bpm for p in pts]


def tempo_same(a, b):
    r = max(a, b) / max(min(a, b), 1e-6)
    return any(abs(r / f - 1) <= TEMPO_JUMP for f in (1, 2, 1.5, 3))


def song_changes(rec):
    gt = sorted(rec.ground_truth, key=lambda g: g.t_seconds)
    return [g.t_seconds for i, g in enumerate(gt) if i and g.item_idx != gt[i - 1].item_idx]


RECS = {  # name: (recording dir, cached features)
    "set": (mr.CASES["set"][0], "set1002"), "medley": (mr.CASES["medley"][0], "nbtb"),
    "since": (mr.CASES["since"][0], "neg_214054_974"), "bml": (mr.CASES["bml"][0], "bml2"),
    "wabn": (mr.CASES["wabn"][0], "wabn"), "rl": (mr.CASES["rl"][0], "rl"),
    "komh": (mr.CASES["komh"][0], "komh"), "isa": (mr.CASES["isa"][0], "isa"),
}


def main() -> int:
    total_min = 0.0
    false_key = false_tempo = 0
    for name, (rdir, feats) in RECS.items():
        rec = load_recording(Path(rdir))
        F = ft.load(mr.SPK / f"{feats}_feats.npz")
        kt, ke = keys_over_time(F)
        key_ch = stable_changes(kt, ke, lambda a, b: a == b, HOLD_S)
        tt, tb = tempo_over_time(Path(rdir))
        tempo_ch = stable_changes(tt, tb, tempo_same, HOLD_S)
        sc = song_changes(rec)
        near = lambda t: any(-20 <= t - s <= 30 for s in sc)  # noqa: E731
        dur = float(F["duration"]) / 60
        total_min += dur
        fk = [round(t) for t in key_ch if not near(t)]
        ftm = [round(t) for t in tempo_ch if not near(t)]
        false_key += len(fk)
        false_tempo += len(ftm)
        hits = [(round(s), any(-20 <= t - s <= 30 for t in key_ch), any(-20 <= t - s <= 30 for t in tempo_ch)) for s in sc]
        print(f"{name:7s} {dur:4.1f} min  song changes {[(s, 'key' if k else '-', 'tempo' if tm else '-') for s, k, tm in hits]}"
              f"  | key changes elsewhere {fk}  tempo changes elsewhere {ftm}", flush=True)
    print(f"\nfalse alarms per minute: key {false_key / total_min:.2f}, tempo {false_tempo / total_min:.2f}  ({total_min:.0f} min)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
