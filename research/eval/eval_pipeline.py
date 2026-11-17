"""Batch eval CLI — replay recordings through a pipeline, score the decisions.

Two modes:

  # One recording, one pipeline → markdown report.
  uv run --project sidecar python research/eval/eval_pipeline.py \\
      --recording data/recordings/rec_20260512-194530_117 \\
      --pipeline stub \\
      --output reports/rec_20260512.md

  # Whole directory of recordings, one pipeline → aggregated report.
  uv run --project sidecar python research/eval/eval_pipeline.py \\
      --recordings data/recordings \\
      --pipeline stub \\
      --output reports/stub_baseline.md

  # A/B comparison (writes a side-by-side markdown table with bootstrap CIs).
  uv run --project sidecar python research/eval/eval_pipeline.py \\
      --recordings data/recordings \\
      --pipeline stub --pipeline-b shadow:data/shadow.jsonl \\
      --output reports/ab.md

Pipelines:

  - `stub`   — emits no decisions. Useful as the "do nothing" baseline.
  - `oracle` — replays the ground-truth events as decisions. The
               upper-bound; metric values for this should be ~zero error.
  - `shadow:<path>` — loads decisions from a shadow-mode JSONL log
                      (see `auto_presenter.eval.shadow`). This is how
                      Phase 5+ scores a live shadow run.

Phase 5+ matchers register here once they exist — see the `_PIPELINES`
dict at the bottom of this module.
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "sidecar"))

from auto_presenter.eval.compare import (
    Pipeline,
    PipelineRun,
    compare,
    evaluate,
    render_report,
)
from auto_presenter.eval.replay import load_recording
from auto_presenter.eval.shadow import decisions_from_shadow, load_shadow_log
from auto_presenter.eval.types import Decision, Recording

log = logging.getLogger("eval_pipeline")


# --------------------------------------------------------------------- pipelines


def stub_pipeline(_recording: Recording) -> list[Decision]:
    """Emit no decisions. Useful as a worst-case baseline."""
    return []


def oracle_pipeline(recording: Recording) -> list[Decision]:
    """Echo the ground truth as decisions. Upper-bound for metric values."""
    return [
        Decision(t_seconds=g.t_seconds, item_idx=g.item_idx, slide_idx=g.slide_idx)
        for g in recording.ground_truth
    ]


def shadow_pipeline_factory(shadow_path: Path) -> Pipeline:
    """Return a pipeline that loads decisions from a shadow log.

    The same shadow log is reused for every recording — that's fine when
    you're scoring a single live session, but flagged in the report so
    multi-recording runs against a single log are obvious.
    """

    entries = load_shadow_log(shadow_path)
    decisions = decisions_from_shadow(entries)
    log.info("loaded %d decisions from shadow log %s", len(decisions), shadow_path)

    def pipeline(_recording: Recording) -> list[Decision]:
        return list(decisions)

    pipeline.__name__ = f"shadow({shadow_path.name})"
    return pipeline


_BUILTIN_PIPELINES: dict[str, Pipeline] = {
    "stub": stub_pipeline,
    "oracle": oracle_pipeline,
}


def resolve_pipeline(spec: str) -> tuple[str, Pipeline]:
    """Parse a `--pipeline` argument into `(label, callable)`."""
    if spec in _BUILTIN_PIPELINES:
        return spec, _BUILTIN_PIPELINES[spec]
    if spec.startswith("shadow:"):
        path = Path(spec[len("shadow:") :])
        if not path.exists():
            raise SystemExit(f"shadow log does not exist: {path}")
        return spec, shadow_pipeline_factory(path)
    raise SystemExit(
        f"unknown pipeline `{spec}`. "
        f"Known: {', '.join(sorted(_BUILTIN_PIPELINES))}, shadow:<path>"
    )


# --------------------------------------------------------------------- recordings


def load_recordings_from_args(args: argparse.Namespace) -> list[Recording]:
    if args.recording and args.recordings:
        raise SystemExit("pass either --recording or --recordings, not both")
    if args.recording:
        return [load_recording(Path(args.recording))]
    if args.recordings:
        root = Path(args.recordings)
        if not root.exists():
            raise SystemExit(f"recordings directory does not exist: {root}")
        recordings: list[Recording] = []
        for entry in sorted(root.iterdir()):
            meta = entry / "recording.json"
            if not meta.exists():
                continue
            try:
                recordings.append(load_recording(entry))
            except Exception as exc:
                log.warning("skipping %s: %s", entry, exc)
        if not recordings:
            raise SystemExit(f"no recordings found under {root}")
        return recordings
    raise SystemExit("must pass --recording or --recordings")


# --------------------------------------------------------------------- reporting


def render_single_pipeline_report(
    pipeline_label: str,
    runs: Sequence[PipelineRun],
) -> str:
    """Aggregate runs from one pipeline into a markdown summary."""
    lines = [
        f"# Eval report — pipeline `{pipeline_label}`",
        "",
        f"Recordings evaluated: **{len(runs)}**",
        "",
        "## Aggregate metrics",
        "",
        "| Metric | Value | n |",
        "|---|---:|---:|",
    ]

    anticip = [v for r in runs for v in r.anticipation_error_ms]
    if anticip:
        lines.append(
            f"| anticipation_error_ms (median) | "
            f"{statistics.median(anticip):+.1f} | {len(anticip)} |"
        )
        lines.append(
            f"| anticipation_error_ms (abs median) | "
            f"{statistics.median(abs(x) for x in anticip):.1f} | {len(anticip)} |"
        )

    recoveries = [v for r in runs for v in r.recovery_time_ms if v is not None]
    if recoveries:
        lines.append(
            f"| recovery_time_ms (median) | "
            f"{statistics.median(recoveries):.1f} | {len(recoveries)} |"
        )

    if runs:
        lines.append(
            f"| wrong_slide_rate (mean) | "
            f"{statistics.fmean(r.wrong_slide_rate for r in runs):.3f} | {len(runs)} |"
        )
        lines.append(
            f"| override_rate (mean) | "
            f"{statistics.fmean(r.override_rate for r in runs):.3f} | {len(runs)} |"
        )

    lines.append("")
    lines.append("## Per-recording detail")
    lines.append("")
    lines.append("| Recording | n decisions | wrong slide rate | override rate |")
    lines.append("|---|---:|---:|---:|")
    for r in runs:
        lines.append(
            f"| {r.recording_id} | {len(r.decisions)} | "
            f"{r.wrong_slide_rate:.3f} | {r.override_rate:.3f} |"
        )
    return "\n".join(lines) + "\n"


def render_ab_report(
    a_label: str,
    b_label: str,
    a_runs: Sequence[PipelineRun],
    b_runs: Sequence[PipelineRun],
) -> str:
    reports = compare(a_runs, b_runs)
    table = render_report(reports)
    return (
        f"# A/B eval — A=`{a_label}` vs B=`{b_label}`\n\n"
        f"Recordings: {len(a_runs)}\n\n"
        f"{table}\n"
    )


# --------------------------------------------------------------------- entrypoint


def build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--recording", help="Single recording directory or recording.json")
    target.add_argument("--recordings", help="Directory containing many recordings")

    parser.add_argument(
        "--pipeline",
        required=True,
        help="Pipeline spec: stub, oracle, shadow:<path>.",
    )
    parser.add_argument(
        "--pipeline-b",
        default=None,
        help="Optional second pipeline; enables A/B comparison.",
    )
    parser.add_argument("--output", default=None, help="Write the report here.")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of markdown.")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argparser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    recordings = load_recordings_from_args(args)
    a_label, a_pipeline = resolve_pipeline(args.pipeline)
    a_runs = evaluate(recordings, a_pipeline)

    if args.pipeline_b:
        b_label, b_pipeline = resolve_pipeline(args.pipeline_b)
        b_runs = evaluate(recordings, b_pipeline)
        if args.json:
            report = _ab_json(a_label, b_label, a_runs, b_runs)
        else:
            report = render_ab_report(a_label, b_label, a_runs, b_runs)
    else:
        if args.json:
            report = _single_json(a_label, a_runs)
        else:
            report = render_single_pipeline_report(a_label, a_runs)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report)
        log.info("wrote report to %s", out_path)
    else:
        sys.stdout.write(report)
        if not report.endswith("\n"):
            sys.stdout.write("\n")
    return 0


def _single_json(label: str, runs: Sequence[PipelineRun]) -> str:
    payload = {
        "pipeline": label,
        "runs": [
            {
                "recording_id": r.recording_id,
                "n_decisions": len(r.decisions),
                "wrong_slide_rate": r.wrong_slide_rate,
                "override_rate": r.override_rate,
                "anticipation_error_ms": r.anticipation_error_ms,
                "recovery_time_ms": r.recovery_time_ms,
            }
            for r in runs
        ],
    }
    return json.dumps(payload, indent=2) + "\n"


def _ab_json(
    a_label: str,
    b_label: str,
    a_runs: Sequence[PipelineRun],
    b_runs: Sequence[PipelineRun],
) -> str:
    reports = compare(a_runs, b_runs)
    payload = {
        "a_pipeline": a_label,
        "b_pipeline": b_label,
        "reports": {
            name: {
                "a_value": r.a_value,
                "b_value": r.b_value,
                "delta": r.delta,
                "ci": list(r.bootstrap_ci_delta) if r.bootstrap_ci_delta else None,
            }
            for name, r in reports.items()
        },
    }
    return json.dumps(payload, indent=2) + "\n"


# Public hook for Phase 5+ matchers to register themselves at import time.
def register_pipeline(name: str, pipeline: Callable[[Recording], list[Decision]]) -> None:
    _BUILTIN_PIPELINES[name] = pipeline


if __name__ == "__main__":
    raise SystemExit(main())
