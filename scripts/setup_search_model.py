"""Install the library search model: e5-small-v2, int8 ONNX, 34 MB.

    uv run --project sidecar python scripts/setup_search_model.py

Downloads a pinned revision from Hugging Face, checks it against the hashes
below, and puts `model.onnx` + `tokenizer.json` where the engine looks in
development (`<app data>/models/search`). Release builds bundle the same
files into the frozen engine (scripts/build_sidecar.py).

Idempotent: an installed model with the right hashes is left alone.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "sidecar"))

from auto_presenter.library_search.embedder import default_model_dir  # noqa: E402

REPO = "Xenova/e5-small-v2"
REVISION = "02af79985278377e65c724a76275707cb0333c70"
#: source path in the repo -> (installed name, sha256)
FILES = {
    "onnx/model_quantized.onnx": (
        "model.onnx",
        "7d9092cb25f2bd1c023b7e8d2aa459044a02030ac880e5a59fdaf27af69f1ded",
    ),
    "tokenizer.json": (
        "tokenizer.json",
        "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66",
    ),
}
LICENSE = REPO_ROOT / "sidecar" / "packaging" / "model_licenses" / "e5-small-v2-LICENSE.txt"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def installed(dest: Path) -> bool:
    return all(
        (dest / name).is_file() and sha256(dest / name) == digest for name, digest in FILES.values()
    )


def install(dest: Path, force: bool = False) -> Path:
    """Put the pinned, hash-checked model and its licence in `dest`."""
    if installed(dest) and not force:
        return dest
    from huggingface_hub import hf_hub_download

    dest.mkdir(parents=True, exist_ok=True)
    for src, (name, digest) in FILES.items():
        path = Path(hf_hub_download(REPO, src, revision=REVISION))
        got = sha256(path)
        if got != digest:
            raise SystemExit(f"{src}: sha256 {got} != expected {digest}")
        shutil.copy2(path, dest / name)
    shutil.copy2(LICENSE, dest / "LICENSE.txt")
    return dest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", type=Path, help="install here instead of the app data folder")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    dest = args.dest or default_model_dir()
    if installed(dest) and not args.force:
        print(f"already installed: {dest}")
        return 0
    install(dest, force=True)
    size = sum(p.stat().st_size for p in dest.iterdir()) / 1e6
    print(f"installed search model in {dest} ({size:.0f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
