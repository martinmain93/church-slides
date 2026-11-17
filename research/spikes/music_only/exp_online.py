"""Does the sidecar's online follower reproduce the spike?

    uv run --project sidecar python research/spikes/music_only/exp_online.py

Feeds `auto_presenter.follower.OnlineFollower` the same cached evidence the
spike's batch `joint.follow` used — live MLX words at their measured arrival,
MLX scored spans (DTW-placed) 0.2 s after each window, BTC mix chords at
0.74 s, mix melody at 1.0 s — reading out every 0.5 s and turning read-out
changes into decisions, scored with the harness metrics on the room clock.

Arms:
  spike-like   uniform start, key from the first minute of chords (as the
               spike did), music from t = 0 — should match the spike
  live         anchored at the intro, key estimated causally (trailing 60 s,
               after 30 s of chords, rechecked every 30 s) — what the app will do
Each with and without melody.
"""
from __future__ import annotations

import json
import sys

import numpy as np

import cv
import fusion
import stage1
from auto_presenter.eval.metrics import anticipation_error_ms, identical_slide_groups, wrong_slide_rate
from auto_presenter.eval.replay import load_recording
from auto_presenter.eval.types import Decision
from auto_presenter.follower import score as fsc
from auto_presenter.follower import sheet
from auto_presenter.follower.online import SCORED_WEIGHT, OnlineFollower
from auto_presenter.follower.reference import SegmentReference

CHORD_LAT, MELODY_LAT, SCORE_LAT = 0.74, 1.0, 0.2
#: diagnostics: keep(place_s, best_span, best_score) -> bool for each scorer step
SCORED_KEEP = None  # noqa
LAST_DECISIONS: list = []


def run(key: str, events, *, melody: bool, live: bool) -> tuple[float, float, int]:
    song = cv.Song(key)
    rec = load_recording(cv.S / key)
    item = stage1.song_item(song.s, cv.S / key)
    ref = SegmentReference(fsc.load(song.s.path), song.slides, stage1.load_sections(cv.S / key, item))
    ig = identical_slide_groups(rec.setlist_content)
    ident = {sl: set(ig.get((item, sl), ())) for sl in range(len(song.slides))}
    f = OnlineFollower(ref, identical=ident)
    _, pcs, ch = song.ev["mix"]
    T = len(ch)
    if live:
        f.anchor(0, 0.0)
    else:
        k, _ = sheet.estimate_key_shift(ch[:600], ref.chord_sheet)
        ref.set_key(k)
        f.music_enabled = True
    # evidence, keyed by arrival time
    data = json.loads((cv.S / f"wscore_mlx_{key}.json").read_text())
    owners: dict[str, list[tuple[int, int]]] = {}
    for kk, sp in data["owner"].items():
        si, j = (int(x) for x in kk.split(":"))
        owners.setdefault(sp, []).append((si, j))
    win = float(data.get("win", 6.0))
    arrivals: list[tuple[float, str, tuple]] = []
    for e in events:
        for w in e["words"]:
            arrivals.append((e["available_at"], "word", (w["t_start"], w["text"])))
    for k, (t, row) in enumerate(zip(data["steps"], data["scores"])):
        span_score = {sw: row[i] for i, sp in enumerate(data["spans"]) for sw in owners.get(sp, [])}
        b = int(np.argmax(row))
        if SCORED_KEEP is not None and not SCORED_KEEP(data["place"][k], data["spans"][b], row[b]):
            continue
        arrivals.append((t + SCORE_LAT, "scored", (t - win + data["place"][k], span_score)))
    arrivals.sort(key=lambda a: a[0])
    decisions, shown, ai = [], None, 0
    key_checked = -1e9
    for i in range(5, T, 5):  # read out every 0.5 s
        wall = i / 10
        while ai < len(arrivals) and arrivals[ai][0] <= wall:
            t_arr, kind, payload = arrivals[ai]
            if kind == "word":
                f.push_word(payload[0], t_arr, payload[1])
            else:
                vec = ref.scored(payload[1])
                heard = ref.heard(payload[1])
                if vec is not None:
                    f.push_scored(payload[0], t_arr, vec, weight=SCORED_WEIGHT * ref.reliability(payload[1]), heard=heard,
                                  entropy=ref.step_entropy(payload[1]))
                elif heard:
                    f.push_heard(t_arr)
            ai += 1
        # music frames that have arrived by now
        for fr in range(i - 5, i):
            if fr / 10 + CHORD_LAT <= wall + 0.5:
                pass
        lo = max(0, i - 5 - int(CHORD_LAT * 10)), max(0, i - int(CHORD_LAT * 10))
        for fr in range(*lo):
            f.push_chords(fr / 10, fr / 10 + CHORD_LAT, ch[fr])
        if melody:
            lo = max(0, i - 5 - int(MELODY_LAT * 10)), max(0, i - int(MELODY_LAT * 10))
            for fr in range(*lo):
                f.push_melody(fr / 10, fr / 10 + MELODY_LAT, pcs[fr])
        if live and wall >= 30 and wall - key_checked >= 30:
            heard = ch[max(0, i - 600) : max(0, i - int(CHORD_LAT * 10))]
            k, _ = sheet.estimate_key_shift(heard, ref.chord_sheet)
            ref.set_key(k)
            f.music_enabled = True
            key_checked = wall
        r = f.readout(wall)
        if r.slide is not None and r.slide != shown:
            shown = r.slide
            f.shown = shown
            decisions.append(Decision(t_seconds=wall, item_idx=item, slide_idx=int(shown), source="ai"))
    LAST_DECISIONS[:] = decisions
    wrong = wrong_slide_rate(decisions, rec.ground_truth, None, forgive_unpredictable=True,
                             setlist_content=rec.setlist_content, forgive_identical_slides=True)
    ant = anticipation_error_ms(decisions, rec.ground_truth)
    return wrong, float(np.median(ant)) if ant else float("nan"), f.late_evidence


def main() -> int:
    all_events = json.loads(fusion.EVENTS.read_text())
    keys = sys.argv[1:] or ["wabn", "rl", "komh", "isa"]
    for live in (False, True):
        for melody in (False, True):
            name = f"{'live' if live else 'spike-like':10s} {'+melody' if melody else 'chords only':11s}"
            rs = []
            for key in keys:
                rec = load_recording(cv.S / key)
                w, a, late = run(key, all_events[rec.id], melody=melody, live=live)
                rs.append(w)
                print(f"  {name} {key:5s} wrong-slide {w:.3f}  anticip {a:+6.0f} ms  late {late}", flush=True)
            print(f"{name} mean {np.mean(rs):.3f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
