"""Tests for ModelManager and the hardware-recommendation helper.

These tests inject a fake `WhisperLoader` so we never hit the network
or the Hugging Face cache. The hardware probe is exercised via its
return type only — actual CUDA/MPS detection is platform-specific.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from auto_presenter.asr.model_manager import (
    _MEASURED_PASS_MS_MLX,
    _STEP_BUDGET_MS,
    DownloadProgressEvent,
    HardwareProfile,
    ModelManager,
    detect_hardware,
    recommend_model,
)


def test_detect_hardware_returns_populated_profile():
    profile = detect_hardware()
    assert isinstance(profile, HardwareProfile)
    assert profile.cpu_count >= 1
    # On any host the OS + machine strings are non-empty.
    assert profile.os
    assert profile.machine


def test_recommend_model_chooses_large_v3_on_cuda():
    profile = HardwareProfile(
        os="linux",
        machine="x86_64",
        has_cuda=True,
        has_mps=False,
        cpu_count=16,
        total_ram_gb=32.0,
    )
    rec = recommend_model(profile)
    assert rec.model_size == "large-v3"
    assert rec.device == "cuda"
    assert rec.compute_type == "float16"
    assert rec.backend == "whisper"


def test_apple_silicon_without_mlx_is_treated_as_cpu_not_a_gpu():
    """CTranslate2 has no MPS backend, so `has_mps` alone must not pick a
    GPU path — that was the large-v3/float16 bug. Without the MLX extra
    the recommendation is the CPU path's: small.en, int8, judged by the
    machine check.
    """
    profile = HardwareProfile(
        os="darwin",
        machine="arm64",
        has_cuda=False,
        has_mps=True,
        cpu_count=8,
        total_ram_gb=16.0,
        has_mlx=False,
    )
    rec = recommend_model(profile)
    assert rec.backend == "whisper"
    assert rec.device == "cpu"
    assert rec.compute_type == "int8"
    assert rec.model_size == "small.en"


def test_apple_silicon_with_mlx_defaults_to_metal_small_en():
    """mlx-whisper is the path that can actually use the GPU, and small.en
    fits the 1 s step (336 ms measured). Recommending CPU base.en here left
    Metal idle.
    """
    profile = HardwareProfile(
        os="darwin",
        machine="arm64",
        has_cuda=False,
        has_mps=True,
        cpu_count=8,
        total_ram_gb=16.0,
        has_mlx=True,
    )
    rec = recommend_model(profile)
    assert rec.backend == "mlx"
    assert rec.device == "mps"
    assert rec.model_size == "small.en"
    assert _MEASURED_PASS_MS_MLX[rec.model_size] <= _STEP_BUDGET_MS


def test_every_cpu_gets_the_model_the_machine_check_judges():
    """Off Apple silicon (no CUDA) the recommendation is always small.en: the
    model live following was measured with on the CPU path. Keeping up is no
    longer guessed from the core count (that picked tiny.en/base.en, measured
    far below the accuracy bar); the machine check times small.en's short
    passes on the computer and live AI is offered only if they fit."""
    from auto_presenter.asr import machine_check

    for cores, ram in ((2, 4.0), (4, 8.0), (8, 16.0), (16, 64.0)):
        profile = HardwareProfile(
            os="linux", machine="x86_64", has_cuda=False, has_mps=False,
            cpu_count=cores, total_ram_gb=ram,
        )
        rec = recommend_model(profile)
        assert (rec.backend, rec.device, rec.compute_type) == ("whisper", "cpu", "int8")
        assert rec.model_size == "small.en"
    # the check times the same model
    import inspect

    assert inspect.signature(machine_check.assess_cpu_path).parameters[
        "whisper_model_size"
    ].default == rec.model_size


def test_no_mlx_recommendation_exceeds_the_pass_budget():
    profile = HardwareProfile(
        os="darwin", machine="arm64", has_cuda=False, has_mps=True,
        cpu_count=8, total_ram_gb=16.0, has_mlx=True,
    )
    rec = recommend_model(profile)
    measured = _MEASURED_PASS_MS_MLX.get(rec.model_size)
    assert rec.backend == "mlx"
    assert measured is not None
    assert measured <= _STEP_BUDGET_MS


def test_model_manager_emits_progress_events_around_load(tmp_path: Path):
    events: list[DownloadProgressEvent] = []

    def fake_loader(name, *, device, compute_type, **_kw):
        return ("fake-model", name, device, compute_type)

    manager = ModelManager(cache_dir=tmp_path, whisper_loader=fake_loader)
    manager.set_progress_callback(events.append)
    model = manager.ensure("tiny.en", device="cpu", compute_type="int8")

    assert model[0] == "fake-model"
    states = [e.state for e in events]
    # Fresh cache → starting + completed events.
    assert "starting" in states
    assert states[-1] == "completed"
    assert events[-1].fraction == 1.0


def test_model_manager_does_not_emit_starting_when_cache_hit(tmp_path: Path):
    # Pre-populate the cache directory so `is_downloaded` returns True.
    model_dir = tmp_path / "models--Systran--faster-whisper-tiny.en" / "snapshots" / "abc"
    model_dir.mkdir(parents=True)
    (model_dir / "model.bin").write_bytes(b"\x00" * (2 * 1024 * 1024))

    events: list[DownloadProgressEvent] = []
    manager = ModelManager(
        cache_dir=tmp_path,
        whisper_loader=lambda *a, **kw: ("ok",),
    )
    manager.set_progress_callback(events.append)
    manager.ensure("tiny.en", device="cpu", compute_type="int8")

    # Cache-hit path emits only the completion event; no starting event.
    states = [e.state for e in events]
    assert states == ["completed"]
    assert "cached" in events[-1].detail


def test_model_manager_emits_failed_when_loader_raises(tmp_path: Path):
    events: list[DownloadProgressEvent] = []

    def boom(*_args, **_kwargs):
        raise RuntimeError("disk full")

    manager = ModelManager(cache_dir=tmp_path, whisper_loader=boom)
    manager.set_progress_callback(events.append)
    try:
        manager.ensure("tiny.en", device="cpu", compute_type="int8")
    except RuntimeError as exc:
        assert "disk full" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
    states = [e.state for e in events]
    assert states[-1] == "failed"
    assert "disk full" in events[-1].detail


def test_cached_model_loads_without_the_network(tmp_path: Path):
    """On a network that accepts connections and never answers, asking the Hub
    for updates stalled a model that was already on disk for 90 s and then
    failed the load. A cached model must be loaded local-only."""
    snapshot = tmp_path / "models--Systran--faster-whisper-tiny.en" / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    (snapshot / "model.bin").write_bytes(b"\0" * 2_000_000)
    calls = []

    def loader(name, *, device, compute_type, local_files_only=False):
        calls.append(local_files_only)
        return object()

    ModelManager(cache_dir=tmp_path, whisper_loader=loader).ensure("tiny.en")
    assert calls == [True]


def test_uncached_model_may_download(tmp_path: Path):
    calls = []

    def loader(name, *, device, compute_type, local_files_only=False):
        calls.append(local_files_only)
        return object()

    ModelManager(cache_dir=tmp_path, whisper_loader=loader).ensure("tiny.en")
    assert calls == [False]


def test_offline_download_failure_says_what_it_needs(tmp_path: Path):
    """The operator sees that it needs internet once, how big it is, and that
    presenting still works — not a Hugging Face stack of connection errors."""
    events: list[DownloadProgressEvent] = []

    class ConnectError(Exception):
        pass

    class LocalEntryNotFoundError(Exception):
        pass

    def offline(*_a, **_kw):
        try:
            raise ConnectError("[Errno 61] Connection refused")
        except ConnectError as inner:
            raise LocalEntryNotFoundError("cannot find the snapshot") from inner

    manager = ModelManager(cache_dir=tmp_path, whisper_loader=offline)
    manager.set_progress_callback(events.append)
    with pytest.raises(LocalEntryNotFoundError):
        manager.ensure("small.en")
    detail = events[-1].detail
    assert "internet connection" in detail
    assert "MB" in detail
    assert "present by hand" in detail


def test_an_interrupted_download_is_not_a_downloaded_model(tmp_path: Path):
    """A restart mid-download leaves a big `blobs/*.incomplete` and no
    snapshot link. That used to count as downloaded, so Settings said ready
    and the service's offline-only load failed."""
    blobs = tmp_path / "models--Systran--faster-whisper-tiny.en" / "blobs"
    blobs.mkdir(parents=True)
    (blobs / "deadbeef.incomplete").write_bytes(b"\0" * 2_000_000)
    manager = ModelManager(cache_dir=tmp_path)
    assert manager.is_downloaded("tiny.en") is False
    assert manager.bytes_in_cache("tiny.en") == 2_000_000


