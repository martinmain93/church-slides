"""How fast can this machine run what live following asks of Whisper?

Live following runs two Whisper workloads on the same 6 s of audio:

* **transcribe**: one pass every 1 s (`asr.streaming.SlidingWindowWrapper`);
* **score**: every 2 s, the decoder's probability of ~160 candidate lyric
  spans given the window (`follower.scorer`), the span scorer that makes the
  score follower accurate and on time. Today it exists only for MLX.

Both start with the encoder (audio -> features), the heavy part, and both
encode the same window. This times each step per engine and model on real
windows from the perf fixture, and adds up the share of real time the pair
needs (`load`: transcribe/1 s + score/2 s; under ~0.7 leaves room for the
rest of the engine). Run it on any machine:

    uv run --project sidecar python scripts/bench_whisper.py
    uv run --project sidecar python scripts/bench_whisper.py --engines ct2 --models base.en,small.en

Engines: `ct2` (faster-whisper / CTranslate2 on the CPU, today's Intel and
Windows engine), `mlx` (Apple silicon GPU, the reference), `whispercpp` (if
`pywhispercpp` is importable). Measurement only: nothing here is used by the
app.

What it found first (M1 CPU, 2026-10-14): faster-whisper pads every window to
30 s, but the CTranslate2 encoder takes the short input too, 6x faster
(small.en 814 -> 133 ms). Decoding from exactly 6 s loops on the last phrase;
2 s of trailing silence mostly stops that (`transcribe_short`). Scoring on
CTranslate2 has only `align`, which also runs its alignment search: 4.2 s
for 160 small.en spans against MLX's 0.46 s.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "data" / "fixtures" / "perf"
WINDOW_S = 6.0
SR = 16000


def windows(n: int) -> list[np.ndarray]:
    import soundfile as sf

    audio, sr = sf.read(FIXTURE / "service.wav", dtype="float32")
    assert sr == SR, sr
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    w = int(WINDOW_S * SR)
    # spread over the sung part of the clip
    starts = np.linspace(10 * SR, len(audio) - w - SR, n).astype(int)
    return [audio[s : s + w].copy() for s in starts]


def spans(n: int = 160) -> list[str]:
    """Candidate lyric spans like the follower's: 3-8 word runs of the
    fixture setlist's lines."""
    content = json.loads((FIXTURE / "service.json").read_text())["setlist_content"]
    words = [w for item in content["items"] for slide in item["slides"] for w in slide]
    out: list[str] = []
    i = 0
    while len(out) < n:
        k = 3 + (i % 6)
        start = (i * 5) % max(1, len(words) - k)
        out.append(" ".join(words[start : start + k]))
        i += 1
    return out


def timed(fn, runs: list[np.ndarray]) -> list[float]:
    fn(runs[0])  # warm-up: model load, kernel compile
    out = []
    for a in runs:
        t = time.perf_counter()
        fn(a)
        out.append((time.perf_counter() - t) * 1000)
    return out


def summary(ms: list[float]) -> dict:
    s = sorted(ms)
    return {"median_ms": round(statistics.median(s), 1), "p90_ms": round(s[int(0.9 * (len(s) - 1))], 1)}


#: Short-context decoding (see `bench_ct2`): trailing silence and a token cap.
SHORT_PAD_S = 2.0
SHORT_MAX_TOKENS = 40


def agreement(ref: list[list[str]], hyp: list[list[str]]) -> dict:
    """Share of the reference's words the hypothesis has, and how many it adds."""
    import difflib

    matched = total = added = 0
    for r, h in zip(ref, hyp, strict=True):
        m = sum(b.size for b in difflib.SequenceMatcher(None, r, h).get_matching_blocks())
        matched, total, added = matched + m, total + len(r), added + len(h) - m
    return {"recall": round(matched / max(total, 1), 2), "added": round(added / max(total, 1), 2)}


# ------------------------------------------------------------------- engines


