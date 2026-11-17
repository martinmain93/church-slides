"""Hardware probe + recommended ASR model + download tracking.

The Phase 4 plan owes us three things:

1. Detect what hardware the operator has, so we can pick a Whisper
   model size that runs at a usable real-time factor.
2. Resolve a sensible *recommendation* (size + device + compute type)
   that the audio session uses by default.
3. Track model downloads so the UI can surface progress — `tiny.en`
   is ~75 MB, `large-v3` is ~3 GB; the operator deserves a progress bar.

This module owns 1-3 in a way that's headless: it has no opinion about
HOW the progress events reach the UI. Callers pass a `progress_callback`
(typically `server.broadcast`) and we invoke it with structured events.

The download itself piggybacks on faster-whisper's own model loader
(which already caches under `~/.cache/huggingface/hub`). We just
intercept the cache directory to count bytes before/after. That's
coarse-grained but accurate enough for a progress bar — and avoids
re-implementing Hugging Face's transfer protocol.

Tests inject a fake `WhisperLoader` so the model_manager logic is
exercised without touching the network or the FS cache.
"""

from __future__ import annotations

import contextlib
import logging
import os
import platform
import shutil
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

log = logging.getLogger(__name__)


ModelSize = Literal[
    "tiny", "tiny.en", "base", "base.en", "small", "small.en",
    "medium", "medium.en", "large-v3", "large-v3-turbo",
]
Device = Literal["cpu", "cuda", "mps", "auto"]
ComputeType = Literal["int8", "float16", "float32", "default"]


#: Approximate download sizes (MB), used to tell the operator what a model
#: costs before the download starts and to size the progress bar.
#: Measured from the Hub's file listings (2026-09-28). The operator reads this
#: as "needs about N MB of internet", so an unknown size falling back to a
#: guess is a visible lie — medium.en was missing and showed 500 MB for a
#: 1.46 GB download.
_MODEL_SIZE_MB: dict[ModelSize, int] = {
    "tiny": 75,
    "tiny.en": 74,
    "base": 141,
    "base.en": 141,
    "small": 464,
    "small.en": 464,
    "medium": 1460,
    "medium.en": 1460,
    "large-v3": 2948,
    "large-v3-turbo": 1547,
}

#: Steady-state Metal pass times on the same host after the first-call
#: compile, mlx-whisper, 6 s window, temperature=0. First pass is 1–5 s
#: of kernel compile — `MLXWhisperBackend.warm()` pays that at session
#: start so it is not on the sung-word path.
_MEASURED_PASS_MS_MLX: dict[str, int] = {
    "tiny.en": 52,
    "base.en": 101,
    "small.en": 336,
}

AsrBackendName = Literal["whisper", "mlx"]

#: A pass must finish inside the interval that feeds it, or the capture queue
#: backs up and starts discarding audio. Mirrors
#: `SlidingWindowWrapper.step_seconds` (1.0 s).
_STEP_BUDGET_MS = 1000


@dataclass(frozen=True)
class HardwareProfile:
    """What we detected about the host's compute capabilities."""

    os: str
    machine: str
    has_cuda: bool
    has_mps: bool
    cpu_count: int
    total_ram_gb: float | None
    #: `mlx_whisper` imports on this host. Independent of `has_mps`:
    #: CTranslate2 cannot use Metal even when MPS exists, so `has_mps`
    #: alone must not pick a GPU Whisper path. MLX can.
    has_mlx: bool = False

    @property
    def has_gpu(self) -> bool:
        return self.has_cuda or self.has_mps


@dataclass(frozen=True)
class ModelRecommendation:
    """Resolved (backend, model, device, compute_type) for this host."""

    model_size: ModelSize
    device: Device
    compute_type: ComputeType
    reason: str
    #: "mlx" on Apple silicon when mlx-whisper is installed; otherwise
    #: "whisper" (faster-whisper / CTranslate2). The live session start
    #: request takes this as `StartAudioSessionRequest.backend`.
    backend: AsrBackendName = "whisper"


@dataclass(frozen=True)
class DownloadProgressEvent:
    """One progress update emitted while a model download is in flight.

    The event is intentionally trivial to serialize so callers (the WS
    layer in Phase 8) can ship it directly to the UI as JSON.
    """

    model_size: ModelSize
    bytes_downloaded: int
    bytes_total: int
    state: Literal["starting", "downloading", "completed", "failed"]
    detail: str = ""

    @property
    def fraction(self) -> float:
        if self.bytes_total <= 0:
            return 0.0
        return min(1.0, self.bytes_downloaded / self.bytes_total)