def test_a_download_cut_off_after_the_tokenizer_is_not_downloaded(tmp_path: Path):
    """faster-whisper's `tokenizer.json` (2.1 MB) lands before `model.bin`.
    A download cut off there used to read as "Ready" because anything over
    1 MB in the snapshot counted, and the service's offline load failed."""
    repo = tmp_path / "models--Systran--faster-whisper-small.en"
    snapshot = repo / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    (snapshot / "tokenizer.json").write_bytes(b"\0" * 2_100_000)
    (repo / "blobs").mkdir()
    (repo / "blobs" / "cafe.incomplete").write_bytes(b"\0" * 50_000_000)
    manager = ModelManager(cache_dir=tmp_path)
    assert manager.is_downloaded("small.en") is False


def test_weights_whose_blob_is_still_incomplete_are_not_downloaded(tmp_path: Path):
    repo = tmp_path / "models--Systran--faster-whisper-small.en"
    snapshot = repo / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    blobs = repo / "blobs"
    blobs.mkdir()
    (blobs / "cafe").write_bytes(b"\0" * 2_000_000)
    (snapshot / "model.bin").symlink_to(blobs / "cafe")
    manager = ModelManager(cache_dir=tmp_path)
    assert manager.is_downloaded("small.en") is True
    (blobs / "cafe.incomplete").write_bytes(b"\0" * 10)
    assert manager.is_downloaded("small.en") is False


