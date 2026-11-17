"""Whisper as a scorer of the words the follower expects.

Instead of transcribing freely, encode the trailing window once and ask the
decoder how probable each *expected* span of the song's slide words is — a
teacher-forced pass, no decoding, so nothing to hallucinate and no stability
margin to wait out. Every distinct span of the current song is scored each
step: limiting it to the follower's own candidates cost 0.03-0.06 wrong-slide
in the spike, because scoring everything also says where the band is *not*.

Three things make that affordable on the GPU queue it shares with
transcription (measured, 160 spans of What A Beautiful Name, this Mac):

* the encoder runs on the 6 s actually used, not 30 s padded (whisper.cpp's
  "audio context"): ~40 ms;
* the audio's cross-attention keys and values are computed **once per
  window** and broadcast over every span — computed per span they were 80%
  of the cost (1128 ms -> 372 ms, identical scores);
* spans are batched by token length (no padding) and the vocabulary
  projection runs only at the positions that are scored.

Teacher forcing scores the window from its *start*, so a span's score
describes when it was sung, not the window's end. The best span is placed by
Whisper's own word timing (DTW over the alignment heads' cross-attention,
as `mlx_whisper.timing.find_alignment` does) — 0.28 s median error against
1.2 s for a flat offset.

`Scorer` is the seam for other backends (a CPU scorer for Intel and Windows
is the next step); `MLXSpanScorer` shares the model instance transcription
already holds (`mlx_whisper`'s `ModelHolder`), so it costs no extra memory.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from auto_presenter.follower import score as sc

#: Words per candidate span: long enough to name a line, short enough that a
#: 6 s window holds one.
SPAN_WORDS = 6
#: Seconds of audio scored per step.
WINDOW_S = 6.0


def spans_for(slides: list[list[str]], span_words: int = SPAN_WORDS) -> dict[tuple[int, int], str]:
    """(slide, word index) -> the span of up to `span_words` words ending there."""
    out = {}
    for si, sw in enumerate(slides):
        w = [x for x in (sc.norm(t) for t in sw) if x]
        for j in range(len(w)):
            out[(si, j)] = " ".join(w[max(0, j - span_words + 1) : j + 1])
    return out


@dataclass
class ScoreResult:
    #: Mean log-probability per token, per span (same order as the request).
    scores: np.ndarray
    #: Index of the best span.
    best: int
    #: When the best span's last word started, seconds from the window's start.
    place_s: float
    timings: dict[str, float] = field(default_factory=dict)


class Scorer(Protocol):
    def score(
        self, audio_16k: np.ndarray, spans: list[str], place_among: int | None = None
    ) -> ScoreResult | None: ...


#: Spans decoded together: bounds the scorer's peak memory.
SCORE_CHUNK = 64


class MLXSpanScorer:
    """Teacher-forced span scoring on Apple silicon (mlx-whisper)."""

    def __init__(self, repo: str, audio_ctx: int = int(WINDOW_S * 50)) -> None:
        #: The exact path/repo string transcription loads, so `ModelHolder`
        #: hands back the same model instance.
        self.repo = repo
        self.audio_ctx = audio_ctx
        self._model = None
        self._tok = None
        self._sot: list[int] = []
        self._cache: dict[str, list[int]] = {}
        #: audio_ctx -> a scorer sharing this model for other lengths (`word_starts`)
        self._siblings: dict[int, MLXSpanScorer] = {}

    def _load(self):
        if self._model is None:
            import mlx.core as mx
            from mlx_whisper.tokenizer import get_tokenizer
            from mlx_whisper.transcribe import ModelHolder

            from auto_presenter.asr.mlx_backend import _configure_mlx_memory

            _configure_mlx_memory()
            self._model = ModelHolder.get_model(self.repo, mx.float16)
            multi = bool(getattr(self._model, "is_multilingual", False))
            self._tok = get_tokenizer(multi, language="en" if multi else None, task="transcribe")
            self._sot = list(self._tok.sot_sequence_including_notimestamps)
        return self._model

    def tokens(self, text: str) -> list[int]:
        if text not in self._cache:
            self._cache[text] = self._tok.encode(" " + text.strip())
        return self._cache[text]

    def _encode(self, audio_16k: np.ndarray):
        import mlx.core as mx
        import mlx.nn as nn
        from mlx_whisper.audio import log_mel_spectrogram, pad_or_trim

        model = self._model
        mel = log_mel_spectrogram(mx.array(audio_16k.astype(np.float32)), n_mels=model.dims.n_mels)
        mel = pad_or_trim(mel, 2 * self.audio_ctx, axis=-2)[None].astype(mx.float16)
        enc = model.encoder
        x = nn.gelu(enc.conv1(mel))
        x = nn.gelu(enc.conv2(x))
        x = x + enc._positional_embedding[: x.shape[1]]
        for block in enc.blocks:
            x, _, _ = block(x)
        return enc.ln_post(x)

    def score(
        self, audio_16k: np.ndarray, spans: list[str], place_among: int | None = None
    ) -> ScoreResult | None:
        """`place_among`: time the best of the first N spans only (others ride along)."""
        if not spans:
            return None
        from auto_presenter.asr.mlx_backend import GPU_LOCK

        with GPU_LOCK:
            return self._score(audio_16k, spans, place_among)

    def _score(
        self, audio_16k: np.ndarray, spans: list[str], place_among: int | None
    ) -> ScoreResult | None:
        import mlx.core as mx

        t0 = time.perf_counter()
        model = self._load()
        dec = model.decoder
        xa = self._encode(audio_16k)
        cross = [(blk.cross_attn.key(xa), blk.cross_attn.value(xa)) for blk in dec.blocks]
        toks = [self.tokens(s) for s in spans]
        n0 = len(self._sot)
        scores = np.zeros(len(spans), np.float32)
        by_len: dict[int, list[int]] = {}
        for i, t in enumerate(toks):
            by_len.setdefault(len(t), []).append(i)
        # in chunks, each evaluated before the next: the vocabulary-wide
        # logits of every span at once took 2.3 GB at 650 spans (an 8-song
        # setlist's openings riding along), against ~1 GB in chunks
        for n_tok, all_idx in by_len.items():
            for c in range(0, len(all_idx), SCORE_CHUNK):
                idx = all_idx[c : c + SCORE_CHUNK]
                x = mx.array(np.asarray([self._sot + toks[i] for i in idx], np.int32))
                h = dec.token_embedding(x) + dec.positional_embedding[: x.shape[1]]
                for blk, kv in zip(dec.blocks, cross, strict=True):
                    h, _, _ = blk(h, xa, mask=dec._mask, kv_cache=(None, kv))
                # only the positions that predict the span's tokens
                h = dec.ln(h[:, n0 - 1 : n0 - 1 + n_tok, :])
                lg = dec.token_embedding.as_linear(h).astype(mx.float32)
                target = mx.array(np.asarray([toks[i] for i in idx], np.int32))
                hit = mx.take_along_axis(lg, target[:, :, None], axis=-1)[:, :, 0]
                picked = (hit - mx.logsumexp(lg, axis=-1)).mean(axis=1)
                mx.eval(picked)
                scores[np.asarray(idx)] = np.array(picked)
        t1 = time.perf_counter()
        best = int(np.argmax(scores[: place_among or len(spans)]))
        place = self._place(xa, toks[best], spans[best])
        t2 = time.perf_counter()
        return ScoreResult(
            scores, best, place, {"score": t1 - t0, "place": t2 - t1, "total": t2 - t0}
        )

    def word_starts(self, audio_16k: np.ndarray, words: list[str]) -> list[float] | None:
        """Each word's start (seconds into the audio), under the GPU lock."""
        from auto_presenter.asr.mlx_backend import GPU_LOCK

        with GPU_LOCK:
            return self._word_starts(audio_16k, words)

    def _word_starts(self, audio_16k: np.ndarray, words: list[str]) -> list[float] | None:
        """Each word's start (seconds into the audio): the words forced onto the
        audio with the same DTW timing `_place` uses, for every word. Lines up
        to ~30 s; the encoder runs at this audio's length (a sibling scorer
        per length, sharing the model)."""
        import mlx.core as mx
        from mlx_whisper.timing import dtw, median_filter

        if not words or len(audio_16k) < 16000:
            return None
        ctx = min(1500, int(np.ceil(len(audio_16k) / 16000)) * 50)
        sib = (
            self
            if ctx == self.audio_ctx
            else self._siblings.setdefault(ctx, MLXSpanScorer(self.repo, audio_ctx=ctx))
        )
        model = sib._load()
        xa = sib._encode(audio_16k)
        per = [sib._tok.encode(" " + w) for w in words]
        toks = [t for p in per for t in p]
        first = np.cumsum([0] + [len(p) for p in per])[:-1]
        x = mx.array(np.asarray([sib._sot + toks + [sib._tok.eot]], np.int32))
        _, _, cross_qk = model.decoder(x, xa)
        heads = np.array(model.alignment_heads)
        w = mx.stack([cross_qk[int(layer)][0, int(head)] for layer, head in heads])
        w = mx.softmax(w.astype(mx.float32), axis=-1, precise=True)
        w = w[:, :, : round(len(audio_16k) / 16000 * 50)]
        mean = mx.mean(w, axis=-2, keepdims=True)
        std = mx.var(w, axis=-2, keepdims=True, ddof=0).sqrt()
        w = median_filter(np.array((w - mean) / std), 7)
        n0 = len(sib._sot)
        matrix = w.mean(axis=0)[n0 - 1 : n0 + len(toks)]
        text_idx, time_idx = dtw(-matrix)
        jumps = np.pad(np.diff(text_idx), (1, 0), constant_values=1).astype(bool)
        jt = time_idx[jumps] * 0.02
        if len(jt) < len(toks):
            return None
        return [float(jt[int(i)]) for i in first]

    def _place(self, xa, toks: list[int], span: str, medfilt_width: int = 7) -> float:
        """Start of the span's last word in the window, seconds (Whisper's DTW timing)."""
        import mlx.core as mx
        from mlx_whisper.timing import dtw, median_filter

        model = self._model
        x = mx.array(np.asarray([self._sot + toks + [self._tok.eot]], np.int32))
        _, _, cross_qk = model.decoder(x, xa)
        heads = np.array(model.alignment_heads)
        w = mx.stack([cross_qk[int(layer)][0, int(head)] for layer, head in heads])
        w = mx.softmax(w.astype(mx.float32), axis=-1, precise=True)
        mean = mx.mean(w, axis=-2, keepdims=True)
        std = mx.var(w, axis=-2, keepdims=True, ddof=0).sqrt()
        w = median_filter(np.array((w - mean) / std), medfilt_width)
        n0 = len(self._sot)
        matrix = w.mean(axis=0)[n0 - 1 : n0 + len(toks)]
        text_idx, time_idx = dtw(-matrix)
        jumps = np.pad(np.diff(text_idx), (1, 0), constant_values=1).astype(bool)
        jump_times = time_idx[jumps] * 0.02
        last = self._tok.encode(" " + span.split()[-1])
        i_last = max(0, len(toks) - len(last))
        return float(jump_times[min(i_last, len(jump_times) - 1)])
