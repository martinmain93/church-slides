"""Replay live follower traces, whole recordings (multi-song too), under follower knobs.

Song changes are taken from the trace (`enter` rows, i.e. what the production
engine/operator did live); each song's follower is replayed from its own
evidence, and its decisions are kept while it was the song on screen.
"""
import json, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import diag_live_trace as d
from auto_presenter.eval.replay import load_recording
from auto_presenter.eval.metrics import anticipation_error_ms, identical_slide_groups, wrong_slide_rate
from auto_presenter.eval.types import Decision
from auto_presenter.follower import score as fsc
from auto_presenter.follower.reference import SegmentReference
from auto_presenter.follower.scorer import spans_for
from auto_presenter.leadsheet import parse_lead_sheet

REC = Path.home() / "Library/Application Support/com.churchslides.app/recordings"
import cv  # noqa: E402

SPK = cv.S
DL = Path.home() / "Downloads"

CASES = {  # name: (recording dir, trace, {item: sheet})
    "wabn": (SPK / "wabn", SPK / "traces3/wabn_on.jsonl", {0: "What A Beautiful Name-lead-D"}),
    "rl": (SPK / "rl", SPK / "traces3/rl_on.jsonl", {0: "Reckless Love-lead-F#"}),
    "komh": (SPK / "komh", SPK / "traces3/komh_on.jsonl", {0: "King Of My Heart-lead-Bb"}),
    "isa": (SPK / "isa", SPK / "traces3/isa_on.jsonl", {0: "I Surrender All-lead-D"}),
    "since": (REC / "rec_20260818-214054_974", SPK / "holdout/traces/rec_20260818-214054_974_on.jsonl", {0: "Since Your Love-lead-B"}),
    "bml": (REC / "rec_20261006-225539_315", SPK / "holdout/traces/rec_20261006-225539_315_on.jsonl", {0: "Build My Life-lead-G"}),
    "medley": (REC / "rec_20260903-115044_425", SPK / "holdout/traces/rec_20260903-115044_425_on.jsonl",
               {0: "The Blood-lead-Eb", 1: "Nothing But The Blood (Plainfield)-lead-G"}),
    "set": (REC / "rec_20261002-185523_091", SPK / "holdout/traces/rec_20261002-185523_091_on.jsonl",
            {0: "Build My Life-lead-G", 1: "Awesome God-lead-Cm", 2: "Doxology-lead-F#", 3: "O Come All Ye Faithful (Adeste Fideles)-lead-G"}),
}
HELD_OUT = {"since", "bml", "medley", "set"}
_sheets = {}

def sheet(name):
    if name not in _sheets:
        f = DL / f"{name}.pdf"
        _sheets[name] = json.loads(parse_lead_sheet(f.read_bytes(), f.name).score_json())
    return _sheets[name]

def flicker(changes, ident, win=5.0):
    same = lambda a, b: a == b or b in ident.get(a, ())
    return sum(1 for i in range(2, len(changes))
               if changes[i-2][1] == changes[i][1] and changes[i-1][1] != changes[i][1]
               and same(changes[i-2][1][1], changes[i][1][1]) and changes[i][1][0] == changes[i-2][1][0]
               and changes[i][0] - changes[i-2][0] <= win)

def run(name):
    rdir, trace, sheets = CASES[name]
    rows = d.load_trace(trace)
    rec = load_recording(rdir)
    ig = identical_slide_groups(rec.setlist_content)
    enters = sorted((r["t"], r["item"], r["slide"]) for r in rows if r["e"] == "enter")
    end = max(r["t"] for r in rows)
    spans_on = []  # (t0, t1, item)
    for i, (t, it, _s) in enumerate(enters):
        spans_on.append((t, enters[i + 1][0] if i + 1 < len(enters) else end + 1, it))
    decisions = []
    for item, pdf in sheets.items():
        snap = [x for x in rec.setlist_content if x.item_idx == item][0]
        ident = {sl: set(ig.get((item, sl), ())) for sl in range(len(snap.slides))}
        owner = spans_for(snap.slides); spans = sorted(set(owner.values()))
        ref = SegmentReference(fsc.from_extract(sheet(pdf)), snap.slides, list(snap.slide_sections))
        out = d.replay(rows, ref, owner, spans, ident, item)
        prev = None
        for t, slide, _ in out:
            if slide is None or slide == prev:
                continue
            prev = slide
            if any(a <= t < b and it == item for a, b, it in spans_on):
                decisions.append((t, item, int(slide)))
    for t, it, s in enters:
        if t > 0:
            decisions.append((t, it, s))
    decisions.sort()
    dec, shown = [], None
    for t, it, s in decisions:
        if (it, s) != shown:
            shown = (it, s); dec.append(Decision(t_seconds=t, item_idx=it, slide_idx=s, source="ai"))
    w = wrong_slide_rate(dec, rec.ground_truth, None, forgive_unpredictable=True,
                         setlist_content=rec.setlist_content, forgive_identical_slides=True)
    a = anticipation_error_ms(dec, rec.ground_truth)
    identall = {}
    for (it, sl), g in ig.items():
        identall[(it, sl)] = {(it, x) for x in g}
    ch = [(x.t_seconds, (x.item_idx, x.slide_idx)) for x in dec]
    flick = sum(1 for i in range(2, len(ch)) if ch[i][1] == ch[i-2][1] and ch[i][0] - ch[i-2][0] <= 5.0)
    return w, float(np.median(a)) if a else float("nan"), len(dec), flick

if __name__ == "__main__":
    from auto_presenter.follower import online
    holds = [None if x == "none" else float(x) for x in sys.argv[1].split(",")]
    names = sys.argv[2].split(",") if len(sys.argv) > 2 and sys.argv[2] != "all" else list(CASES)
    nulls = [x == "1" for x in (sys.argv[3].split(",") if len(sys.argv) > 3 else ["0"])]
    for h, nu in [(h, nu) for nu in nulls for h in holds]:
        online.LYRIC_HOLD_S = h
        online.INTRO_NULL_ONLY_BEFORE_SINGING = nu
        res = {n: run(n) for n in names}
        tune = [res[n][0] for n in names if n not in HELD_OUT]
        held = [res[n][0] for n in names if n in HELD_OUT]
        print(f"hold {h} intro-null-before-singing {nu}: tuning {np.mean(tune) if tune else float('nan'):.3f}  held-out {np.mean(held) if held else float('nan'):.3f} | "
              + "  ".join(f"{n} {r[0]:.3f}/{r[2]}ch/{r[3]}fl" for n, r in res.items()), flush=True)
