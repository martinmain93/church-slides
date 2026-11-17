"""Why is the joint follower late, and does it know the tempo well enough to anticipate?

    uv run --project sidecar python research/spikes/music_only/diag_anticipation.py

Base setup throughout: the scorer-first GPU queue (scorer evidence 0.2 s
after its window, free words at their measured delay), words + scored +
mel_mix + chd_mix, observation weighting.

1. The operator's lead. In each golden, how long before a slide's first sung
   word did the operator change to it — in seconds and in beats? Whichever
   unit varies less is the one a lead should be stated in.
2. Where the follower switches. The same cues, measured from the follower's
   own switch to the slide: against the first sung word, and against the cue.
   A follower with no anticipation can at best switch *at* the first word.
3. Tempo. The tempo the transcript implies (quarter notes / s), the beat
   tracker's (median inter-beat interval over the trailing 8 beats, offline
   Beat This), and the follower's own tempo state at each read-out.
"""
from __future__ import annotations

import json

import numpy as np

import cv
import fusion
import gt
import joint
import run_joint as rj
import segref
import follow as fl
from auto_presenter.eval.metrics import identical_slide_groups
from auto_presenter.eval.replay import load_recording

ARM = ["words", "scored", "mel_mix", "chd_mix"]


def setup():
    rj.WEIGHTING = "observation"
    rj.SCORER = "mlx_flat"
    rj.SCORE_SHIFT = 3.0
    rj.SCORE_LATENCY = 0.2
    rj.WORD_EXTRA_LAG = 0.0


def first_word_times(song: cv.Song, cues: list[gt.Cue]) -> list[tuple[float, float, int, float] | None]:
    """(cue time, first sung word time, slide, song tempo) per cue that starts a slide's first line."""
    rate = gt.song_rate(cues)
    out = []
    for c in cues:
        if c.span is None or c.slide < 0:
            out.append(None)
            continue
        f = gt.fit(c, rate, 0.0)
        if not c.anchors or f is None:
            out.append(None)
            continue
        icpt, slope = f
        t_first = (c.span.start - icpt) / slope
        out.append((c.t0, t_first, c.slide, rate))
    return out


def follower(song, rec, events, item):
    _, corr = fl.estimate_key_shift(song.ev["mix"][2][:600], song.scorer.m)
    ref = segref.SegmentReference(song.s, song.slides, song.spans, key_shift=int(np.argmax(corr)),
                                  sequences=[rj.roadmap_sequence(song)])
    srcs = rj.sources_for(song, ref, events[rec.id], ARM)
    T = len(song.ev["mix"][0])
    walls, pos, rate, marg = joint.follow(ref, srcs, T)
    ig = identical_slide_groups(rec.setlist_content)
    ident = {sl: set(ig.get((item, sl), ())) for sl in range(len(song.slides))}
    shown = rj.slide_readout(marg, list(ref.slide_of), ident)
    return walls, shown, rate * fl.CELL  # quarter notes per second


def beat_tempo(key: str, t: np.ndarray) -> np.ndarray:
    d = json.loads((cv.S / f"beats_{key}.json").read_text())
    b = np.array(d["beats"])
    out = np.full(len(t), np.nan)
    for i, x in enumerate(t):
        past = b[b <= x][-9:]
        if len(past) >= 5:
            out[i] = 1.0 / np.median(np.diff(past))
    return out


def main() -> int:
    setup()
    events = json.loads(fusion.EVENTS.read_text())
    print("1-2. operator lead and follower switch, per in-song slide change with a sung first word")
    print("     (lead: cue -> first word; follower: its switch to the slide, relative to the first word and to the cue)")
    tempo_rows = []
    for key in ("wabn", "rl", "komh", "isa"):
        song = cv.Song(key)
        rec = load_recording(cv.S / key)
        item = cv.stage1.song_item(song.s, cv.S / key)
        cues = gt.build(song.s, song.slides, cv.S / key, float(song.F["duration"]))
        fw = [x for x in first_word_times(song, cues) if x is not None]
        walls, shown, frate = follower(song, rec, events, item)
        changes = [(walls[k], shown[k]) for k in range(len(shown)) if shown[k] is not None and (k == 0 or shown[k] != shown[k - 1])]
        lead_s, lead_b, vs_word, vs_cue = [], [], [], []
        for t_cue, t_first, sl, rate in fw:
            lead_s.append(t_first - t_cue)
            lead_b.append((t_first - t_cue) * rate)
            hit = [t for t, s in changes if s == sl and t_cue - 6 <= t <= t_cue + 10]
            if hit:
                vs_word.append(hit[0] - t_first)
                vs_cue.append(hit[0] - t_cue)
        q = lambda x: f"median {np.median(x):+.2f} (IQR {np.percentile(x, 25):+.2f}..{np.percentile(x, 75):+.2f})"
        print(f"  {key:5s} n={len(fw):2d} | operator lead {q(lead_s)} s = {q(lead_b)} beats"
              f" | follower vs first word {q(vs_word)} s, vs cue {q(vs_cue)} s (matched {len(vs_word)})")
        cv_s = np.std(lead_s) / max(1e-9, abs(np.mean(lead_s)))
        cv_b = np.std(lead_b) / max(1e-9, abs(np.mean(lead_b)))
        print(f"        spread / mean: seconds {cv_s:.2f}, beats {cv_b:.2f}")
        # tempo
        gt_q = float(gt.song_rate(cues))
        bt = beat_tempo(key, walls)
        sung = np.array([song.tl["slide"][min(len(song.tl["slide"]) - 1, int(w * 10))] >= 0 for w in walls])
        tempo_rows.append((key, gt_q, bt[sung], frate[sung]))
    print("\n3. tempo, quarter notes per second (GT from the transcript's anchors: song median)")
    for key, g, bt, fr in tempo_rows:
        bt = bt[np.isfinite(bt)]
        # beat level may be the half note or eighth: report the ratio and the octave-corrected error
        ratio = np.median(bt) / g
        octave = 2.0 ** np.round(np.log2(ratio))
        bt_err = np.abs(bt / octave / g - 1)
        fr_err = np.abs(fr / g - 1)
        print(f"  {key:5s} GT {g:.2f} ({g * 60:.0f} bpm) | beat tracker median {np.median(bt):.2f} (x{1 / octave:g} = {np.median(bt) / octave:.2f}),"
              f" within 5% {np.mean(bt_err <= 0.05):4.0%} | follower median {np.median(fr):.2f}, within 5% {np.mean(fr_err <= 0.05):4.0%},"
              f" IQR {np.percentile(fr, 25):.2f}..{np.percentile(fr, 75):.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
