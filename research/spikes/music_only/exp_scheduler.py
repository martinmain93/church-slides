"""Two scheduler corrections, derived (not swept), on the room's clock.

    uv run --project sidecar python research/spikes/music_only/exp_scheduler.py

Diagnosed by `diag_scheduler.py`:

* half-gap lead — the lead is `delivery lag + gap to the next update`, the
  rule for never being late. Wrong-slide scores time, early and late alike,
  and the error-minimising time to act is when waiting for the next chance
  would overshoot by more than acting now: `lag + gap / 2`.
* retract — an anticipation predicts the band reaches the next slide within
  the lead. If a confident tracker still says the previous slide after
  `lead + gap` (the prediction has had its time and one more update), it was
  wrong: allow that one-slide move back. This is the existing
  `backward_one_after_seconds`, set from the scheduler's own lead instead of
  a constant. Its 2026-08-21 revert was because a correction re-anchored the
  particle filter; the forward tracker does not re-anchor on commits.

Nothing else changes. Reported: wrong-slide (harness's forgiving mode),
median anticipation (negative = early), commits.
"""

from __future__ import annotations

import json

import numpy as np

import cv
import fusion
from auto_presenter.asr.types import TranscriptEvent, Word
from auto_presenter.decision.production import build_engine
from auto_presenter.eval.metrics import anticipation_error_ms, wrong_slide_rate
from auto_presenter.eval.replay import load_recording
from auto_presenter.eval.types import Decision
from auto_presenter.index.library import build_index
from auto_presenter.protocol import ParticleFilterTunables


def run(rec_dir, events, half_gap=False, retract=False):
    rec = load_recording(rec_dir)
    engine = build_engine(build_index(fusion.content(rec)), ParticleFilterTunables())
    sch = engine.scheduler
    cfg = sch.config
    if half_gap:
        orig_lead = sch.effective_lead_seconds

        def lead():
            if cfg.fixed_lead_time_ms is not None:
                return orig_lead()
            lag = sch.observed_lag_seconds if sch.observed_lag_seconds is not None else cfg.fallback_lag_seconds
            gap = sch._gap_estimate if sch._gap_estimate is not None else 0.0
            return max(cfg.min_lead_seconds, min(cfg.max_lead_seconds, lag + gap / 2 + cfg.target_margin_ms / 1000.0))

        sch.effective_lead_seconds = lead
    if retract:
        orig = sch.on_position

        def on_position(tracked):
            gap = sch._gap_estimate if sch._gap_estimate is not None else 0.0
            cfg.backward_one_after_seconds = sch.effective_lead_seconds() + gap
            return orig(tracked)

        sch.on_position = on_position
    decisions = []
    for ev in events:
        te = TranscriptEvent(words=[Word(text=w["text"], t_start=w["t_start"], t_end=w["t_end"]) for w in ev["words"]],
                             is_final=True, segment_id=ev["segment_id"])
        d = engine.update(te)
        if d is not None:
            decisions.append(Decision(t_seconds=ev["available_at"], item_idx=d.item_idx, slide_idx=d.slide_idx, source="ai"))
    wrong = wrong_slide_rate(decisions, rec.ground_truth, None, forgive_unpredictable=True,
                             setlist_content=rec.setlist_content, forgive_identical_slides=True)
    ant = anticipation_error_ms(decisions, rec.ground_truth)
    return wrong, float(np.median(ant)) if ant else float("nan"), len(decisions)


def main() -> int:
    events = json.loads(fusion.EVENTS.read_text())
    arms = {"production": {}, "half-gap lead": {"half_gap": True}, "retract": {"retract": True},
            "both": {"half_gap": True, "retract": True}}
    keys = list(cv.SONGS) + ["set1002"]
    res = {}
    for key in keys:
        rid = load_recording(cv.S / key).id
        if rid not in events:
            continue
        res[key] = {a: run(cv.S / key, events[rid], **kw) for a, kw in arms.items()}
    print(f"{'recording':9s} " + "".join(f"{a:>24s}" for a in arms))
    for key, r in res.items():
        print(f"{key:9s} " + "".join(f"{r[a][0]:10.3f} {r[a][1]:+6.0f}ms {r[a][2]:3d}c" for a in arms))
    five = [k for k in res if k in cv.SONGS]
    print(f"{'mean (5)':9s} " + "".join(f"{np.mean([res[k][a][0] for k in five]):10.3f} {np.median([res[k][a][1] for k in five]):+6.0f}ms     " for a in arms))
    for a in list(arms)[1:]:
        d = [res[k][a][0] - res[k]["production"][0] for k in five]
        print(f"  {a:14s} vs production on the 5: better {sum(x < -0.005 for x in d)}, worse {sum(x > 0.005 for x in d)}, mean {np.mean(d):+.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
