"""Idea 4: incremental streaming, keeping work between steps.

The app's wrapper re-runs the whole 6 s window from scratch every 1 s. The
package's own streaming (`mlx_qwen3_asr.streaming`) instead grows one window
(up to `--max-context` s), keeps the encoder output and decoder cache for each
finished 8 s encoder block, and re-decodes only the tail with the previous
text forced in as a prefix (minus its last 5 tokens). So each step is cheap
and it can step every 0.5 s.

Word times come from Qwen3-ForcedAligner on the live window after each step
(timed separately; included in when a word is "available"). Words are
emitted as interim every step, and as final once two consecutive steps agree
on them (LocalAgreement-2, the usual streaming rule), or when the window is
committed. Logs are wrapper-format, scored by `stream_eval.py score`.

    cd sidecar && PYTHONPATH=. $V ../research/spikes/qwen_improve/incremental_eval.py \
        --chunk 0.5 --max-context 10 --out <run dir>
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

from common import MAIN6, MODELS, SR, WORK, audio_of, load_clip, load_qwen, norm, pct


def run_clip(model, aligner, samples: np.ndarray, args) -> tuple[list[dict], list[float], list[float]]:
    from mlx_qwen3_asr.streaming import feed_audio, init_streaming

    st = init_streaming(model=MODELS[args.model], chunk_size_sec=args.chunk, max_context_sec=args.max_context,
                        language="English", unfixed_token_num=args.unfixed)
    step = int(args.chunk * SR)
    rows: list[dict] = []
    asr_s: list[float] = []
    al_s: list[float] = []
    prev_words: list[tuple[str, float, float]] = []
    finalized = 0  # words of the live window already emitted final
    fed = 0
    clock = 0.0  # when the (simulated) speech thread is free again: steps queue behind slow ones
    for i in range(0, len(samples) - step + 1, step):
        a = time.monotonic()
        committed_before = st.committed_text
        feed_audio(samples[i : i + step], st, model=model)
        b = time.monotonic()
        fed += step
        new_window = st.committed_text != committed_before
        if new_window:
            # the old window was frozen: flush what it still held as final
            rest = prev_words[finalized:]
            if rest:
                rows.append({"t": max(fed / SR, clock) + (b - a), "final": True, "words": [list(w) for w in rest]})
            prev_words, finalized = [], 0
        win_start = (fed - len(st.buffer) - len(st.audio_accum)) / SR
        text = st.window_text.strip()
        words: list[tuple[str, float, float]] = []
        if text and not args.align_tail:
            for w in aligner.align(st.audio_accum, text, "English"):
                if w.text.strip():
                    words.append((w.text, win_start + float(w.start_time), win_start + float(w.end_time)))
        elif text:
            # Re-align only what isn't final yet: final words keep their times,
            # and the aligner sees the audio from just before the last final word's end.
            from mlx_qwen3_asr.forced_aligner import ForcedAlignTextProcessor as P

            toks = P.tokenize_space_lang(text)
            keep = 0
            while (keep < min(finalized, len(toks), len(prev_words))
                   and norm(toks[keep]) == norm(prev_words[keep][0])):
                keep += 1
            words = list(prev_words[:keep])
            if keep < len(toks):
                cut = max(0.0, (prev_words[keep - 1][2] - win_start - 0.2) if keep else 0.0)
                off = win_start + cut
                for w in aligner.align(st.audio_accum[int(cut * SR):], " ".join(toks[keep:]), "English"):
                    if w.text.strip():
                        words.append((w.text, off + float(w.start_time), off + float(w.end_time)))
        c = time.monotonic()
        asr_s.append(b - a)
        al_s.append(c - b)
        clock = max(fed / SR, clock) + (c - a)
        t_avail = clock
        # LocalAgreement-2: a word is final once two steps agree on it and all before it
        agree = 0
        while (agree < min(len(words), len(prev_words))
               and norm(words[agree][0]) == norm(prev_words[agree][0])):
            agree += 1
        if agree > finalized:
            rows.append({"t": t_avail, "final": True, "words": [list(w) for w in words[finalized:agree]]})
            finalized = agree
        if len(words) > finalized:
            rows.append({"t": t_avail, "final": False, "words": [list(w) for w in words[finalized:]]})
        prev_words = words
    if prev_words[finalized:]:
        rows.append({"t": fed / SR, "final": True, "words": [list(w) for w in prev_words[finalized:]]})
    return rows, asr_s, al_s


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="0.6b")
    ap.add_argument("--bits", type=int, default=16)
    ap.add_argument("--chunk", type=float, default=0.5)
    ap.add_argument("--max-context", type=float, default=10.0)
    ap.add_argument("--unfixed", type=int, default=5)
    ap.add_argument("--ebits", type=int, default=None, help="encoder bits when --bits < 16")
    ap.add_argument("--aligner-bits", type=int, default=16)
    ap.add_argument("--align-tail", action="store_true", help="re-align only the words not yet final")
    ap.add_argument("--clips", default=",".join(MAIN6))
    ap.add_argument("--max-seconds", type=float, default=0.0)
    ap.add_argument("--out", default=str(WORK / "runs" / "incremental"))
    args = ap.parse_args()

    import mlx.core as mx
    import mlx_qwen3_asr as q

    model = load_qwen(args.model, args.bits, args.ebits)
    aligner = q.ForcedAligner()
    if args.aligner_bits < 16:
        from mlx_qwen3_asr.convert import quantize_model

        aligner._ensure_loaded()
        quantize_model(aligner._backend.model, bits=args.aligner_bits, group_size=64)
    label = (f"inc{args.model.replace('.', '')}-{args.bits}{f'e{args.ebits}' if args.ebits else ''}-c{args.chunk:g}"
             f"-w{args.max_context:g}-u{args.unfixed}" + (f"-a{args.aligner_bits}" if args.aligner_bits < 16 else "")
             + ("-tail" if args.align_tail else "") + "-q")
    root = Path(args.out)
    asr_all: list[float] = []
    al_all: list[float] = []
    for name in args.clips.split(","):
        samples = audio_of(load_clip(name))
        if args.max_seconds:
            samples = samples[: int(args.max_seconds * SR)]
        t = time.monotonic()
        rows, asr_s, al_s = run_clip(model, aligner, samples, args)
        asr_all += asr_s
        al_all += al_s
        out = root / name / f"{label}.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w") as f:
            f.write(json.dumps({"meta": {"label": label}}) + "\n")
            for r in rows:
                f.write(json.dumps({"wrapper": r}) + "\n")
        print(f"{name}: step asr p50 {pct(asr_s, .5):.3f}s p90 {pct(asr_s, .9):.3f}s, "
              f"align p50 {pct(al_s, .5):.3f}s ({time.monotonic() - t:.0f}s)", flush=True)
    speed_path = root / "speed.json"
    speed = json.loads(speed_path.read_text()) if speed_path.exists() else {}
    tot = [a + b for a, b in zip(asr_all, al_all)]
    speed[label] = {"steps": len(tot), "asr_p50": pct(asr_all, .5), "asr_p90": pct(asr_all, .9),
                    "align_p50": pct(al_all, .5), "align_p90": pct(al_all, .9),
                    "step_p50": pct(tot, .5), "step_p90": pct(tot, .9),
                    "busy_fraction": sum(tot) / (len(tot) * args.chunk) if tot else None,
                    "peak_mem_gb": mx.get_peak_memory() / 1e9}
    speed_path.write_text(json.dumps(speed, indent=1))
    print(json.dumps(speed[label]))


if __name__ == "__main__":
    main()
