"""Continuous (scrolling) mode from the score follower, replayed on live traces.

    uv run --project sidecar python research/spikes/music_only/flow_replay.py [CASES]

Three sources for the column's position, all from the same live words (the
8 follower traces, see `multi_replay.CASES`), all scored by
`research/eval/eval_continuous.py`'s own measure — at each moment, is the sung
line's text inside the readable band of the viewport? — against the golden:

* ``words``   — the production word tracker through `FlowFollower`, what a
  song without a followed reference scrolled with before 2026-10-08
  (`eval_continuous.replay` fed the trace's words at their arrival times);
* ``library`` — the follower on library slides alone (`spec.library_spec`),
  what a song without a lead sheet scrolls with now;
* ``sheet``   — the follower on its lead sheet.

The follower arms run exactly the live path: each read-out becomes
`live.flow_evidence`, mapped onto the display's words and fed to a
`FlowFollower` as `server_follower.observe_follower_flow` does. Song changes
come from the trace (what production decided live); the word arm's engine
picks songs itself, as it does live.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import eval_continuous as ec  # noqa: E402
import nosheet_replay as N  # noqa: E402
from music_replay import d, load_recording, mr, online  # noqa: E402

from auto_presenter.asr.types import TranscriptEvent, Word  # noqa: E402
from auto_presenter.decision.flow import FlowConfig, FlowFollower  # noqa: E402
from auto_presenter.follower.live import flow_evidence  # noqa: E402
from auto_presenter.index.library import build_index  # noqa: E402

LIBRARY = Path.home() / "Library/Application Support/com.churchslides.app/library.db"
VIEWS = (ec.Viewport(lines=7.0), ec.Viewport(lines=10.0))
STEP = 0.25


def trace_events(rows) -> list[ec.TimedEvent]:
    """The trace's words as final events, grouped by when they arrived."""
    by_arr: dict[float, list[Word]] = {}
    for r in rows:
        if r["e"] == "word":
            by_arr.setdefault(round(r["arr"], 3), []).append(
                Word(text=r["text"], t_start=r["t"], t_end=r["t"] + 0.3, confidence=1.0)
            )
    return [
        ec.TimedEvent(TranscriptEvent(words=ws, is_final=True, segment_id=i), arr)
        for i, (arr, ws) in enumerate(sorted(by_arr.items()))
    ]


def follower_tracks(name: str, mode: str, rec, indexed) -> list:
    """Run the follower over the trace the live way; return [(t, FlowTrack)]."""
    captured = []
    orig = online.OnlineFollower.readout
    orig_item = N.M.replay_item
    current = {"item": None}

    def readout(self, wall, *a, **k):
        r = orig(self, wall, *a, **k)
        captured.append((wall, current["item"], self, r))
        return r

    def replay_item(rows, ref, owner, spans, ident, item, *a, **k):
        current["item"] = item
        yield from orig_item(rows, ref, owner, spans, ident, item, *a, **k)

    online.OnlineFollower.readout = readout
    N.M.replay_item = replay_item
    N.MODE = mode
    try:
        _arm_name, arm = N.M.parse("x:")
        N.M.apply(arm)
        N.decisions(name, arm)
    finally:
        online.OnlineFollower.readout = orig
        N.M.replay_item = orig_item
    rows = d.load_trace(mr.CASES[name][1])
    enters = sorted((r["t"], r["item"]) for r in rows if r["e"] == "enter")

    def item_at(t):
        cur = None
        for te, it in enters:
            if te > t:
                break
            cur = it
        return cur

    flow = FlowFollower(setlist=indexed, config=FlowConfig())
    tracks = []
    sfs: dict[int, SimpleNamespace] = {}
    for wall, item_idx, f, r in sorted(captured, key=lambda x: x[0]):
        if item_idx is None or item_at(wall) != item_idx:
            continue  # this follower's song is not on screen
        sf = sfs.setdefault(id(f), SimpleNamespace(item_idx=item_idx, ref=f.ref, cell_words=None))
        ev = flow_evidence(sf, r, wall)
        if ev is None:
            continue
        item = indexed.item(item_idx)
        alpha: dict = {}
        for (si, j), mass in ev.mass.items():
            if si >= len(item.slides):
                continue
            n_shown, n_mine = len(item.slides[si].words), ev.words.get(si, 0)
            if n_shown == 0 or n_mine == 0:
                continue
            w = min(n_shown - 1, round(j * n_shown / n_mine))
            key = (item_idx, si, w, 0)
            alpha[key] = alpha.get(key, 0.0) + mass
        track = flow.update(alpha, t_observed=ev.t, t_now=ev.t, rate_wps=ev.rate_wps)
        if track is not None:
            tracks.append((wall, track))
    return tracks


def main() -> int:
    names = sys.argv[1].split(",") if len(sys.argv) > 1 else list(mr.CASES)
    breaks = ec.library_line_breaks(LIBRARY)
    totals = {arm: {v.lines: ec.Tally() for v in VIEWS} for arm in ("words", "library", "sheet")}
    for name in names:
        rdir, trace, _sheets = mr.CASES[name]
        rec = load_recording(rdir)
        pos_of = {s.item_idx: i for i, s in enumerate(rec.setlist_content)}
        layouts = {i: ec.build_layout(s.slides, breaks)[0] for i, s in enumerate(rec.setlist_content)}
        samples = ec.ground_truth_track(rec, layouts, pos_of, STEP)
        indexed = build_index(ec._content(rec))
        arms = {
            "words": ec.replay(rec, trace_events(d.load_trace(trace)), FlowConfig())[1],
            "library": follower_tracks(name, "libspec", rec, indexed),
            "sheet": follower_tracks(name, "sheet", rec, indexed),
        }
        cells = [f"{name:7s}"]
        for arm, tracks in arms.items():
            # FlowTrack.item_idx is the setlist item; layouts are by position
            tracks = [(t, tr) for t, tr in tracks]
            for v in VIEWS:
                tally = ec.score_flow(samples, _by_pos(tracks, pos_of), layouts, v)
                totals[arm][v.lines].add(tally)
                if v.lines == 7.0:
                    cells.append(f"{arm} {ec.pct(tally.sung_visible, tally.samples)}")
        print("  ".join(cells), flush=True)
    print()
    for arm, by_view in totals.items():
        for lines, t in by_view.items():
            print(
                f"{arm:8s} {lines:4.0f} lines: sung line visible {ec.pct(t.sung_visible, t.samples)}, "
                f"next too {ec.pct(t.next_visible, t.samples)}, near anchor {ec.pct(t.near_anchor, t.samples)}, "
                f"{t.jumps} jumps, {t.back_travel:.0f} lines back, travel {t.travel:.0f}"
            )
    return 0


def _by_pos(tracks, pos_of):
    """Tracks with their item index turned into the setlist position."""
    from dataclasses import replace

    return [(t, replace(tr, item_idx=pos_of.get(tr.item_idx, tr.item_idx))) for t, tr in tracks]


if __name__ == "__main__":
    raise SystemExit(main())
