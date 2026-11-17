"""Whisper as a scorer, on the GPU with MLX.

Same idea as `wscore.py` (CTranslate2 on the CPU): encode the trailing
window once, then score each candidate span by the decoder's probability of
exactly its tokens — teacher-forced, one batched decoder pass, no free
decoding. Also returns where each span's last token sits in the window, from
the decoder's cross-attention (alignment heads), so evidence can be placed at
the moment the text was sung rather than at the window's end.

`audio_ctx`: the encoder normally takes 30 s; only the trailing 6 s carry
audio. Truncating the encoder to the frames actually used (whisper.cpp's
"audio context") cuts its cost about five-fold. `None` = the full 30 s.
"""
from __future__ import annotations

import time

import mlx.core as mx
import numpy as np

from mlx_whisper.audio import N_FRAMES, log_mel_spectrogram, pad_or_trim
from mlx_whisper.load_models import load_model
from mlx_whisper.tokenizer import get_tokenizer

REPO = "mlx-community/whisper-small.en-mlx"


class MLXScorer:
    def __init__(self, repo: str = REPO, audio_ctx: int | None = 300):
        self.model = load_model(repo, dtype=mx.float16)
        self.tok = get_tokenizer(False)
        self.sot = list(self.tok.sot_sequence_including_notimestamps)
        self.audio_ctx = audio_ctx
        self.n_mels = self.model.dims.n_mels
        self._cache: dict[str, list[int]] = {}
        self.timings: dict[str, float] = {}

    def tokens(self, text: str) -> list[int]:
        if text not in self._cache:
            self._cache[text] = self.tok.encode(" " + text.strip())
        return self._cache[text]

    def encode(self, audio_16k: np.ndarray) -> mx.array:
        mel = log_mel_spectrogram(mx.array(audio_16k.astype(np.float32)), n_mels=self.n_mels)
        enc = self.model.encoder
        if self.audio_ctx is None:
            mel = pad_or_trim(mel, N_FRAMES, axis=-2)
            x = enc(mel[None].astype(mx.float16))
        else:
            # the encoder's own forward, with the positional embedding cut to fit
            frames = 2 * self.audio_ctx
            mel = pad_or_trim(mel, frames, axis=-2)[None].astype(mx.float16)
            x = mx.array(mel)
            import mlx.nn as nn
            x = nn.gelu(enc.conv1(x))
            x = nn.gelu(enc.conv2(x))
            x = x + enc._positional_embedding[: x.shape[1]]
            for block in enc.blocks:
                x, _, _ = block(x)
            x = enc.ln_post(x)
        return x

    def score(self, audio_16k: np.ndarray, spans: list[str]) -> tuple[np.ndarray, np.ndarray]:
        """(mean log-prob per token, last-token time in seconds from window start)."""
        t0 = time.perf_counter()
        xa = self.encode(audio_16k)
        mx.eval(xa)
        t1 = time.perf_counter()
        toks = [self.tokens(s) for s in spans]
        L = max(len(t) for t in toks)
        eot = self.tok.eot
        batch = np.full((len(spans), len(self.sot) + L), eot, np.int32)
        for i, t in enumerate(toks):
            batch[i, : len(self.sot) + len(t)] = self.sot + t
        x = mx.array(batch)
        xa_b = mx.broadcast_to(xa, (len(spans),) + tuple(xa.shape[1:]))
        logits, _, cross_qk = self.model.decoder(x, xa_b)
        logp = logits.astype(mx.float32) - mx.logsumexp(logits.astype(mx.float32), axis=-1, keepdims=True)
        # alignment: mean over the model's alignment heads
        heads = np.array(self.model.alignment_heads)
        att = mx.stack([cross_qk[int(l)][:, int(h)] for l, h in heads], axis=1).mean(axis=1)  # B x Ltok x T
        mx.eval(logp, att)
        t2 = time.perf_counter()
        lp = np.array(logp)
        at = np.array(att)
        n0 = len(self.sot)
        scores = np.zeros(len(spans), np.float32)
        ends = np.zeros(len(spans), np.float32)
        frame_s = 0.02
        for i, t in enumerate(toks):
            # token j is predicted at position n0 - 1 + j
            pos = np.arange(n0 - 1, n0 - 1 + len(t))
            scores[i] = float(np.mean(lp[i, pos, t]))
            ends[i] = float(np.argmax(at[i, n0 + len(t) - 1])) * frame_s
        self.timings = {"encode": t1 - t0, "score": t2 - t1, "total": t2 - t0}
        self._last = (toks, cross_qk)
        return scores, ends

    def align(self, audio_16k: np.ndarray, span: str, medfilt_width: int = 7) -> tuple[float, float]:
        """When `span` was sung in the window: (start of its last word, end of
        the span), seconds from the window's start — Whisper's own word-timing
        method (mlx_whisper.timing.find_alignment): the alignment heads'
        cross-attention, softmaxed over frames, normalised per head, median-
        filtered, averaged, then dynamic time warping over tokens x frames.
        The raw argmax of the same attention (`score`'s `ends`) is one noisy
        frame per token; DTW forces the tokens through the window in order."""
        from mlx_whisper.timing import dtw, median_filter

        self.score(audio_16k, [span])
        toks, cross_qk = self._last
        t = toks[0]
        n0 = len(self.sot)  # sot sequence + no-timestamps; row n0-1 predicts the first text token
        heads = np.array(self.model.alignment_heads)
        w = mx.stack([cross_qk[int(l)][0, int(h)] for l, h in heads])  # heads x tokens x frames
        w = mx.softmax(w.astype(mx.float32), axis=-1, precise=True)
        mean = mx.mean(w, axis=-2, keepdims=True)
        std = mx.var(w, axis=-2, keepdims=True, ddof=0).sqrt()
        w = median_filter(np.array((w - mean) / std), medfilt_width)
        matrix = w.mean(axis=0)[n0 - 1 : n0 + len(t)]  # rows predicting text tokens 0..L-1, then EOT
        text_idx, time_idx = dtw(-matrix)
        jumps = np.pad(np.diff(text_idx), (1, 0), constant_values=1).astype(bool)
        jump_times = time_idx[jumps] * 0.02
        last = self.tok.encode(" " + span.split()[-1])
        i_last = max(0, len(t) - len(last))
        return float(jump_times[i_last]), float(jump_times[min(len(jump_times) - 1, len(t))])
