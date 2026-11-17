"""Fast loop, scored the way the room sees it: the perceptual model on sung timing.

    uv run --project sidecar python research/spikes/music_only/perceived_replay.py ARMS [CASES]

ARMS is `;`-separated `name:key=value,...`, music_replay's keys plus:
  ref = sheet | library     (lead-sheet reference, or the library-only follower
                             every song without a sheet gets)
  ms  = MIN_SWITCH_SHARE    (share a slide needs to replace the one shown)
  talk = 1 | 0              (online.TALK_GATE: hold the screen through talk)
  bnl  = 1 | 0              (online.BLANK_NEEDS_LYRICS: leave the blank on lyrics only)
  fbs, fbsh                 (online.FLIP_BACK_S / FLIP_BACK_SHARE; fbs=0: off)

CASES may include `talk`: the 2026-10-24 recording of people chatting over
Reckless Love, nobody singing. It is not scored; its line counts the slide
moves the follower made, and the right number is 0.

Per arm: wrong_slide_rate, the perceptual score (`eval.metrics.perceptual_score`
with `eval.sung_timing` from each recording's transcript), its early and late
parts, and each slide change against the band's first word — median, and the
share landing in the free window (inside the last line .. first word + grace).
Song changes come from the trace, as in music_replay. LEADS=1 also prints
the switches binned by how far from the first word they landed.
"""
from __future__ import annotations

import os
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "eval"))

import multi_replay as _mr  # noqa: E402

TALK = "rec_20261024-223254_551"
_mr.CASES.setdefault("talk", (_mr.REC / TALK, _mr.SPK / "talk" / f"{TALK}_nosheet.jsonl",
                              {0: "Reckless Love-lead-F#"}))


def _decide(name: str, spec: str):
    os.environ.setdefault("REF", "libspec")
    import music_replay as M

    sheet_dec = M.decisions
    import nosheet_replay as N  # replaces M.decisions with the library version

    lib_dec = N.decisions
    M.decisions = sheet_dec
    from auto_presenter.follower import online

    aname, arm = M.parse(spec)
    M.apply(arm)
    online.CHORD_WEIGHT = M.BASE_CW * arm["cw"]
    online.MELODY_WEIGHT = M.BASE_MW * arm["mw"]
    online.LYRIC_HOLD_S = arm["hold"]
    online.HOLD_MIN_SHARE = arm["hms"]
    if "ms" in arm:
        online.MIN_SWITCH_SHARE = float(arm["ms"])
    online.TALK_GATE = bool(int(arm.get("talk", 1)))
    online.BLANK_NEEDS_LYRICS = bool(int(arm.get("bnl", 1)))
    online.FLIP_BACK_S = float(arm.get("fbs", online.FLIP_BACK_S))
    online.FLIP_BACK_SHARE = float(arm.get("fbsh", online.FLIP_BACK_SHARE))
    fn = lib_dec if arm.get("ref", "sheet") == "library" else sheet_dec
    dec, rec = fn(name, arm)
    return dec, rec


def score_case(name: str, spec: str) -> dict:
    import sung_timing_check as stc
    import multi_replay as mr
    from auto_presenter.eval.metrics import perceptual_score, wrong_slide_rate, identical_slide_groups
    from auto_presenter.eval.metrics import PerceptualWeights

    dec, rec = _decide(name, spec)
    if name == "talk":
        return {"name": name, "moves": [round(d.t_seconds, 1) for d in dec if d.t_seconds > 0]}
    tm = stc.timings(Path(mr.CASES[name][0]), rec)
    p = perceptual_score(dec, rec.ground_truth, rec.setlist_content, timings=tm)
    wsr = wrong_slide_rate(dec, rec.ground_truth, None, forgive_unpredictable=True,
                           setlist_content=rec.setlist_content, forgive_identical_slides=True)
    # each slide change against the band's first word
    w = PerceptualWeights()
    eq = identical_slide_groups(rec.setlist_content)
    gt = sorted(rec.ground_truth, key=lambda g: g.t_seconds)
    slides = {i.item_idx: i.slides for i in rec.setlist_content}
    same = lambda d, g: d.item_idx == g.item_idx and d.slide_idx in eq.get((g.item_idx, g.slide_idx), {g.slide_idx})
    leads, free = [], 0
    for k in range(1, len(gt)):
        g = gt[k]
        if not slides[g.item_idx][g.slide_idx]:
            continue
        lo = tm[k - 1].first_word
        hi = tm[k + 1].first_word if k + 1 < len(gt) else tm[k].first_word + 15
        hit = next((d for d in dec if lo <= d.t_seconds < hi and same(d, g)), None)
        if hit is None:
            continue
        leads.append(hit.t_seconds - tm[k].first_word)
        free += tm[k].last_line <= hit.t_seconds < tm[k].first_word + w.late_grace_seconds
    return {"name": name, "wsr": wsr, "p": p, "leads": leads, "free": free}


def main() -> int:
    import multi_replay as mr

    specs = sys.argv[1].split(";")
    names = sys.argv[2].split(",") if len(sys.argv) > 2 else [n for n in mr.CASES if n != "talk"]
    jobs = [(n, s) for s in specs for n in names]
    with ProcessPoolExecutor(max_workers=int(os.environ.get("JOBS", "6"))) as ex:
        res = list(ex.map(score_case, *zip(*jobs)))
    by = {(s, n): r for (n, s), r in zip(jobs, res)}
    for s in specs:
        if "talk" in names:
            print(f"{s:28s} talk recording: {len(by[(s, 'talk')]['moves'])} slide moves {by[(s, 'talk')]['moves']}")
        rows = [by[(s, n)] for n in names if n != "talk"]
        if not rows:
            continue
        # means over recordings, like music_replay's wrong-slide figures
        perc = statistics.mean(r["p"].score for r in rows)
        early = statistics.mean(r["p"].early_rate for r in rows)
        late = statistics.mean(r["p"].late_rate for r in rows)
        leads = [x for r in rows for x in r["leads"]]
        free = sum(r["free"] for r in rows)
        print(f"{s:28s} wrong {statistics.mean(r['wsr'] for r in rows):.3f}  perceived {perc:.3f}"
              f" (early {early:.3f} late {late:.3f})  vs first word median {statistics.median(leads):+.2f} s,"
              f" in free window {free / len(leads):.0%} of {len(leads)} | "
              + " ".join(f"{r['name']} {r['p'].score:.3f}" for r in rows), flush=True)
        if os.environ.get("LEADS"):
            edges = (-99, -3, -1, 0, 1, 2, 4, 99)
            print("    switches vs first word: " + " | ".join(
                f"{lo:+d}..{hi:+d}s {sum(lo <= x < hi for x in leads)}" for lo, hi in zip(edges, edges[1:])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
