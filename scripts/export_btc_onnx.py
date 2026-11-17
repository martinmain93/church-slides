#!/usr/bin/env python3
"""Export BTC (Park et al., ISMIR 2019) to ONNX for live chord recognition.

BTC is 3 M parameters and MIT-licensed, and its pretrained weights ship in the
upstream repository. Measured against chord charts on the approved goldens it
is roughly twice as accurate as the numpy template matcher the sidecar falls
back to — see Plan/CHORD_DOWNBEAT.md.

PyTorch is needed only for the export and must never reach the shipped
sidecar; the runtime needs onnxruntime and numpy alone.

    git clone --depth 1 https://github.com/jayg996/BTC-ISMIR19.git /tmp/btc
    uv venv /tmp/bx --python 3.11
    VIRTUAL_ENV=/tmp/bx uv pip install torch onnx onnxruntime pyyaml
    /tmp/bx/bin/python scripts/export_btc_onnx.py /tmp/btc <dest-dir>

The clone is a 2019 codebase and does not import on current numpy/PyYAML. This
script applies the three substitutions in memory rather than asking you to edit
the checkout — `np.float`/`np.int` were removed in numpy 2, and `yaml.load`
requires a Loader. (Note that BSD `sed` on macOS has no `\\b`, so the obvious
word-boundary sed silently does nothing; that is why this is done in Python.)

What is exported is the bidirectional self-attention stack plus the output
projection, emitting **logits** rather than argmax indices, so the runtime can
report the top three candidates the panel shows.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

#: BTC's fixed analysis block: 108 frames of 144 CQT bins.
TIMESTEP = 108
FEATURE_SIZE = 144


def _patch_repo_in_memory(repo: Path) -> None:
    """Make the 2019 sources importable without modifying the checkout."""
    subs = [
        (r"\bnp\.float\b", "np.float64"),
        (r"\bnp\.int\b", "np.int64"),
        (r"\bnp\.bool\b", "bool"),
        (r"\bnp\.object\b", "object"),
        (r"yaml\.load\(f\)", "yaml.safe_load(f)"),
    ]
    import importlib.abc
    import importlib.machinery

    class Patching(importlib.abc.SourceLoader):
        def __init__(self, path: str) -> None:
            self._path = path

        def get_filename(self, fullname: str = "") -> str:
            return self._path

        def get_data(self, path) -> bytes:
            text = Path(path).read_text()
            for a, b in subs:
                text = re.sub(a, b, text)
            return text.encode()

    class Finder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            rel = fullname.replace(".", "/")
            for cand in (repo / f"{rel}.py", repo / rel / "__init__.py"):
                if cand.exists():
                    return importlib.machinery.ModuleSpec(
                        fullname, Patching(str(cand)),
                        is_package=cand.name == "__init__.py",
                    )
            return None

    sys.meta_path.insert(0, Finder())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", type=Path, help="BTC-ISMIR19 checkout")
    ap.add_argument("dst", type=Path, help="where to write btc_chords.onnx")
    ap.add_argument("--large-voca", action="store_true",
                    help="170-chord vocabulary instead of 25 (maj/min)")
    args = ap.parse_args()

    repo = args.repo.resolve()
    if not (repo / "btc_model.py").exists():
        print(f"not a BTC checkout: {repo}", file=sys.stderr)
        return 1
    sys.path.insert(0, str(repo))
    _patch_repo_in_memory(repo)

    import torch
    from btc_model import BTC_model, HParams

    config = HParams.load(str(repo / "run_config.yaml"))
    if args.large_voca:
        config.feature["large_voca"] = True
        config.model["num_chords"] = 170
        weights = repo / "test" / "btc_model_large_voca.pt"
    else:
        weights = repo / "test" / "btc_model.pt"
    if not weights.exists():
        print(f"missing weights: {weights}", file=sys.stderr)
        return 1

    model = BTC_model(config=config.model)
    ck = torch.load(weights, map_location="cpu", weights_only=False)
    model.load_state_dict(ck["model"])
    model.eval()

    class Export(torch.nn.Module):
        """Attention stack + projection, emitting logits.

        The upstream output layer returns argmax indices, which would throw
        away exactly what the debug panel is for: how close the runners-up are.
        """

        def __init__(self, inner):
            super().__init__()
            self.attn = inner.self_attn_layers
            self.proj = inner.output_layer.output_projection

        def forward(self, x):
            hidden, _ = self.attn(x)
            return self.proj(hidden)

    wrapper = Export(model).eval()
    dummy = torch.randn(1, TIMESTEP, FEATURE_SIZE)
    args.dst.mkdir(parents=True, exist_ok=True)
    onnx_path = args.dst / "btc_chords.onnx"
    torch.onnx.export(
        wrapper,
        (dummy,),
        str(onnx_path),
        input_names=["features"],
        output_names=["logits"],
        opset_version=17,
        dynamo=False,
    )

    # The normalisation statistics travel with the weights upstream; without
    # them the exported graph is fed the wrong distribution and quietly
    # produces confident nonsense.
    meta = {
        "timestep": TIMESTEP,
        "feature_size": FEATURE_SIZE,
        "num_chords": int(config.model["num_chords"]),
        "sample_rate": int(config.mp3["song_hz"]),
        "hop_length": int(config.feature["hop_length"]),
        "bins_per_octave": int(config.feature["bins_per_octave"]),
        "n_bins": int(config.feature["n_bins"]),
        "mean": float(ck["mean"]),
        "std": float(ck["std"]),
        "source": "BTC-ISMIR19 (Park et al. 2019), MIT",
    }
    (args.dst / "btc_chords.json").write_text(json.dumps(meta, indent=2))

    # Numerical agreement with PyTorch, on the spot. An export that silently
    # differs is worse than one that fails.
    import numpy as np
    import onnxruntime as ort

    sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    rng = np.random.default_rng(0)
    worst = 0.0
    agree = []
    for _ in range(5):
        x = rng.standard_normal((1, TIMESTEP, FEATURE_SIZE)).astype(np.float32)
        with torch.no_grad():
            ref = wrapper(torch.from_numpy(x)).numpy()
        got = sess.run(["logits"], {"features": x})[0]
        worst = max(worst, float(np.abs(ref - got).max()))
        agree.append(float((ref.argmax(-1) == got.argmax(-1)).mean()))
    size_mb = onnx_path.stat().st_size / 1e6
    print(f"wrote {onnx_path} ({size_mb:.1f} MB)")
    print(f"max |logit difference| vs PyTorch: {worst:.3e}")
    print(f"argmax agreement: {100 * float(np.mean(agree)):.1f}%")
    if worst > 1e-3 or float(np.mean(agree)) < 1.0:
        print("EXPORT DOES NOT MATCH PYTORCH", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
