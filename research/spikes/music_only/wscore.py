"""Whisper as a scorer: how well does known text explain the last few seconds?

No free decoding: the audio window is encoded once, then each candidate word
span is scored by the decoder's probability of exactly those tokens
(CTranslate2 `align`, the forced-alignment path faster-whisper uses for word
timestamps). Cost is one encoder pass plus a batched, teacher-forced decoder
pass per candidate, and there is nothing to hallucinate or revise, so no
stability margin has to be waited out.

`Scorer.score(window_audio, spans)` -> mean log-probability per token, per span.
"""
from __future__ import annotations

import numpy as np

import ctranslate2
from faster_whisper import WhisperModel
from faster_whisper.tokenizer import Tokenizer


class Scorer:
    def __init__(self, size: str = "small.en", threads: int = 4, keep_frames: int | None = None):
        self.fw = WhisperModel(size, device="cpu", compute_type="int8", cpu_threads=threads)
        self.model = self.fw.model
        self.tok = Tokenizer(self.fw.hf_tokenizer, False, task="transcribe", language="en")
        self.fe = self.fw.feature_extractor
        self.keep_frames = keep_frames
        self._cache: dict[str, list[int]] = {}

    def tokens(self, text: str) -> list[int]:
        if text not in self._cache:
            self._cache[text] = self.tok.encode(" " + text.strip())
        return self._cache[text]

    def encode(self, audio_16k: np.ndarray):
        feats = self.fe(audio_16k)[:, : self.fe.nb_max_frames]
        n = feats.shape[-1]
        if n < self.fe.nb_max_frames:
            feats = np.pad(feats, ((0, 0), (0, self.fe.nb_max_frames - n)))
        enc = self.fw.encode(feats)
        return enc, n

    def score(self, audio_16k: np.ndarray, spans: list[str], batch: int = 32) -> np.ndarray:
        enc, n_frames = self.encode(audio_16k)
        arr = np.array(enc)  # 1 x 1500 x d
        if self.keep_frames:
            arr = arr[:, : self.keep_frames]
        out = np.zeros(len(spans), np.float32)
        self.last_end = np.full(len(spans), np.nan, np.float32)  # seconds from window start
        for i in range(0, len(spans), batch):
            chunk = spans[i : i + batch]
            toks = [self.tokens(s) for s in chunk]
            sv = ctranslate2.StorageView.from_array(np.ascontiguousarray(np.repeat(arr, len(chunk), axis=0)))
            res = self.model.align(sv, self.tok.sot_sequence, toks, [n_frames // 2] * len(chunk), median_filter_width=7)
            for j, r in enumerate(res):
                p = np.array(r.text_token_probs, np.float64)
                out[i + j] = float(np.mean(np.log(np.maximum(p, 1e-6)))) if len(p) else -20.0
                # where in the window the span's last token sits (20 ms per encoder frame)
                ix = [a[1] for a in r.alignments if a[0] < len(toks[j])]
                self.last_end[i + j] = max(ix) * 0.02 if ix else np.nan
        return out
