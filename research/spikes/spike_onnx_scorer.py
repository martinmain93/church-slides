"""Constrained slide scoring with cross-attention K/V computed ONCE per window.

CTranslate2's align() recomputes the cross-attention keys and values for every
candidate, because the only way to score N candidates is to hand it N copies of
an identical encoder output. Measured, that is ~30 ms per candidate and scales
perfectly linearly — essentially all of it redundant.

Here the encoder runs once, `decoder_model` runs once purely to harvest
`present.*.encoder.{key,value}`, and every candidate afterwards reuses those
tensors. What remains per candidate is self-attention plus a query projection
over ~8 tokens, which is negligible.
"""
from __future__ import annotations
import numpy as np, onnxruntime as ort

class OnnxSlideScorer:
    def __init__(self, model_dir="whisper_onnx", providers=("CPUExecutionProvider",),
                 n_layers=6, encoder_providers=None):
        so = ort.SessionOptions(); so.log_severity_level = 3
        p = list(providers)
        # The encoder can take a different provider from the decoders. CoreML
        # rejects the decoders outright: scoring passes a zero-length
        # self-attention past, and the CoreML EP refuses any input whose
        # runtime shape has zero elements.
        ep = list(encoder_providers) if encoder_providers else p
        self.enc = ort.InferenceSession(f"{model_dir}/encoder_model.onnx", so, providers=ep)
        self.dec = ort.InferenceSession(f"{model_dir}/decoder_model.onnx", so, providers=p)
        self.dwp = ort.InferenceSession(f"{model_dir}/decoder_with_past_model.onnx", so, providers=p)
        self.n_layers = n_layers
        self._dec_out = [o.name for o in self.dec.get_outputs()]

    def encode(self, features: np.ndarray) -> np.ndarray:
        return self.enc.run(None, {"input_features": features})[0]

    def cross_kv(self, hidden: np.ndarray, sot: int) -> dict[str, np.ndarray]:
        """One decoder call, purely to harvest the cross-attention K/V."""
        out = self.dec.run(None, {
            "input_ids": np.array([[sot]], dtype=np.int64),
            "encoder_hidden_states": hidden,
        })
        return {n: v for n, v in zip(self._dec_out, out) if "encoder" in n}

    def score(self, cross: dict[str, np.ndarray], sot_seq: list[int],
              candidates: list[list[int]], batch: int = 8) -> list[float]:
        """Mean log P(token | prefix, audio) for each candidate."""
        scores: list[float] = []
        for start in range(0, len(candidates), batch):
            chunk = candidates[start:start + batch]
            B = len(chunk)
            seqs = [sot_seq + c for c in chunk]
            L = max(len(s) for s in seqs)
            # Right-pad; padded positions are masked out of the score below.
            ids = np.full((B, L), sot_seq[0], dtype=np.int64)
            for i, s in enumerate(seqs):
                ids[i, :len(s)] = s
            feed = {"input_ids": ids}
            for i in range(self.n_layers):
                # Tiling already-projected K/V is a memcpy; recomputing them
                # per candidate is 6 layers of 1500x512x512 matmuls.
                feed[f"past_key_values.{i}.encoder.key"] = np.repeat(
                    cross[f"present.{i}.encoder.key"], B, axis=0)
                feed[f"past_key_values.{i}.encoder.value"] = np.repeat(
                    cross[f"present.{i}.encoder.value"], B, axis=0)
                feed[f"past_key_values.{i}.decoder.key"] = np.zeros((B, 8, 0, 64), np.float32)
                feed[f"past_key_values.{i}.decoder.value"] = np.zeros((B, 8, 0, 64), np.float32)
            logits = self.dwp.run(["logits"], feed)[0]
            logits -= logits.max(-1, keepdims=True)
            logprobs = logits - np.log(np.exp(logits).sum(-1, keepdims=True))
            for i, s in enumerate(seqs):
                n_text = len(s) - len(sot_seq)
                if n_text <= 0:
                    scores.append(-1e9); continue
                # logits[t] predicts token t+1
                lp = [logprobs[i, len(sot_seq) - 1 + k, s[len(sot_seq) + k]]
                      for k in range(n_text)]
                scores.append(float(np.mean(lp)))
        return scores
