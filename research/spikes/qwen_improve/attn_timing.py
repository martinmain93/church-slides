"""Option 5: word times from Qwen3-ASR's own attention, without the aligner.

Qwen3-ASR has no cross-attention: its audio is ~13 tokens a second inside the
language model's prompt, and every text token attends to them by ordinary
self-attention. Whisper's word times come from a few cross-attention heads
that track the audio (DTW over them); this asks whether some of Qwen's
decoder heads do the same.

Per JamendoLyrics song: 8 s windows every 4 s; the reference words wholly
inside a window are teacher-forced after the prompt, and every layer and
head's attention from those text tokens to the audio tokens is kept.

- Pick heads on the first 3 songs: per head, median |error| of each word's
  attention peak against its hand-timed start.
- Time the other 3 songs with the best heads' mean attention and a monotonic
  DTW (word order is known), and report |error| p50/p90 and the share over 1 s,
  next to the forced aligner on the same words and windows.

    cd sidecar && PYTHONPATH=. .venv/bin/python ../research/spikes/qwen_improve/attn_timing.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from common import MAIN6, SR, WORK, audio_of, load_clip, song_lines  # noqa: E402

WIN, HOP = 8.0, 4.0
CAPTURE: list | None = None


def _install_capture() -> None:
    import mlx.core as mx
    import mlx_qwen3_asr.decoder as dec

    orig = dec._scaled_dot_product_attention

    def sdpa(q, k, v, mask=None, scale=None):
        if CAPTURE is None:
            return orig(q, k, v, mask=mask, scale=scale)
        rep = q.shape[1] // k.shape[1]
        kk = mx.repeat(k, rep, axis=1)
        s = (q * (scale or q.shape[-1] ** -0.5)) @ kk.transpose(0, 1, 3, 2)
        if mask is not None:
            s = s + mask if mask.dtype != mx.bool_ else mx.where(mask, s, -1e9)
        w = mx.softmax(s.astype(mx.float32), axis=-1)
        CAPTURE.append(w[0])
        return (w.astype(v.dtype) @ mx.repeat(v, rep, axis=1))

    dec._scaled_dot_product_attention = sdpa


def attention(model, tok, audio: np.ndarray, words: list[str]):
    """(layers, heads, n_words, n_audio) attention summed over each word's
    tokens, and the seconds per audio token."""
    global CAPTURE
    import mlx.core as mx
    from mlx_qwen3_asr.audio import compute_features

    mel, lens = compute_features(audio.astype(np.float32))
    feats, _ = model.audio_tower(mel.astype(mx.float16), lens)
    n_a = int(feats.shape[1])
    prompt = tok.build_prompt_tokens(n_audio_tokens=n_a, language="English", context="")
    a0 = prompt.index(int(model.audio_token_id))
    ends, toks = [], []
    for i in range(len(words)):
        toks = tok.encode(" ".join(words[: i + 1]))
        ends.append(len(toks))
    ids = list(prompt) + toks
    pos = mx.arange(len(ids))[None, :]
    CAPTURE = []
    cache = model.create_cache()
    model.prefill(mx.array([ids]), feats, mx.stack([pos, pos, pos], axis=1), cache)
    caps, CAPTURE = CAPTURE, None
    t0 = len(prompt)
    a = np.stack([np.array(c[:, t0:, a0 : a0 + n_a]) for c in caps])  # (L, H, T, A)
    out = np.zeros(a.shape[:2] + (len(words), n_a), np.float32)
    s = 0
    for i, e in enumerate(ends):
        out[:, :, i] = a[:, :, s:e].sum(2)
        s = e
    out /= out.sum(-1, keepdims=True) + 1e-9
    return out, len(audio) / SR / n_a


def dtw_starts(m: np.ndarray) -> np.ndarray:
    """Monotonic path over (word, audio token) maximising attention; each
    word's first token on the path."""
    n_w, n_a = m.shape
    cost = -np.log(m + 1e-6)
    acc = np.full((n_w + 1, n_a + 1), np.inf)
    acc[0, 0] = 0
    back = np.zeros((n_w + 1, n_a + 1), np.int8)
    for i in range(1, n_w + 1):
        for j in range(1, n_a + 1):
            opts = (acc[i - 1, j - 1], acc[i, j - 1], acc[i - 1, j])
            k = int(np.argmin(opts))
            acc[i, j] = cost[i - 1, j - 1] + opts[k]
            back[i, j] = k
    i, j = n_w, int(np.argmin(acc[n_w, 1:])) + 1
    first = np.zeros(n_w, int)
    while i > 0 and j > 0:
        first[i - 1] = j - 1
        k = back[i, j]
        if k == 0:
            i, j = i - 1, j - 1
        elif k == 1:
            j -= 1
        else:
            i -= 1
    return first


