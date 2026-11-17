"""Why tune-position fusion does not move wrong-slide: where the error is.

    uv run --project sidecar python research/spikes/music_only/diag_fusion.py

On the room's clock (decisions at `available_at`), every scored second the
screen is wrong is put in exactly one bucket, in this order:

* scheduler — the tracker's latest *available* belief is the right slide
  now, but the screen is not showing it;
* stale     — that belief was right for the audio it was built from (the
  event's `t_end`), but the band has since moved on; only timing can fix it;
* identity  — the belief was wrong even for the audio it had heard. This is
  the only error a better "where in the song" signal can fix.

For identity seconds it then asks whether music, as fusion would have read
it at that event, had the right answer — and an oracle run injects *perfect*
tune position (the ground truth's sheet position, no lag) through the same
fusion code, which bounds what the mechanism could ever buy.

"Right" uses the scorer's own notion: slides with identical words count as
the same display; music is right if its top tune position is one the true
slide's music covers.
"""

from __future__ import annotations

import json

import numpy as np

import cv
import fusion
import follow as fl
import gt
from auto_presenter.asr.types import TranscriptEvent, Word
from auto_presenter.decision.production import build_engine
from auto_presenter.eval.metrics import (
    DEFAULT_DEVIATION_GRACE_S,
    blank_cue_windows,
    derive_deviation_markers,
    identical_slide_groups,
    wrong_slide_rate,
)
from auto_presenter.eval.replay import load_recording
from auto_presenter.eval.types import Decision
from auto_presenter.index.library import build_index
from auto_presenter.protocol import ParticleFilterTunables

FPS = 10


