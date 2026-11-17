"""Run the app's machine check on this computer and print its verdict.

The same check the engine runs off Apple silicon before it offers live AI
(sidecar/auto_presenter/asr/machine_check.py), with the models fetched the way
the app fetches them: the speech model through the ModelManager, the scorer
from its pinned, checksummed release. For runners and test machines:

    uv run --project sidecar python scripts/machine_check.py [--out verdict.json]

Pass --cpu on Apple silicon to time the CPU path instead of passing on MLX.
Pass --providers to score on a GPU instead, e.g. DirectML on Windows (needs
onnxruntime-directml in place of onnxruntime):

    --providers DmlExecutionProvider,CPUExecutionProvider
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import platform
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "sidecar"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, help="also write the verdict here as JSON")
    ap.add_argument("--cpu", action="store_true", help="time the CPU path even on Apple silicon")
    ap.add_argument("--providers", help="ONNX Runtime providers for the scorer, comma-separated")
    args = ap.parse_args()
    if args.cpu:
        os.environ["AUTO_PRESENTER_DISABLE_MLX"] = "1"
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    from auto_presenter.asr import machine_check
    from auto_presenter.asr.model_manager import ModelManager
    from auto_presenter.follower import scorer_models

    print(f"{platform.platform()} | {platform.processor() or platform.machine()} | "
          f"{os.cpu_count()} logical CPUs")
    ModelManager().ensure(machine_check.CPU_PATH_MODEL)
    onnx_dir = scorer_models.download()
    if args.providers:
        a = assess_with(onnx_dir, args.providers.split(","))
    else:
        a = machine_check.assess(onnx_dir)
    print(f"\nverdict: {a.verdict}\n{a.reason}")
    if a.speech_load is not None:
        print(f"speech load {a.speech_load:.2f} (limit {machine_check.LOAD_LIMIT:.2f}), "
              f"transcribe p90 {a.transcribe_ms_p90} ms, score p90 {a.score_ms_p90} ms, "
              f"chords {a.chord_ms_per_s} ms/s")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(
            {"check": machine_check.check_id(), "platform": platform.platform(),
             "cpus": os.cpu_count(), **dataclasses.asdict(a)}, indent=2))
    return 0


def assess_with(onnx_dir: Path, providers: list[str]):
    """The CPU path's check, with the scorer on `providers`."""
    from faster_whisper import WhisperModel

    from auto_presenter.asr import machine_check
    from auto_presenter.asr.whisper_backend import SHORT_CONTEXT_PAD_S
    from auto_presenter.follower.onnx_scorer import OnnxSpanScorer

    scorer = OnnxSpanScorer(onnx_dir, providers=providers)
    active = scorer.active_provider()
    print(f"scorer on {active} (asked for {', '.join(providers)})")
    if active != providers[0]:
        print(f"warning: {providers[0]} didn't start; this times {active}")
    model = WhisperModel(machine_check.CPU_PATH_MODEL, device="cpu", compute_type="int8",
                         local_files_only=True)
    return machine_check.assess_cpu(model, scorer, SHORT_CONTEXT_PAD_S)


if __name__ == "__main__":
    raise SystemExit(main())
