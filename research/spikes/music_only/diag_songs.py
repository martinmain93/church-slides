"""Song changes, traced: what the tracker thought, what the screen did, and
which gate held it.

    uv run --project sidecar python research/spikes/music_only/diag_songs.py [rec ...]

Production engine with the half-gap lead, live MLX transcripts, room's clock.
The singing detector is run after every update and its score pushed onto the
scheduler exactly as `server._observe_singing` does, so the talking gate acts
as it would live (the plain eval harness leaves it at 0 and never blocks).

Per update: the tracker's top song and that song's share of the whole belief,
the song that is truly being sung (golden, at the update's audio time), the
song on screen, and — when the tracker wants another song — the first
cross-song gate that refuses, in the scheduler's own order: conditional
confidence < 0.70, talking >= 0.5, song share < 0.6, fewer than 3
consecutive observations, fewer than 4 words.

Per song change in the golden: seconds until the tracker first names the new
song (and holds it 3 updates), until the screen shows it, and the gate
refusals in between.
"""
from __future__ import annotations

import json
import sys
from collections import Counter

import numpy as np

import cv
import diag_fusion as dg
import exp_music_clock as emc
import fusion
from auto_presenter.asr.types import TranscriptEvent, Word
from auto_presenter.decision.production import build_engine
from auto_presenter.decision.singing import SingingDetector
from auto_presenter.eval.metrics import wrong_slide_rate
from auto_presenter.eval.replay import load_recording
from auto_presenter.eval.types import Decision
from auto_presenter.index.library import build_index
from auto_presenter.protocol import ParticleFilterTunables


def gate(sch, tracked) -> str | None:
    """The first cross-song gate that refuses `tracked`, or None — read-only,
    replicating `_cross_song_ready`'s order without touching its state."""
    cfg = sch.config
    shown = sch._last_committed
    if shown is None or shown[0] == tracked.item_idx:
        return None
    if tracked.confidence < cfg.cross_song_marginal_threshold:
        return "confidence<0.70"
    if cfg.cross_song_max_talking_score is not None and sch.talking_score >= cfg.cross_song_max_talking_score:
        return "talking"
    if cfg.cross_song_min_song_share is not None and tracked.song_share is not None and tracked.song_share < cfg.cross_song_min_song_share:
        return "song share<0.6"
    pend = sch._pending_count + (1 if tracked.item_idx == sch._pending_item else 1)
    if pend < cfg.cross_song_min_observations:
        return "streak<3"
    return "words<4 or other"


def run(key, events, talking=True, hook=None):
    rec = load_recording(cv.S / key)
    engine = build_engine(build_index(fusion.content(rec)), ParticleFilterTunables())
    sch = engine.scheduler
    emc.half_gap(sch)
    singing = SingingDetector()
    log = []
    orig = sch.on_position
    cur = {}

    def traced(tracked):
        cur["gate"] = gate(sch, tracked)
        cur["tracked"] = tracked
        d = orig(tracked)
        cur["d"] = d
        return d

    sch.on_position = traced
    decisions = []
    for ev in events:
        if hook:
            hook(engine, ev)
        cur.clear()
        te = TranscriptEvent(words=[Word(text=w["text"], t_start=w["t_start"], t_end=w["t_end"]) for w in ev["words"]],
                             is_final=True, segment_id=ev["segment_id"])
        d = engine.update(te)
        ff = engine.filter.filter
        if talking:
            singing.observe(ff.belief.evidence, ev["t_end"], len(ev["words"]))
            sch.talking_score = singing.score
        b = ff.belief
        shares = Counter()
        for (i, _s, _w, _d), v in ff._alpha.items():
            shares[i] += v
        tot = sum(shares.values()) or 1.0
        log.append({"t_end": ev["t_end"], "avail": ev["available_at"], "words": len(ev["words"]),
                    "item": b.item_idx, "share": b.song_share, "shares": {k: v / tot for k, v in shares.items()},
                    "conf": cur["tracked"].confidence if "tracked" in cur else None,
                    "gate": cur.get("gate"), "talk": sch.talking_score,
                    "shown": sch._last_committed})
        if d is not None:
            decisions.append(Decision(t_seconds=ev["available_at"], item_idx=d.item_idx, slide_idx=d.slide_idx, source="ai"))
    return rec, decisions, log


def report(key, events):
    rec, dec, log = run(key, events)
    _, dec_nt, _ = run(key, events, talking=False)
    kw = dict(forgive_unpredictable=True, setlist_content=rec.setlist_content, forgive_identical_slides=True)
    print(f"=== {key}: wrong-slide {wrong_slide_rate(dec, rec.ground_truth, None, **kw):.3f} with the talking gate live"
          f" ({wrong_slide_rate(dec_nt, rec.ground_truth, None, **kw):.3f} without it, as the eval harness runs)")
    gt = rec.ground_truth
    changes = [g for i, g in enumerate(gt) if i == 0 or g.item_idx != gt[i - 1].item_idx]
    for ci, g in enumerate(changes):
        t0 = g.t_seconds
        t_end_song = changes[ci + 1].t_seconds if ci + 1 < len(changes) else 1e9
        seg = [e for e in log if t0 - 2 <= e["avail"] < t_end_song]
        # tracker: first update where the top song is the new one and stays for 3 updates
        tr = None
        for k in range(len(seg) - 2):
            if all(seg[k + j]["item"] == g.item_idx for j in range(3)):
                tr = seg[k]["avail"] - t0
                break
        sc_ = next((d.t_seconds - t0 for d in dec if t0 - 2 <= d.t_seconds < t_end_song and d.item_idx == g.item_idx), None)
        gates = Counter(e["gate"] for e in seg if e["gate"] is not None and e["item"] == g.item_idx)
        share_true = [e["shares"].get(g.item_idx, 0.0) for e in seg[:30]]
        words = sum(e["words"] for e in seg if e["avail"] - t0 < 20)
        talk = [e["talk"] for e in seg[:30]]
        print(f"  song {g.item_idx} sung from {t0:5.0f} s (until {min(t_end_song, 9999):5.0f}): tracker names it after "
              f"{('%.0f s' % tr) if tr is not None else 'never'}; screen after {('%.0f s' % sc_) if sc_ is not None else 'never'}")
        print(f"      first 30 updates: its share of the belief median {np.median(share_true) if share_true else 0:.2f}"
              f" (max {max(share_true) if share_true else 0:.2f}); talking score median {np.median(talk) if talk else 0:.2f};"
              f" words delivered in its first 20 s: {words}")
        if gates:
            print("      gates refusing while the tracker named it: " + ", ".join(f"{k} x{v}" for k, v in gates.most_common()))
    return rec, dec, log


def main() -> int:
    events = json.loads(fusion.EVENTS.read_text())
    for key in (sys.argv[1:] or ["set1002", "nbtb"]):
        report(key, events[load_recording(cv.S / key).id])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
