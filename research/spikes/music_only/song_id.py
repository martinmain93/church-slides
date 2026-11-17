"""Which song is the band playing, from the music alone?

    uv run --project sidecar python research/spikes/music_only/song_id.py [set|medley] [arm...]

For every setlist song with a lead sheet, a music-only follower (the
production OnlineFollower's reference and transition, evidence from the
offline mix chords and/or melody at the live latency) runs over the whole
recording. Each step's predictive log-likelihood, log sum(alpha_pred *
exp(evidence)), says how well that song's sheet foresaw the music; a
trailing-window rate per song, compared across songs, is music's answer to
"which song". Each song re-estimates its own key every 30 s from the
trailing 60 s of chords (the live rule).

Scored against the golden: share of sung time (screen on a lyric slide)
where music names the screen's song, and at each song change how long until
music's answer moves to the new song (and stays 5 s).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import follow as fl  # noqa: E402
import features as ft  # noqa: E402
import multi_replay as mr  # noqa: E402
from auto_presenter.eval.replay import load_recording  # noqa: E402
from auto_presenter.follower import online, sheet  # noqa: E402
from auto_presenter.follower import score as fsc  # noqa: E402
from auto_presenter.follower.online import SegmentTransition  # noqa: E402
from auto_presenter.follower.reference import SegmentReference  # noqa: E402

FPS = 10.0
FEATS = {"set": "set1002", "medley": "nbtb"}


def song_loglik(ref, ch, pcs, use_ch, use_mel, cw, mw, words=(), ww=1.0, key=None):
    """Per-frame predictive log-likelihood of the music under this song."""
    tr = SegmentTransition(ref, FPS, p_jump=0.03, p_rate=0.02)
    R = len(ref.rates)
    a = np.full((R, ref.P), 1.0 / (R * (ref.P - 1)), np.float32)
    a[:, ref.intro_cell] = 0.0  # mid-recording: anywhere in the song
    T = len(ch)
    ll = np.zeros(T, np.float32)
    word_at: dict[int, list[np.ndarray]] = {}
    for t, text in words:  # (sung time, text), from the live run's kept words
        v = ref.word(text)
        if v is not None:
            word_at.setdefault(int(t * FPS), []).append(v)
    for f in range(T):
        if key is not None:
            if f == 0:
                ref.set_key(int(key))
        elif f % 300 == 0 and f >= 300:  # key every 30 s from the trailing 60 s
            k, _ = sheet.estimate_key_shift(ch[max(0, f - 600) : f], ref.chord_sheet)
            ref.set_key(int(k))
        a = tr.step(a)
        e = np.zeros(ref.P, np.float32)
        warm = key is not None or f >= 300
        if use_ch and warm:
            e += cw * online.CHORD_WEIGHT * ref.chords(ch[f])[0]
        if use_mel and warm:
            e += mw * online.MELODY_WEIGHT * ref.melody(pcs[f])[0]
        for v in word_at.get(f, ()):
            e += ww * online.WORD_WEIGHT * v if hasattr(online, "WORD_WEIGHT") else ww * v
        e[ref.intro_cell] = 0.0
        w = a * np.exp(e - e.max())[None, :]
        s = float(w.sum())
        ll[f] = np.log(max(s, 1e-30)) + float(e.max())
        a = w / max(s, 1e-30)
    return ll


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "set"
    arms = sys.argv[2:] or ["chords", "melody", "both"]
    rdir, _trace, sheets = mr.CASES[name]
    rec = load_recording(rdir)
    F = ft.load(mr.SPK / f"{FEATS[name]}_feats.npz")
    dur = float(F["duration"])
    _t, pcs, ch = fl.frame_evidence(F, "mix", FPS, dur)
    T = len(ch)
    lag = 10  # evidence arrives ~1 s after the audio it describes
    gt = sorted(rec.ground_truth, key=lambda g: g.t_seconds)
    screen = np.full(T, -1)
    sung = np.zeros(T, bool)
    for i, g in enumerate(gt):
        a = int(g.t_seconds * FPS)
        b = int(gt[i + 1].t_seconds * FPS) if i + 1 < len(gt) else T
        screen[a:b] = g.item_idx
        sung[a:b] = g.slide_idx > 0
    switches = [(g.t_seconds, g.item_idx) for i, g in enumerate(gt) if i == 0 or g.item_idx != gt[i - 1].item_idx]
    for arm in arms:
        for win in (10, 20, 40):
            ll = {}
            for item, pdf in sheets.items():
                snap = [x for x in rec.setlist_content if x.item_idx == item][0]
                ref = SegmentReference(fsc.from_extract(mr.sheet(pdf)), snap.slides, list(snap.slide_sections))
                ll[item] = song_loglik(ref, ch, pcs, arm in ("chords", "both"), arm in ("melody", "both"), 1.0, 1.0)
            items = sorted(ll)
            L = np.stack([ll[i] for i in items])
            n = int(win * FPS)
            c = np.concatenate([np.zeros((len(items), 1)), np.cumsum(L, axis=1)], axis=1)
            idx = np.arange(T)
            rate = (c[:, idx + 1] - c[:, np.maximum(0, idx + 1 - n)]) / win
            # what music says at time t is computed from audio up to t - lag
            pick = np.full(T, -1)
            pick[lag:] = np.array(items)[np.argmax(rate[:, : T - lag], axis=0)]
            ok = sung & np.isin(screen, items)
            acc = float((pick[ok] == screen[ok]).mean())
            delays = []
            for t, it in switches[1:]:
                if it not in items:
                    continue
                f0 = int(t * FPS)
                got = None
                for f in range(max(0, f0 - 100), min(T - 50, f0 + 1200)):
                    if np.all(pick[f : f + 50] == it):
                        got = (f - f0) / FPS
                        break
                delays.append((round(t), it, got))
            print(f"{name} {arm:7s} window {win:2d}s: right song {acc:.0%} of sung time | at switches "
                  + "  ".join(f"{t}s->{it}: {'never' if d is None else f'{d:+.0f}s'}" for t, it, d in delays), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
