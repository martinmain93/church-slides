"""Quantize decoder_extend.onnx's weights (MatMulNBits, ONNX Runtime >= 1.23).

int4 (RTN, block 64) cost the processor backend 0.381 -> 0.466 word error on
the six public songs; full precision matches MLX exactly
(docs/research/QWEN3_ASR_CPU.md). This makes the variants compared there.

    python quantize_extend.py SRC_ONNX OUT_ONNX --bits 8 [--keep-head] [--block 64]

`--keep-head` leaves the vocabulary projection (`/lm_head/MatMul`) at full
precision. The tool's own quantize_nbits.py can't be used for this: with an
algorithm config it ignores `--bits`, and with a different output folder it
copies its input over what it just wrote.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    import onnx
    from onnxruntime.quantization.matmul_nbits_quantizer import MatMulNBitsQuantizer

    ap = argparse.ArgumentParser()
    ap.add_argument("src", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--bits", type=int, default=4, choices=(4, 8))
    ap.add_argument("--block", type=int, default=64)
    ap.add_argument("--accuracy-level", type=int, default=None)
    ap.add_argument("--keep-head", action="store_true")
    args = ap.parse_args()

    model = onnx.load(str(args.src))
    q = MatMulNBitsQuantizer(
        model, bits=args.bits, block_size=args.block, is_symmetric=False,
        accuracy_level=args.accuracy_level,
        nodes_to_exclude=["/lm_head/MatMul"] if args.keep_head else None,
    )
    q.process()
    m = q.model.model
    if not any(o.domain == "com.microsoft" for o in m.opset_import):
        o = m.opset_import.add()
        o.domain, o.version = "com.microsoft", 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    q.model.save_model_to_file(str(args.out), use_external_data_format=True)
    print("saved", args.out, sum(f.stat().st_size for f in args.out.parent.glob(args.out.name + "*")) >> 20, "MB")


if __name__ == "__main__":
    main()