def bench_ct2(model: str, wins: list[np.ndarray], cand: list[str], threads: int) -> dict:
    import ctranslate2
    from faster_whisper import WhisperModel
    from faster_whisper.tokenizer import Tokenizer

    fw = WhisperModel(model, device="cpu", compute_type="int8", cpu_threads=threads)
    fe = fw.feature_extractor
    tok = Tokenizer(fw.hf_tokenizer, False, task="transcribe", language="en")
    toks = [tok.encode(" " + s) for s in cand]

    def feats(a, frames):
        f = fe(a)[:, :frames]
        return np.pad(f, ((0, 0), (0, max(0, frames - f.shape[-1]))))

    def transcribe(a):
        segs, _ = fw.transcribe(
            a, language="en", beam_size=1, vad_filter=False, temperature=0.0,
            word_timestamps=True, condition_on_previous_text=False,
        )
        list(segs)

    def encode_full(a):
        fw.encode(feats(a, fe.nb_max_frames))

    out = {
        "transcribe": summary(timed(transcribe, wins)),
        "encode_30s": summary(timed(encode_full, wins)),
    }
    # Does the encoder take a 6 s input (no padding to 30 s)?
    try:
        fw.encode(feats(wins[0], int(WINDOW_S * 100)))
        out["encode_6s"] = summary(timed(lambda a: fw.encode(feats(a, int(WINDOW_S * 100))), wins))
    except Exception as exc:  # noqa: BLE001 - reported, not handled
        out["encode_6s"] = {"unsupported": str(exc).splitlines()[0][:160]}

    # Short context: encode the 6 s window plus 2 s of silence instead of
    # padding to 30 s, and decode greedily without timestamps. Exactly 6 s
    # loops (the decoder never hears the audio end); the silence stops most
    # of that. Its words are compared with the padded transcription's.
    prompt = tok.sot_sequence + [tok.no_timestamps]

    def decode(a, pad_s):
        if pad_s is None:
            f = feats(a, fe.nb_max_frames)
        else:
            a = np.concatenate([a, np.zeros(int(pad_s * SR), np.float32)])
            f = feats(a, int(round(len(a) / SR * 100)))
        r = fw.model.generate(fw.encode(f), [prompt], beam_size=1, max_length=SHORT_MAX_TOKENS)
        return tok.decode([t for t in r[0].sequences_ids[0] if t < tok.eot]).lower().split()

    out["transcribe_short"] = summary(timed(lambda a: decode(a, SHORT_PAD_S), wins))
    out["short_vs_padded"] = agreement(
        [decode(a, None) for a in wins[1:]], [decode(a, SHORT_PAD_S) for a in wins[1:]]
    )

    n_frames = int(WINDOW_S * 100)

    def score(a, keep=None):
        enc = np.array(fw.encode(feats(a, fe.nb_max_frames)))
        if keep:
            enc = enc[:, :keep]
        for i in range(0, len(toks), 32):
            chunk = toks[i : i + 32]
            sv = ctranslate2.StorageView.from_array(np.ascontiguousarray(np.repeat(enc, len(chunk), axis=0)))
            fw.model.align(sv, tok.sot_sequence, chunk, [n_frames // 2] * len(chunk), median_filter_width=7)

    out["score_160"] = summary(timed(score, wins))
    # cross-attention over the 6 s actually sung only (encoder still 30 s)
    out["score_160_keep6s"] = summary(timed(lambda a: score(a, keep=int(WINDOW_S * 50)), wins))
    return out


def bench_mlx(model: str, wins: list[np.ndarray], cand: list[str]) -> dict:
    sys.path.insert(0, str(ROOT / "sidecar"))
    import mlx_whisper

    from auto_presenter.asr.mlx_backend import local_snapshot, repo_for
    from auto_presenter.follower.scorer import MLXSpanScorer

    repo = local_snapshot(repo_for(model))

    def transcribe(a):
        mlx_whisper.transcribe(
            a, path_or_hf_repo=repo, language="en", word_timestamps=True,
            condition_on_previous_text=False, temperature=0.0, verbose=None,
        )

    scorer = MLXSpanScorer(repo)
    return {
        "transcribe": summary(timed(transcribe, wins)),
        "score_160": summary(timed(lambda a: scorer.score(a, cand), wins)),
        "_scores": [np.asarray(scorer.score(a, cand).scores) for a in wins[1:]],
    }


def bench_onnx(
    model: str, wins: list[np.ndarray], cand: list[str], threads: int, providers: list[str],
    variant: str = "",
    root: Path | None = None,
) -> dict:
    """The batched span scorer from scripts/export_whisper_scorer_onnx.py:
    the 6 s window encoded as it is, every span scored in one call."""
    import onnxruntime as ort
    from faster_whisper.feature_extractor import FeatureExtractor

    # variant "int8": the graphs quantised (onnxruntime.quantization.quantize_dynamic)
    d = (root or ROOT / "build" / "onnx") / (f"{model}-{variant}" if variant else model)
    if not (d / "scorer.onnx").exists():
        raise FileNotFoundError(f"{d}/scorer.onnx: run scripts/export_whisper_scorer_onnx.py {model}")
    meta = json.loads((d / "tokens.json").read_text())
    assert meta["spans"] == cand, "tokens.json was exported for other spans"
    opts = ort.SessionOptions()
    if threads:
        opts.intra_op_num_threads = threads
    enc = ort.InferenceSession(str(d / "encoder.onnx"), opts, providers=providers)
    sc = ort.InferenceSession(str(d / "scorer.onnx"), opts, providers=providers)
    fe = FeatureExtractor()
    seqs = [meta["prompt"] + ids for ids in meta["span_ids"]]
    n_prompt = len(meta["prompt"])
    # Spans grouped by length (one call per length, so none is padded), and
    # only the positions predicting a span's own tokens are scored.
    groups: list[tuple[list[int], dict]] = []
    for n in sorted({len(s) for s in seqs}):
        idx = [i for i, s in enumerate(seqs) if len(s) == n]
        toks = np.array([seqs[i] for i in idx], np.int64)
        rows = np.array([b * n + p for b in range(len(idx)) for p in range(n_prompt - 1, n - 1)], np.int64)
        targets = toks[:, n_prompt:].reshape(-1)
        groups.append((idx, {"tokens": toks, "rows": rows, "targets": targets}))

    def encode(a):
        mel = fe(a)[:, : int(WINDOW_S * 100)][None].astype(np.float32)
        return enc.run(None, {"mel": mel})[0]

    kv_format = json.loads((d / "meta.json").read_text()).get("format", 1) >= 2 if (d / "meta.json").exists() else False

    def score(a):
        enc_out = encode(a)
        out = np.zeros(len(seqs), np.float32)
        for idx, feed in groups:
            first = {"kv": enc_out} if kv_format else {"features": enc_out}
            lp = sc.run(None, {**first, **feed})[0].reshape(len(idx), -1)
            out[idx] = lp.mean(1)
        return out

    return {
        "providers": sc.get_providers(),
        "encode_6s": summary(timed(encode, wins)),
        "score_160": summary(timed(score, wins)),
        "_scores": [score(a) for a in wins[1:]],
    }


def ranking_agreement(a: list[np.ndarray], b: list[np.ndarray]) -> dict:
    """Two scorers' span rankings per window: same best span, and rank correlation."""
    def ranks(x):
        r = np.empty(len(x)); r[np.argsort(x)] = np.arange(len(x)); return r
    same = np.mean([int(np.argmax(x) == np.argmax(y)) for x, y in zip(a, b, strict=True)])
    rho = np.mean([np.corrcoef(ranks(x), ranks(y))[0, 1] for x, y in zip(a, b, strict=True)])
    return {"same_best": round(float(same), 2), "rank_corr": round(float(rho), 2)}


def bench_whispercpp(model: str, wins: list[np.ndarray], threads: int) -> dict:
    from pywhispercpp.model import Model

    out = {}
    for label, ctx in (("transcribe", 0), ("transcribe_ctx6s", int(WINDOW_S * 50))):
        m = Model(model.removesuffix(".en") + ".en" if model.endswith(".en") else model,
                  n_threads=threads, print_progress=False, print_realtime=False,
                  audio_ctx=ctx, no_context=True, single_segment=True)
        out[label] = summary(timed(lambda a, m=m: m.transcribe(a), wins))
    return out


# ---------------------------------------------------------------------- main


def load(r: dict) -> float | None:
    """Share of real time: a transcription pass per 1 s, a scoring pass per 2 s."""
    t = r.get("transcribe", {}).get("median_ms")
    s = r.get("score_160_keep6s", r.get("score_160", {})).get("median_ms")
    if t is None:
        return None
    return round(t / 1000 + (s or 0) / 2000, 2)



def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", default="mlx,ct2,onnx,whispercpp")
    ap.add_argument("--models", default="tiny.en,base.en,small.en")
    ap.add_argument("--windows", type=int, default=8)
    ap.add_argument("--threads", type=int, default=0, help="CPU threads (0: the engine's default)")
    ap.add_argument("--onnx-variant", default="", help='"int8": the quantised ONNX graphs')
    ap.add_argument("--onnx-root", type=Path, help="where the exported models are (default build/onnx)")
    ap.add_argument(
        "--providers",
        default="CPUExecutionProvider",
        help="ONNX Runtime providers in order, e.g. DmlExecutionProvider,CPUExecutionProvider",
    )
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    wins = windows(args.windows + 1)
    cand = spans()
    machine = f"{platform.system()} {platform.machine()} · {platform.processor() or '?'} · {os.cpu_count()} cores"
    print(f"{machine}\n{len(wins) - 1} windows of {WINDOW_S:.0f} s, {len(cand)} spans\n", flush=True)
    results = []
    #: model -> engine -> per-window span scores, to compare scorers' rankings
    reference: dict[str, dict[str, list[np.ndarray]]] = {}
    for engine in args.engines.split(","):
        for model in args.models.split(","):
            try:
                if engine == "ct2":
                    r = bench_ct2(model, wins, cand, args.threads)
                elif engine == "mlx":
                    r = bench_mlx(model, wins, cand)
                elif engine == "whispercpp":
                    r = bench_whispercpp(model, wins, args.threads or 4)
                elif engine == "onnx":
                    r = bench_onnx(model, wins, cand, args.threads, args.providers.split(","),
                                   args.onnx_variant, args.onnx_root)
                else:
                    raise SystemExit(f"unknown engine {engine}")
            except ImportError as exc:
                print(f"{engine:10s} {model:9s} not available here ({exc.name})", flush=True)
                break
            except FileNotFoundError as exc:
                print(f"{engine:10s} {model:9s} {exc}", flush=True)
                continue
            scores = r.pop("_scores", None)
            if scores is not None:
                reference.setdefault(model, {})[engine] = scores
                mlx = reference[model].get("mlx")
                if engine != "mlx" and mlx is not None:
                    r["vs_mlx_scorer"] = ranking_agreement(mlx, scores)
            r = {"engine": engine, "model": model, **r, "load": load(r)}
            results.append(r)
            steps = "  ".join(
                f"{k} {v['median_ms']:.0f}/{v['p90_ms']:.0f}" if "median_ms" in v
                else f"{k} recall {v['recall']:.0%} added {v['added']:.0%}" if "recall" in v
                else f"{k} same best {v['same_best']:.0%} rank corr {v['rank_corr']:.2f}" if "same_best" in v
                else f"{k} {v.get('unsupported', '?')}"
                for k, v in r.items() if isinstance(v, dict)
            )
            print(f"{engine:10s} {model:9s} load {r['load']}   {steps}", flush=True)
    # The CPU plan: CTranslate2's short-context transcription with ONNX
    # Runtime's batched scorer. Under ~0.7 of real time leaves the follower,
    # chords and the rest of the engine room.
    plans = []
    for model in args.models.split(","):
        ct2 = next((r for r in results if r["engine"] == "ct2" and r["model"] == model), None)
        onnx = next((r for r in results if r["engine"] == "onnx" and r["model"] == model), None)
        if ct2 and onnx and "transcribe_short" in ct2:
            t, s = ct2["transcribe_short"]["median_ms"], onnx["score_160"]["median_ms"]
            plan = {"model": model, "transcribe_ms": t, "score_ms": s, "load": round(t / 1000 + s / 2000, 2)}
            plans.append(plan)
            print(f"CPU plan  {model:9s} load {plan['load']}   short transcribe {t:.0f} ms + onnx score {s:.0f} ms", flush=True)
    if args.out:
        args.out.write_text(json.dumps({"machine": machine, "results": results, "cpu_plan": plans}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
