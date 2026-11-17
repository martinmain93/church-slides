"""Compute WER between a `run_asr.py` JSONL output and a plaintext reference.

  uv run --project sidecar python research/eval/compute_wer.py \\
      --hypothesis hyp.jsonl \\
      --reference reference.txt

Where `hyp.jsonl` is the output of `research/eval/run_asr.py` (one JSON object
per line, each carrying a `words[]` payload with `{text, ...}`) and
`reference.txt` is a plain transcript of what was actually sung. Both
sides are tokenized via `eval.metrics.tokenize_for_wer`, which mirrors
the `Slide.words` tokenization so the matcher and WER both see the same
words.

Pass `--final-only` to score only `is_final=True` events from the
hypothesis stream; partials are noisy and only useful when measuring
anticipation rather than transcription quality.

Output: markdown by default, JSON with `--json`.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "sidecar"))

from auto_presenter.eval.metrics import (
    WerResult,
    tokenize_for_wer,
    word_error_rate,
)

log = logging.getLogger("compute_wer")


def load_hypothesis_words(path: Path, *, final_only: bool) -> list[str]:
    """Read a `run_asr.py` JSONL stream and return the flat list of words."""
    words: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            log.warning("skipping malformed hypothesis line: %r", line[:120])
            continue
        if final_only and not event.get("is_final", True):
            continue
        for w in event.get("words", []):
            text = (w.get("text") or "").strip()
            if text:
                # Tokenize each ASR word individually so multi-token
                # outputs (rare but possible) are split the same way the
                # reference would be.
                words.extend(tokenize_for_wer(text))
    return words


def render_markdown(result: WerResult) -> str:
    lines = [
        "# WER report",
        "",
        f"| Metric | Value |",
        f"|---|---:|",
        f"| WER | {result.wer:.3f} |",
        f"| Edit distance | {result.edit_distance} |",
        f"| Substitutions | {result.substitutions} |",
        f"| Deletions | {result.deletions} |",
        f"| Insertions | {result.insertions} |",
        f"| Reference words | {result.n_reference_words} |",
        f"| Hypothesis words | {result.n_hypothesis_words} |",
    ]
    return "\n".join(lines) + "\n"


def render_json(result: WerResult) -> str:
    return (
        json.dumps(
            {
                "wer": result.wer,
                "substitutions": result.substitutions,
                "deletions": result.deletions,
                "insertions": result.insertions,
                "n_reference_words": result.n_reference_words,
                "n_hypothesis_words": result.n_hypothesis_words,
                "edit_distance": result.edit_distance,
            },
            indent=2,
        )
        + "\n"
    )


def build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--hypothesis", required=True, help="JSONL produced by run_asr.py"
    )
    parser.add_argument(
        "--reference",
        required=True,
        help="Plain-text transcript of what was actually sung.",
    )
    parser.add_argument(
        "--final-only",
        action="store_true",
        help="Score only is_final=True events from the hypothesis stream.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Write the report here (markdown unless --json).",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argparser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    hyp_path = Path(args.hypothesis)
    ref_path = Path(args.reference)
    if not hyp_path.exists():
        raise SystemExit(f"hypothesis file not found: {hyp_path}")
    if not ref_path.exists():
        raise SystemExit(f"reference file not found: {ref_path}")

    hypothesis = load_hypothesis_words(hyp_path, final_only=args.final_only)
    reference = tokenize_for_wer(ref_path.read_text(encoding="utf-8"))
    result = word_error_rate(hypothesis, reference)

    report = render_json(result) if args.json else render_markdown(result)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report)
        log.info("wrote WER report to %s", out)
    else:
        sys.stdout.write(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