def windows(name: str):
    lines = song_lines(name)
    words = [(w, n, s) for ln in lines for w, n, s, e in zip(ln.words, ln.norms, ln.starts, ln.ends, strict=True)
             if n and e > s]
    ends = {(w, s): e for ln in lines for w, s, e in zip(ln.words, ln.starts, ln.ends, strict=True)}
    audio = audio_of(load_clip(name))
    t = 0.0
    while t + WIN <= len(audio) / SR:
        inside = [(w, s - t) for w, n, s in words if s >= t and ends[(w, s)] <= t + WIN]
        if len(inside) >= 4:
            yield audio[int(t * SR) : int((t + WIN) * SR)], [w for w, _ in inside], np.array([s for _, s in inside])
        t += HOP


def main() -> None:
    import mlx_qwen3_asr  # noqa: F401
    from mlx_qwen3_asr.load_models import load_model

    from auto_presenter.asr.qwen_backend import ALIGNER_REPO, QWEN_REPO
    from auto_presenter.asr.mlx_backend import local_snapshot

    _install_capture()
    path = local_snapshot(QWEN_REPO)
    model, _ = load_model(path)
    from mlx_qwen3_asr.tokenizer import _TokenizerHolder

    tok = _TokenizerHolder.get(path)
    from mlx_qwen3_asr import ForcedAligner

    al = ForcedAligner(model_path=local_snapshot(ALIGNER_REPO))
    out = WORK / "attn_timing"
    out.mkdir(parents=True, exist_ok=True)
    pick, test = MAIN6[:3], MAIN6[3:]
    prev = out / "summary.json"
    if prev.exists():  # reuse the head choice; only the held-out part reruns
        best = [tuple(x) for x in json.loads(prev.read_text())["best"]]
        pick = []
    # 1. per-head peak error on the pick songs
    errs: list[np.ndarray] = []
    st = time.monotonic()
    for name in pick:
        for audio, ws, truth in windows(name):
            a, spt = attention(model, tok, audio, ws)
            peak = a.argmax(-1) * spt  # (L, H, W)
            errs.append(np.abs(peak - truth[None, None]))
    if not pick:
        med = np.array(json.loads(prev.read_text())["med"])
        return _test(model, tok, al, best, med, test, out)
    e = np.concatenate(errs, axis=-1)
    med = np.median(e, axis=-1)
    order = np.dstack(np.unravel_index(np.argsort(med, axis=None), med.shape))[0]
    best = [tuple(map(int, x)) for x in order[:8]]
    print(f"pick: {e.shape[-1]} words, {time.monotonic() - st:.0f} s; best heads (layer, head, median err s):")
    for li, hi in best:
        print(f"  L{li:2d} H{hi:2d}  {med[li, hi]:.3f}")
    _test(model, tok, al, best, med, test, out)


def _test(model, tok, al, best, med, test, out) -> None:
    # 2. held-out songs: the best heads' mean attention peak, vs the aligner
    res: dict[str, list[float]] = {"aligner": []}
    for name in test:
        for audio, ws, truth in windows(name):
            a, spt = attention(model, tok, audio, ws)
            for n in (1, 4, 8):
                m = np.mean([a[li, hi] for li, hi in best[:n]], axis=0)
                peak = m.argmax(-1) * spt
                res.setdefault(f"peak_top{n}", []).extend(np.abs(peak - truth))
                # word order is known: never earlier than the word before
                res.setdefault(f"mono_top{n}", []).extend(np.abs(np.maximum.accumulate(peak) - truth))
            got = al.align(audio, " ".join(ws), "English")
            got = [w for w in got if w.text.strip()]
            if len(got) == len(ws):
                res["aligner"] += list(np.abs(np.array([float(w.start_time) for w in got]) - truth))
    summary = {}
    for k, v in res.items():
        v = np.array(v)
        summary[k] = {"n": len(v), "p50": float(np.median(v)), "p90": float(np.quantile(v, 0.9)),
                      "over_1s": float(np.mean(v > 1.0))}
        print(f"{k:10s} n={len(v):5d}  |err| p50 {summary[k]['p50']:.2f}  p90 {summary[k]['p90']:.2f}  "
              f">1 s {summary[k]['over_1s']:.1%}")
    (out / "summary.json").write_text(json.dumps({"best": best, "med": med.tolist(), "summary": summary}, indent=1))


if __name__ == "__main__":
    main()
