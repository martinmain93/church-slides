"""Install the learned chord recogniser: fetch BTC, export it, put it in place.

Chord detection ships with a numpy template matcher that works everywhere and
is about half as accurate as BTC (Park et al., ISMIR 2019 — 3 M parameters,
MIT, pretrained weights in the upstream repo). This installs the better one.

    uv run --project sidecar python scripts/setup_chord_model.py

PyTorch is needed only for the export and is installed into a throwaway venv
that is removed afterwards, so it never reaches the shipped sidecar. What ends
up installed is a 13 MB ONNX graph and a small JSON of the normalisation
statistics — about 60 s of work, most of it the torch download.

Idempotent: an existing model is left alone unless `--force` is passed.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "sidecar"))

from auto_presenter.music.btc import default_model_dir  # noqa: E402

BTC_REPO = "https://github.com/jayg996/BTC-ISMIR19.git"


def run(cmd: list[str], **kw) -> None:
    print("  $", " ".join(str(c) for c in cmd))
    subprocess.run(cmd, check=True, **kw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--keep-temp", action="store_true",
                    help="leave the export venv and checkout in place")
    args = ap.parse_args()

    dest = default_model_dir()
    if (dest / "btc_chords.onnx").exists() and not args.force:
        print(f"already installed: {dest}")
        print("pass --force to rebuild")
        return 0

    if shutil.which("uv") is None:
        print("uv is required to build the export environment", file=sys.stderr)
        return 1
    if shutil.which("git") is None:
        print("git is required to fetch the BTC repository", file=sys.stderr)
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="btc-export-"))
    try:
        repo = tmp / "btc"
        venv = tmp / "venv"
        print(f"fetching {BTC_REPO}")
        run(["git", "clone", "--depth", "1", BTC_REPO, str(repo)])
        print("building the export environment (this is the slow part)")
        run(["uv", "venv", str(venv), "--python", "3.11"])
        # uv puts a Windows venv's Python in Scripts\python.exe
        env_python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        run(
            ["uv", "pip", "install", "--python", str(env_python),
             "torch", "onnx", "onnxruntime", "pyyaml", "numpy"],
        )
        print("exporting")
        dest.mkdir(parents=True, exist_ok=True)
        run([str(env_python), str(REPO_ROOT / "scripts" / "export_btc_onnx.py"),
             str(repo), str(dest)])
    except subprocess.CalledProcessError as exc:
        print(f"\nsetup failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if not args.keep_temp:
            shutil.rmtree(tmp, ignore_errors=True)

    print()
    print(f"installed to {dest}")
    print("Turn on Chords in the debug panel's Music tab; it will say `btc`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