ProgressCallback = Callable[[DownloadProgressEvent], None]


class WhisperLoader(Protocol):
    """The single fn we depend on from faster-whisper, factored for testing."""

    def __call__(
        self,
        model_size: str,
        *,
        device: str,
        compute_type: str,
        local_files_only: bool = False,
    ) -> Any: ...


# --------------------------------------------------------------------- detection


def detect_hardware() -> HardwareProfile:
    """Probe the host. No I/O outside `platform`/`os`/optional torch."""
    has_cuda = False
    has_mps = False
    try:
        # `torch` is an indirect dependency of faster-whisper; if it's
        # installed we use it for accurate detection, otherwise fall back
        # to OS hints.
        import torch  # type: ignore[import-not-found]

        has_cuda = bool(torch.cuda.is_available())
        has_mps = bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())
    except Exception:  # pragma: no cover - torch not present in CI
        pass

    machine = platform.machine().lower()
    if not has_mps and sys.platform == "darwin" and machine in {"arm64", "aarch64"}:
        # Apple Silicon laptop without torch — assume MPS is usable, since
        # faster-whisper will pick "auto" → CoreML on that hardware.
        has_mps = True

    has_mlx = False
    if sys.platform == "darwin" and machine in {"arm64", "aarch64"}:
        try:
            from auto_presenter.asr.mlx_backend import MLXWhisperBackend

            has_mlx = MLXWhisperBackend.is_available()
        except Exception:  # pragma: no cover - optional extra missing
            has_mlx = False

    return HardwareProfile(
        os=sys.platform,
        machine=machine,
        has_cuda=has_cuda,
        has_mps=has_mps,
        cpu_count=_cpu_count(),
        total_ram_gb=_total_ram_gb(),
        has_mlx=has_mlx,
    )


def recommend_model(profile: HardwareProfile) -> ModelRecommendation:
    """Pick a default model this host can actually keep up with.

    **This used to recommend by memory and it was wrong.** The old rules asked
    whether a model *fits* — "large-v3 fits in unified memory" — but fitting
    is not the constraint. The constraint is throughput: `SlidingWindowWrapper`
    asks for one pass per second of audio, and a pass that takes longer than
    that makes the capture queue back up and drop audio. The lag then grows
    without bound, which from the operator's chair is "the AI stopped working".

    The old heuristic recommended `large-v3` with `device="auto"` and
    `compute_type="float16"` on any Apple Silicon host with 16 GB. Measured on
    exactly such a host:

      - CTranslate2 4.7.1 reports **no CUDA and no MPS backend**. Apple Silicon
        GPUs are not available to *faster-whisper* at all, so that path runs
        on CPU.
      - CPU supports `int8`, `int8_float32`, `float32` — **not `float16`**.
      - One pass over a 6 s window, int8 on CPU: tiny.en 344 ms, base.en
        794 ms, small.en **2098 ms**. `small.en` is already twice the budget
        on CPU.

    **MLX is the exception on Apple silicon.** `mlx-whisper` does use Metal.
    Steady-state on an idle M1 (2026-09-10): tiny.en 52 ms, base.en 101 ms,
    small.en 336 ms — all inside the 1 s step, and small.en is the one that
    measured better accuracy on the goldens (wrong-slide 0.330 → 0.276).
    `has_mps` still does not pick this path on its own: only `has_mlx`
    (the extra is actually installed) does.
    """
    if profile.has_cuda:
        # A real CUDA device is the one case where a large model keeps up.
        return ModelRecommendation(
            model_size="large-v3",
            device="cuda",
            compute_type="float16",
            backend="whisper",
            reason="CUDA GPU detected — large-v3 runs comfortably with float16.",
        )

    if profile.has_mlx:
        # Metal is not the CPU: do not shrink the budget for core count.
        chosen, pass_ms = _largest_fitting(_MEASURED_PASS_MS_MLX, _STEP_BUDGET_MS)
        return ModelRecommendation(
            model_size=chosen,  # type: ignore[arg-type]
            device="mps",
            compute_type="float16",
            backend="mlx",
            reason=(
                f"Apple GPU via mlx-whisper — {chosen} runs a pass in about "
                f"{pass_ms} ms against a {_STEP_BUDGET_MS} ms budget, leaving "
                f"the CPU free for the matcher. Warm the model at session "
                f"start; the first call compiles Metal kernels."
            ),
        )

    # faster-whisper on CPU (Intel Macs, Windows PCs): always small.en, the
    # model live following was measured with on the CPU path (short-context
    # passes and the ONNX scorer, as accurate as Apple silicon's: 0.256 vs
    # 0.240 in the real-time gold eval, 2026-10-15). Whether this computer can
    # keep up with it is the machine check's call (asr.machine_check), not a
    # guess from its core count: a smaller model was measured too inaccurate
    # to offer (base.en word-only 0.427), so a computer too slow for small.en
    # gets no live AI rather than a worse one.
    return ModelRecommendation(
        model_size="small.en",
        device="cpu",
        compute_type="int8",
        backend="whisper",
        reason=(
            f"CPU transcription ({profile.cpu_count} cores): small.en with short "
            f"passes. Whether this computer keeps up is measured by its machine "
            f"check before live AI is offered."
        ),
    )


