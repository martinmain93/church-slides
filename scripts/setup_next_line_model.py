"""Install the next-line check's phone model (`follower.next_line`).

The model is a 4.4 M-parameter GRU over Whisper small.en's encoder output,
trained with CTC on public music only (Free Music Archive songs with Whisper
pseudo-labels, run on Kaggle: never church recordings). MIT licence.

By default this downloads the published model (a float16 numpy archive the
engine reads, 8.7 MB, pinned by SHA-256) and puts it where the engine looks in
development, the app data folder's `models/next_line` (or
`AUTO_PRESENTER_NEXT_LINE_MODEL`). `scripts/build_sidecar.py` bundles it from
there into release builds.

    uv run --project sidecar python scripts/setup_next_line_model.py [--force]

With `--checkpoint PT` it converts a training checkpoint instead (for a newly
trained head). PyTorch is needed only for that, in a throwaway `uv run --with
torch` environment, so it never reaches the shipped engine. Publishing a new
model means a new release asset and new URL and SHA-256 below, in one commit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "sidecar"))

from auto_presenter.follower.next_line import (  # noqa: E402
    ENCODER_REPO,
    MODEL_FILE,
    PHONES,
    PhoneHead,
    model_dir,
)

#: The published model (MIT; LICENSE.txt and NOTICE.txt inside the zip).
MODEL_URL = (
    "https://github.com/martinmain93/church-slides-releases/releases/download/"
    "models-next-line-ngru512x2/church-slides-next-line-ngru512x2.zip"
)
MODEL_SHA256 = "84be6e7fd7e1f1059e9afa97690149a4b217894c2c1b2b3bdf6e9740b17c9e20"

CONVERT = """
import sys, numpy as np, torch
sd = torch.load(sys.argv[1], map_location="cpu")
np.savez(sys.argv[2], **{k: v.numpy().astype(np.float16) for k, v in sd.items()})
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkpoint", type=Path, help="convert this training checkpoint instead")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    out = model_dir()
    target = out / MODEL_FILE
    if target.is_file() and not a.force:
        print(f"next-line model already at {target} (--force to replace)")
        return 0
    if a.checkpoint is None:
        return download(out)
    if not a.checkpoint.is_file():
        print(f"checkpoint not found: {a.checkpoint}", file=sys.stderr)
        return 1
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        npz = Path(tmp) / MODEL_FILE
        subprocess.run(
            ["uv", "run", "-q", "--no-project", "--with", "torch", "--with", "numpy",
             "python", "-c", CONVERT, str(a.checkpoint), str(npz)],
            check=True,
        )  # fmt: skip
        head = PhoneHead.load(npz)  # reads back, or fails here rather than in a service
        if head.out_w.shape[1] != len(PHONES) + 1:
            print(f"{a.checkpoint} has {head.out_w.shape[1]} outputs, not {len(PHONES) + 1}")
            return 1
        target.write_bytes(npz.read_bytes())
    meta = {
        "source": a.checkpoint.name,
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "encoder": ENCODER_REPO,
        "phones": ["<blank>", *PHONES],
        "trained_on": "Free Music Archive songs (public, openly licensed), Whisper pseudo-labels",
    }
    (out / "next_line_head.json").write_text(json.dumps(meta, indent=1))
    print(f"next-line model installed: {target} ({target.stat().st_size / 1e6:.1f} MB)")
    return 0


def download(out: Path) -> int:
    """Fetch the published model, check its SHA-256, and unpack it into `out`."""
    with tempfile.TemporaryDirectory() as tmp:
        z = Path(tmp) / "model.zip"
        print(f"downloading {MODEL_URL}")
        urllib.request.urlretrieve(MODEL_URL, z)
        got = hashlib.sha256(z.read_bytes()).hexdigest()
        if got != MODEL_SHA256:
            print(f"checksum mismatch: got {got}, expected {MODEL_SHA256}", file=sys.stderr)
            return 1
        with zipfile.ZipFile(z) as zf:
            zf.extractall(Path(tmp) / "x")
        npz = Path(tmp) / "x" / MODEL_FILE
        head = PhoneHead.load(npz)  # reads back, or fails here rather than in a service
        if head.out_w.shape[1] != len(PHONES) + 1:
            print(f"downloaded model has {head.out_w.shape[1]} outputs, not {len(PHONES) + 1}")
            return 1
        out.mkdir(parents=True, exist_ok=True)
        for name in (MODEL_FILE, "next_line_head.json"):
            (out / name).write_bytes((Path(tmp) / "x" / name).read_bytes())
    target = out / MODEL_FILE
    print(f"next-line model installed: {target} ({target.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
