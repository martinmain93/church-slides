"""Qwen3-ASR 0.6B audio-tower features in PyTorch (official qwen-asr package),
for 2.5 s windows: the Kaggle job's encoder. Shared by the parity probe."""
import numpy as np, torch
from transformers import WhisperFeatureExtractor
from qwen_asr.core.transformers_backend.modeling_qwen3_asr import Qwen3ASRForConditionalGeneration

class QwenFeats:
    def __init__(self, path="Qwen/Qwen3-ASR-0.6B", device="cpu", dtype=torch.float32):
        m = Qwen3ASRForConditionalGeneration.from_pretrained(path, torch_dtype=dtype)
        self.tower = m.thinker.audio_tower.to(device).eval(); del m
        self.fe = WhisperFeatureExtractor.from_pretrained(path)
        self.device, self.dtype = device, dtype
    @torch.no_grad()
    def __call__(self, clip: np.ndarray) -> np.ndarray:
        a = self.fe(clip, sampling_rate=16000, padding=True, truncation=False, return_attention_mask=True, return_tensors="pt")
        n = int(a["attention_mask"].sum())
        x = a["input_features"][0, :, :n].to(self.device, self.dtype)
        out = self.tower(x, feature_lens=torch.tensor([n], device=self.device)).last_hidden_state
        return out.float().cpu().numpy()
