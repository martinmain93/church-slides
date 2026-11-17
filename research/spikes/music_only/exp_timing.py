"""Music for *when*: the lead sheet's time-left feeding the scheduler's
"is this slide ending within the lead?" test. Nothing else changes.

    uv run --project sidecar python research/spikes/music_only/exp_timing.py

Arms, all on top of the half-gap lead (exp_scheduler.py):
* words    — the scheduler as is (time-left from words / word-rate);
* average  — per position hypothesis, time-left = mean of the words estimate
             and the music estimate (equal weights, fixed in advance);
* music    — the music estimate replaces words where music has one.
Music is the live filter (causal, 3 s stem lag, projected to the belief's
time; `diag_timing.py` measured it at IQR 1.5 s against words' 2.4 s). Where
music has no estimate (no sheet, or its position is outside this slide), the
scheduler is untouched.
"""
from __future__ import annotations

import json

import numpy as np

import cv
import diag_timing as dtm
import exp_scheduler as exs
import fusion
from auto_presenter.asr.types import TranscriptEvent, Word
from auto_presenter.decision.production import build_engine
from auto_presenter.eval.metrics import anticipation_error_ms, wrong_slide_rate
from auto_presenter.eval.replay import load_recording
from auto_presenter.eval.types import Decision
from auto_presenter.index.library import build_index
from auto_presenter.protocol import ParticleFilterTunables


class SheetOracle:
    """Time left from the ground truth's sheet position and the song's tempo:
    the most a tune-position timing signal could give the scheduler."""

    def __init__(self, song):
        self.song = song
        self.lag = 0.0
        self.bpm_song = 60.0 * float(song.tl["rate"])

    def remaining(self, ev, slide):
        i = min(int(ev["t_end"] * self.song.fps), len(self.song.tl["pos"]) - 1)
        gp = self.song.tl["pos"][i]
        if not np.isfinite(gp):
            return None
        cell = min(self.song.scorer.m.P - 1, int(gp // 0.5))
        return dtm.remaining_from_position(self.song, slide, cell, self.bpm_song)


def music_remaining(music, song, ev, slide):
    if isinstance(music, SheetOracle):
        return music.remaining(ev, slide)
    p = music.belief_at(ev["t_end"], ev["available_at"])
    if p is None:
        return None
    i = min(int(min(ev["t_end"], ev["available_at"] - music.lag) * song.fps), len(music.bpm) - 1)
    return dtm.remaining_from_position(song, slide, int(np.argmax(p)), music.bpm[i])


def run(key, events, mode, music=None, song=None, commits_out=None):
    rec = load_recording(cv.S / key)
    engine = build_engine(build_index(fusion.content(rec)), ParticleFilterTunables())
    sch = engine.scheduler
    cfg = sch.config

    def lead():
        lag = sch.observed_lag_seconds if sch.observed_lag_seconds is not None else cfg.fallback_lag_seconds
        gap = sch._gap_estimate if sch._gap_estimate is not None else 0.0
        return max(cfg.min_lead_seconds, min(cfg.max_lead_seconds, lag + gap / 2 + cfg.target_margin_ms / 1000.0))

    sch.effective_lead_seconds = lead
    item = cv.stage1.song_item(song.s, cv.S / key) if song is not None else None
    cur = {}
    if mode != "words" and music is not None:
        def ending(tracked, n_words, rate, lead_seconds):
            spread = tracked.position_spread
            if cfg.transition_probability is None or not spread:
                return None
            ev = cur["ev"]
            m = music_remaining(music, song, ev, tracked.slide_idx) if tracked.item_idx == item else None
            total = sum(w for _p, w in spread)
            if total <= 0:
                return None
            hit = 0.0
            for pos, w in spread:
                words_left = (1.0 - max(0.0, min(1.0, pos))) * n_words / rate
                if m is None:
                    left = words_left
                elif mode == "average":
                    left = 0.5 * (words_left + m)
                else:
                    left = m
                if left < lead_seconds:
                    hit += w
            return hit / total

        sch._ending_probability = ending
    decisions = []
    for ev in events:
        cur["ev"] = ev
        te = TranscriptEvent(words=[Word(text=w["text"], t_start=w["t_start"], t_end=w["t_end"]) for w in ev["words"]],
                             is_final=True, segment_id=ev["segment_id"])
        d = engine.update(te)
        if d is not None:
            decisions.append(Decision(t_seconds=ev["available_at"], item_idx=d.item_idx, slide_idx=d.slide_idx, source="ai"))
    if commits_out is not None:
        commits_out.extend(decisions)
    wrong = wrong_slide_rate(decisions, rec.ground_truth, None, forgive_unpredictable=True,
                             setlist_content=rec.setlist_content, forgive_identical_slides=True)
    ant = anticipation_error_ms(decisions, rec.ground_truth)
    return wrong, float(np.median(ant)) if ant else float("nan"), len(decisions)


def main() -> int:
    events = json.loads(fusion.EVENTS.read_text())
    held = fusion.held_settings("stems melody")
    res = {}
    for key in list(cv.SONGS) + ["set1002"]:
        rid = load_recording(cv.S / key).id
        if key in cv.SONGS:
            song = cv.Song(key)
            music = fusion.Music(song, "stems melody", held[key])
        else:
            song = music = None
        res[key] = {m: run(key, events[rid], m, music, song) for m in ("words", "average", "music")}
        res[key]["production"] = exs.run(cv.S / key, events[rid])
    arms = ["production", "words", "average", "music"]
    print(f"{'':9s}" + "".join(f"{a:>22s}" for a in ["production", "half-gap (words)", "+ music avg", "+ music only"]))
    for key, r in res.items():
        print(f"{key:9s}" + "".join(f"{r[a][0]:9.3f} {r[a][1]:+6.0f}ms {r[a][2]:3d}c" for a in arms))
    five = list(cv.SONGS)
    print(f"{'mean (5)':9s}" + "".join(f"{np.mean([res[k][a][0] for k in five]):9.3f} {np.median([res[k][a][1] for k in five]):+6.0f}ms     " for a in arms))
    for a in ("average", "music"):
        d = [res[k][a][0] - res[k]["words"][0] for k in five]
        print(f"  {a:8s} vs half-gap words: better {sum(x < -0.005 for x in d)}, worse {sum(x > 0.005 for x in d)}, mean {np.mean(d):+.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
