"""Where learned slides make the follower worse, episode by episode.

    TUNE=metre LEARN=perf CHORD_SRC=mix uv run --project sidecar python \\
        research/spikes/music_only/learned_diff.py [names]

Replays each recording twice with `tune_share_replay`: the library slides
alone (TUNE=none) and with what the look-back learns (the env's arm). Every
0.1 s is scored as `error_buckets` does (identical slides, blank cues and
post-deviation grace excused). Prints each stretch of 1 s or more where one
arm is right and the other wrong: what the band sang, what each arm showed,
and whether those slides had learned material at the time (L) or not (-).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import error_buckets as eb  # noqa: E402
import multi_replay as mr  # noqa: E402
import tune_learn  # noqa: E402
import tune_share_replay as T  # noqa: E402
from auto_presenter.eval.metrics import (  # noqa: E402
    DEFAULT_DEVIATION_GRACE_S,
    blank_cue_windows,
    derive_deviation_markers,
    identical_slide_groups,
)

STEP = 0.1
MIN_S = float(os.environ.get("MIN_S", "1.0"))
CAPTURED: dict[int, list] = {}
_orig = tune_learn.perf_schedule


def _capture(rows, item, *a, **k):
    sched = _orig(rows, item, *a, **k)
    CAPTURED[item] = sched
    return sched


tune_learn.perf_schedule = _capture


def known_at(item: int, t: float) -> set[int]:
    got: set[int] = set()
    for ts, known in CAPTURED.get(item, []):
        if ts > t:
            break
        got = set(known)
    return got


def run(name: str):
    rdir, _old, sheets = mr.CASES[name]
    mr.CASES[name] = (rdir, eb.TRACE[name], sheets)
    arm = {}
    learned_tune = T.TUNE
    T.TUNE = "none"
    lib, rec = T.decisions(name, arm)
    T.TUNE = learned_tune
    CAPTURED.clear()
    learned, _ = T.decisions(name, arm)
    gt = sorted(rec.ground_truth, key=lambda g: g.t_seconds)
    equiv = identical_slide_groups(rec.setlist_content)
    excused = list(blank_cue_windows(gt, rec.setlist_content))
    excused += [(m.t_seconds, m.t_seconds + DEFAULT_DEVIATION_GRACE_S) for m in derive_deviation_markers(gt)]
    at_lib, at_learned = eb.shown_at(lib), eb.shown_at(learned)

    def same(g, d):
        return d is not None and (g == d or (d[0] == g[0] and d[1] in equiv.get(g, frozenset())))

    rows = []  # (t, band, lib shown, learned shown, lib ok, learned ok)
    gi, t = 0, gt[0].t_seconds
    while t < gt[-1].t_seconds:
        while gi + 1 < len(gt) and gt[gi + 1].t_seconds <= t:
            gi += 1
        if not any(a <= t < b for a, b in excused):
            g = (gt[gi].item_idx, gt[gi].slide_idx)
            dl, dn = at_lib(t), at_learned(t)
            rows.append((t, g, dl, dn, same(g, dl), same(g, dn)))
        t += STEP
    eps = []
    cur = None
    for r in rows:
        kind = "worse" if r[4] and not r[5] else "better" if r[5] and not r[4] else None
        key = (kind, r[1], r[2], r[3]) if kind else None
        if cur and key == cur[0] and r[0] - cur[2] <= STEP * 1.5:
            cur[2] = r[0]
        else:
            if cur and cur[0]:
                eps.append(cur)
            cur = [key, r[0], r[0]] if key else None
    if cur and cur[0]:
        eps.append(cur)
    worse = sum(e[2] - e[1] + STEP for e in eps if e[0][0] == "worse")
    better = sum(e[2] - e[1] + STEP for e in eps if e[0][0] == "better")
    print(f"\n=== {name}: learned worse {worse:.0f} s, better {better:.0f} s")
    for (kind, g, dl, dn), a, b in eps:
        if b - a + STEP < MIN_S:
            continue
        kn = known_at(g[0], a)

        def tag(x):
            if x is None:
                return "none"
            return f"{x[0]}:{x[1]}{'L' if x[0] == g[0] and x[1] in kn else '-'}"

        print(f"  {kind:6s} {a:6.1f}-{b:6.1f} ({b - a + STEP:4.1f} s)  band {tag(g)}  "
              f"library {tag(dl)}  learned {tag(dn)}  known {sorted(kn)}")


def main() -> int:
    for name in sys.argv[1:] or list(eb.TRACE):
        run(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