def _largest_fitting(measured: dict[str, int], budget: int) -> tuple[str, int]:
    affordable = [
        (name, ms)
        for name, ms in sorted(measured.items(), key=lambda kv: kv[1])
        if ms <= budget
    ]
    if affordable:
        return affordable[-1]
    # Nothing fits: still return the cheapest so a session can start.
    return min(measured.items(), key=lambda kv: kv[1])


# --------------------------------------------------------------------- downloads


@dataclass
class ModelManager:
    """Owns model selection + download lifecycle.

    The default `whisper_loader` calls `faster_whisper.WhisperModel`;
    tests inject a fake. The cache directory is the same one
    faster-whisper uses (`~/.cache/huggingface/hub`), so a model
    downloaded here is immediately visible to a future bare
    `WhisperBackend.load()` call without copying.
    """

    #: None: wherever huggingface_hub downloads to, read each time it's
    #: needed. The server builds its manager on import, before `main` has
    #: pointed downloads at the app's folder (`asr.model_home`); a folder
    #: fixed then is the old one, so moved models read as missing and were
    #: downloaded again.
    cache_dir: Path | None = None
    whisper_loader: WhisperLoader | None = None
    _progress: ProgressCallback | None = field(default=None, init=False)

    def set_progress_callback(self, callback: ProgressCallback | None) -> None:
        self._progress = callback

    def is_downloaded(self, model_size: ModelSize, backend: str = "whisper") -> bool:
        """Heuristic: does the HF cache contain a directory for this model?

        `backend="mlx"` checks the MLX conversion, which is a different repo
        (and a different download) from the CTranslate2 one.
        """
        # The HF hub layout puts shard files under `snapshots/<hash>/`, and
        # links a file in there only once it has finished downloading. Look
        # nowhere else: an interrupted download leaves a large
        # `blobs/<hash>.incomplete`, which used to pass for a downloaded model
        # — then the offline-only load of it failed at the start of a service.
        #
        # Look for the weights file itself, not just "something big": the
        # small files finish first, and faster-whisper's `tokenizer.json`
        # (2.1 MB) alone used to read as a downloaded model, so a download
        # cut off mid-`model.bin` showed "Ready" and the service-time
        # offline load failed.
        target = self._model_cache_dir(model_size, backend) / "snapshots"
        if not target.exists():
            return False
        names = _MLX_WEIGHTS if backend == "mlx" else _WHISPER_WEIGHTS
        for snapshot in target.iterdir():
            for name in names:
                weights = snapshot / name
                if _is_complete_file(weights):
                    return True
        return False

    def ensure(
        self,
        model_size: ModelSize,
        *,
        device: Device = "auto",
        compute_type: ComputeType = "default",
        progress: ProgressCallback | None = None,
    ) -> Any:
        """Load the model, downloading it first if necessary.

        Returns the loaded `WhisperModel` (or whatever the loader
        returns). Progress events are fired around the download. The
        loader call is synchronous; callers wanting non-blocking
        behavior should run this on a worker thread (see Phase 4 Slice
        2B-2 for the established pattern).
        """
        loader = self.whisper_loader or _default_loader
        # A per-call callback, when given, instead of the manager-wide one:
        # two loads at once must each report to their own caller.
        emit = self._emit if progress is None else _guarded(progress)
        already_cached = self.is_downloaded(model_size)

        # Unknown model sizes (e.g. faster-whisper aliases like `base.en`) get
        # a conservative default so the progress bar still has something to
        # show. Off-by-2x is fine — the bar normalizes to fraction anyway.
        total_bytes = _MODEL_SIZE_MB.get(model_size, 500) * 1024 * 1024
        if not already_cached:
            emit(
                DownloadProgressEvent(
                    model_size=model_size,
                    bytes_downloaded=0,
                    bytes_total=total_bytes,
                    state="starting",
                    detail="downloading model from Hugging Face",
                )
            )

        started = time.monotonic()
        watch = (
            contextlib.nullcontext()
            if already_cached
            else self.watch_download(model_size, "whisper", total_bytes, emit)
        )
        try:
            # A cached model loads without touching the network. Otherwise
            # faster-whisper asks the Hub for updates first, and on a network
            # that accepts connections but never answers — a captive portal, a
            # stalled church Wi-Fi — that stalled a model already on disk for
            # 90 s and then failed the load outright.
            with watch:
                model = loader(
                    model_size,
                    device=device,
                    compute_type=compute_type,
                    local_files_only=already_cached,
                )
        except Exception as exc:
            emit(
                DownloadProgressEvent(
                    model_size=model_size,
                    bytes_downloaded=0,
                    bytes_total=total_bytes,
                    state="failed",
                    detail=(
                        download_failed_message(model_size, total_bytes)
                        if not already_cached and looks_offline(exc)
                        else str(exc)
                    ),
                )
            )
            raise

        elapsed = time.monotonic() - started
        actual_bytes = self._actual_bytes_on_disk(model_size) or total_bytes
        emit(
            DownloadProgressEvent(
                model_size=model_size,
                bytes_downloaded=actual_bytes,
                bytes_total=actual_bytes,
                state="completed",
                detail=(
                    f"cached (loaded in {elapsed:.1f}s)"
                    if already_cached
                    else f"downloaded in {elapsed:.1f}s"
                ),
            )
        )
        return model

    @contextlib.contextmanager
    def watch_download(
        self,
        model_size: ModelSize,
        backend: str,
        total_bytes: int,
        emit: ProgressCallback,
        *,
        interval_s: float = 1.0,
    ) -> Iterator[None]:
        """Report bytes on disk while a download runs, about once a second.

        Neither faster-whisper nor `snapshot_download` reports progress, so a
        first download used to say "starting" and then nothing for minutes —
        indistinguishable from a stall, and an operator gave up on one. The
        cache's `blobs/` grows as files arrive (`.incomplete` included), which
        is the progress.
        """
        stop = threading.Event()

        def run() -> None:
            last = -1
            while not stop.wait(interval_s):
                done = self.bytes_in_cache(model_size, backend)
                if done == last:
                    continue
                last = done
                emit(
                    DownloadProgressEvent(
                        model_size=model_size,
                        bytes_downloaded=done,
                        bytes_total=max(total_bytes, done),
                        state="downloading",
                        detail="downloading model from Hugging Face",
                    )
                )

        thread = threading.Thread(target=run, name="model-download-watch", daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join()

    def bytes_in_cache(self, model_size: ModelSize, backend: str = "whisper") -> int:
        """Bytes downloaded so far, finished files and partial ones alike."""
        blobs = self._model_cache_dir(model_size, backend) / "blobs"
        try:
            return sum(p.stat().st_size for p in blobs.iterdir() if p.is_file())
        except OSError:
            return 0

    # ------------------------------------------------------------- internals

    def _emit(self, event: DownloadProgressEvent) -> None:
        if self._progress is not None:
            _guarded(self._progress)(event)

    def _cache(self) -> Path:
        return self.cache_dir if self.cache_dir is not None else _default_cache_dir()

    def _model_cache_dir(self, model_size: ModelSize, backend: str = "whisper") -> Path:
        # HF naming: `models--<org>--<name>`
        if backend == "mlx":
            from auto_presenter.asr.mlx_backend import repo_for

            return self._cache() / ("models--" + repo_for(model_size).replace("/", "--"))
        return self._cache() / f"models--Systran--faster-whisper-{model_size}"

    def _actual_bytes_on_disk(self, model_size: ModelSize) -> int:
        target = self._model_cache_dir(model_size)
        if not target.exists():
            return 0
        return sum(p.stat().st_size for p in target.rglob("*") if p.is_file())


# --------------------------------------------------------------------- helpers


def _guarded(callback: ProgressCallback) -> ProgressCallback:
    def emit(event: DownloadProgressEvent) -> None:
        try:
            callback(event)
        except Exception:  # pragma: no cover - defensive
            log.exception("model download progress callback raised")

    return emit


#: The file in each repo that only exists once the model itself is down:
#: CTranslate2 conversions ship `model.bin`; MLX conversions ship
#: `weights.npz` (older) or `weights.safetensors` (newer).
_WHISPER_WEIGHTS = ("model.bin",)
_MLX_WEIGHTS = ("weights.safetensors", "weights.npz")


def _is_complete_file(path: Path) -> bool:
    """True when `path` resolves to a real, non-trivial file whose blob is
    not still being written (`blobs/<hash>.incomplete`)."""
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    if not resolved.is_file() or resolved.stat().st_size <= 1_000_000:
        return False
    return not resolved.with_name(resolved.name + ".incomplete").exists()


def _default_cache_dir() -> Path:
    """Where huggingface_hub puts downloads: `~/.cache/huggingface/hub`
    unless `HF_HUB_CACHE` / `HF_HOME` say otherwise. Read from the same
    environment the downloader reads, or "is it downloaded?" checks one
    folder while the download lands in another."""
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"]).expanduser()
    if os.environ.get("HF_HOME"):
        return Path(os.environ["HF_HOME"]).expanduser() / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def _default_loader(
    model_size: str, *, device: str, compute_type: str, local_files_only: bool = False
) -> Any:
    from faster_whisper import WhisperModel

    return WhisperModel(
        model_size,
        device=device,
        compute_type=compute_type,
        local_files_only=local_files_only,
    )


#: Exception class-name fragments that mean "couldn't reach the Hub" across
#: huggingface_hub, httpx, requests and the socket layer. Matched by name so
#: this needs none of those imported, and walked down the cause chain because
#: faster-whisper re-raises the Hub's error, which wraps the transport's.
_OFFLINE_ERROR_NAMES = (
    "LocalEntryNotFound",
    "OfflineMode",
    "Connect",
    "Timeout",
    "gaierror",
    "ReadError",
)


def looks_offline(exc: BaseException) -> bool:
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if any(part in type(cur).__name__ for part in _OFFLINE_ERROR_NAMES):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def download_size_mb(model_size: str) -> int:
    """Approximate first-time download for this model size."""
    return _MODEL_SIZE_MB.get(model_size, 500)


def download_failed_message(model_size: str, total_bytes: int) -> str:
    """What the operator reads when a first-time model download fails.

    The raw exception is a Hugging Face stack of connection errors; what
    matters in the room is that this needs internet once, how big it is, and
    that presenting carries on by hand in the meantime.
    """
    mb = round(total_bytes / (1024 * 1024))
    return (
        f"Couldn't download the {model_size} speech model (about {mb} MB). "
        "It needs an internet connection the first time; after that it works "
        "offline. You can still present by hand."
    )


def _cpu_count() -> int:
    n = None
    try:
        n = len(_sched_getaffinity()) if hasattr(__import__("os"), "sched_getaffinity") else None
    except Exception:
        n = None
    return n or shutil.os.cpu_count() or 1


def _sched_getaffinity() -> set[int]:
    import os

    return os.sched_getaffinity(0)


def _total_ram_gb() -> float | None:
    """Best-effort RAM probe. Returns None if we can't tell."""
    try:
        import psutil  # type: ignore[import-not-found]

        return psutil.virtual_memory().total / (1024**3)
    except Exception:
        pass
    if sys.platform == "darwin":
        try:
            import subprocess

            out = subprocess.check_output(
                ["sysctl", "-n", "hw.memsize"], timeout=1.0
            )
            return int(out.strip()) / (1024**3)
        except Exception:
            return None
    if sys.platform == "win32":
        # Without this, `recommend_model` reads unknown RAM as 0 GB and offers
        # every Windows machine the smallest model.
        try:
            import ctypes

            class _MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = _MemoryStatusEx()
            status.dwLength = ctypes.sizeof(status)
            if ctypes.WinDLL("kernel32").GlobalMemoryStatusEx(ctypes.byref(status)):
                return status.ullTotalPhys / (1024**3)
        except Exception:
            return None
        return None
    if sys.platform.startswith("linux"):
        try:
            meminfo = Path("/proc/meminfo").read_text()
            for line in meminfo.splitlines():
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return kb / (1024**2)
        except Exception:
            return None
    return None
