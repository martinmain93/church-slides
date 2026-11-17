"""Speech models move from the shared Hugging Face cache into the app's folder."""

from __future__ import annotations

import os
from pathlib import Path

from auto_presenter.asr import model_home
from auto_presenter.asr.model_home import migrate_speech_models, use_models_dir


def _repo(cache: Path, name: str, *, incomplete: bool = False) -> Path:
    """A repo laid out the way huggingface_hub lays one out: the snapshot's
    files are relative links into the repo's own blobs."""
    repo = cache / name
    (repo / "blobs").mkdir(parents=True)
    (repo / "blobs" / "abc123").write_bytes(b"weights")
    if incomplete:
        (repo / "blobs" / "def456.incomplete").write_bytes(b"half")
    snap = repo / "snapshots" / "rev1"
    snap.mkdir(parents=True)
    (snap / "weights.npz").symlink_to(Path("../../blobs/abc123"))
    return repo


def test_a_speech_model_moves_and_its_files_still_resolve(tmp_path):
    old, new = tmp_path / "shared", tmp_path / "app" / "speech"
    _repo(old, "models--mlx-community--whisper-small.en-mlx")
    new.mkdir(parents=True)

    moved = migrate_speech_models(old, new)

    assert moved == ["models--mlx-community--whisper-small.en-mlx"]
    assert not (old / "models--mlx-community--whisper-small.en-mlx").exists()
    repo = new / "models--mlx-community--whisper-small.en-mlx"
    weights = repo / "snapshots" / "rev1" / "weights.npz"
    assert weights.read_bytes() == b"weights"


def test_other_tools_models_stay_in_the_shared_cache(tmp_path):
    old, new = tmp_path / "shared", tmp_path / "speech"
    _repo(old, "models--Systran--faster-whisper-base.en")
    _repo(old, "models--facebook--wav2vec2-base")
    new.mkdir()

    assert migrate_speech_models(old, new) == ["models--Systran--faster-whisper-base.en"]
    assert (old / "models--facebook--wav2vec2-base").exists()


def test_an_unfinished_download_or_one_already_moved_is_left_alone(tmp_path):
    old, new = tmp_path / "shared", tmp_path / "speech"
    _repo(old, "models--mlx-community--whisper-tiny.en-mlx", incomplete=True)
    _repo(old, "models--mlx-community--whisper-base.en-mlx")
    _repo(new, "models--mlx-community--whisper-base.en-mlx")

    assert migrate_speech_models(old, new) == []
    assert (old / "models--mlx-community--whisper-tiny.en-mlx").exists()


def test_across_volumes_it_copies_then_lets_go(tmp_path, monkeypatch):
    old, new = tmp_path / "shared", tmp_path / "speech"
    _repo(old, "models--Systran--faster-whisper-small.en")
    new.mkdir()
    real_rename = os.rename

    def rename(src, dst):
        if Path(src).parent == old:
            raise OSError(18, "Invalid cross-device link")
        real_rename(src, dst)

    monkeypatch.setattr(model_home.os, "rename", rename)
    assert migrate_speech_models(old, new) == ["models--Systran--faster-whisper-small.en"]
    assert not (old / "models--Systran--faster-whisper-small.en").exists()
    snap = new / "models--Systran--faster-whisper-small.en" / "snapshots" / "rev1" / "weights.npz"
    assert snap.is_symlink() and snap.read_bytes() == b"weights"
    assert not list(new.glob("*.moving"))


def test_downloads_go_to_the_apps_folder_from_then_on(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    speech = tmp_path / "app" / "models" / "speech"
    use_models_dir(speech, shared_cache=tmp_path / "nothing-here")
    assert os.environ["HF_HUB_CACHE"] == str(speech)
    assert speech.is_dir()