def test_mlx_weights_count_as_downloaded(tmp_path: Path):
    snapshot = tmp_path / "models--mlx-community--whisper-small.en-mlx" / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_bytes(b"{}")
    manager = ModelManager(cache_dir=tmp_path)
    assert manager.is_downloaded("small.en", backend="mlx") is False
    (snapshot / "weights.safetensors").write_bytes(b"\0" * 2_000_000)
    assert manager.is_downloaded("small.en", backend="mlx") is True


def test_a_download_reports_bytes_as_they_arrive(tmp_path: Path):
    """Neither loader reports progress, so a first download said "starting"
    and then nothing for minutes — which an operator took for a stall."""
    blobs = tmp_path / "models--Systran--faster-whisper-tiny.en" / "blobs"

    def slow_loader(model_size, **_kwargs):
        blobs.mkdir(parents=True)
        for i in range(3):
            (blobs / f"part{i}.incomplete").write_bytes(b"\0" * 1000)
            time.sleep(0.05)
        return object()

    events: list[DownloadProgressEvent] = []
    manager = ModelManager(cache_dir=tmp_path, whisper_loader=slow_loader)
    manager.set_progress_callback(events.append)
    with manager.watch_download("tiny.en", "whisper", 3000, events.append, interval_s=0.01):
        slow_loader("tiny.en")
    downloading = [e for e in events if e.state == "downloading"]
    assert downloading, "no progress while the download ran"
    assert downloading[-1].bytes_downloaded == 3000
    assert [e.bytes_downloaded for e in downloading] == sorted(
        e.bytes_downloaded for e in downloading
    )


def test_the_cache_is_where_huggingface_hub_downloads_to(monkeypatch, tmp_path):
    """`HF_HOME` / `HF_HUB_CACHE` move huggingface_hub's downloads; "is it
    downloaded?" has to look there too, or it checks the wrong folder."""
    from auto_presenter.asr.model_manager import _default_cache_dir

    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    monkeypatch.setenv("HF_HOME", str(tmp_path / "home"))
    assert _default_cache_dir() == tmp_path / "home" / "hub"
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    assert _default_cache_dir() == tmp_path / "hub"


def test_a_manager_built_before_the_models_move_looks_in_the_new_folder(monkeypatch, tmp_path):
    """The server builds its manager on import, before `main` points
    downloads at the app's folder: it must follow, or moved models read as
    missing and download again."""
    from auto_presenter.asr.model_manager import ModelManager

    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    monkeypatch.setenv("HF_HOME", str(tmp_path / "shared"))
    manager = ModelManager()
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "app"))
    assert manager._model_cache_dir("tiny.en").parent == tmp_path / "app"
