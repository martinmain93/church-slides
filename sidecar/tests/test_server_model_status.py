"""`model_status`: what live AI needs on this machine before a service.

The operator's indicator reads this to say, before anyone sings, whether live
AI will work offline or needs internet to fetch a model first — instead of
finding out when the service session fails to start.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_presenter import server as server_mod
from auto_presenter.asr.model_manager import ModelManager
from auto_presenter.protocol import ModelStatusRequest


@pytest.fixture(autouse=True)
def apple_silicon(monkeypatch):
    """These describe a computer with Apple silicon's speech engine, where the
    speech model stands alone; `TestCpuPath` below is the other kind."""
    monkeypatch.setattr(server_mod.MLXWhisperBackend, "is_available", staticmethod(lambda: True))


@pytest.fixture
def sent(monkeypatch):
    replies = []

    async def capture(_ws, reply):
        replies.append(reply)

    monkeypatch.setattr(server_mod, "_send", capture)
    return replies


def _cache_model(cache: Path, slug: str) -> None:
    snap = cache / slug / "snapshots" / "abc"
    snap.mkdir(parents=True)
    weights = "weights.npz" if "mlx" in slug else "model.bin"
    (snap / weights).write_bytes(b"\0" * 2_000_000)


@pytest.mark.asyncio
async def test_a_missing_model_reports_its_download(tmp_path, monkeypatch, sent):
    monkeypatch.setattr(server_mod._state, "model_manager", ModelManager(cache_dir=tmp_path))
    await server_mod._handle_model_status(
        None, ModelStatusRequest(id=1, backend="whisper", model_size="small.en")
    )
    reply = sent[-1]
    assert reply.downloaded is False
    assert reply.download_mb == 464
    assert reply.needs_internet_while_running is False


@pytest.mark.asyncio
async def test_a_cached_model_needs_nothing(tmp_path, monkeypatch, sent):
    _cache_model(tmp_path, "models--Systran--faster-whisper-small.en")
    monkeypatch.setattr(server_mod._state, "model_manager", ModelManager(cache_dir=tmp_path))
    await server_mod._handle_model_status(
        None, ModelStatusRequest(id=1, backend="whisper", model_size="small.en")
    )
    assert sent[-1].downloaded is True
    assert sent[-1].download_mb == 0


@pytest.mark.asyncio
async def test_mlx_checks_its_own_conversion(tmp_path, monkeypatch, sent):
    """The MLX weights are a different repo and a different download: having
    the CTranslate2 model cached says nothing about them."""
    _cache_model(tmp_path, "models--Systran--faster-whisper-small.en")
    monkeypatch.setattr(server_mod._state, "model_manager", ModelManager(cache_dir=tmp_path))
    await server_mod._handle_model_status(
        None, ModelStatusRequest(id=1, backend="mlx", model_size="small.en")
    )
    assert sent[-1].downloaded is False
    _cache_model(tmp_path, "models--mlx-community--whisper-small.en-mlx")
    await server_mod._handle_model_status(
        None, ModelStatusRequest(id=2, backend="mlx", model_size="small.en")
    )
    assert sent[-1].downloaded is True


@pytest.mark.asyncio
async def test_hosted_needs_internet_throughout(tmp_path, monkeypatch, sent):
    monkeypatch.setattr(server_mod._state, "model_manager", ModelManager(cache_dir=tmp_path))
    await server_mod._handle_model_status(
        None,
        ModelStatusRequest(id=1, backend="groq", model_size="whisper-large-v3-turbo"),
    )
    assert sent[-1].needs_internet_while_running is True
    assert sent[-1].download_mb == 0


@pytest.mark.asyncio
async def test_prepare_model_downloads_and_loads_whisper(tmp_path, monkeypatch, sent):
    from auto_presenter.protocol import PrepareModelRequest

    loaded = []

    def fake_load(size, *, progress_callback=None):
        loaded.append(size)
        return object()

    monkeypatch.setattr(server_mod, "_get_or_load_whisper_backend", fake_load)
    await server_mod._handle_prepare_model(
        None, PrepareModelRequest(id=1, backend="whisper", model_size="base.en")
    )
    assert loaded == ["base.en"]
    assert sent[-1].downloaded is True


@pytest.mark.asyncio
async def test_prepare_model_reports_a_failed_download(tmp_path, monkeypatch, sent):
    from auto_presenter.protocol import PrepareModelRequest

    errors = []

    async def capture_error(_ws, _id, message):
        errors.append(message)

    def fails(size, *, progress_callback=None):
        raise RuntimeError("Couldn't download the base.en speech model")

    monkeypatch.setattr(server_mod, "_get_or_load_whisper_backend", fails)
    monkeypatch.setattr(server_mod, "_send_error", capture_error)
    await server_mod._handle_prepare_model(
        None, PrepareModelRequest(id=1, backend="whisper", model_size="base.en")
    )
    assert errors == ["Couldn't download the base.en speech model"]
    assert sent == []


class TestCpuPath:
    """Off Apple silicon the speech model comes with the ONNX span scorer:
    live following's CPU path needs both, so they count as one."""

    @pytest.fixture(autouse=True)
    def no_mlx(self, monkeypatch):
        monkeypatch.setattr(
            server_mod.MLXWhisperBackend, "is_available", staticmethod(lambda: False)
        )

    @pytest.mark.asyncio
    async def test_the_speech_model_alone_is_not_enough(self, tmp_path, monkeypatch, sent):
        from auto_presenter.follower import scorer_models

        _cache_model(tmp_path, "models--Systran--faster-whisper-small.en")
        monkeypatch.setattr(server_mod._state, "model_manager", ModelManager(cache_dir=tmp_path))
        monkeypatch.setattr(scorer_models, "is_downloaded", lambda *a: False)
        monkeypatch.setattr(scorer_models, "download_mb", lambda *a: 162)
        await server_mod._handle_model_status(
            None, ModelStatusRequest(id=1, backend="whisper", model_size="small.en")
        )
        assert sent[-1].downloaded is False
        assert sent[-1].download_mb == 162

    @pytest.mark.asyncio
    async def test_both_together_are_ready(self, tmp_path, monkeypatch, sent):
        from auto_presenter.follower import scorer_models

        _cache_model(tmp_path, "models--Systran--faster-whisper-small.en")
        monkeypatch.setattr(server_mod._state, "model_manager", ModelManager(cache_dir=tmp_path))
        monkeypatch.setattr(scorer_models, "is_downloaded", lambda *a: True)
        await server_mod._handle_model_status(
            None, ModelStatusRequest(id=1, backend="whisper", model_size="small.en")
        )
        assert sent[-1].downloaded is True
        assert sent[-1].download_mb == 0

    @pytest.mark.asyncio
    async def test_neither_adds_both_sizes(self, tmp_path, monkeypatch, sent):
        from auto_presenter.follower import scorer_models

        monkeypatch.setattr(server_mod._state, "model_manager", ModelManager(cache_dir=tmp_path))
        monkeypatch.setattr(scorer_models, "is_downloaded", lambda *a: False)
        monkeypatch.setattr(scorer_models, "download_mb", lambda *a: 162)
        await server_mod._handle_model_status(
            None, ModelStatusRequest(id=1, backend="whisper", model_size="small.en")
        )
        assert sent[-1].download_mb == 464 + 162

    @pytest.mark.asyncio
    async def test_downloading_both_is_one_bar_that_never_runs_backwards(
        self, tmp_path, monkeypatch, sent
    ):
        """The speech model's own "completed" used to end the download for
        the home card, which re-asked for status, got only the scorer's
        162 MB, and dropped the bar from 11% to 0 before refilling it."""
        from auto_presenter.asr.model_manager import DownloadProgressEvent
        from auto_presenter.follower import scorer_models
        from auto_presenter.protocol import PrepareModelRequest

        mb = 2**20
        monkeypatch.setattr(server_mod._state, "model_manager", ModelManager(cache_dir=tmp_path))
        events = []

        async def capture(event):
            events.append(event)

        monkeypatch.setattr(server_mod, "_broadcast_model_progress", capture)

        def fake_load(size, *, progress_callback=None):
            steps = (("starting", 0), ("downloading", 200 * mb), ("completed", 470 * mb))
            for state, done in steps:
                progress_callback(DownloadProgressEvent(
                    model_size=size, bytes_downloaded=done, bytes_total=464 * mb,
                    state=state, detail="",
                ))
            return object()

        def fake_download(model=None, progress=None, open_url=None):
            progress(scorer_models.CPU_SCORER.bytes // 2, scorer_models.CPU_SCORER.bytes)

        monkeypatch.setattr(server_mod, "_get_or_load_whisper_backend", fake_load)
        monkeypatch.setattr(scorer_models, "is_downloaded", lambda *a: False)
        monkeypatch.setattr(scorer_models, "download", fake_download)
        await server_mod._handle_prepare_model(
            None, PrepareModelRequest(id=1, backend="whisper", model_size="small.en")
        )
        import asyncio

        await asyncio.sleep(0)  # let the threadsafe broadcasts land
        states = [e.state for e in events]
        assert states.count("completed") == 1 and states[-1] == "completed"
        done = [e.bytes_downloaded for e in events]
        assert done == sorted(done)
        # the scorer half continues from the speech model's real bytes
        assert events[-1].bytes_downloaded == 470 * mb + scorer_models.CPU_SCORER.bytes
        assert sent[-1].downloaded is True
