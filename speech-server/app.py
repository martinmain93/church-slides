"""The speech server on Modal: the Mac's streaming Qwen3-ASR engine on an
NVIDIA GPU, behind the websocket in `server.py`.

Deploy with `scripts/deploy_speech.sh staging|production`, not by hand: it
records the app commit the image was built from (`/health` → `build`).

The engine is the app's own code (`sidecar/auto_presenter`, copied in at
build), so a deploy is tied to one commit of this repo. Models are pinned by
revision and the next-line phone model by SHA-256.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import modal

HERE = Path(__file__).resolve().parent
IN_CONTAINER = not (HERE.parent / "sidecar").exists()

#: Which Modal environment this deploy is for (set by deploy_speech.sh).
ENV = os.environ.get("SPEECH_ENV", "staging")
#: `git describe` of the commit the image was built from.
BUILD = os.environ.get("SPEECH_BUILD", "dev")
#: Containers at most: each takes 4 streams (below).
MAX_CONTAINERS = {"staging": 2, "production": 10}.get(ENV, 2)

QWEN = ("Qwen/Qwen3-ASR-0.6B", "5eb144179a02acc5e5ba31e748d22b0cf3e303b0")
ALIGNER = ("Qwen/Qwen3-ForcedAligner-0.6B", "c7cbfc2048c462b0d63a45797104fc9db3ad62b7")


def _published_phone_model() -> tuple[str, str]:
    """(url, sha256) from scripts/setup_next_line_model.py, read as text: importing
    it would load the engine, which the deploy tool's Python doesn't have."""
    import ast

    src = (HERE.parent / "scripts" / "setup_next_line_model.py").read_text()
    found = {}
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in ("QWEN_MODEL_URL", "QWEN_MODEL_SHA256"):
                found[node.targets[0].id] = ast.literal_eval(node.value)
    return found["QWEN_MODEL_URL"], found["QWEN_MODEL_SHA256"]


PHONE_URL, PHONE_SHA256 = ("", "") if IN_CONTAINER else _published_phone_model()
PHONE_HEAD = "/root/models/next_line_head_qwen.npz"
#: 40 s of "Holy, Holy, Holy" (LibriVox, public domain): warm/README.md.
WARM = "/root/warm/hymn.s16le.gz"
#: GPU types, in order of preference. When Modal has no L4 free it takes the
#: next rather than wait (2026-11-26: queued minutes for one). Kernels are
#: compiled at build for each architecture: L4 and L40S are 8.9, A10 is 8.6.
GPUS = ["L4", "A10", "L40S"]


def _fetch_models() -> None:
    import hashlib
    import io
    import urllib.request
    import zipfile

    from huggingface_hub import snapshot_download

    for repo, rev in (QWEN, ALIGNER):
        snapshot_download(repo, revision=rev)
    z = urllib.request.urlopen(os.environ["PHONE_URL"]).read()
    got = hashlib.sha256(z).hexdigest()
    if got != os.environ["PHONE_SHA256"]:
        raise SystemExit(f"phone model checksum {got}, expected {os.environ['PHONE_SHA256']}")
    Path(PHONE_HEAD).parent.mkdir(parents=True, exist_ok=True)
    Path(PHONE_HEAD).write_bytes(zipfile.ZipFile(io.BytesIO(z)).read(Path(PHONE_HEAD).name))


def _kernel_dirs() -> str:
    """Point MLX's and the driver's kernel caches at this GPU architecture's
    own folder, before MLX loads. MLX keys its cache by kernel name only, so an
    L4's kernels fail to load on an A10 (2026-11-26, "Failed to load compiled
    gather_int32_uint32_1 kernel" on every step). Returns "<name>, <cap>"."""
    import subprocess

    out = (
        subprocess.run(
            ["nvidia-smi", "--query-gpu=name,compute_cap", "--format=csv,noheader"],
            capture_output=True,
            text=True,
        )
        .stdout.strip()
        .splitlines()[0]
    )
    cap = out.rsplit(",", 1)[1].strip().replace(".", "")
    os.environ["MLX_PTX_CACHE_DIR"] = f"/root/kernels/sm{cap}/mlx"
    os.environ["CUDA_CACHE_PATH"] = f"/root/kernels/sm{cap}/nv"
    return out


def _warm_pcm():
    import gzip

    import numpy as np

    return np.frombuffer(gzip.open(WARM).read(), "<i2").astype(np.float32) / 32768.0


