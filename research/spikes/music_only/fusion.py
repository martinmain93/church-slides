"""Fuse music-only tune position into the production word tracker.

    uv run --project sidecar python research/spikes/music_only/fusion.py [--arm "stems melody"]

The word tracker is the app's own (`decision.production.build_engine`), fed
live-windowed MLX small.en transcripts (`transcribe_live.py`). The only
change is one extra likelihood, applied after each transcript event and
before the scheduler sees the belief:

    alpha(song, slide, word) *= (eps + r(slide, word)) ** lam

where r is the music filter's probability that the band is at that word's
place in the tune, relative to its average over the song's words (so a
position music knows nothing about keeps weight 1, and states of another
song in the set are untouched). Music cannot tell chorus 1 from chorus 3,
so r is computed on *canonical* positions: every copy of an identical bar
gets the full probability of that music, and the words decide the pass.

Timing is causal. A transcript event covering audio up to `t_end` reaches
the tracker at `available_at`. By then the music filter has used audio up to
`available_at - L` (L: the music features' latency, 3 s for the vocal stem),
so its belief from there is projected forward to `t_end` at its own tempo.

Scored with the harness's own metrics (forgiving mode), on the sung clock
(decisions at the event's `t_end`, as `run_realtime_eval.py` does) and on the
availability clock (decisions at `available_at`, what the room sees).
`lam` is chosen leave-one-song-out.
"""

from __future__ import annotations

import argparse
import json
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

import cv
import follow as fl
import score as sc
import stage4
from auto_presenter.asr.types import TranscriptEvent, Word
from auto_presenter.decision.production import build_engine
from auto_presenter.eval.metrics import anticipation_error_ms, wrong_slide_rate
from auto_presenter.eval.replay import load_recording
from auto_presenter.eval.types import Decision
from auto_presenter.index.library import build_index
from auto_presenter.protocol import ParticleFilterTunables, SetlistContent, SetlistItemContent

EVENTS = cv.S / "live_events.json"
GATE = 0.0  # apply music only when its best tune position holds this much mass
LEVEL = "word"  # or "slide"


def held_settings(arm: str) -> dict[str, tuple]:
    res = json.loads((cv.S / "cv_causal.json").read_text())
    if arm not in res["results"]:
        # not cross-validated: the setting the other arms chose most often
        return {k: (0.2, 0.4, 0.03) for k in res["songs"]}
    configs = [tuple(c) for c in res["configs"]]
    table = np.array(res["results"][arm])
    out = {}
    for si, key in enumerate(res["songs"]):
        others = [j for j in range(len(res["songs"])) if j != si]
        out[key] = configs[int(np.argmax(table[:, others, 0].mean(axis=1)))]
    return out


class Music:
    """The causal music filter for one song, as the word tracker will query it."""

    def __init__(self, song: cv.Song, arm: str, setting: tuple):
        self.song = song
        robust, w, pj = setting
        psrc, csrc, wm, wc = cv.ARMS[arm]
        t, pcs, _ = song.ev[psrc]
        _, _, ch = song.ev[csrc]
        self.lag = max(stage4.LATENCY[psrc]["pitch"] if wm else 0, stage4.LATENCY[csrc]["chord"] if wc else 0)
        margs, cums, bpms = [], [], []
        n = int(cv.KEY_WINDOW * song.fps)
        for k in song.shifts:
            m = song.models[(pj, k)]
            lo = fl.log_obs(m, pcs, ch, wm * w, wc * w, robust)
            marg, _ = fl.forward(m, lo)
            c = np.concatenate([[0.0], np.cumsum(m.loglik)])
            i = np.arange(len(lo))
            cums.append(c[i + 1] - c[np.maximum(0, i + 1 - n)])
            margs.append(marg)
            bpms.append(m.bpm[m.rate_marg.argmax(axis=1)])
        best = np.argmax(np.stack(cums), axis=0)
        idx = np.arange(len(best))
        self.marg = np.stack(margs)[best, idx]  # T x P, audio time
        self.bpm = np.stack(bpms)[best, idx]
        self.P = self.marg.shape[1]
        # canonical copies of each cell, for "this music, wherever it recurs"
        self.equiv = song.scorer.equiv

    def belief_at(self, t_end: float, available_at: float) -> np.ndarray | None:
        """Probability per sheet cell that audio time `t_end` is there, using
        only what the music filter could have known at `available_at`."""
        fps = self.song.fps
        known = available_at - self.lag
        if known < 1.0:
            return None
        src = min(t_end, known)
        i = min(int(src * fps), len(self.marg) - 1)
        p = self.marg[i]
        shift = int(round((t_end - src) * self.bpm[i] / 60.0 / fl.CELL))
        if shift > 0:
            p = np.concatenate([np.zeros(shift), p[:-shift]])  # mass past the end is dropped
        # canonical mass, smoothed over one beat either side
        canon = np.array([p[e].sum() for e in self.equiv])
        k = int(round(1.0 / fl.CELL))
        kernel = np.ones(2 * k + 1)
        return np.convolve(canon, kernel, mode="same")


