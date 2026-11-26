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
WARM_AUDIO = Path(os.environ.get(
    "WARM_AUDIO", Path.home() / "Development/auto-presenter-research/work/hosted_speech/warm_hymn.npy"
))

QWEN = "Qwen/Qwen3-ASR-0.6B"
ALIGNER = "Qwen/Qwen3-ForcedAligner-0.6B"


def _download() -> None:
    from huggingface_hub import snapshot_download

    for repo in (QWEN, ALIGNER):
        snapshot_download(repo)


def _compile_kernels() -> None:
    """Stream 40 s of a public-domain hymn through the engine at build time, so
    every kernel a service needs is compiled before any church connects."""
    import time

    import numpy as np

    from auto_presenter.asr.qwen_backend import QwenStreamingBackend
    from auto_presenter.audio.types import AudioFrame

    t = time.monotonic()
    pcm = np.load("/root/spike/warm_hymn.npy").astype(np.float32) / 32768.0
    b = QwenStreamingBackend(None)
    b.load()
    frames = [
        AudioFrame(samples=pcm[i : i + 1600], t_start=i / 16000, t_end=(i + 1600) / 16000)
        for i in range(0, len(pcm), 1600)
    ]
    words = [w.text for e in b.streamer().transcribe(frames) for w in e.words if e.is_final]
    print(f"compiled kernels in {time.monotonic() - t:.1f}s; heard {len(words)} words")


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
    # the image's own CUDA libraries are older than the ones MLX's wheels ship:
    # load MLX's first (keep the image only for its headers)
    .env({"LD_LIBRARY_PATH": ":".join(
        f"/usr/local/lib/python3.12/site-packages/nvidia/{d}/lib"
        for d in ("cublas", "cusolver", "cusparse", "cudnn", "cuda_runtime", "cuda_nvrtc",
                  "nvjitlink", "cufft", "curand", "nccl", "cuda_cupti")
    )})
    .run_function(_download)  # weights baked into the image: no download at start
    .env({
        "HF_HUB_OFFLINE": "1",
        "AUTO_PRESENTER_QWEN_NO_PROBS": os.environ.get("NO_PROBS", "0"),
        # MLX's compiled kernels (PTX) and the driver's (SASS) live in the image:
        # compiled once at build, on the same GPU type (`_compile_kernels`)
        "MLX_PTX_CACHE_DIR": "/root/kernels/mlx",
        "CUDA_CACHE_PATH": "/root/kernels/nv",
        "CUDA_CACHE_MAXSIZE": "4294967296",
    })
    .add_local_dir(SIDECAR, remote_path="/root/auto_presenter", copy=True)
    # 40 s of a LibriVox (public-domain) hymn, kept outside the repo
    .add_local_file(WARM_AUDIO, remote_path="/root/spike/warm_hymn.npy", copy=True)
    .run_function(_compile_kernels, gpu=GPU)
    .add_local_file(HERE / "speech_server.py", remote_path="/root/spike/speech_server.py")
)

app = modal.App("church-slides-speech", image=image)


@app.cls(
    # L4 first; when Modal has none free (2026-11-26: queued minutes for one),
    # take the next type rather than wait. PTX kernels carry across types.
    gpu=[GPU, "A10", "L40S"],
    scaledown_window=20 * 60,  # a service's quiet stretches never cold-start it
    timeout=4 * 60 * 60,  # one websocket can last a whole service
    secrets=[modal.Secret.from_name("church-slides-speech")],
    # region="us" left the container waiting for an L4 (2026-11-26); unpinned schedules at once
)
# one GPU step at a time per container (GPU_LOCK): 4 streams kept final words
# at 2.5 s, 8 made them 7.5 s (2026-11-26), so 4 per container, then scale out
@modal.concurrent(max_inputs=4)
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
def main(which: str = "probe") -> None:
    print({"probe": probe, "cache": probe_cache, "gather": probe_gather}[which].remote())


@app.function(gpu=GPU, timeout=900)
def probe_cache() -> str:
    """Where (if anywhere) MLX keeps compiled CUDA kernels, and what warm costs."""
    import glob
    import os
    import subprocess
    import time

    import mlx.core as mx

    lib = glob.glob("/usr/local/lib/python3.12/site-packages/mlx/lib/libmlx*.so*")
    out = [f"libs {lib}"]
    for f in lib:
        s = subprocess.run(f"strings {f} | grep -iE 'MLX_[A-Z_]*(CACHE|JIT|PTX)|cache_dir|\\.cache/mlx|XDG_CACHE' | sort -u | head -20",
                           shell=True, capture_output=True, text=True).stdout
        out.append(s)
    t = time.monotonic()
    from auto_presenter.asr.qwen_backend import QwenStreamingBackend

    b = QwenStreamingBackend(None)
    b.warm()
    out.append(f"warm {time.monotonic() - t:.1f}s")
    s = subprocess.run("find / -xdev -newer /tmp -type f \\( -name '*.ptx' -o -name '*.cubin' -o -path '*mlx*' \\) 2>/dev/null | grep -v site-packages | head -20; du -sh ~/.cache 2>/dev/null; ls -la ~/.cache 2>/dev/null",
                       shell=True, capture_output=True, text=True).stdout
    out.append(s)
    return "\n".join(out)


@app.function(gpu=GPU, timeout=900)
def probe_gather() -> str:
    """Which steps fail with "Failed to load compiled ... kernel" (2026-11-26
    gate), and does it depend on the thread or the audio?"""
    import glob
    import logging
    import os
    import threading

    import numpy as np

    from auto_presenter.asr.qwen_backend import QwenStreamingBackend
    from auto_presenter.audio.types import AudioFrame

    fails: list[str] = []

    class H(logging.Handler):
        def emit(self, r):
            if "step failed" in r.getMessage():
                fails.append(r.getMessage())

    logging.getLogger().addHandler(H())
    out = []
    cache = sorted(glob.glob("/root/kernels/mlx/**/*", recursive=True))
    g = [c for c in cache if "gather" in c]
    out.append(f"cache files {len(cache)}; gather {len(g)}: {[ (os.path.basename(c), os.path.getsize(c)) for c in g][:12]}")
    out.append(f"cache writable {os.access('/root/kernels/mlx', os.W_OK)}")
    b = QwenStreamingBackend(None)
    b.load()
    hymn = np.load("/root/spike/warm_hymn.npy").astype(np.float32) / 32768.0
    rng = np.random.default_rng(0)
    noisy = np.concatenate([hymn * 0.5 + rng.normal(0, 0.02, len(hymn)).astype(np.float32)] * 3)

    def run(pcm, label):
        fails.clear()
        fr = [AudioFrame(samples=pcm[i:i + 1600], t_start=i / 16000, t_end=(i + 1600) / 16000)
              for i in range(0, len(pcm), 1600)]
        n = sum(len(e.words) for e in b.streamer().transcribe(fr) if e.is_final)
        out.append(f"{label}: {n} final words, {len(fails)} failed steps {fails[:1]}")

    run(hymn, "hymn main thread")
    t = threading.Thread(target=run, args=(hymn, "hymn new thread")); t.start(); t.join()
    t = threading.Thread(target=run, args=(noisy, "120 s noisy new thread")); t.start(); t.join()
    new = sorted(set(glob.glob("/root/kernels/mlx/**/*", recursive=True)) - set(cache))
    out.append(f"new cache files {len(new)}: {[os.path.basename(c) for c in new][:10]}")
    return "\n".join(out)