def _compile_kernels() -> None:
    """Stream the hymn through the engine and the next-line check at build
    time, so every kernel a service needs is compiled before anyone connects.
    Run once per GPU architecture in `GPUS`."""
    gpu = _kernel_dirs()
    import time

    import mlx.core as mx

    from auto_presenter.asr.qwen_backend import QwenStreamingBackend
    from auto_presenter.audio.types import AudioFrame
    from auto_presenter.follower import next_line

    t = time.monotonic()
    pcm = _warm_pcm()
    b = QwenStreamingBackend(None)
    b.load()
    frames = [
        AudioFrame(samples=pcm[i : i + 1600], t_start=i / 16000, t_end=(i + 1600) / 16000)
        for i in range(0, len(pcm), 1600)
    ]
    words = [w.text for e in b.streamer().transcribe(frames) for w in e.words if e.is_final]
    encode = next_line.qwen_encoder(b)
    head = next_line.PhoneHead.load(Path(PHONE_HEAD))
    n = int(next_line.WINDOW_S * 16000)
    for i in range(n, len(pcm), 4000):
        head(encode(pcm[i - n : i]))
    # gathers the hymn may not reach: every value/index dtype pair
    for vt in (mx.int32, mx.uint32, mx.int64, mx.float16, mx.float32, mx.bfloat16):
        x = mx.arange(64).astype(vt).reshape(8, 8)
        for it in (mx.int32, mx.uint32, mx.int64):
            i = mx.array([1, 3]).astype(it)
            mx.eval(x.reshape(-1)[i], x[i], mx.take(x, i, axis=1), mx.take(x.reshape(-1), i))
    # Fed faster than real time, the engine skips ahead, so the count is low
    # (12-23 words on the servers measured 2026-11-26): this only proves the
    # engine runs on this GPU.
    if len(words) < 5:
        raise SystemExit(f"warm-up heard only {len(words)} words on {gpu}")
    print(f"compiled kernels on {gpu} in {time.monotonic() - t:.1f}s; heard {len(words)} words")


image = (
    # MLX's CUDA backend compiles kernels at run time and needs the CUDA headers
    modal.Image.from_registry("nvidia/cuda:12.8.1-devel-ubuntu24.04", add_python="3.12")
    .env({"CUDA_HOME": "/usr/local/cuda", "CUDA_PATH": "/usr/local/cuda"})
    .pip_install_from_requirements(str(HERE / "requirements.txt"))
    # the image's own CUDA libraries are older than the ones MLX's wheels ship:
    # load MLX's first (the image is kept for its headers)
    .env(
        {
            "LD_LIBRARY_PATH": ":".join(
                f"/usr/local/lib/python3.12/site-packages/nvidia/{d}/lib"
                for d in (
                    "cublas",
                    "cusolver",
                    "cusparse",
                    "cudnn",
                    "cuda_runtime",
                    "cuda_nvrtc",
                    "nvjitlink",
                    "cufft",
                    "curand",
                    "nccl",
                    "cuda_cupti",
                )
            )
        }
    )
    .env({"PHONE_URL": PHONE_URL, "PHONE_SHA256": PHONE_SHA256})
    .run_function(_fetch_models)  # baked into the image: nothing downloads at start
    .env({"HF_HUB_OFFLINE": "1", "CUDA_CACHE_MAXSIZE": "4294967296"})
    .add_local_dir(
        HERE.parent / "sidecar" / "auto_presenter",
        remote_path="/root/auto_presenter",
        copy=True,
        ignore=["**/__pycache__", "**/*.pyc"],
    )
    .add_local_file(HERE / "warm" / "hymn.s16le.gz", remote_path=WARM, copy=True)
    .run_function(_compile_kernels, gpu="L4")
    .run_function(_compile_kernels, gpu="A10")
    .env({"SPEECH_BUILD": BUILD, "SPEECH_ENV": ENV})
    .add_local_file(HERE / "server.py", remote_path="/root/speech/server.py")
    .add_local_file(HERE / "tokens.py", remote_path="/root/speech/tokens.py")
)

app = modal.App("church-slides-speech", image=image)


@app.cls(
    gpu=GPUS,
    scaledown_window=20 * 60,  # a service's quiet stretches never cold-start it
    timeout=4 * 60 * 60,  # one websocket can last a whole service
    max_containers=MAX_CONTAINERS,
    secrets=[modal.Secret.from_name("church-slides-speech")],
)
# Steps run one at a time per container (the engine's GPU lock). 4 streams
# keep final words under 2 s, with the next-line check on (2026-11-27);
# 8 made them 7.5 s. So 4 per container, then Modal starts another.
@modal.concurrent(max_inputs=4)
class Speech:
    @modal.enter()
    def load(self) -> None:
        import logging
        import time

        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
        t = time.monotonic()
        self.gpu = _kernel_dirs()
        from auto_presenter.asr.qwen_backend import QwenStreamingBackend

        self.backend = QwenStreamingBackend(None)
        self.backend.warm()  # loads both models and runs one step
        self.load_s = time.monotonic() - t
        print(f"loaded in {self.load_s:.1f}s on {self.gpu}, build {BUILD}", flush=True)

    @modal.asgi_app()
    def web(self):
        sys.path.insert(0, "/root/speech")
        from server import make_api

        from auto_presenter.follower import next_line

        return make_api(
            self.backend,
            secret=os.environ["SPEECH_TOKEN_SECRET"],
            gpu=self.gpu,
            load_s=self.load_s,
            build=os.environ.get("SPEECH_BUILD", "dev"),
            phone_head=next_line.PhoneHead.load(Path(PHONE_HEAD)),
        )


@app.function()
def versions() -> str:
    """`modal run --env staging speech-server/app.py::versions`: the image's packages."""
    import subprocess

    return subprocess.run(
        [sys.executable, "-m", "pip", "freeze"], capture_output=True, text=True
    ).stdout
