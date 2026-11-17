"""Install the fast channel: its model, and the runtime that reads it.

The fast channel is a low-latency second listener. It is off by default and
lives behind a toggle in the debug panel, so neither the model nor the runtime
ships with the app — this fetches both on demand.

    uv run --project sidecar python research/spikes/fast_channel/setup_fast_channel.py

It is idempotent: an existing model is left alone unless `--force` is passed.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "sidecar"))

from auto_presenter.config import app_data_dir  # noqa: E402

#: 20M parameters, ~142 MB unpacked, Apache-2.0. Chosen for latency rather than
#: accuracy: it delivers a word about 0.4 s after it is sung against Whisper's
#: 2-3 s, and transcribes sung audio far worse. See docs/research/FAST_CHANNEL.md.
MODEL_NAME = "sherpa-onnx-streaming-zipformer-en-20M-2023-02-17"
MODEL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
    f"{MODEL_NAME}.tar.bz2"
)


def model_root() -> Path:
    """Beside the library and the recordings, so it survives a rebuild."""
    return app_data_dir() / "models" / "fast-channel"


def install_runtime() -> bool:
    """Install `sherpa-onnx` into the sidecar environment.

    Deliberately `uv pip install` rather than a project dependency. Resolving it
    through the lock selects a macOS wheel whose `lib/` is missing the bundled
    `libonnxruntime.dylib`, and the import then dies on a dlopen of
    `@rpath/libonnxruntime.dylib`. Installing directly picks the complete
    platform wheel.
    """
    try:
        import sherpa_onnx  # noqa: F401
        print("runtime: sherpa-onnx already installed")
        return True
    except ImportError:
        pass
    print("runtime: installing sherpa-onnx ...")
    result = subprocess.run(
        ["uv", "pip", "install", "sherpa-onnx>=1.13"],
        cwd=REPO_ROOT / "sidecar",
    )
    if result.returncode != 0:
        print("runtime: install failed", file=sys.stderr)
        return False
    return True


def install_model(force: bool = False) -> Path | None:
    target = model_root() / MODEL_NAME
    if target.exists() and not force:
        print(f"model: already present at {target}")
        return target
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    archive = target.parent / f"{MODEL_NAME}.tar.bz2"
    print(f"model: downloading {MODEL_URL}")
    try:
        with urllib.request.urlopen(MODEL_URL, timeout=300) as response, \
                open(archive, "wb") as out:
            shutil.copyfileobj(response, out)
    except OSError as exc:
        print(f"model: download failed: {exc}", file=sys.stderr)
        return None
    print(f"model: extracting into {target.parent}")
    try:
        with tarfile.open(archive, "r:bz2") as tar:
            tar.extractall(target.parent, filter="data")
    finally:
        archive.unlink(missing_ok=True)
    return target if target.exists() else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true",
                    help="re-download even if the model is already present")
    args = ap.parse_args()

    ok = install_runtime()
    target = install_model(force=args.force)
    if not ok or target is None:
        print("\nsetup incomplete", file=sys.stderr)
        return 1
    print(f"\nfast channel ready.\n  model:   {target}")
    print("  enable:  debug panel -> Fast channel -> 'Let it drive slide changes'")
    print("\nNothing else to configure — the sidecar finds the model here on its")
    print("own. AUTO_PRESENTER_FAST_MODEL still overrides it for experiments.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
