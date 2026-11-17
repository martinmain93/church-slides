"""Export Whisper's encoder and a batched span scorer to ONNX, for ONNX Runtime.

Builds on two earlier findings (see docs/research/ASR_ENGINE.md, "ONNX + shared
cross-attention K/V"): the August spike (`export_whisper_onnx.py`,
`spike_onnx_scorer.py`) found CTranslate2's `align` recomputes the
cross-attention keys/values once per candidate, and shared them through
Optimum's with-past decoders, one candidate per call, with the 30 s encoder.
The October MLX scorer encodes only the window (positional embedding sliced,
no measurable accuracy cost in the follower: 0.252 vs 0.247), unlike
truncating a 30 s encoding's output, which the August spike found harmful.
This export does both: the window encoded as it is, and every span scored in
one batched call against keys/values computed once.

The span scorer (`follower.scorer`) asks Whisper's decoder how well each of
~160 candidate lyric spans explains the last 6 s of audio. On Apple silicon
MLX does it in ~0.46 s (small.en); on a CPU, CTranslate2's only teacher-forced
path (`align`) takes ~4.2 s. This builds the two graphs a scorer needs, for
any machine ONNX Runtime runs on (CPU everywhere; DirectML for Windows GPUs;
CUDA):

* `encoder.onnx`: log-mel [1, 80, frames] -> every decoder layer's
  cross-attention keys and values for the window, [layers, 2, T, d], for any
  `frames` up to 3000, so a 6 s window is encoded as 6 s, not padded to 30
  (the positional embedding is sliced, as MLX's scorer does). The keys and
  values are computed here once a window, not once per span or length group;
* `scorer.onnx`: (kv, tokens [B, L], rows, targets) -> the log probability
  of each scored next token: only the positions predicting a span's own
  tokens go through the vocabulary projection;
  It also outputs the alignment heads' cross-attention for the first
  sequence [heads, L, T]: run on the best span alone, that is its DTW
  placement (one decoder ships, not two).

Both are written from Whisper's weights with a plain forward pass (no
Hugging Face model code in the graph), checked against the reference model,
and the weights are OpenAI's (MIT). Torch is only needed here, never by the
app:

    uv run --no-project --with "torch==2.5.1" --with "transformers==4.46.3" \\
        --with "onnx==1.17.0" --with "onnxruntime==1.19.2" --with soundfile \\
        python scripts/export_whisper_scorer_onnx.py base.en small.en --out build/onnx

Writes build/onnx/<model>/{encoder,scorer}.onnx and tokens.json (the prompt
and the tokenizer's ids for the benchmark's spans, so ONNX Runtime needs no
tokenizer).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import torch
from torch import nn

ROOT = Path(__file__).resolve().parent.parent
#: meta.json "format": what the engine's OnnxSpanScorer must find (`FORMAT`).
FORMAT = 3


def _attention(q, k, v, n_heads: int, mask=None):
    """q [B, L, d], k/v [B or 1, S, d] -> [B, L, d]; k/v broadcast over B."""
    b, l, d = q.shape
    dh = d // n_heads
    q = q.view(b, l, n_heads, dh).transpose(1, 2)  # B H L dh
    k = k.view(k.shape[0], k.shape[1], n_heads, dh).transpose(1, 2)  # 1|B H S dh
    v = v.view(v.shape[0], v.shape[1], n_heads, dh).transpose(1, 2)
    w = (q @ k.transpose(-1, -2)) / math.sqrt(dh)  # B H L S (k broadcasts)
    if mask is not None:
        w = w + mask
    return (w.softmax(-1) @ v).transpose(1, 2).reshape(b, l, d)


def cross_kv(decoder, features):
    """Every decoder layer's cross-attention keys and values for the window,
    [layers, 2, T, d]: computed once, here, and read by every span (they were
    recomputed for each length group, ~20% of a scoring pass)."""
    return torch.stack(
        [torch.stack([lyr.encoder_attn.k_proj(features)[0], lyr.encoder_attn.v_proj(features)[0]])
         for lyr in decoder.layers]
    )


class Encoder(nn.Module):
    def __init__(self, hf, with_kv: bool = False):
        super().__init__()
        self.e = hf.model.encoder
        self.d = hf.model.decoder
        self.h = hf.config.encoder_attention_heads
        self.with_kv = with_kv

    def forward(self, mel):  # [1, 80, frames]
        e = self.e
        x = nn.functional.gelu(e.conv1(mel))
        x = nn.functional.gelu(e.conv2(x)).transpose(1, 2)  # [1, T, d]
        x = x + e.embed_positions.weight[: x.shape[1]]
        for lyr in e.layers:
            a = lyr.self_attn
            h = lyr.self_attn_layer_norm(x)
            x = x + a.out_proj(_attention(a.q_proj(h), a.k_proj(h), a.v_proj(h), self.h))
            h = lyr.final_layer_norm(x)
            x = x + lyr.fc2(nn.functional.gelu(lyr.fc1(h)))
        x = e.layer_norm(x)
        return cross_kv(self.d, x) if self.with_kv else x


class Scorer(nn.Module):
    def __init__(self, hf):
        super().__init__()
        self.d = hf.model.decoder
        self.h = hf.config.decoder_attention_heads
        self.heads = [tuple(x) for x in hf.generation_config.alignment_heads]

    def forward(self, kv, tokens, rows, targets):
        """kv [layers, 2, T, d] (`cross_kv`), tokens [B, L]; `rows` [N]: flat indexes (b*L + i)
        of the positions whose next token is scored, `targets` [N] those tokens.
        Only those positions go through the vocabulary projection (a quarter
        of the work if every position did, padding and prompt included).

        Also returns the alignment heads' cross-attention for the first
        sequence, [heads, L, T]: run on the best span alone it places it
        (Whisper's DTW timing), so no second copy of the decoder ships for
        that (format 3; format 2 had `align.onnx`)."""
        d = self.d
        L = tokens.shape[1]
        x = d.embed_tokens(tokens) + d.embed_positions.weight[:L]
        causal = torch.full((L, L), float("-inf")).triu(1)
        picked = []
        for li, lyr in enumerate(d.layers):
            a = lyr.self_attn
            h = lyr.self_attn_layer_norm(x)
            x = x + a.out_proj(_attention(a.q_proj(h), a.k_proj(h), a.v_proj(h), self.h, causal))
            c = lyr.encoder_attn
            h = lyr.encoder_attn_layer_norm(x)
            # the window's keys and values ([1, T, d]), computed once, for every span
            q = c.q_proj(h)
            mine = [hd for (ly, hd) in self.heads if ly == li]
            if mine:
                dh = q.shape[-1] // self.h
                q0 = q[:1].view(1, L, self.h, dh).transpose(1, 2)
                k0 = kv[li, 0][None].view(1, -1, self.h, dh).transpose(1, 2)
                probs = ((q0 @ k0.transpose(-1, -2)) / math.sqrt(dh)).softmax(-1)  # 1 H L T
                picked += [probs[0, hd] for hd in mine]
            x = x + c.out_proj(_attention(q, kv[li, 0][None], kv[li, 1][None], self.h))
            h = lyr.final_layer_norm(x)
            x = x + lyr.fc2(nn.functional.gelu(lyr.fc1(h)))
        h = d.layer_norm(x).reshape(-1, x.shape[-1]).index_select(0, rows)  # [N, d]
        # tied output embedding, read as it is stored (`weight.T` exported a
        # transposed copy of the vocabulary matrix: 106 MB in base.en)
        logp = nn.functional.linear(h, d.embed_tokens.weight).log_softmax(-1)
        return logp.gather(-1, targets[:, None])[:, 0], torch.stack(picked)  # [N], [heads, L, T]


def untie_transposed_embedding(path: Path) -> int:
    """Read the tied output projection from the embedding itself.

    The exporter constant-folds `linear(h, embedding)` into a MatMul with a
    stored transposed copy of the vocabulary matrix (106 MB in base.en). A
    Gemm with transB=1 reads the embedding as it is. Each copy is checked to
    be the embedding's exact transpose before it goes. Returns bytes saved."""
    import onnx
    from onnx import helper, numpy_helper

    m = onnx.load(str(path))
    inits = {i.name: i for i in m.graph.initializer}
    emb = next(n for n in inits if n.endswith("embed_tokens.weight"))
    e = numpy_helper.to_array(inits[emb])
    saved = 0
    for node in list(m.graph.node):
        if node.op_type != "MatMul" or node.input[1] not in inits:
            continue
        w = numpy_helper.to_array(inits[node.input[1]])
        if w.shape != e.T.shape or not np.array_equal(w, e.T):
            continue
        gemm = helper.make_node("Gemm", [node.input[0], emb], list(node.output),
                                name=node.name + "_tied", transB=1)
        m.graph.node.insert(list(m.graph.node).index(node), gemm)
        m.graph.node.remove(node)
        if not any(node.input[1] in n.input for n in m.graph.node):
            m.graph.initializer.remove(inits[node.input[1]])
            saved += w.nbytes
    onnx.checker.check_model(m)
    onnx.save(m, str(path))
    return saved


def scored_positions(tokens, lengths: list[int], n_prompt: int):
    """Flat indexes of the positions that predict a span's own tokens, and
    those tokens: position i predicts token i+1, from the prompt's last."""
    width = tokens.shape[1]
    rows, targets = [], []
    for b, n in enumerate(lengths):
        for i in range(n_prompt - 1, n - 1):
            rows.append(b * width + i)
            targets.append(int(tokens[b, i + 1]))
    return torch.tensor(rows, dtype=torch.int64), torch.tensor(targets, dtype=torch.int64)


def export(model: str, out: Path, spans: list[str]) -> None:
    from transformers import WhisperForConditionalGeneration, WhisperProcessor

    repo = f"openai/whisper-{model}"
    hf = WhisperForConditionalGeneration.from_pretrained(repo).eval()
    proc = WhisperProcessor.from_pretrained(repo)
    out.mkdir(parents=True, exist_ok=True)

    # A real 6 s window from the perf fixture, for the export and the checks.
    sys.path.insert(0, str(ROOT / "scripts"))
    import bench_whisper as bw

    audio = bw.windows(2)[1]
    mel30 = proc.feature_extractor(audio, sampling_rate=16000, return_tensors="pt").input_features
    mel6 = mel30[:, :, :600]

    enc, sc = Encoder(hf), Scorer(hf)
    tok = proc.tokenizer
    prompt = tok.convert_tokens_to_ids(["<|startoftranscript|>", "<|notimestamps|>"])
    span_ids = [tok.encode(" " + s, add_special_tokens=False) for s in spans]

    with torch.no_grad():
        # 1. the plain forward pass is the reference model's (30 s, as it requires)
        ref_feat = hf.model.encoder(mel30).last_hidden_state
        mine = enc(mel30)
        err = (ref_feat - mine).abs().max().item()
        assert err < 1e-3, f"encoder differs from the reference by {err}"
        ids = torch.tensor([prompt + span_ids[0]])
        ref_logits = hf(encoder_outputs=(ref_feat,), decoder_input_ids=ids).logits
        ref_lp = ref_logits[:, :-1].log_softmax(-1).gather(-1, ids[:, 1:, None])[..., 0][0]
        rows, targets = scored_positions(ids, [ids.shape[1]], 1)
        ref_kv = cross_kv(hf.model.decoder, ref_feat)
        err = (ref_lp - sc(ref_kv, ids, rows, targets)[0]).abs().max().item()
        assert err < 1e-3, f"scorer differs from the reference by {err}"
        ref_cross = hf(encoder_outputs=(ref_feat,), decoder_input_ids=ids, output_attentions=True).cross_attentions
        ref_heads = torch.stack([ref_cross[ly][0, hd] for ly, hd in sc.heads])
        err = (ref_heads - sc(ref_kv, ids, rows, targets)[1]).abs().max().item()
        assert err < 1e-3, f"alignment heads differ from the reference by {err}"
        print(f"{model}: forward pass matches the reference model", flush=True)

        enc_kv = Encoder(hf, with_kv=True)
        kv6 = enc_kv(mel6)
        batch = [prompt + s for s in span_ids[:4]]
        width = max(map(len, batch))
        toks = torch.tensor([b + [tok.eos_token_id] * (width - len(b)) for b in batch])
        rows, targets = scored_positions(toks, [len(b) for b in batch], len(prompt))

        torch.onnx.export(
            enc_kv, (mel6,), str(out / "encoder.onnx"), input_names=["mel"], output_names=["kv"],
            dynamic_axes={"mel": {2: "frames"}, "kv": {2: "positions"}}, opset_version=17,
        )
        torch.onnx.export(
            sc, (kv6, toks, rows, targets), str(out / "scorer.onnx"),
            input_names=["kv", "tokens", "rows", "targets"], output_names=["logp", "heads"],
            dynamic_axes={"kv": {2: "positions"}, "tokens": {0: "spans", 1: "length"},
                          "rows": {0: "scored"}, "targets": {0: "scored"}, "logp": {0: "scored"},
                          "heads": {1: "length", 2: "positions"}},
            opset_version=17,
        )
    saved = untie_transposed_embedding(out / "scorer.onnx")
    print(f"{model}: scorer reads the tied embedding directly ({saved / 2**20:.0f} MB saved)", flush=True)
    # What the engine needs at run time besides the graphs: the tokenizer (the
    # `tokenizers` library it already ships reads this file) and the prompt.
    from transformers import WhisperTokenizerFast

    fast = WhisperTokenizerFast.from_pretrained(repo)
    fast.backend_tokenizer.save(str(out / "tokenizer.json"))
    (out / "meta.json").write_text(json.dumps({
        "model": model, "prompt": prompt, "eot": tok.eos_token_id,
        "alignment_heads": [list(h) for h in sc.heads], "window_s": 6.0,
        # 3: the encoder outputs every layer's cross-attention K/V ("kv"); the
        # scorer also outputs the alignment heads (no align.onnx)
        "format": FORMAT,
    }))
    (out / "tokens.json").write_text(
        json.dumps({"prompt": prompt, "eot": tok.eos_token_id, "spans": spans, "span_ids": span_ids})
    )
    # 2. ONNX Runtime agrees with torch on the 6 s window
    import onnxruntime as ort

    e = ort.InferenceSession(str(out / "encoder.onnx"), providers=["CPUExecutionProvider"])
    s = ort.InferenceSession(str(out / "scorer.onnx"), providers=["CPUExecutionProvider"])
    kv = e.run(None, {"mel": mel6.numpy()})[0]
    lp = s.run(None, {"kv": kv, "tokens": toks.numpy(), "rows": rows.numpy(), "targets": targets.numpy()})[0]
    with torch.no_grad():
        ref = sc(kv6, toks, rows, targets)[0].numpy()
    err = float(np.abs(lp - ref).max())
    assert err < 1e-2, f"ONNX Runtime differs from torch by {err}"
    size = sum(p.stat().st_size for p in out.glob("*.onnx")) / 2**20
    print(f"{model}: exported to {out} ({size:.0f} MB), ONNX Runtime matches torch (max diff {err:.1e})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="+")
    ap.add_argument("--out", type=Path, default=ROOT / "build" / "onnx")
    ap.add_argument(
        "--int8",
        action="store_true",
        help="also write <model>-int8: the matrix multiplications quantised to int8 "
        "(faster on a CPU, but dynamic quantisation scales activations per batch, "
        "so a span's score depends on its batch-mates: for benchmarking, not the "
        "engine; the encoder's convolutions stay fp32, ORT's CPU has no int8 Conv)",
    )
    args = ap.parse_args()
    sys.path.insert(0, str(ROOT / "scripts"))
    import bench_whisper as bw

    spans = bw.spans()
    for m in args.models:
        export(m, args.out / m, spans)
        if args.int8:
            quantize(args.out / m, args.out / f"{m}-int8")
    return 0


def quantize(src: Path, dst: Path) -> None:
    import shutil

    from onnxruntime.quantization import QuantType, quantize_dynamic

    dst.mkdir(parents=True, exist_ok=True)
    for name in ("encoder", "scorer"):
        quantize_dynamic(
            str(src / f"{name}.onnx"), str(dst / f"{name}.onnx"),
            weight_type=QuantType.QInt8, op_types_to_quantize=["MatMul", "Gemm"],
        )
    # placement reads attention patterns: kept full precision, it runs once a step
    for name in ("tokens.json", "tokenizer.json", "meta.json"):
        shutil.copy(src / name, dst / name)
    size = sum(p.stat().st_size for p in dst.glob("*.onnx")) / 2**20
    print(f"quantised to {dst} ({size:.0f} MB)", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
