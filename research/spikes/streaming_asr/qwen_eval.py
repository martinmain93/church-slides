"""Qwen3-ASR (open weights, Apache 2.0) on the same public sung clips.

Companion to `stream_eval.py`, for docs/research/STREAMING_ASR_SPIKE.md.
Qwen3-ASR has no true streaming on a Mac (its "streaming" re-decodes the
whole window each chunk), so it would sit where Whisper does: a batch backend
behind the app's `SlidingWindowWrapper` (6 s window). Word times come from
the separate Qwen3-ForcedAligner-0.6B, which Qwen lists for speech only.

Runs in its own venv (`mlx-qwen3-asr[aligner]`, `pydantic`), never the app's:

    V=~/Development/auto-presenter-research/work/qwen3asr/.venv/bin/python
    cd sidecar && PYTHONPATH=. $V ../research/spikes/streaming_asr/qwen_eval.py \
        --out ~/Development/auto-presenter-research/work/streaming_asr/runs/main6

It writes `qwen06-off.jsonl` etc. beside the other streams' logs, in the
wrapper format `stream_eval.py score` reads, plus `qwen_offline.json` (whole
song in one call: the vendor-style ceiling) and `qwen_speed.json` (seconds per
6 s pass). No real-time pacing is needed: the wrapper steps on audio time and
stamps each event with window end + measured pass time, which is what a live
run would see on an otherwise idle machine.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from stream_eval import SR, align, load_clip, norm, pct  # noqa: E402

MODELS = {"qwen06": "Qwen/Qwen3-ASR-0.6B", "qwen17": "Qwen/Qwen3-ASR-1.7B"}


class QwenBackend:
    """`ASRBackend` over mlx-qwen3-asr: transcribe + forced-align one window."""

    def __init__(self, model, aligner, context: str, label: str) -> None:
        self._model = model
        self._aligner = aligner
        self._context = context
        self._label = label
        self.pass_seconds: list[float] = []

    def name(self) -> str:
        return self._label

    def transcribe(self, frames):
        from auto_presenter.asr.types import TranscriptEvent, Word
        import mlx_qwen3_asr as q

        chunks = list(frames)
        if not chunks:
            return iter(())
        audio = np.concatenate([f.samples for f in chunks]).astype(np.float32)
        t0 = chunks[0].t_start
        started = time.monotonic()
        res = q.transcribe(
            audio, model=self._model, context=self._context, language="English",
            return_timestamps=True, forced_aligner=self._aligner,
        )
        self.pass_seconds.append(time.monotonic() - started)
        words = [
            Word(text=s["text"], t_start=t0 + float(s["start"]), t_end=t0 + float(s["end"]))
            for s in (res.segments or []) if str(s.get("text", "")).strip()
        ]
        return iter((TranscriptEvent(words=words, is_final=True),)) if words else iter(())


def run_window(backend: QwenBackend, clip, out: Path) -> None:
    from auto_presenter.asr.streaming import SlidingWindowWrapper
    from auto_presenter.audio.types import AudioFrame

    samples = np.frombuffer(clip.pcm16, dtype=np.int16).astype(np.float32) / 32768.0
    rows: list[dict] = []

    def observe(ev, available_at: float) -> None:
        rows.append({"t": available_at, "final": ev.is_final,
                     "words": [[w.text, w.t_start, w.t_end] for w in ev.words]})

    frames = (
        AudioFrame(samples[i : i + 1600], i / SR, (i + len(samples[i : i + 1600])) / SR)
        for i in range(0, len(samples), 1600)
    )
    wrapper = SlidingWindowWrapper(backend, emission_observer=observe)
    for _ in wrapper.transcribe(frames):
        pass
    with out.open("w") as f:
        f.write(json.dumps({"meta": {"label": out.stem, "model": backend.name()}}) + "\n")
        for r in rows:
            f.write(json.dumps({"wrapper": r}) + "\n")


def offline(model, aligner, clip, context: str) -> dict:
    """Whole song in one call (the package chunks at ~30 s on quiet points)."""
    import mlx_qwen3_asr as q

    audio = np.frombuffer(clip.pcm16, dtype=np.int16).astype(np.float32) / 32768.0
    res = q.transcribe(audio, model=model, context=context, language="English",
                       return_timestamps=True, forced_aligner=aligner)
    hyp = [(norm(s["text"]), float(s["start"])) for s in (res.segments or [])]
    hyp = [h for h in hyp if h[0]]
    ref = [r[0] for r in clip.ref]
    s, d, i, hits = align(ref, [h[0] for h in hyp])
    timing = [hyp[hj][1] - clip.ref[ri][1] for ri, hj in hits]
    return {"n_ref": len(ref), "errors": s + d + i, "hits": len(hits), "timing": timing}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="run dir from stream_eval.py (one subdir per clip)")
    ap.add_argument("--models", default="qwen06,qwen17")
    ap.add_argument("--variants", default="off,kt")
    ap.add_argument("--clips", default="", help="comma list; default every clip dir in --out")
    args = ap.parse_args()

    import mlx_qwen3_asr as q
    from mlx_qwen3_asr.load_models import load_model

    root = Path(args.out)
    names = args.clips.split(",") if args.clips else sorted(p.name for p in root.iterdir() if p.is_dir())
    aligner = q.ForcedAligner()
    offline_rows: dict[str, dict] = {}
    speed: dict[str, list[float]] = {}
    for label in args.models.split(","):
        model, _ = load_model(MODELS[label])
        for name in names:
            clip = load_clip(name)
            context_text = " ".join(clip.lines)
            for v in args.variants.split(","):
                ctx = context_text if v == "kt" else ""
                key = f"{label}-{v}"
                be = QwenBackend(model, aligner, ctx, key)
                t = time.monotonic()
                run_window(be, clip, root / name / f"{key}.jsonl")
                speed.setdefault(key, []).extend(be.pass_seconds)
                o = offline(model, aligner, clip, ctx)
                agg = offline_rows.setdefault(key, {"n_ref": 0, "errors": 0, "hits": 0, "timing": []})
                for k in ("n_ref", "errors", "hits"):
                    agg[k] += o[k]
                agg["timing"] += o["timing"]
                print(f"{name} {key}: window passes p50 {statistics.median(be.pass_seconds):.2f}s, "
                      f"offline WER {o['errors'] / o['n_ref']:.3f} ({time.monotonic() - t:.0f}s)", flush=True)
    summary = {
        k: {"wer": a["errors"] / a["n_ref"], "recall": a["hits"] / a["n_ref"],
            "timing_abs_p50": pct([abs(x) for x in a["timing"]], 0.5),
            "timing_bias": statistics.median(a["timing"]) if a["timing"] else None}
        for k, a in offline_rows.items()
    }
    (root / "qwen_offline.json").write_text(json.dumps(summary, indent=1))
    (root / "qwen_speed.json").write_text(json.dumps(
        {k: {"p50": pct(v, 0.5), "p90": pct(v, 0.9), "n": len(v)} for k, v in speed.items()}, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
