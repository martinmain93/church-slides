"""How fast does the follower lock on when the band starts mid-song?

    uv run --project sidecar python research/spikes/music_only/exp_midstart.py

The follower starts in the intro (the blank slide) at a moment when the band
is already singing — a song that starts on its chorus, or the screen left on
a blank. Evidence (live words, scored spans, chords; key known) is fed from
that moment as `exp_online.py` does, and the lock time is how long until the
follower shows the band's slide (or an identical one) and keeps it for 3 s.

Start points: the start of every operator cue in the second half of each
golden (sung slides only), so each start is a different line.
"""
from __future__ import annotations

import json

import numpy as np

import cv
import exp_online as eo
import fusion
import stage1
from auto_presenter.eval.metrics import identical_slide_groups
from auto_presenter.eval.replay import load_recording
from auto_presenter.follower import reference as fref
from auto_presenter.follower import score as fsc
from auto_presenter.follower import sheet
from auto_presenter.follower.online import OnlineFollower
from auto_presenter.follower.reference import SegmentReference


def lock_times(key: str, events, neutral_intro: bool) -> list[float]:
    song = cv.Song(key)
    rec = load_recording(cv.S / key)
    item = stage1.song_item(song.s, cv.S / key)
    ig = identical_slide_groups(rec.setlist_content)
    ident = {sl: set(ig.get((item, sl), ())) for sl in range(len(song.slides))}
    ref = SegmentReference(fsc.load(song.s.path), song.slides, stage1.load_sections(cv.S / key, item))
    if not neutral_intro:
        orig = ref.word

        def word(text, orig=orig):
            v = orig(text)
            if v is not None:
                sim = ref._word_vec("".join(ch for ch in text.lower() if ch.isalpha() or ch == "'"))
                v = v.copy()
                v[ref.intro_cell] = float(np.log(0.1) - np.log(0.1 + sim.mean()))
            return v

        ref.word = word
    _, pcs, ch = song.ev["mix"]
    k, _ = sheet.estimate_key_shift(ch[:600], ref.chord_sheet)
    ref.set_key(k)
    cues = sorted((c.t_seconds, c.slide_idx) for c in rec.ground_truth if c.item_idx == item)
    T = len(ch)
    half = T / 20
    starts = [(t, s) for t, s in cues if t > half and song.slides[s]]
    data = json.loads((cv.S / f"wscore_mlx_{key}.json").read_text())
    owners: dict[str, list[tuple[int, int]]] = {}
    for kk, sp in data["owner"].items():
        si, j = (int(x) for x in kk.split(":"))
        owners.setdefault(sp, []).append((si, j))
    win = float(data.get("win", 6.0))
    out = []
    for t0, _ in starts:
        f = OnlineFollower(ref, identical=ident)
        f.anchor(0, t0)
        f.music_enabled = True
        arrivals = []
        for e in events:
            if e["available_at"] > t0:
                for w in e["words"]:
                    if w["t_start"] > t0:
                        arrivals.append((e["available_at"], "w", (w["t_start"], w["text"])))
        for kk, (t, row) in enumerate(zip(data["steps"], data["scores"], strict=True)):
            if t - win >= t0:
                score = {sw: row[i] for i, sp in enumerate(data["spans"]) for sw in owners.get(sp, [])}
                arrivals.append((t + eo.SCORE_LAT, "s", (t - win + data["place"][kk], score)))
        arrivals.sort(key=lambda a: a[0])
        ai, held_since, lock = 0, None, None
        for i in range(int(t0 * 10) + 5, min(T, int(t0 * 10) + 600), 5):
            wall = i / 10
            while ai < len(arrivals) and arrivals[ai][0] <= wall:
                t_arr, kind, p = arrivals[ai]
                if kind == "w":
                    f.push_word(p[0], t_arr, p[1])
                else:
                    v = ref.scored(p[1])
                    if v is not None:
                        f.push_scored(p[0], t_arr, v)
                ai += 1
            for fr in range(max(int(t0 * 10), i - 5 - 7), i - 7):
                f.push_chords(fr / 10, fr / 10 + eo.CHORD_LAT, ch[fr])
            r = f.readout(wall)
            band = max((s for t, s in cues if t <= wall), default=None, key=lambda s: s if False else 0)
            band = [s for t, s in cues if t <= wall][-1]
            ok = r.slide is not None and (r.slide == band or r.slide in ident.get(band, ()))
            if r.slide is not None:
                f.shown = r.slide
            if ok:
                held_since = wall if held_since is None else held_since
                if wall - held_since >= 3.0:
                    lock = held_since - t0
                    break
            else:
                held_since = None
        out.append(lock if lock is not None else float("inf"))
    return out


def main() -> int:
    all_events = json.loads(fusion.EVENTS.read_text())
    for neutral in (False, True):
        locks = []
        for key in ("wabn", "rl", "komh", "isa"):
            rec = load_recording(cv.S / key)
            lk = lock_times(key, all_events[rec.id], neutral)
            locks += lk
            fin = [x for x in lk if np.isfinite(x)]
            print(f"  {'neutral' if neutral else 'old    '} intro {key:5s} starts {len(lk):2d}  locked {len(fin)}  "
                  f"median {np.median(fin) if fin else float('nan'):.1f} s", flush=True)
        fin = [x for x in locks if np.isfinite(x)]
        print(f"{'neutral' if neutral else 'old'} intro: locked {len(fin)}/{len(locks)} within 60 s, median {np.median(fin):.1f} s, "
              f"p75 {np.percentile(fin, 75):.1f} s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
