"""Fetching the ONNX scorer model: verified before unpacking, unpacked whole
or not at all, never outside its folder."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from auto_presenter.follower import scorer_models as sm
from auto_presenter.follower.onnx_scorer import FORMAT


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def _model_files() -> dict[str, bytes]:
    return {
        "encoder.onnx": b"enc",
        "scorer.onnx": b"sc",
        "tokenizer.json": b"{}",
        "meta.json": json.dumps({"format": FORMAT, "prompt": [1, 2], "eot": 3}).encode(),
        "LICENSE-whisper.txt": b"MIT",
    }


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTO_PRESENTER_ONNX_SCORER", raising=False)
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "speech" / "hub"))
    pytest.importorskip("onnxruntime")
    return tmp_path


def _model(data: bytes, sha: str | None = None) -> sm.ScorerModel:
    return sm.ScorerModel("base.en-f3", "https://example.invalid/m.zip",
                          sha or hashlib.sha256(data).hexdigest(), len(data))


def test_a_good_download_lands_whole_and_reports_progress(home):
    data = _zip(_model_files())
    model = _model(data)
    seen = []
    d = sm.download(model, progress=lambda done, total: seen.append((done, total)),
                    open_url=lambda url: _Resp(data))
    assert d == sm.models_root() / "base.en-f3"
    assert sm.is_downloaded(model)
    assert (d / "LICENSE-whisper.txt").read_bytes() == b"MIT"
    assert seen[-1] == (len(data), len(data))
    # nothing half-made is left beside it
    assert [p.name for p in d.parent.iterdir()] == ["base.en-f3"]


def test_a_corrupted_download_is_refused_and_leaves_nothing(home):
    data = _zip(_model_files())
    model = _model(data, sha="0" * 64)
    with pytest.raises(sm.ScorerDownloadError, match="checksum"):
        sm.download(model, open_url=lambda url: _Resp(data))
    assert not sm.is_downloaded(model)
    assert not any(p.name == "base.en-f3" for p in sm.models_root().iterdir())


def test_a_file_escaping_its_folder_is_refused(home):
    files = _model_files() | {"../../evil.txt": b"x"}
    data = _zip(files)
    with pytest.raises(sm.ScorerDownloadError, match="unexpected path"):
        sm.download(_model(data), open_url=lambda url: _Resp(data))
    assert not (home / "evil.txt").exists()


def test_an_out_of_date_export_is_refused(home):
    files = _model_files() | {"meta.json": json.dumps({"format": FORMAT - 1}).encode()}
    data = _zip(files)
    with pytest.raises(sm.ScorerDownloadError, match="incomplete or out of date"):
        sm.download(_model(data), open_url=lambda url: _Resp(data))


def test_an_unpublished_model_says_so(home):
    with pytest.raises(sm.ScorerDownloadError, match="isn't published"):
        sm.download(sm.ScorerModel("x", "", "", 0))


def test_the_development_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTO_PRESENTER_ONNX_SCORER", str(tmp_path / "mine"))
    assert sm.scorer_dir() == tmp_path / "mine"
