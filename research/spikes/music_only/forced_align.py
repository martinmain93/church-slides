"""Every word's start, from the slide's known lyrics forced onto the audio.

The look-back's rhythm source. The live follower times a line by the span
scorer's DTW over Whisper's cross-attention (`follower.scorer.MLXSpanScorer.
_place`: the start of the span's last word); this is the same machinery run
over a whole line's audio with the whole line's text, returning the start of
*every* word. The look-back is not in a hurry: a line of up to ~28 s takes one
encoder pass and one decoder pass. Cached per (recording, slide, window).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from auto_presenter.follower.scorer import MLXSpanScorer

SR = 16000
OUT = Path(__file__).resolve().parents[3] / "build" / "forced_align"
_aligners: dict[int, MLXSpanScorer] = {}
_cache: dict[str, dict] = {}


def _aligner(seconds: float) -> MLXSpanScorer:
    from auto_presenter.asr.mlx_backend import local_snapshot, repo_for

    ctx = min(1500, int(np.ceil(seconds)) * 50)
    if ctx not in _aligners:
        _aligners[ctx] = MLXSpanScorer(local_snapshot(repo_for("small.en")), audio_ctx=ctx)
    return _aligners[ctx]


def word_starts(audio: np.ndarray, words: list[str]) -> list[float] | None:
    """Start of each word (seconds into `audio`), or None if it cannot align."""
    import mlx.core as mx
    from mlx_whisper.timing import dtw, median_filter

    if not words or len(audio) < SR:
        return None
    sc = _aligner(len(audio) / SR)
    model = sc._load()
    xa = sc._encode(audio)
    per = [sc._tok.encode(" " + w) for w in words]
    toks = [t for p in per for t in p]
    first = np.cumsum([0] + [len(p) for p in per])[:-1]
    x = mx.array(np.asarray([sc._sot + toks + [sc._tok.eot]], np.int32))
    _, _, cross_qk = model.decoder(x, xa)
    heads = np.array(model.alignment_heads)
    w = mx.stack([cross_qk[int(layer)][0, int(head)] for layer, head in heads])
    w = mx.softmax(w.astype(mx.float32), axis=-1, precise=True)
    n_frames = int(round(len(audio) / SR * 50))
    w = w[:, :, :n_frames]
    mean = mx.mean(w, axis=-2, keepdims=True)
    std = mx.var(w, axis=-2, keepdims=True, ddof=0).sqrt()
    w = median_filter(np.array((w - mean) / std), 7)
    n0 = len(sc._sot)
    matrix = w.mean(axis=0)[n0 - 1 : n0 + len(toks)]
    text_idx, time_idx = dtw(-matrix)
    jumps = np.pad(np.diff(text_idx), (1, 0), constant_values=1).astype(bool)
    jt = time_idx[jumps] * 0.02
    if len(jt) < len(toks):
        return None
    return [float(jt[int(i)]) for i in first]


def aligned(name: str, audio: np.ndarray, slide: int, words: list[str], t0: float, t1: float):
    """Word starts (absolute seconds) for `words` sung between t0 and t1."""
    path = OUT / f"{name}.json"
    if name not in _cache:
        _cache[name] = json.loads(path.read_text()) if path.exists() else {}
    key = f"{slide}:{t0:.1f}:{t1:.1f}:{len(words)}:{words[0]}"
    if key not in _cache[name]:
        a, b = max(0, int(t0 * SR)), min(len(audio), int(t1 * SR))
        got = word_starts(audio[a:b].astype(np.float32), words)
        _cache[name][key] = None if got is None else [t0 + x for x in got]
        OUT.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_cache[name]))
    return _cache[name][key]
