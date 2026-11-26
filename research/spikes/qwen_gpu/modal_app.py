"""Prototype: the Mac's Qwen3-ASR streaming engine on a rented NVIDIA GPU (Modal).

docs/roadmap/HOSTED_SPEECH.md, candidate A. The app's own
`asr.qwen_backend.QwenStreamer` (Qwen3-ASR 0.6B, streamed with its encoder
blocks and text prefix carried forward, Qwen3-ForcedAligner word times) runs
on MLX's CUDA backend behind a websocket. The app streams audio in and gets
the same words back that the Mac's engine would emit
(`asr.remote_backend.RemoteStreamingBackend`).

    modal deploy research/spikes/qwen_gpu/modal_app.py     # from the repo root
    modal app stop church-slides-speech                     # stop paying

Wire protocol (version 0):
  client -> server  text  {"type": "start", "t0": <audio seconds of the first sample>}
                    bytes 16 kHz mono int16 little-endian PCM, contiguous
                    text  {"type": "end"}   finish: flush the last words
  server -> client  text  {"type": "ready", "load_s": ..., "gpu": ...}
                    text  {"type": "words", "final": bool, "segment_id": int,
                           "ends_segment": bool, "pass_s": float,
                           "words": [[text, t_start, t_end, confidence], ...]}

Auth: `?token=<payload>.<hmac>` signed with the SPEECH_TOKEN_SECRET Modal
secret (HMAC-SHA256 over the payload, `{"exp": <unix s>}` base64url). Our
server would mint these as it mints AI keys; for the prototype,
`mint_token.py` does.

Production would live in the private server repo; this is a measurement.
"""

from __future__ import annotations

import os
from pathlib import Path

import modal

HERE = Path(__file__).resolve().parent
# in the container this file sits at /root/modal_app.py: only paths built locally matter
SIDECAR = HERE.parents[2] / "sidecar" / "auto_presenter" if len(HERE.parents) > 2 else HERE
GPU = os.environ.get("SPEECH_GPU", "L4")

QWEN = "Qwen/Qwen3-ASR-0.6B"
ALIGNER = "Qwen/Qwen3-ForcedAligner-0.6B"


def _download() -> None:
    from huggingface_hub import snapshot_download

    for repo in (QWEN, ALIGNER):
        snapshot_download(repo)


image = (
    # MLX's CUDA backend compiles kernels at run time and needs the CUDA headers
    modal.Image.from_registry("nvidia/cuda:12.8.1-devel-ubuntu24.04", add_python="3.12")
    .env({"CUDA_HOME": "/usr/local/cuda", "CUDA_PATH": "/usr/local/cuda"})
    .pip_install(
        "mlx[cuda]==0.32.4",
        "mlx-qwen3-asr==0.4.4",
        "numpy",
        "pydantic>=2",
        "huggingface_hub",
        "regex",
        "fastapi[standard]",
    )
    .run_function(_download)  # weights baked into the image: no download at start
    .env({"HF_HUB_OFFLINE": "1", "AUTO_PRESENTER_QWEN_NO_PROBS": os.environ.get("NO_PROBS", "0")})
    .add_local_dir(SIDECAR, remote_path="/root/auto_presenter")
    .add_local_file(HERE / "speech_server.py", remote_path="/root/spike/speech_server.py")
)

app = modal.App("church-slides-speech", image=image)


@app.cls(
    gpu=GPU,
    scaledown_window=20 * 60,  # a service's quiet stretches never cold-start it
    timeout=4 * 60 * 60,  # one websocket can last a whole service
    secrets=[modal.Secret.from_name("church-slides-speech")],
    region="us",
)
@modal.concurrent(max_inputs=16)
class Speech:
    @modal.enter()
    def load(self) -> None:
        import time

        t = time.monotonic()
        from auto_presenter.asr.qwen_backend import QwenStreamingBackend

        self.backend = QwenStreamingBackend(None)
        self.backend.warm()  # loads both models and runs one step (compiles kernels)
        self.load_s = time.monotonic() - t

    @modal.asgi_app()
    def web(self):
        import sys

        sys.path.insert(0, "/root/spike")
        from speech_server import make_api

        return make_api(
            self.backend, secret=os.environ["SPEECH_TOKEN_SECRET"], gpu=GPU, load_s=self.load_s
        )


@app.function(gpu=GPU, timeout=900)
def probe() -> str:
    """`modal run research/spikes/qwen_gpu/modal_app.py::probe`: does MLX run on
    this GPU, and how long do loading and one step take?"""
    import time
    import traceback

    out = []
    try:
        t = time.monotonic()
        import mlx.core as mx

        out.append(f"mlx {mx.__version__} device {mx.default_device()} import {time.monotonic() - t:.1f}s")
        a = mx.ones((1024, 1024))
        mx.eval(a @ a)
        out.append("matmul ok")
        t = time.monotonic()
        from auto_presenter.asr.qwen_backend import QwenStreamingBackend

        b = QwenStreamingBackend(None)
        b.load()
        out.append(f"load {time.monotonic() - t:.1f}s")
        t = time.monotonic()
        b.warm()
        out.append(f"warm {time.monotonic() - t:.1f}s")
    except Exception:
        out.append(traceback.format_exc())
    return "\n".join(out)


@app.local_entrypoint()
def main() -> None:
    print(probe.remote())