def word_cells(song: cv.Song, indexed_words: dict[int, tuple[str, ...]]) -> dict[tuple[int, int], list[int]]:
    """(slide, library word index) -> sheet cells where that word is sung."""
    out: dict[tuple[int, int], list[int]] = {}
    s = song.s
    for si, words in indexed_words.items():
        for sp in song.spans.get(si, []):
            sheet = s.words[sp.w0 : sp.w1 + 1]
            a = [sc.norm(w) for w in words]
            b = [w.text for w in sheet]
            pos = [None] * len(a)
            for blk in SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
                for q in range(blk.size):
                    pos[blk.a + q] = sheet[blk.b + q].onset
            # unmatched library words sit between their matched neighbours
            known = [(j, p) for j, p in enumerate(pos) if p is not None]
            if not known:
                continue
            for j in range(len(pos)):
                if pos[j] is None:
                    lo = max((kp for kp in known if kp[0] < j), default=known[0])
                    hi = min((kp for kp in known if kp[0] > j), default=known[-1])
                    pos[j] = lo[1] if hi[0] == lo[0] else lo[1] + (hi[1] - lo[1]) * (j - lo[0]) / (hi[0] - lo[0])
            for j, p in enumerate(pos):
                out.setdefault((si, j), []).append(min(song.scorer.m.P - 1, int(p // fl.CELL)))
    return out


def content(rec) -> SetlistContent:
    return SetlistContent(items=[
        SetlistItemContent(item_idx=s.item_idx, song_id=s.song_id, slides=list(s.slides), slide_sections=list(s.slide_sections))
        for s in rec.setlist_content
    ])


def run_song(key: str, lam: float, eps: float, music: Music | None, events: list[dict], clock: str):
    song = music.song if music else cv.Song(key)
    rec = load_recording(cv.S / key)
    indexed = build_index(content(rec))
    engine = build_engine(indexed, ParticleFilterTunables())
    ff = engine.filter.filter
    item = cv.stage1.song_item(song.s, cv.S / key)
    it = indexed.item(item)
    words = {si: it.slide(si).words for si in range(len(it.slides)) if it.slide(si).words}
    cells = word_cells(song, words)
    orig = ff.update
    state = {"ev": None}

    def fused(event):
        belief = orig(event)
        if music is None or lam == 0 or not event.is_final or not event.words:
            return belief
        ev = state["ev"]
        p = music.belief_at(ev["t_end"], ev["available_at"])
        if p is None:
            return belief
        if GATE and p.max() < GATE:
            return belief  # music has not settled: say nothing
        r = {sw: max(p[c] for c in cs) for sw, cs in cells.items()}
        if LEVEL == "slide":
            # music picks the line of the tune; words keep the place within it
            per = {}
            for (si, wi), v in r.items():
                per.setdefault(si, []).append(v)
            r = {(si, wi): float(np.mean(per[si])) for (si, wi) in r}
        mean = np.mean(list(r.values())) or 1.0
        alpha = ff._alpha
        # music says where in the tune, not which song (stage 6): reweight
        # within the song and give it back exactly the share it had
        before = sum(v for st, v in alpha.items() if st[0] == item)
        for st in list(alpha):
            i, si, wi, _d = st
            if i == item and (si, wi) in r:
                alpha[st] *= (eps + r[(si, wi)] / mean) ** lam
        after = sum(v for st, v in alpha.items() if st[0] == item)
        if after > 0 and before > 0:
            for st in alpha:
                if st[0] == item:
                    alpha[st] *= before / after
        ff._belief_cache = None
        return ff._snapshot()

    ff.update = fused
    decisions = []
    for ev in events:
        state["ev"] = ev
        te = TranscriptEvent(words=[Word(text=w["text"], t_start=w["t_start"], t_end=w["t_end"]) for w in ev["words"]],
                             is_final=True, segment_id=ev["segment_id"])
        d = engine.update(te)
        if d is not None:
            t = ev["available_at"] if clock == "available" else d.t_seconds
            decisions.append(Decision(t_seconds=t, item_idx=d.item_idx, slide_idx=d.slide_idx, source="ai"))
    wrong = wrong_slide_rate(decisions, rec.ground_truth, rec.deviation_markers or None, forgive_unpredictable=True,
                             setlist_content=rec.setlist_content, forgive_identical_slides=True, score_blank_cues=False)
    ant = anticipation_error_ms(decisions, rec.ground_truth)
    return wrong, (float(np.median(ant)) if ant else float("nan")), len(decisions)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", default="stems melody")
    ap.add_argument("--lams", default="0,0.25,0.5,1,2")
    ap.add_argument("--eps", type=float, default=0.2)
    ap.add_argument("--gate", type=float, default=0.0)
    ap.add_argument("--level", default="word")
    args = ap.parse_args()
    global GATE, LEVEL
    GATE, LEVEL = args.gate, args.level
    events = json.loads(EVENTS.read_text())
    held = held_settings(args.arm)
    lams = [float(x) for x in args.lams.split(",")]
    keys = list(cv.SONGS)
    rec_ids = {k: load_recording(cv.S / k).id for k in keys}
    table = {}  # (clock, key, lam) -> (wrong, anticip)
    for key in keys:
        song = cv.Song(key)
        music = Music(song, args.arm, held[key])
        for lam in lams:
            for clock in ("sung", "available"):
                table[(clock, key, lam)] = run_song(key, lam, args.eps, music, events[rec_ids[key]], clock)[:2]
        print(f"{key}: " + "  ".join(f"lam {l:g}: {table[('available', key, l)][0]:.3f}" for l in lams), flush=True)
    for clock in ("sung", "available"):
        print(f"\n=== {args.arm}, level {LEVEL}, gate {GATE}, eps {args.eps}, clock: {clock}  (wrong-slide, median anticipation ms)")
        print("  song   " + "".join(f"  lam={l:<12g}" for l in lams) + "  | held-out lam -> wrong")
        held_res = []
        for key in keys:
            others = [k for k in keys if k != key]
            best = min(lams, key=lambda l: np.mean([table[(clock, o, l)][0] for o in others]))
            held_res.append((table[(clock, key, 0.0)][0], table[(clock, key, best)][0], table[(clock, key, 0.0)][1], table[(clock, key, best)][1]))
            print(f"  {key:6s} " + "".join(f"  {table[(clock, key, l)][0]:.3f} {table[(clock, key, l)][1]:+6.0f}" for l in lams)
                  + f"  | {best:g} -> {table[(clock, key, best)][0]:.3f}")
        h = np.array(held_res)
        print(f"  MEAN baseline {h[:, 0].mean():.3f} (anticip {np.nanmedian(h[:, 2]):+.0f})  ->  fused, held-out lam {h[:, 1].mean():.3f}"
              f" (anticip {np.nanmedian(h[:, 3]):+.0f});  better on {int((h[:, 1] < h[:, 0] - 1e-9).sum())}/5, worse on {int((h[:, 1] > h[:, 0] + 1e-9).sum())}/5")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
