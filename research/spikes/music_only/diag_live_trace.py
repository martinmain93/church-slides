"""Why is the live follower later than the offline replay?

    uv run --project sidecar python research/spikes/music_only/diag_live_trace.py KEY TRACE.jsonl

`TRACE.jsonl` is a live run's evidence trace (`AUTO_PRESENTER_FOLLOWER_TRACE`,
written by `follower.live` when `run_follower_eval.py --trace-dir` asks).
The follower is rebuilt exactly as the live session built it and the
recorded evidence is replayed by arrival time:

1. **as recorded** must reproduce the live read-outs (otherwise the trace is
   incomplete and nothing below means anything);
2. then one assumption at a time is moved back to what the offline replay
   (`exp_online.py`) assumed, and the timing measured each way:
   * scored spans arriving 0.2 s after their window (live: compute time),
   * chords placed at other latencies than the 0.74 s assumed,
   * chords off entirely,
   * the offline replay's chords (the cached full-file BTC) in place of the
     live analyser's,
   * the offline replay's words (the cached live MLX run) in place of this run's.

Also: per-source latency (arrival minus the audio described) live, and the
live analyser's true chord delay, by cross-correlating its probabilities with
the offline BTC's over shifts.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

import cv
import fusion
import stage1
from auto_presenter.eval.metrics import anticipation_error_ms, identical_slide_groups, wrong_slide_rate
from auto_presenter.eval.replay import load_recording
from auto_presenter.eval.types import Decision
from auto_presenter.follower import score as fsc
from auto_presenter.follower.online import SCORED_WEIGHT, OnlineFollower
from auto_presenter.follower.reference import SegmentReference
from auto_presenter.follower.scorer import spans_for
from auto_presenter.leadsheet import parse_lead_sheet


def load_trace(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def replay(rows: list[dict], ref: SegmentReference, owner, spans, ident, item: int, *,
           scored_arrival=None, chord_shift=0.0, chords=True, chord_source=None, word_source=None):
    """Re-run the follower from the trace; returns [(t, slide)] read-outs."""
    f = OnlineFollower(ref, identical=ident)
    evidence = []  # (arrival, order, kind, payload)
    control = []  # (t, order, row) — enters, keys, shown, read-outs, in trace order
    for k, r in enumerate(rows):
        if r.get("item") not in (None, item) and r["e"] != "enter":
            continue
        e = r["e"]
        if e == "word" and word_source is None:
            evidence.append((r["arr"], k, "word", (r["t"], r["text"])))
        elif e == "scored":
            arr = r["arr"] if scored_arrival is None else r["win_end"] + scored_arrival
            evidence.append((arr, k, "scored", (r["t"], r["scores"])))
        elif e == "chords" and chords and chord_source is None:
            for i in range(r["n"]):
                t = r["t"] - (r["n"] - 1 - i) * 0.1 - chord_shift
                evidence.append((r["arr"], k, "chord", (t, np.asarray(r["probs"], np.float32))))
        elif e in ("enter", "key", "shown", "readout"):
            control.append((r["t"], k, r))
    if word_source is not None:
        for t, arr, text in word_source:
            evidence.append((arr, -1, "word", (t, text)))
    if chord_source is not None and chords:
        t_arr, frames = chord_source
        for fr, probs in enumerate(frames):
            evidence.append((fr / 10 + t_arr - chord_shift, -1, "chord", (fr / 10 - chord_shift, probs)))
    evidence.sort(key=lambda x: (x[0], x[1]))
    control.sort(key=lambda x: (x[0], x[1]))
    out, ei = [], 0
    for t, _k, r in control:
        e = r["e"]
        if e == "enter":
            f.anchor(r["slide"], r["t"]) if r["item"] == item else None
            continue
        if e == "key":
            ref.set_key(r["k"])
            f.music_enabled = True
            continue
        if e == "shown":
            f.shown = r["slide"]
            continue
        while ei < len(evidence) and evidence[ei][0] <= t + 1e-9:
            _arr, _o, kind, p = evidence[ei]
            if kind == "word":
                f.push_word(p[0], _arr, p[1])
            elif kind == "scored":
                score = {sw: p[1][i] for sw, sp in owner.items() for i in [spans.index(sp)]}
                vec = ref.scored(score)
                heard = ref.heard(score)
                if vec is not None:
                    f.push_scored(p[0], _arr, vec, weight=SCORED_WEIGHT * ref.reliability(score), heard=heard,
                                  entropy=ref.step_entropy(score))
                elif heard:
                    f.push_heard(_arr)
            else:
                f.push_chords(p[0], _arr, p[1])
            ei += 1
        res = f.readout(t)
        if res.slide is not None and res.slide != f.shown:
            f.shown = res.slide
        out.append((t, res.slide, r.get("slide")))
    return out


def score(readouts, rec, item):
    decisions, shown = [], None
    for t, slide, _ in readouts:
        if slide is not None and slide != shown:
            shown = slide
            decisions.append(Decision(t_seconds=t, item_idx=item, slide_idx=int(slide), source="ai"))
    w = wrong_slide_rate(decisions, rec.ground_truth, None, forgive_unpredictable=True,
                         setlist_content=rec.setlist_content, forgive_identical_slides=True)
    a = anticipation_error_ms(decisions, rec.ground_truth)
    return w, float(np.median(a)) if a else float("nan"), np.array(a)


def main() -> int:
    key, trace_path = sys.argv[1], Path(sys.argv[2])
    rows = load_trace(trace_path)
    song = cv.Song(key)
    rec = load_recording(cv.S / key)
    item = stage1.song_item(song.s, cv.S / key)
    pdf = Path.home() / "Downloads" / f"{cv.SONGS[key]}.pdf"
    data = json.loads(parse_lead_sheet(pdf.read_bytes(), pdf.name).score_json())
    snap = rec.setlist_content[item]
    ig = identical_slide_groups(rec.setlist_content)
    ident = {sl: set(ig.get((item, sl), ())) for sl in range(len(snap.slides))}

    def fresh():
        return SegmentReference(fsc.from_extract(data), snap.slides, list(snap.slide_sections))

    owner = spans_for(snap.slides)
    spans = sorted(set(owner.values()))

    # 1. per-source latency live
    lat = defaultdict(list)
    for r in rows:
        if r["e"] in ("word", "scored", "chords"):
            lat[r["e"]].append(r["arr"] - r["t"])
        if r["e"] == "scored":
            lat["scored compute"].append(r["arr"] - r["win_end"])
            lat["scored place before window end"].append(r["win_end"] - r["t"])
    print("live latency (arrival - audio described), seconds: median / p90")
    for k, v in lat.items():
        print(f"   {k:32s} {np.median(v):5.2f} / {np.percentile(v, 90):5.2f}  (n={len(v)})")
    events = json.loads(fusion.EVENTS.read_text())[rec.id]
    off_words = [(w["t_start"], e["available_at"], w["text"]) for e in events for w in e["words"]]
    print(f"   {'offline replay words':32s} {np.median([a - t for t, a, _ in off_words]):5.2f} / "
          f"{np.percentile([a - t for t, a, _ in off_words], 90):5.2f}")

    # 2. the live analyser's chord delay against the offline BTC
    live_ch = [(r["t"] + 0.74, np.asarray(r["probs"])) for r in rows if r["e"] == "chords"]  # (frame end, probs)
    off = song.ev["mix"][2]
    best = []
    for shift in np.arange(-1.0, 3.01, 0.25):
        agree = []
        for t_end, p in live_ch[::5]:
            fr = int((t_end - shift) * 10)
            if 0 <= fr < len(off):
                agree.append(float(np.argmax(p[:24]) == np.argmax(off[fr][:24])))
        best.append((np.mean(agree) if agree else 0, shift))
    print("live chords agree with the offline BTC best when they describe audio "
          f"{max(best)[1]:.2f} s before their frame end (agreement {max(best)[0]:.0%}; assumed 0.74 s)")
    print("   by shift: " + " ".join(f"{s:+.2f}:{a:.0%}" for a, s in best))

    # chord *changes*: how long after the offline BTC changes chord does the live one follow?
    live_lab = [(t_end, int(np.argmax(p[:24]))) for t_end, p in live_ch]
    delays = []
    for i in range(1, len(live_lab)):
        t_end, lab = live_lab[i]
        if lab == live_lab[i - 1][1]:
            continue
        # the offline frame where this chord began, searching back up to 4 s
        fr = int(t_end * 10)
        start = None
        for back in range(0, 40):
            j = fr - back
            if j <= 0 or j >= len(off):
                break
            if int(np.argmax(off[j][:24])) == lab and int(np.argmax(off[j - 1][:24])) != lab:
                start = j / 10
                break
        if start is not None:
            delays.append(t_end - start)
    if delays:
        print(f"live chord changes trail the offline ones by median {np.median(delays):.2f} s "
              f"(IQR {np.percentile(delays, 25):.2f}-{np.percentile(delays, 75):.2f}, n={len(delays)})")

    # 3. replays
    variants = [
        ("as recorded", {}),
        ("scored arrive win_end+0.2", {"scored_arrival": 0.2}),
        # chord_shift: how much earlier in the audio each chord frame is placed
        ("chords at the measured 1.0 s latency", {"chord_shift": 0.26}),
        ("chords placed 0.5 s earlier", {"chord_shift": 0.5}),
        ("chords placed 1.0 s earlier", {"chord_shift": 1.0}),
        ("chords placed 0.5 s later", {"chord_shift": -0.5}),
        ("no chords", {"chords": False}),
        ("offline chords (cached BTC, 0.74 s)", {"chord_source": (0.74, off)}),
        ("offline chords arriving 1.0 s late", {"chord_source": (1.0, off)}),
        ("offline words (cached run)", {"word_source": off_words}),
    ]
    live_read = [(r["t"], r["slide"]) for r in rows if r["e"] == "readout" and r["item"] == item]
    for name, kw in variants:
        out = replay(rows, fresh(), owner, spans, ident, item, **kw)
        if name == "as recorded":
            same = np.mean([a == b for (_, a, b) in out]) if out else 0
            print(f"replay reproduces the live read-outs: {same:.1%} of {len(out)} (live read-outs: {len(live_read)})")
        w, a, arr = score(out, rec, item)
        print(f"   {name:38s} wrong-slide {w:.3f}  anticipation median {a:+5.0f} ms  "
              f"(late >1 s on {np.mean(arr > 1000) if len(arr) else 0:.0%} of cues)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
