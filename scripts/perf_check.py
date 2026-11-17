"""Measure the matcher's cost and compare it against a committed baseline.

Correctness regressions get caught by tests. Cost regressions don't: a change
that quietly doubles the work per transcript event passes every assertion and
only shows up later as an overloaded pipeline dropping audio mid-service. This
puts a number on it.

What it measures, replaying a real recording through the real engine:

  - wall time per matcher update (the per-event cost that has to fit in the
    gaps between ASR passes)
  - peak resident memory
  - peak live states in the forward filter's posterior, the dense-filter
    analogue of the particle filter's cloud size — bounded by the setlist's
    own structure and the recursion's pruning, but still the number a broken
    prune threshold would show up in first

Usage:

    # See where things stand
    uv run --project sidecar python scripts/perf_check.py

    # Record the current numbers as the baseline to defend
    uv run --project sidecar python scripts/perf_check.py --update-baseline

    # Fail (exit 1) if anything regressed beyond tolerance
    uv run --project sidecar python scripts/perf_check.py --check

ASR is deliberately excluded: it's dominated by the model and the machine, so
it belongs in a manual measurement rather than a regression gate that would
fire on a busy laptop. `PipelineHealthMessage.asr_ms` reports it live.

Reading the numbers
-------------------

`matcher_ms_*` is **strongly load-sensitive**. Measured on one busy laptop,
the identical code path read 5.4ms idle and 8.0-8.3ms while a test suite ran
alongside — a 1.5x swing with no code change at all, which is the entire
tolerance budget. Two consequences:

- A single run proves nothing. To attribute a cost to a feature, interleave
  control and treatment across several reps and compare medians. Doing
  exactly that once turned an apparent "+21% from the hold policy" into a
  difference of 0.01ms.
- Never refresh the baseline on a busy machine: `--update-baseline` would bake
  the load in and quietly raise the bar for everything after it.

The engine wired here must mirror `server.py` — which as of this file means
calling `decision.production.build_engine()` rather than constructing one by
hand, for the same reason `production.py` exists at all: every hand-rolled
copy of the production engine has eventually drifted from what `server.py`
actually runs and gone on measuring a configuration nobody ships (`trace.py`,
the integration suite, `run_realtime_eval.py`, and this file, in that order —
see `production.py`'s docstring).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sidecar"))

from auto_presenter.eval.replay import load_recording  # noqa: E402
from auto_presenter.eval.trace import (  # noqa: E402
    _content,
    synthesize_schedule,
)
from auto_presenter.asr.backend import MockASRBackend  # noqa: E402
from auto_presenter.asr.streaming import SlidingWindowWrapper  # noqa: E402
from auto_presenter.decision import production  # noqa: E402
from auto_presenter.eval.replay import Replayer  # noqa: E402
from auto_presenter.index.library import build_index  # noqa: E402
from auto_presenter.pipeline.perf import PerfMonitor, _rss_mb  # noqa: E402
from auto_presenter.protocol import ParticleFilterTunables  # noqa: E402

BASELINE = Path(__file__).resolve().parent / "perf_baseline.json"
DEFAULT_RECORDINGS = Path.home() / (
    "Library/Application Support/com.churchslides.app/recordings"
)

#: How much worse than baseline is tolerated before this reports a regression.
#: Wall-clock timings on a shared laptop are noisy; 1.5x is loose enough not to
#: cry wolf and tight enough to catch a doubling.
TOLERANCE = {
    "matcher_ms_mean": 1.5,
    "matcher_ms_p95": 1.5,
    "peak_live_states": 1.1,  # should be a hard cap — near-zero slack on purpose
    "rss_mb": 1.5,
}


def measure(recording_dir: Path, *, tunables: ParticleFilterTunables | None = None) -> dict:
    recording = load_recording(recording_dir)
    indexed = build_index(_content(recording))
    # The production engine, unmodified — see the module docstring for why
    # this is not built by hand here.
    engine = production.build_engine(indexed, tunables)
    backend = SlidingWindowWrapper(
        MockASRBackend(schedule=synthesize_schedule(recording)),
        window_seconds=4.0, step_seconds=0.5, stable_margin_seconds=0.5,
    )

    perf = PerfMonitor()
    per_update: list[float] = []
    peak_live_states = 0
    started = time.monotonic()
    for event in backend.transcribe(Replayer(recording, realtime=False).audio_frames()):
        if not event.is_final:
            continue
        t0 = time.monotonic()
        with perf.stage("matcher"):
            engine.update(event)
        per_update.append((time.monotonic() - t0) * 1000.0)
        # `engine.belief` is the just-cached result of the `update()` above
        # (`ParticleFilterEngine._last_belief`), not a fresh recomputation —
        # reading it here doesn't add to what's being measured.
        peak_live_states = max(peak_live_states, len(engine.belief.particles))
    wall = time.monotonic() - started

    ordered = sorted(per_update)
    return {
        "recording": recording.id,
        "updates": len(per_update),
        "matcher_ms_mean": round(statistics.mean(per_update), 3) if per_update else 0.0,
        "matcher_ms_p95": round(
            ordered[int(0.95 * (len(ordered) - 1))], 3
        ) if ordered else 0.0,
        "peak_live_states": peak_live_states,
        "rss_mb": round(_rss_mb(), 1),
        "wall_seconds": round(wall, 2),
    }


def compare(current: dict, baseline: dict) -> list[str]:
    """Return human-readable regressions, empty when everything is within
    tolerance."""
    problems: list[str] = []
    for key, factor in TOLERANCE.items():
        now = current.get(key)
        was = baseline.get(key)
        if not isinstance(now, (int, float)) or not isinstance(was, (int, float)):
            continue
        if was <= 0:
            continue
        if now > was * factor:
            problems.append(
                f"{key}: {now} vs baseline {was} "
                f"({now / was:.2f}x, tolerance {factor}x)"
            )
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--recordings", type=Path, default=DEFAULT_RECORDINGS)
    ap.add_argument("--recording-id", default=None,
                    help="Defaults to the first approved golden recording.")
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="Exit non-zero if a metric regressed.")
    args = ap.parse_args()

    target: Path | None = None
    if args.recording_id:
        target = args.recordings / args.recording_id
    else:
        # Prefer an approved golden: its cue density is realistic, and using
        # the same recording every time is what makes the numbers comparable.
        for d in sorted(args.recordings.iterdir()):
            golden = d / "golden.json"
            if golden.exists() and json.loads(golden.read_text()).get("approved"):
                target = d
                break
    if target is None or not (target / "recording.json").exists():
        print("no suitable recording found", file=sys.stderr)
        return 2

    current = measure(target)
    print(json.dumps(current, indent=2))

    if args.update_baseline:
        BASELINE.write_text(json.dumps(current, indent=2) + "\n")
        print(f"\nbaseline written to {BASELINE}")
        return 0

    if not BASELINE.exists():
        print(f"\nno baseline at {BASELINE} — run with --update-baseline")
        return 0

    baseline = json.loads(BASELINE.read_text())
    if baseline.get("recording") != current["recording"]:
        print(
            f"\nWARNING: baseline is for {baseline.get('recording')}, "
            f"measured {current['recording']} — not comparable"
        )
        return 0

    problems = compare(current, baseline)
    if problems:
        print("\nPERFORMANCE REGRESSION:")
        for p in problems:
            print(f"  - {p}")
        return 1 if args.check else 0
    print("\nwithin tolerance of baseline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
