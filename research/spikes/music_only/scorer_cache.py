"""Span-scorer evidence offline, at any window / step: the live `scored` rows, regenerated.

    python research/spikes/music_only/wt_run.py scorer_cache.py WINDOW STEP [SPAN] [names...] [--trace]

Runs the production `MLXSpanScorer` (small.en, MLX, encoder truncated to the
window) over each case's audio.wav the way `follower.live.LiveFollowers.side_pass`
does: the trailing WINDOW s ending at each step's `win_end`, every span of
the song current at that moment (song changes from the trace's `enter` rows,
spans in `sorted(set(spans_for(slides).values()))` order), and writes rows in
the trace's own format:

    t = win_start + place,  arr = win_end + measured compute,  win_end, place, scores

`win_end` runs on a grid 0.3 + k * STEP (the live 2 s grid sat at 6.3, 8.3, ...),
from WINDOW - 0.5 (the live buffer gate) on. A coarser step is a subsample of
a finer one: cache STEP 1 (and `--phase=0.5` for 0.5 s) and subsample (`load_rows`).

`--trace`: score at the trace's own win_end values instead (sanity check
against the live rows).

Cached under build/scorer_lat/ in this checkout.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diag_live_trace as d  # noqa: E402
import multi_replay as mr  # noqa: E402
from auto_presenter.eval.replay import load_recording  # noqa: E402
from auto_presenter.follower.scorer import MLXSpanScorer, spans_for  # noqa: E402
from auto_presenter.music.inspect import load_wav  # noqa: E402

OUT = Path(__file__).resolve().parents[3] / "build" / "scorer_lat"
SR = 16000
GRID0 = 0.3


def cache_path(name, window, step, span, trace=False, phase=0.0):
    tag = "trace" if trace else f"s{step:g}" + (f"p{phase:g}" if phase else "")
    return OUT / f"{name}_w{window:g}_{tag}_n{span}.jsonl"


def active(rows, items):
    """[(t0, t1, item)] while each item with a sheet is the current song."""
    enters = sorted((r["t"], r["item"]) for r in rows if r["e"] == "enter")
    end = max(r["t"] for r in rows)
    out = []
    for i, (t, it) in enumerate(enters):
        t1 = enters[i + 1][0] if i + 1 < len(enters) else end + 1
        if it in items:
            out.append((t, t1, it))
    return out


_scorer = None


def scorer():
    global _scorer
    if _scorer is None:
        from auto_presenter.asr.mlx_backend import local_snapshot, repo_for

        _scorer = local_snapshot(repo_for("small.en"))
    return _scorer


def make(name, window, step, span, trace=False, phase=0.0):
    path = cache_path(name, window, step, span, trace, phase)
    if path.exists():
        return path
    rdir, trace_p, sheets = mr.CASES[name]
    rows = d.load_trace(trace_p)
    rec = load_recording(rdir)
    audio = load_wav(Path(rdir) / "audio.wav")
    dur = len(audio) / SR
    sc = MLXSpanScorer(scorer(), audio_ctx=int(round(window * 50)))
    spans_of = {}
    for item in sheets:
        snap = [x for x in rec.setlist_content if x.item_idx == item][0]
        spans_of[item] = sorted(set(spans_for(snap.slides, span).values()))
    # warm up (model load + kernels) so the first step's compute is honest
    sc.score(audio[: int(window * SR)], spans_of[next(iter(spans_of))])
    jobs = []
    if trace:
        jobs = [(r["win_end"], r["item"]) for r in rows if r["e"] == "scored"]
    else:
        for t0, t1, item in active(rows, set(sheets)):
            g0 = GRID0 + phase
            k = max(0, int(np.ceil((max(t0, window - 0.5) - g0) / step - 1e-9)))
            while g0 + k * step < min(t1, dur) - 1e-9:
                jobs.append((round(g0 + k * step, 3), item))
                k += 1
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    started = time.time()
    with tmp.open("w") as fh:
        for win_end, item in jobs:
            a = audio[max(0, int(round((win_end - window) * SR))): int(round(win_end * SR))]
            t0 = time.perf_counter()
            res = sc.score(a.astype(np.float32), spans_of[item])
            el = time.perf_counter() - t0
            if res is None:
                continue
            win_start = win_end - len(a) / SR
            fh.write(json.dumps({
                "e": "scored", "item": item, "t": win_start + res.place_s, "arr": win_end + el,
                "win_end": win_end, "place": res.place_s, "compute": round(el, 4),
                "n_spans": len(spans_of[item]),
                "scores": [round(float(x), 4) for x in res.scores],
            }, separators=(",", ":")) + "\n")
    tmp.rename(path)
    print(f"{name} w{window:g} {'trace' if trace else f's{step:g}'} n{span}: {len(jobs)} steps "
          f"in {time.time() - started:.0f}s", flush=True)
    return path


def _read(p):
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def load_rows(name, window, step, span=6):
    """Cached rows at `step` (0.5, or a whole number of seconds), from the
    1 s caches (phase 0, plus phase 0.5 for a 0.5 s step)."""
    rows = _read(cache_path(name, window, 1.0, span))
    if step == 0.5:
        rows = sorted(rows + _read(cache_path(name, window, 1.0, span, phase=0.5)), key=lambda x: x["win_end"])
        return rows
    r = round(step)
    assert abs(step - r) < 1e-9 and r >= 1, step
    return [x for x in rows if round(x["win_end"] - GRID0) % r == 0]


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    trace = "--trace" in sys.argv
    window, step = float(args[0]), float(args[1])
    span = int(args[2]) if len(args) > 2 and args[2].isdigit() else 6
    phase = float(next((a.split("=")[1] for a in sys.argv if a.startswith("--phase=")), 0))
    names = [a for a in args[2:] if not a.isdigit()] or list(mr.CASES)
    for n in names:
        make(n, window, step, span, trace, phase)
