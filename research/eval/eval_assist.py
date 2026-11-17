"""The AI-assisted mode yardstick: engine tapes + simulated operators.

    uv run --project sidecar python research/eval/eval_assist.py [--tapes eval_tapes] \\
        [--set place_dwell_s=3 --set next_late_s=1 ...] [--json out.json] [-v]

Replays each tape from `research/eval/record_engine_tapes.py` through
`decision.assist.AssistInterpreter` against four operators, on a 0.1 s clock:

  - **good**: follows the golden 0.5-1.5 s after each cue. Every place-tier
    suggestion is a false alarm; a next-tier pulse while the screen is
    already right is a false pulse.
  - **missed_song**: stays on the previous song when the band starts a new
    one (a decoy song, for single-song goldens), up to 30 s.
  - **wrong_section**: every 5th cue of a song, puts up a different section of
    the same song and stays there up to 15 s.
  - **stuck**: every 5th cue (offset), stops advancing for up to 20 s.

Operators react like people: a correct suggestion is taken 1 s after it
appears (that ends the mistake), a wrong one is dismissed after 3 s. A
suggestion is correct when it names the place the golden had at any time in
the 4 s before it was raised or the 1 s after (the engine hears late).

What a tape can't show: a dismissal re-anchors the engine live, but the tape's
engine ran free, so here a dismissal only mutes. See Plan/AI_ASSISTED_MODE.md.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from itertools import pairwise
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "sidecar"))

from auto_presenter.decision.assist import AssistConfig, AssistInterpreter  # noqa: E402
from auto_presenter.eval.replay import load_recording  # noqa: E402
from auto_presenter.index.library import build_index  # noqa: E402
from auto_presenter.protocol import SetlistContent, SetlistItemContent  # noqa: E402

RECORDINGS = Path("~/Library/Application Support/com.churchslides.app/recordings").expanduser()
DT = 0.1
REACT_TAKE_S = 1.0
REACT_DISMISS_S = 3.0
CORRECT_BEFORE_S = 4.0
CORRECT_AFTER_S = 1.0
SCENARIOS = ("good", "missed_song", "wrong_section", "stuck")


@dataclass
class Mistake:
    """The operator shows `shown` instead of the truth from `start` to `end`
    (the end moves earlier when a correct suggestion is taken)."""

    start: float
    end: float
    shown: tuple[int, int] | None  # None: frozen on whatever was up at `start`
    caught_at: float | None = None
    caught_tier: str = ""
    wrong_suggestions: int = 0
    alt_had_truth: int = 0


@dataclass
class Result:
    recording: str
    scenario: str
    seconds: float
    mistakes: list[Mistake] = field(default_factory=list)
    false_place: int = 0
    false_next: int = 0
    next_pulses: int = 0
    place_raised: int = 0
    raises: list[tuple[float, str, int, int, bool]] = field(default_factory=list)


class Truth:
    def __init__(self, cues: list[tuple[float, int, int]], start: tuple[int, int]):
        self.cues = cues
        self.start = start

    def at(self, t: float) -> tuple[int, int]:
        cur = self.start
        for ct, i, s in self.cues:
            if ct > t:
                break
            cur = (i, s)
        return cur

    def places_between(self, interp: AssistInterpreter, a: float, b: float) -> set:
        out = {interp.place(*self.at(a))}
        for ct, i, s in self.cues:
            if a < ct <= b:
                out.add(interp.place(i, s))
        return out


def load(tape_path: Path):
    tape = json.loads(tape_path.read_text())
    rec = load_recording(RECORDINGS / tape["recording"])
    content = SetlistContent(
        items=[
            SetlistItemContent(
                item_idx=i["item_idx"], song_id=i["song_id"], slides=i["slides"],
                slide_sections=i.get("slide_sections") or [], title=i.get("title", ""),
            )
            for i in tape["setlist"]
        ]
    )
    setlist = build_index(content)
    item_map = {int(k): v for k, v in tape["item_map"].items()}
    cues = sorted(
        (g.t_seconds, item_map.get(g.item_idx, g.item_idx), g.slide_idx) for g in rec.ground_truth
    )
    first_item = cues[0][1] if cues else 0
    truth = Truth(cues, (first_item, 0))
    duration = rec.duration_seconds or (cues[-1][0] + 10 if cues else 0)
    return tape, setlist, truth, duration


def has_words(setlist, item_idx: int, slide_idx: int) -> bool:
    item = setlist.item(item_idx)
    slide = item.slide(slide_idx) if item else None
    return bool(slide and slide.words)


def plant(scenario: str, tape, setlist, truth: Truth, rng: random.Random) -> list[Mistake]:
    places = AssistInterpreter(setlist=setlist)
    lyric_cues = [c for c in truth.cues if has_words(setlist, c[1], c[2])]
    out: list[Mistake] = []
    if scenario == "missed_song":

        def last_lyric(item_idx: int) -> tuple[int, int]:
            item = setlist.item(item_idx)
            return item_idx, max(sl.slide_idx for sl in item.slides if sl.words)

        decoy = tape["start_item_idx"]
        if lyric_cues and decoy not in {c[1] for c in truth.cues}:
            # The band starts a song the screen isn't on, from the very top.
            out.append(Mistake(start=0.0, end=lyric_cues[0][0] + 30.0, shown=last_lyric(decoy)))
        for (_, prev, _), (t, i, _) in pairwise(lyric_cues):
            if i != prev:
                out.append(Mistake(start=t + rng.uniform(0.5, 1.5), end=t + 30.0, shown=last_lyric(prev)))
        return out
    per_song: dict[int, list[tuple[float, int, int]]] = defaultdict(list)
    for c in lyric_cues:
        per_song[c[1]].append(c)
    offset = 2 if scenario == "wrong_section" else 4
    for song_cues in per_song.values():
        for t, i, s in song_cues[offset::5]:
            start = t + rng.uniform(0.5, 1.5)
            if scenario == "stuck":
                out.append(Mistake(start=start, end=t + 20.0, shown=None))
                continue
            near = {places.place(i, s + d) for d in (-1, 0, 1)}
            options = [sl.slide_idx for sl in setlist.item(i).slides
                       if sl.words and places.place(i, sl.slide_idx) not in near]
            if options:
                out.append(Mistake(start=start, end=t + 15.0, shown=(i, rng.choice(options))))
    # No overlaps: a later mistake waits for the earlier one's planned end.
    kept: list[Mistake] = []
    for m in sorted(out, key=lambda m: m.start):
        if not kept or m.start >= kept[-1].end + 5.0:
            kept.append(m)
    return kept


def simulate(tape, setlist, truth: Truth, duration: float, scenario: str, cfg: AssistConfig,
             seed: int) -> Result:
    rng = random.Random(seed)
    interp = AssistInterpreter(setlist=setlist, config=cfg)
    mistakes = plant(scenario, tape, setlist, truth, rng) if scenario != "good" else []
    reactions = {i: rng.uniform(0.5, 1.5) for i in range(len(truth.cues))}
    res = Result(tape["recording"], scenario, duration, mistakes)
    events = tape["events"]
    ei = 0
    shown: tuple[int, int] | None = None
    pending_dismiss: list[tuple[float, int]] = []
    frozen: dict[int, tuple[int, int]] = {}

    def good_at(t: float) -> tuple[int, int]:
        cur = truth.start
        for k, (ct, i, s) in enumerate(truth.cues):
            if ct + reactions[k] > t:
                break
            cur = (i, s)
        return cur

    def active(t: float) -> tuple[int, Mistake] | None:
        for k, m in enumerate(mistakes):
            if m.start <= t < m.end:
                return k, m
        return None

    def handle(out: list, t: float, act) -> None:
        for ev in out:
            if ev.kind != "raise":
                continue
            sug = ev.suggestion
            correct = interp.place(sug.item_idx, sug.slide_idx) in truth.places_between(
                interp, t - CORRECT_BEFORE_S, t + CORRECT_AFTER_S)
            res.raises.append((t, sug.tier, sug.item_idx, sug.slide_idx, correct))
            on_time = shown is not None and interp.place(*shown) == interp.place(*truth.at(t))
            if sug.tier == "next":
                res.next_pulses += 1
                if on_time and act is None:
                    res.false_next += 1
            else:
                res.place_raised += 1
                if act is None:
                    res.false_place += 1
            if act is not None:
                _, m = act
                if correct and m.caught_at is None:
                    m.caught_at = t
                    m.caught_tier = sug.tier
                    m.end = min(m.end, t + REACT_TAKE_S)
                elif not correct:
                    m.wrong_suggestions += 1
                    truth_place = interp.place(*truth.at(t))
                    if any(interp.place(*a) == truth_place for a in sug.alternatives):
                        m.alt_had_truth += 1
            if not correct and sug.tier == "place":
                pending_dismiss.append((t + REACT_DISMISS_S, sug.id))
    t = 0.0
    while t <= duration:
        out = []
        while ei < len(events) and events[ei]["t"] <= t:
            ev = events[ei]
            ei += 1
            if ev["kind"] == "slide_change":
                out += interp.engine_move(t, ev["item_idx"], ev["slide_idx"], ev.get("confidence", 1.0))
            elif ev["kind"] == "belief_state":
                ranked = [(int(h["song_id"]), h["slide_idx"]) for h in ev.get("top_3", [])]
                out += interp.candidates(t, ranked, lost=bool(ev.get("is_lost")))
        act = active(t)
        handle(out, t, act)
        out = []
        if act is not None:
            k, m = act
            if m.shown is None:
                frozen.setdefault(k, shown or good_at(m.start))
                want = frozen[k]
            else:
                want = m.shown
        else:
            want = good_at(t)
        if want != shown:
            shown = want
            out += interp.displayed(t, *want)
        for due, sid in list(pending_dismiss):
            if due <= t:
                pending_dismiss.remove((due, sid))
                out += interp.dismiss(t, sid)
        out += interp.tick(t)
        handle(out, t, act)
        t = round(t + DT, 6)
    return res


def summarise(results: list[Result]) -> dict:
    out: dict[str, dict] = {}
    for sc in SCENARIOS:
        rs = [r for r in results if r.scenario == sc]
        if not rs:
            continue
        hours = sum(r.seconds for r in rs) / 3600
        ms = [m for r in rs for m in r.mistakes]
        caught = [m for m in ms if m.caught_at is not None]
        lat = sorted(m.caught_at - m.start for m in caught)
        out[sc] = {
            "hours": round(hours, 2),
            "false_place_per_hour": round(sum(r.false_place for r in rs) / hours, 2) if hours else 0,
            "false_next_per_hour": round(sum(r.false_next for r in rs) / hours, 2) if hours else 0,
            "next_pulses_per_hour": round(sum(r.next_pulses for r in rs) / hours, 2) if hours else 0,
            "mistakes": len(ms),
            "caught": len(caught),
            "caught_within_15s": sum(1 for x in lat if x <= 15.0),
            "median_latency_s": round(lat[len(lat) // 2], 1) if lat else None,
            "wrong_suggestions": sum(m.wrong_suggestions for m in ms),
            "wrong_with_truth_in_alternatives": sum(m.alt_had_truth for m in ms),
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tapes", type=Path, default=REPO / "eval_tapes")
    ap.add_argument("--set", action="append", default=[], help="AssistConfig field=value")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--json", type=Path)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    cfg = AssistConfig()
    for kv in args.set:
        k, _, v = kv.partition("=")
        kind = type(getattr(cfg, k))
        # bool("0") is True: read a switch by its word
        setattr(cfg, k, v.lower() in ("1", "true", "yes", "on") if kind is bool else kind(v))
    tapes = sorted(args.tapes.glob("rec_*.json"))
    if not tapes:
        print(f"no tapes in {args.tapes}; record some with research/eval/record_engine_tapes.py")
        return 1
    results: list[Result] = []
    for path in tapes:
        tape, setlist, truth, duration = load(path)
        for sc in SCENARIOS:
            r = simulate(tape, setlist, truth, duration, sc, cfg, args.seed)
            results.append(r)
            if args.verbose:
                caught = sum(1 for m in r.mistakes if m.caught_at is not None)
                print(f"  {r.recording} {sc:13s} place {r.place_raised:2d} (false {r.false_place}) "
                      f"next {r.next_pulses:2d} (false {r.false_next})  caught {caught}/{len(r.mistakes)}")
    summary = summarise(results)
    print(f"\nconfig: {cfg}")
    for sc, row in summary.items():
        print(f"\n{sc}:")
        for k, v in row.items():
            print(f"  {k:34s} {v}")
    if args.json:
        args.json.write_text(json.dumps({"config": cfg.__dict__, "summary": summary}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
