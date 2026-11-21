"""Can Qwen3-ASR stand in for Whisper as the follower's lyric-span scorer?

Offline, on the church goldens (never leaves this Mac): every 2 s, the last
6 s of audio, every distinct 6-word span of the song being sung, scored by
`MLXSpanScorer` (Whisper small.en, production) and by `QwenSpanScorer` here
(teacher-forced over Qwen's prompt: the audio prompt is prefilled once and
its KV cache broadcast over each batch of spans).

Per window: is the best span's slide the one sung at its placed time
("placed"), and at any time in the window's last 3 s ("recent")? Windows on
slides with no words are skipped. Also ms per call.

    cd sidecar && uv run --no-sync python ../research/spikes/qwen_improve/span_scorer.py [rec ...]
"""

from __future__ import annotations

import json
import string
import sys
import time
from pathlib import Path

import numpy as np

from auto_presenter.asr.mlx_backend import local_snapshot
from auto_presenter.asr.qwen_backend import (
    QWEN_REPO,
    _attention_times,
    _install_attention_capture,
)
from auto_presenter.eval.replay import iter_ground_truth, load_recording
from auto_presenter.follower.scorer import SCORE_CHUNK, MLXSpanScorer, ScoreResult, spans_for
from auto_presenter.music.inspect import load_wav

SR = 16000
WIN = 6.0
STEP = 2.0
RECS = Path.home() / "Library/Application Support/com.churchslides.app/recordings"
GATE = (
    "20260818-213104_293 20260818-213617_974 20260818-213825_312 20260818-214054_974 "
    "20260818-214759_057 20260818-215943_266 20260903-115044_425 20261002-185523_091 "
    "20261006-225539_315"
).split()
OUT = Path.home() / "Development/auto-presenter-research/work/qwen_backend/span_scorer"


class QwenSpanScorer:
    """Teacher-forced span scoring with Qwen3-ASR (research version)."""

    def __init__(self, model, tok) -> None:
        self.model, self.tok = model, tok
        self._cache: dict[str, list[int]] = {}

    def tokens(self, text: str) -> list[int]:
        if text not in self._cache:
            self._cache[text] = list(self.tok.encode(" " + text.strip()))
        return self._cache[text]

    def score(self, audio, spans, place_among=None) -> ScoreResult | None:
        import mlx.core as mx
        from mlx_qwen3_asr.audio import compute_features
        from mlx_qwen3_asr.decoder import KVCache

        if not spans:
            return None
        t0 = time.perf_counter()
        m = self.model
        mel, lens = compute_features(audio.astype(np.float32))
        feats, _ = m.audio_tower(mel.astype(mx.float16), lens)
        n_a = int(feats.shape[1])
        prompt = list(self.tok.build_prompt_tokens(n_audio_tokens=n_a, language="English", context=""))
        n_p = len(prompt)
        pos = mx.arange(n_p)[None, :]
        cache = m.create_cache()
        last = m.prefill(mx.array([prompt]), feats, mx.stack([pos, pos, pos], axis=1), cache)
        mx.eval(last, *[k for k in cache.keys if k is not None])
        first_lp = last[0, 0].astype(mx.float32)
        first_lp = first_lp - mx.logsumexp(first_lp)
        toks = [self.tokens(s) for s in spans]
        scores = np.zeros(len(spans), np.float32)
        by_len: dict[int, list[int]] = {}
        for i, t in enumerate(toks):
            by_len.setdefault(len(t), []).append(i)
        for n_tok, all_idx in by_len.items():
            for c in range(0, len(all_idx), SCORE_CHUNK):
                idx = all_idx[c : c + SCORE_CHUNK]
                b = len(idx)
                kv = KVCache(len(cache.keys))
                kv.keys = [mx.broadcast_to(k, (b, *k.shape[1:])) for k in cache.keys]
                kv.values = [mx.broadcast_to(v, (b, *v.shape[1:])) for v in cache.values]
                kv.offset = cache.offset
                tgt = np.asarray([toks[i] for i in idx], np.int32)  # (b, n)
                # inputs: the span's tokens but the last (the first token is
                # predicted by the prompt's last position)
                p = mx.arange(n_p, n_p + n_tok - 1)[None, :]
                p = mx.broadcast_to(mx.stack([p, p, p], axis=1), (b, 3, n_tok - 1))
                hit0 = first_lp[mx.array(tgt[:, 0])]
                if n_tok > 1:
                    lg = m.step_many(mx.array(tgt[:, :-1]), p, kv).astype(mx.float32)
                    t = mx.array(tgt[:, 1:])
                    hit = mx.take_along_axis(lg, t[:, :, None], axis=-1)[:, :, 0]
                    lp = hit - mx.logsumexp(lg, axis=-1)
                    total = (hit0 + lp.sum(axis=1)) / n_tok
                else:
                    total = hit0
                mx.eval(total)
                scores[np.asarray(idx)] = np.array(total)
        t1 = time.perf_counter()
        best = int(np.argmax(scores[: place_among or len(spans)]))
        words = _attention_times(m, self.tok, audio, spans[best])
        place = words[-1][1] if words else WIN / 2
        t2 = time.perf_counter()
        return ScoreResult(scores, best, place, {"score": t1 - t0, "place": t2 - t1, "total": t2 - t0})


