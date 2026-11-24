"""Export a Qwen3-ASR decoder graph that extends a key/value cache by N tokens.

The published export (andrewleech/qwen3-asr-onnx, Apache 2.0) has a prefill
graph that always starts from an empty cache and a step graph fixed at one
token. Streaming as the Apple-silicon backend does it keeps the cache of the
prompt head and finished audio blocks and prefills only the new tail, which
needs a multi-token step: `decoder_extend`.

    input_embeds [1, S, 1024]  token embeddings, audio features at audio positions
    position_ids [1, S]
    past_keys / past_values [28, 1, 8, P, 128]
    -> logits [1, 1, vocab] (last token only), present_keys/values [28, 1, 8, P+S, 128],
       attention [8, S, P+S]: the softmaxed attention of `ATTN_HEADS`, for word times

A causal mask lets new token i see all P cached tokens and new tokens <= i.

Research only: needs the export tool's environment (PyTorch, transformers,
qwen-asr), cloned at ../auto-presenter-research/work/qwen_cpu/qwen3-asr-onnx.

    <tool>/.venv/bin/python research/spikes/qwen_cpu/export_extend.py OUT_DIR
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import torch
from torch import nn

TOOL = Path(os.environ.get(
    "QWEN_ONNX_TOOL",
    Path.home() / "Development/auto-presenter-research/work/qwen_cpu/qwen3-asr-onnx",
))
sys.path.insert(0, str(TOOL))

from export import load_model  # noqa: E402
from src.decoder_wrapper import _apply_rotary_pos_emb, _decoder_layer_forward  # noqa: E402
from src.onnx_fixup import fix_reshape_allowzero  # noqa: E402

MODEL = "Qwen/Qwen3-ASR-0.6B"
#: (layer, query head) whose attention over the audio gives word times: the
#: Apple-silicon backend's `qwen_backend.ATTN_HEADS`, kept in step by hand.
ATTN_HEADS = ((6, 6), (6, 10), (6, 7), (6, 13), (12, 6), (11, 11), (2, 11), (11, 12))


class DecoderExtendWrapper(nn.Module):
    def __init__(self, text_model, lm_head, text_config):
        super().__init__()
        self.layers = text_model.layers
        self.norm = text_model.norm
        self.rotary_emb = text_model.rotary_emb
        self.lm_head = lm_head
        self.num_kv_groups = text_config.num_attention_heads // text_config.num_key_value_heads

    def forward(self, input_embeds, position_ids, past_keys, past_values):
        pos_3d = position_ids.unsqueeze(0).expand(3, -1, -1)
        cos, sin = self.rotary_emb(input_embeds, pos_3d)
        s = input_embeds.shape[1]
        p = past_keys.shape[3]
        rows = torch.arange(s, device=input_embeds.device).unsqueeze(1)
        cols = torch.arange(p + s, device=input_embeds.device).unsqueeze(0)
        blocked = cols > rows + p
        mask = torch.zeros(s, p + s, dtype=input_embeds.dtype, device=input_embeds.device)
        mask = mask.masked_fill(blocked, torch.finfo(input_embeds.dtype).min)
        mask = mask.unsqueeze(0).unsqueeze(0)
        hidden = input_embeds
        keys, values = [], []
        attn_rows: dict[tuple[int, int], torch.Tensor] = {}
        for i, layer in enumerate(self.layers):
            heads = [h for li, h in ATTN_HEADS if li == i]
            if heads:
                q = self._queries(layer, hidden, cos, sin)
            hidden, k, v = _decoder_layer_forward(
                layer, hidden, cos, sin, mask,
                past_key=past_keys[i], past_value=past_values[i],
                num_kv_groups=self.num_kv_groups,
            )
            for h in heads:
                sc = q[0, h] @ k[0, h // self.num_kv_groups].transpose(0, 1)
                sc = sc * layer.self_attn.scaling + mask[0, 0]
                attn_rows[(i, h)] = torch.softmax(sc.float(), dim=-1)
            keys.append(k)
            values.append(v)
        logits = self.lm_head(self.norm(hidden[:, -1:, :]))
        attn = torch.stack([attn_rows[lh] for lh in ATTN_HEADS], dim=0)
        return logits, torch.stack(keys, dim=0), torch.stack(values, dim=0), attn

    @staticmethod
    def _queries(layer, hidden, cos, sin):
        a = layer.self_attn
        normed = layer.input_layernorm(hidden)
        shape = (*normed.shape[:-1], -1, a.head_dim)
        q = a.q_norm(a.q_proj(normed).view(shape)).transpose(1, 2)
        k = a.k_norm(a.k_proj(normed).view(shape)).transpose(1, 2)
        q, _ = _apply_rotary_pos_emb(q, k, cos, sin)
        return q


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    model = load_model(MODEL)
    cfg = model.config.thinker_config.text_config
    w = DecoderExtendWrapper(model.thinker.model, model.thinker.lm_head, cfg).eval()
    s, p = 12, 100
    args = (
        torch.randn(1, s, cfg.hidden_size),
        torch.arange(p, p + s).unsqueeze(0),
        torch.randn(cfg.num_hidden_layers, 1, cfg.num_key_value_heads, p, cfg.head_dim),
        torch.randn(cfg.num_hidden_layers, 1, cfg.num_key_value_heads, p, cfg.head_dim),
    )
    path = out / "decoder_extend.onnx"
    with torch.no_grad():
        torch.onnx.export(
            w, args, str(path),
            input_names=["input_embeds", "position_ids", "past_keys", "past_values"],
            output_names=["logits", "present_keys", "present_values", "attention"],
            dynamic_axes={
                "input_embeds": {1: "seq_len"},
                "position_ids": {1: "seq_len"},
                "past_keys": {3: "past_seq_len"},
                "past_values": {3: "past_seq_len"},
                "present_keys": {3: "total_seq_len"},
                "present_values": {3: "total_seq_len"},
                "attention": {1: "seq_len", 2: "total_seq_len"},
            },
            opset_version=17,
            do_constant_folding=True,
            dynamo=False,
        )
    print("fixed", fix_reshape_allowzero(str(path)), "reshapes;", path)


if __name__ == "__main__":
    main()
