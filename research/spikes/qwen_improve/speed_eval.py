"""Idea 1: make Qwen3-ASR faster. Quantized weights and speculative decoding.

Each variant runs through the app's own `SlidingWindowWrapper` (6 s window,
1 s step) on the parent spike's six JamendoLyrics songs and writes
wrapper-format logs that `stream_eval.py score` reads. The pass time is split
into the speech model and the forced aligner (word times), so we can see
where the time goes.

    cd sidecar && PYTHONPATH=. $V ../research/spikes/qwen_improve/speed_eval.py \
        --variants 0.6b-16,0.6b-8,0.6b-4e8 --out ~/Development/auto-presenter-research/work/qwen_improve/runs/speed

Variant names: <size>-<bits>[e<encoder bits>][+d<size>-<bits>[e<bits>]], e.g.
`1.7b-16+d0.6b-16` is 1.7B at full precision drafted by 0.6B.

Time a variant only when nothing else heavy runs on the Mac.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import numpy as np

from common import MAIN6, SR, WORK, audio_of, load_clip, load_qwen, pct


def parse_variant(v: str):
    m = re.fullmatch(r"(0\.6b|1\.7b)-(\d+)(?:e(\d+))?", v)
    if not m:
        raise ValueError(v)
    return m.group(1), int(m.group(2)), int(m.group(3)) if m.group(3) else None


class TimedQwenBackend:
    """`ASRBackend` over mlx-qwen3-asr; speech model and aligner timed apart."""

    def __init__(self, model, aligner, label: str, draft=None, num_draft_tokens: int = 4) -> None:
        self._model, self._aligner, self._label = model, aligner, label
        self._draft, self._k = draft, num_draft_tokens
        self.asr_s: list[float] = []
        self.align_s: list[float] = []
        self.texts: list[str] = []

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
        a = time.monotonic()
        kw = {"draft_model": self._draft, "num_draft_tokens": self._k} if self._draft is not None else {}
        res = q.transcribe(audio, model=self._model, language="English", **kw)
        b = time.monotonic()
        text = (res.text or "").strip()
        aligned = self._aligner.align(audio, text, "English") if text else []
        c = time.monotonic()
        self.asr_s.append(b - a)
        self.align_s.append(c - b)
        self.texts.append(text)
        words = [Word(text=w.text, t_start=t0 + float(w.start_time), t_end=t0 + float(w.end_time))
                 for w in aligned if w.text.strip()]
        return iter((TranscriptEvent(words=words, is_final=True),)) if words else iter(())


def run_window(backend, samples: np.ndarray, out: Path) -> None:
    from auto_presenter.asr.streaming import SlidingWindowWrapper
    from auto_presenter.audio.types import AudioFrame

    rows: list[dict] = []

    def observe(ev, available_at: float) -> None:
        rows.append({"t": available_at, "final": ev.is_final,
                     "words": [[w.text, w.t_start, w.t_end] for w in ev.words]})

    frames = (AudioFrame(samples[i : i + 1600], i / SR, (i + len(samples[i : i + 1600])) / SR)
              for i in range(0, len(samples), 1600))
    for _ in SlidingWindowWrapper(backend, emission_observer=observe).transcribe(frames):
        pass
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        f.write(json.dumps({"meta": {"label": out.stem, "model": backend.name()}}) + "\n")
        for r in rows:
            f.write(json.dumps({"wrapper": r}) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", required=True)
    ap.add_argument("--clips", default=",".join(MAIN6))
    ap.add_argument("--out", default=str(WORK / "runs" / "speed"))
    ap.add_argument("--draft-tokens", type=int, default=4)
    ap.add_argument("--aligner-bits", type=int, default=16, help="quantize the forced aligner too")
    ap.add_argument("--max-seconds", type=float, default=0.0, help="only the first N s of each clip (smoke)")
    args = ap.parse_args()

    import mlx.core as mx
    import mlx_qwen3_asr as q

    root = Path(args.out)
    aligner = q.ForcedAligner()
    if args.aligner_bits < 16:
        from mlx_qwen3_asr.convert import quantize_model

        aligner._ensure_loaded()
        quantize_model(aligner._backend.model, bits=args.aligner_bits, group_size=64)
    speed_path = root / "speed.json"
    speed = json.loads(speed_path.read_text()) if speed_path.exists() else {}
    for v in args.variants.split(","):
        main_spec, _, draft_spec = v.partition("+d")
        size, bits, ebits = parse_variant(main_spec)
        model = load_qwen(size, bits, ebits)
        draft = None
        if draft_spec:
            dsize, dbits, debits = parse_variant(draft_spec)
            draft = load_qwen(dsize, dbits, debits)
        label = "q" + v.replace(".", "").replace("+", "_") + (f"-a{args.aligner_bits}" if args.aligner_bits < 16 else "")
        mx.reset_peak_memory()
        be = TimedQwenBackend(model, aligner, label, draft, args.draft_tokens)
        # warm-up so compile/first-touch cost isn't in the pass times
        warm = np.zeros(SR * 6, dtype=np.float32)
        q.transcribe(warm, model=model, language="English")
        be.asr_s.clear()
        be.align_s.clear()
        t = time.monotonic()
        for name in args.clips.split(","):
            clip = load_clip(name)
            samples = audio_of(clip)
            if args.max_seconds:
                samples = samples[: int(args.max_seconds * SR)]
            run_window(be, samples, root / name / f"{label}.jsonl")
            print(f"{v} {name}: {len(be.asr_s)} passes so far, asr p50 {pct(be.asr_s, .5):.3f}s "
                  f"align p50 {pct(be.align_s, .5):.3f}s ({time.monotonic() - t:.0f}s)", flush=True)
        tot = [a + b for a, b in zip(be.asr_s, be.align_s)]
        speed[label] = {
            "variant": v, "passes": len(tot),
            "asr_p50": pct(be.asr_s, .5), "asr_p90": pct(be.asr_s, .9),
            "align_p50": pct(be.align_s, .5), "align_p90": pct(be.align_s, .9),
            "pass_p50": pct(tot, .5), "pass_p90": pct(tot, .9),
            "peak_mem_gb": mx.get_peak_memory() / 1e9,
        }
        print(json.dumps(speed[label]), flush=True)
        speed_path.write_text(json.dumps(speed, indent=1))
        del model, draft, be
        mx.clear_cache()


if __name__ == "__main__":
    main()
