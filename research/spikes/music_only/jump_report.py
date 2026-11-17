"""The 5x errors: time the screen jumped "anywhere else" (a random mishearing,
not stale, early or following the written order), per recording and episode.

    [env as for error_buckets] uv run --project sidecar python \\
        research/spikes/music_only/jump_report.py [names]

Same scoring as `error_buckets.buckets` (perceived = wrong time with jumps
weighted 5x); episodes are runs of jump samples no more than 0.3 s apart.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import error_buckets as eb  # noqa: E402

# The jump guard (`online.GUARD_*`) this used to sweep was removed from the
# follower: research/dead_ends/follower_switches/README.md.
if any(os.environ.get(k) for k in ("GUARD_FAR", "GUARD_NEAR_SHARE", "GUARD_SHARED_SHARE", "GUARD_BLANK")):
    raise SystemExit("the jump guard was removed (research/dead_ends/follower_switches)")
if os.environ.get("LOOPS"):
    from auto_presenter.follower import reference as _ref

    _ref.LOOPS = os.environ["LOOPS"] == "1"


def main() -> int:
    names = sys.argv[1:] or list(eb.TRACE)
    tot = {"span": 0.0, "wrong": 0.0, "perc": 0.0, "jump": 0.0}
    episodes = []
    for name in names:
        eb.JUMPS.clear()
        out, span, _marks = eb.buckets(name)
        wrong = sum(v for k, v in out.items() if not k.startswith("_"))
        perc = sum(v * eb.PERCEIVED.get(k[3:], 1.0) for k, v in out.items() if k.startswith("_p:"))
        jump = out.get("_p:jump (anywhere else)", 0.0)
        for k, v in zip(tot, (span, wrong, perc, jump), strict=True):
            tot[k] += v
        print(f"{name:7s} wrong {wrong / span:.3f}  perceived {perc / span:.3f}  jumps {jump:5.1f} s", flush=True)
        cur = None
        for _n, t, d, g in eb.JUMPS:
            if cur and t - cur[2] <= 0.31 and (d, g) == cur[3]:
                cur[2] = t
            else:
                if cur:
                    episodes.append(cur)
                cur = [name, t, t, (d, g)]
        if cur:
            episodes.append(cur)
    s = tot["span"]
    print(f"\nall: wrong {tot['wrong'] / s:.3f}  perceived {tot['perc'] / s:.3f}  "
          f"jumps {tot['jump']:.1f} s ({tot['jump'] / s:.3%} of the time)")
    print("\njump episodes (screen vs band):")
    for name, a, b, (d, g) in episodes:
        print(f"  {name:7s} {a:6.1f}-{b:6.1f} ({b - a + 0.1:4.1f} s)  screen {d}  band {g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