def truth_at(gt, t):
    cur = None
    for g in gt:
        if g.t_seconds <= t:
            cur = (g.item_idx, g.slide_idx)
        else:
            break
    return cur


def norm(w):
    return w.lower().strip(string.punctuation)


def main() -> None:
    from mlx_qwen3_asr.load_models import load_model
    from mlx_qwen3_asr.tokenizer import _TokenizerHolder

    _install_attention_capture()
    path = local_snapshot(QWEN_REPO)
    qm, _ = load_model(path)
    qs = QwenSpanScorer(qm, _TokenizerHolder.get(path))
    ws = MLXSpanScorer("mlx-community/whisper-small.en-mlx")
    ws._load()
    OUT.mkdir(parents=True, exist_ok=True)
    recs = sys.argv[1:] or GATE
    rows = []
    for rid in recs:
        rdir = RECS / f"rec_{rid}"
        rec = load_recording(rdir)
        gt = list(iter_ground_truth(rec))
        audio = load_wav(rdir / "audio.wav")
        items = {x.item_idx: x for x in rec.setlist_content}
        t = WIN
        while t <= len(audio) / SR:
            tr = truth_at(gt, t)
            if tr is None or tr[0] not in items:
                t += STEP
                continue
            slides = items[tr[0]].slides
            texts = [" ".join(norm(w) for w in s) for s in slides]
            if not slides[tr[1]]:
                t += STEP
                continue
            sp = spans_for(slides)
            uniq = sorted(set(sp.values()))
            owner: dict[str, set[str]] = {}
            for (si, _), s in sp.items():
                owner.setdefault(s, set()).add(texts[si])
            a = audio[int((t - WIN) * SR) : int(t * SR)]
            row = {"rec": rid, "t": t, "n": len(uniq)}
            for name, scr in (("whisper", ws), ("qwen", qs)):
                r = scr.score(a, uniq)
                bt = owner[uniq[r.best]]
                at = truth_at(gt, t - WIN + r.place_s)
                recent = {truth_at(gt, t - d) for d in (0.0, 1.0, 2.0, 3.0)}
                row[name] = {
                    "placed": bool(at and at[0] == tr[0] and texts[at[1]] in bt),
                    "recent": any(x and x[0] == tr[0] and texts[x[1]] in bt for x in recent),
                    "ms": r.timings["total"] * 1000,
                    "score_ms": r.timings["score"] * 1000,
                }
            rows.append(row)
            t += STEP
        print(rid, len([r for r in rows if r["rec"] == rid]), "windows", flush=True)
    (OUT / "rows.json").write_text(json.dumps(rows))
    for name in ("whisper", "qwen"):
        n = len(rows)
        pl = sum(r[name]["placed"] for r in rows) / n
        rc = sum(r[name]["recent"] for r in rows) / n
        ms = np.array([r[name]["ms"] for r in rows])
        sms = np.array([r[name]["score_ms"] for r in rows])
        print(
            f"{name:8s} {n} windows  placed {pl:.1%}  recent {rc:.1%}  "
            f"ms p50 {np.median(ms):.0f} p90 {np.percentile(ms, 90):.0f}  (scoring p50 {np.median(sms):.0f})"
        )


if __name__ == "__main__":
    main()
