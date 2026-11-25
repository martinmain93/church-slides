"""Whole-song MLX Whisper on the same clips, for comparison with
`qwen_eval.py`'s whole-song ("offline") numbers. Accuracy only: this is the
best case for each model, not what the app sees through its 6 s window.

    cd sidecar && PYTHONPATH=. ~/Development/church-slides/sidecar/.venv/bin/python \
        ../research/spikes/streaming_asr/whisper_offline.py --out <run dir>
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from stream_eval import align, load_clip, norm, pct  # noqa: E402

REPOS = {"small.en": "mlx-community/whisper-small.en-mlx", "large-v3-turbo": "mlx-community/whisper-large-v3-turbo"}


def main() -> None:
    import mlx_whisper

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--models", default="small.en,large-v3-turbo")
    args = ap.parse_args()
    root = Path(args.out)
    names = sorted(p.name for p in root.iterdir() if p.is_dir())
    summary = {}
    for m in args.models.split(","):
        agg = {"n_ref": 0, "errors": 0, "hits": 0, "timing": []}
        for name in names:
            clip = load_clip(name)
            audio = np.frombuffer(clip.pcm16, dtype=np.int16).astype(np.float32) / 32768.0
            res = mlx_whisper.transcribe(audio, path_or_hf_repo=REPOS[m], language="en",
                                         word_timestamps=True, condition_on_previous_text=False)
            hyp = [(norm(w["word"]), float(w["start"])) for s in res["segments"] for w in s.get("words", [])]
            hyp = [h for h in hyp if h[0]]
            ref = [r[0] for r in clip.ref]
            s, d, i, hits = align(ref, [h[0] for h in hyp])
            agg["n_ref"] += len(ref)
            agg["errors"] += s + d + i
            agg["hits"] += len(hits)
            agg["timing"] += [hyp[hj][1] - clip.ref[ri][1] for ri, hj in hits]
            print(f"{name} {m}: WER {(s + d + i) / len(ref):.3f}", flush=True)
        summary[f"whisper-{m}"] = {
            "wer": agg["errors"] / agg["n_ref"], "recall": agg["hits"] / agg["n_ref"],
            "timing_abs_p50": pct([abs(x) for x in agg["timing"]], 0.5),
            "timing_bias": statistics.median(agg["timing"]) if agg["timing"] else None,
        }
    (root / "whisper_offline.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