class Oracle:
    """Perfect tune position: the ground truth's sheet position at t_end,
    in the same canonical, one-beat-smoothed form `fusion.Music` returns."""

    def __init__(self, song: cv.Song):
        self.song = song
        self.tl = song.tl
        self.equiv = song.scorer.equiv
        self.P = song.scorer.m.P

    def belief_at(self, t_end: float, available_at: float):
        i = int(t_end * self.song.fps)
        if i >= len(self.tl["pos"]) or not np.isfinite(self.tl["pos"][i]):
            return None
        p = np.zeros(self.P)
        p[min(self.P - 1, int(self.tl["pos"][i] // fl.CELL))] = 1.0
        canon = np.array([p[e].sum() for e in self.equiv])
        k = int(round(1.0 / fl.CELL))
        return np.convolve(canon, np.ones(2 * k + 1), mode="same")


def run_traced(key, lam, eps, music, events, tick: float | None = None):
    """`fusion.run_song`, keeping the belief after every event."""
    song = music.song
    rec = load_recording(cv.S / key)
    indexed = build_index(fusion.content(rec))
    engine = build_engine(indexed, ParticleFilterTunables())
    ff = engine.filter.filter
    item = cv.stage1.song_item(song.s, cv.S / key)
    it = indexed.item(item)
    words = {si: it.slide(si).words for si in range(len(it.slides)) if it.slide(si).words}
    cells = fusion.word_cells(song, words)
    orig = ff.update
    cur = {}
    trace = []

    def fused(event):
        belief = orig(event)
        cur["pre"] = (belief.item_idx, belief.slide_idx)
        cur["music"] = None
        if not event.is_final or not event.words or music is None:
            return belief
        ev = cur["ev"]
        p = music.belief_at(ev["t_end"], ev["available_at"])
        cur["music"] = None if p is None else int(np.argmax(p))
        if p is None or lam == 0:
            return belief
        r = {sw: max(p[c] for c in cs) for sw, cs in cells.items()}
        mean = np.mean(list(r.values())) or 1.0
        alpha = ff._alpha
        before = sum(v for st, v in alpha.items() if st[0] == item)
        for st in list(alpha):
            if st[0] == item and (st[1], st[2]) in r:
                alpha[st] *= (eps + r[(st[1], st[2])] / mean) ** lam
        after = sum(v for st, v in alpha.items() if st[0] == item)
        if after > 0 and before > 0:
            for st in alpha:
                if st[0] == item:
                    alpha[st] *= before / after
        ff._belief_cache = None
        return ff._snapshot()

    ff.update = fused
    decisions = []
    last_tick = 0.0
    for ev in events:
        # the app's watchdog: ticked on the audio clock between events
        if tick:
            while last_tick + tick < ev["available_at"]:
                last_tick += tick
                d = engine.tick(last_tick)
                if d is not None:
                    decisions.append(Decision(t_seconds=last_tick, item_idx=d.item_idx, slide_idx=d.slide_idx, source="ai"))
        cur["ev"] = ev
        te = TranscriptEvent(words=[Word(text=w["text"], t_start=w["t_start"], t_end=w["t_end"]) for w in ev["words"]],
                             is_final=True, segment_id=ev["segment_id"])
        d = engine.update(te)
        b = ff.belief
        trace.append({"t_end": ev["t_end"], "avail": ev["available_at"], "pre": cur.get("pre"),
                      "post": (b.item_idx, b.slide_idx), "music": cur.get("music"),
                      "marginal": b.marginal, "display_marginal": b.display_marginal, "song_share": b.song_share})
        if d is not None:
            decisions.append(Decision(t_seconds=ev["available_at"], item_idx=d.item_idx, slide_idx=d.slide_idx, source="ai"))
    return rec, item, decisions, trace


def gt_at(gt_events, t):
    cur = None
    for g in gt_events:
        if g.t_seconds <= t:
            cur = (g.item_idx, g.slide_idx)
        else:
            break
    return cur


def decompose(rec, item, decisions, trace, song: cv.Song):
    gte = rec.ground_truth
    equiv = identical_slide_groups(rec.setlist_content)

    def same(g, d):
        return g is not None and d is not None and (g == d or (d[0] == g[0] and d[1] in equiv.get(g, frozenset())))

    excused = list(blank_cue_windows(gte, rec.setlist_content))
    excused += [(m.t_seconds, m.t_seconds + DEFAULT_DEVIATION_GRACE_S) for m in derive_deviation_markers(gte)]
    t0, t1 = gte[0].t_seconds, gte[-1].t_seconds
    ts = np.arange(t0, t1, 1 / FPS)
    di = 0
    shown = None
    ti = -1
    out = {"scheduler": 0, "stale": 0, "identity": 0, "right": 0}
    ident_music = {"music right": 0, "music wrong": 0, "music silent": 0}
    sched = {"unsure (<0.4)": 0, "backward move": 0, "other song shown": 0, "confident, forward": 0}
    same_music = {"same music": 0, "different music": 0, "not comparable": 0}
    for t in ts:
        if any(a <= t < b for a, b in excused):
            continue
        while di < len(decisions) and decisions[di].t_seconds <= t:
            shown = (decisions[di].item_idx, decisions[di].slide_idx)
            di += 1
        while ti + 1 < len(trace) and trace[ti + 1]["avail"] <= t:
            ti += 1
        g = gt_at(gte, t)
        if same(g, shown):
            out["right"] += 1
            continue
        if ti < 0:
            out["identity"] += 1
            ident_music["music silent"] += 1
            continue
        tr = trace[ti]
        belief = tr["post"]
        if same(g, belief):
            out["scheduler"] += 1
            if tr["display_marginal"] < 0.4:
                sched["unsure (<0.4)"] += 1
            elif shown is not None and shown[0] == belief[0] and belief[1] < shown[1]:
                sched["backward move"] += 1
            elif shown is not None and shown[0] != belief[0]:
                sched["other song shown"] += 1
            else:
                sched["confident, forward"] += 1
        elif same(gt_at(gte, tr["t_end"]), belief):
            out["stale"] += 1
        else:
            out["identity"] += 1
            src_g = gt_at(gte, tr["t_end"])
            m = tr["music"]
            if m is None or src_g is None or src_g[0] != item:
                ident_music["music silent"] += 1
            elif src_g[1] in song.scorer.cover_eq[m]:
                ident_music["music right"] += 1
            else:
                ident_music["music wrong"] += 1
            # could ANY tune signal separate the believed slide from the true one?
            i_true = int(song.tl["t"].searchsorted(tr["t_end"]))
            pos = song.tl["pos"][min(i_true, len(song.tl["pos"]) - 1)]
            if src_g is not None and src_g[0] == item and belief[0] == item and np.isfinite(pos):
                c = min(song.scorer.m.P - 1, int(pos // fl.CELL))
                if belief[1] in song.scorer.cover_eq[c]:
                    same_music["same music"] += 1
                else:
                    same_music["different music"] += 1
            else:
                same_music["not comparable"] += 1
    return out, ident_music, sched, same_music


def main() -> int:
    events = json.loads(fusion.EVENTS.read_text())
    held = fusion.held_settings("stems melody")
    agg = {}
    print(f"{'song':6s} {'wrong':>6s} {'+tick':>6s} | sched  stale  ident | scheduler: unsure / backward / other-song / confident-fwd | identity: same music as truth / different / n.a.")
    for key in cv.SONGS:
        song = cv.Song(key)
        rid = load_recording(cv.S / key).id
        music = fusion.Music(song, "stems melody", held[key])
        rec, item, dec, tr = run_traced(key, 0.0, 0.2, music, events[rid])
        _, _, dect, _ = run_traced(key, 0.0, 0.2, music, events[rid], tick=0.5)
        parts, im, sched, sm = decompose(rec, item, dec, tr, song)
        kw = dict(forgive_unpredictable=True, setlist_content=rec.setlist_content, forgive_identical_slides=True)
        base = wrong_slide_rate(dec, rec.ground_truth, None, **kw)
        ticked = wrong_slide_rate(dect, rec.ground_truth, None, **kw)
        W = max(1, parts["scheduler"] + parts["stale"] + parts["identity"])
        ns, ni = max(1, parts["scheduler"]), max(1, parts["identity"])
        print(f"{key:6s} {base:6.3f} {ticked:6.3f} | {parts['scheduler'] / W:5.0%} {parts['stale'] / W:5.0%} {parts['identity'] / W:5.0%} |"
              f"  {sched['unsure (<0.4)'] / ns:5.0%} / {sched['backward move'] / ns:5.0%} / {sched['other song shown'] / ns:5.0%} / {sched['confident, forward'] / ns:5.0%}"
              f"            |  {sm['same music'] / ni:5.0%} / {sm['different music'] / ni:5.0%} / {sm['not comparable'] / ni:5.0%}")
        for d in (parts, sched, sm, im):
            for k, v in d.items():
                agg[k] = agg.get(k, 0) + v
    W = agg["scheduler"] + agg["stale"] + agg["identity"]
    print(f"\nALL: scheduler {agg['scheduler'] / W:.0%}, stale {agg['stale'] / W:.0%}, identity {agg['identity'] / W:.0%}")
    print("  scheduler seconds: " + ", ".join(f"{k} {agg[k] / agg['scheduler']:.0%}" for k in ("unsure (<0.4)", "backward move", "other song shown", "confident, forward")))
    print("  identity seconds: " + ", ".join(f"{k} {agg[k] / agg['identity']:.0%}" for k in ("same music", "different music", "not comparable")))
    print("  identity seconds, live music: " + ", ".join(f"{k} {agg[k] / agg['identity']:.0%}" for k in ("music right", "music wrong", "music silent")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
