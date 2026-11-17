"""Let music move the screen between transcript updates.

    uv run --project sidecar python research/spikes/music_only/exp_music_clock.py

Diagnosis (diag_drought_music.py): at slide changes the live music filter
knows a new line has started sooner than the words-driven screen gets there
(54% within 1 s vs 44%; 50% vs 25% after >= 4 s without words; control 17%),
but the scheduler only acts when a transcript update arrives — and in a word
drought none do. So music's earlier knowledge has had nowhere to go.

The rule tested here, deliberately minimal, with settings fixed in advance:
on the audio clock at 10 Hz, between transcript updates, if the live music
belief (causal, 3 s stem lag, projected to now) has sat for HOLD seconds in
music that the *next slide in order* covers, with at least PEAK of its mass
there, show that slide. Music says only "the next line has started"; slide
order supplies which pass. It never moves backward and never changes song.

Base: the production engine with the half-gap lead (exp_scheduler.py),
live MLX transcripts, scored on the room's clock with the harness's metric.
"""
from __future__ import annotations

import json

import numpy as np

import cv
import diag_drought_music as ddm
import fusion
from auto_presenter.asr.types import TranscriptEvent, Word
from auto_presenter.decision.production import build_engine
from auto_presenter.eval.metrics import anticipation_error_ms, wrong_slide_rate
from auto_presenter.eval.replay import load_recording
from auto_presenter.eval.types import Decision
from auto_presenter.index.library import build_index
from auto_presenter.protocol import ParticleFilterTunables

HOLD = 1.0
PEAK = 0.5
STEP = 0.1


def half_gap(sch):
    cfg = sch.config

    def lead():
        lag = sch.observed_lag_seconds if sch.observed_lag_seconds is not None else cfg.fallback_lag_seconds
        gap = sch._gap_estimate if sch._gap_estimate is not None else 0.0
        return max(cfg.min_lead_seconds, min(cfg.max_lead_seconds, lag + gap / 2 + cfg.target_margin_ms / 1000.0))

    sch.effective_lead_seconds = lead


def commit(engine, item, slide, t):
    """Show a slide through the engine's own bookkeeping, as the scheduler
    would have: displayed, last committed, commit time, song progress."""
    sch = engine.scheduler
    engine._displayed = (item, slide)
    sch._last_committed = (item, slide)
    sch._last_commit_t = t
    sch.progress.note_commit(item, slide)


def run(key, events, musics: dict | None):
    rec = load_recording(cv.S / key)
    engine = build_engine(build_index(fusion.content(rec)), ParticleFilterTunables())
    sch = engine.scheduler
    half_gap(sch)
    decisions, music_moves = [], 0
    run_len = 0.0
    cand = None
    t = 0.0
    for ev in events:
        # the music clock, up to this update's delivery
        while musics and t + STEP <= ev["available_at"]:
            t += STEP
            shown = engine._displayed
            if shown is None or shown[0] not in musics:
                continue
            song, mu = musics[shown[0]]
            nxt = shown[1] + 1
            if nxt >= len(rec.setlist_content[shown[0]].slides) or not rec.setlist_content[shown[0]].slides[nxt]:
                continue
            p = mu.belief_at(t, t)
            # "the next line has started" = the music is in the next slide's
            # music AND has left the shown slide's. Without the second half a
            # hymn, whose verses share one tune, satisfies it mid-verse.
            cov = song.scorer.cover_eq[int(np.argmax(p))] if p is not None else set()
            ok = p is not None and p.max() >= PEAK and nxt in cov and shown[1] not in cov
            if ok and cand == (shown[0], nxt):
                run_len += STEP
            else:
                cand, run_len = ((shown[0], nxt), STEP) if ok else (None, 0.0)
            if cand and run_len >= HOLD - 1e-9:
                commit(engine, cand[0], cand[1], t)
                decisions.append(Decision(t_seconds=t, item_idx=cand[0], slide_idx=cand[1], source="ai"))
                music_moves += 1
                cand, run_len = None, 0.0
        te = TranscriptEvent(words=[Word(text=w["text"], t_start=w["t_start"], t_end=w["t_end"]) for w in ev["words"]],
                             is_final=True, segment_id=ev["segment_id"])
        d = engine.update(te)
        if d is not None:
            decisions.append(Decision(t_seconds=ev["available_at"], item_idx=d.item_idx, slide_idx=d.slide_idx, source="ai"))
    wrong = wrong_slide_rate(decisions, rec.ground_truth, None, forgive_unpredictable=True,
                             setlist_content=rec.setlist_content, forgive_identical_slides=True)
    ant = anticipation_error_ms(decisions, rec.ground_truth)
    return wrong, float(np.median(ant)) if ant else float("nan"), len(decisions), music_moves


def main() -> int:
    events = json.loads(fusion.EVENTS.read_text())
    res = {}
    for key in list(cv.SONGS) + list(cv.SETS):
        rid = load_recording(cv.S / key).id
        musics = {}
        for song in ddm.songs_for(key):
            item = cv.stage1.song_item(song.s, cv.S / key)
            musics[item] = (song, fusion.Music(song, "stems melody", ddm.SETTING))
        res[key] = (run(key, events[rid], None), run(key, events[rid], musics))
    print(f"{'recording':9s}  half-gap lead (words)         + music clock")
    for key, (a, b) in res.items():
        print(f"{key:9s}  {a[0]:.3f}  {a[1]:+6.0f} ms  {a[2]:3d}c      {b[0]:.3f}  {b[1]:+6.0f} ms  {b[2]:3d}c  ({b[3]} music moves)")
    five = list(cv.SONGS)
    d = [res[k][1][0] - res[k][0][0] for k in five]
    print(f"\nmean (5 goldens): {np.mean([res[k][0][0] for k in five]):.3f} -> {np.mean([res[k][1][0] for k in five]):.3f};"
          f" better {sum(x < -0.005 for x in d)}, worse {sum(x > 0.005 for x in d)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
