"""The ONNX span-scorer model, fetched once and kept with the app's models.

Off Apple silicon, live following's scorer (`follower.onnx_scorer`) reads
graphs exported by scripts/export_whisper_scorer_onnx.py (OpenAI's Whisper
weights, MIT) and packaged by scripts/package_scorer_model.py: one zip per
model, with the licence beside the graphs. This fetches the pinned zip,
checks its SHA-256 before anything is unpacked, and unpacks it under a
temporary name that is renamed into place only when complete, so a cut-off
download is never mistaken for a model.

Where it lands: beside the speech models (`HF_HUB_CACHE`, the app's folder
in a release build: asr/model_home.py), under `onnx-scorer/<name>`.
`AUTO_PRESENTER_ONNX_SCORER` overrides the directory outright (development:
build/onnx/base.en).
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScorerModel:
    #: Folder name under `onnx-scorer/`, and what the scorer reports.
    name: str
    url: str
    sha256: str
    bytes: int


#: The scorer the CPU path runs: base.en (fp32), as accurate as the
#: Apple-silicon reference in the real-time gold eval (0.256 vs 0.240,
#: 2026-10-15) and within an M1 CPU's budget. Pinned: a changed file is a
#: changed checksum, which fails the download rather than loading it.
CPU_SCORER = ScorerModel(
    name="base.en-f3",
    url=(
        "https://github.com/martinmain93/church-slides-releases/releases/download/"
        "models-scorer-base.en-f3/church-slides-scorer-base.en-f3.zip"
    ),
    sha256="c23c797e2ddf6bab2558c534af893ede8e3e806dbfc4e20cf5217d41d3c44cde",
    bytes=170331997,
)


def models_root() -> Path:
    from auto_presenter.asr.model_manager import _default_cache_dir

    return _default_cache_dir().parent / "onnx-scorer"


def scorer_dir(model: ScorerModel = CPU_SCORER) -> Path:
    override = os.environ.get("AUTO_PRESENTER_ONNX_SCORER")
    if override:
        return Path(override).expanduser()
    return models_root() / model.name


def is_downloaded(model: ScorerModel = CPU_SCORER) -> bool:
    from auto_presenter.follower.onnx_scorer import available

    return available(scorer_dir(model))


def download_mb(model: ScorerModel = CPU_SCORER) -> int:
    return round(model.bytes / 2**20)


class ScorerDownloadError(RuntimeError):
    pass


def download(
    model: ScorerModel = CPU_SCORER,
    progress: Callable[[int, int], None] | None = None,
    open_url: Callable[[str], object] | None = None,
) -> Path:
    """Fetch, verify and unpack `model`; returns its directory. `progress`
    gets (bytes so far, total). Raises ScorerDownloadError on a bad checksum
    or an unusable file; network errors propagate as they are."""
    dest = scorer_dir(model)
    if is_downloaded(model):
        return dest
    if not model.url:
        raise ScorerDownloadError("the scoring model isn't published yet")
    if open_url is None:
        from auto_presenter.http_client import open_get

        def open_url(url: str):
            return open_get(url, timeout_seconds=60.0)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest.parent, prefix=".download-") as tmp:
        zpath = Path(tmp) / "model.zip"
        digest = hashlib.sha256()
        done = 0
        with open_url(model.url) as resp, open(zpath, "wb") as f:
            while chunk := resp.read(1 << 20):
                f.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, model.bytes)
        if digest.hexdigest() != model.sha256:
            raise ScorerDownloadError(
                "the scoring model didn't download correctly (checksum mismatch); try again"
            )
        unpacked = Path(tmp) / "model"
        with zipfile.ZipFile(zpath) as z:
            for info in z.infolist():
                # nothing may land outside the folder it's unpacked into
                target = (unpacked / info.filename).resolve()
                if unpacked.resolve() not in target.parents and target != unpacked.resolve():
                    raise ScorerDownloadError(f"unexpected path in the model file: {info.filename}")
            z.extractall(unpacked)
        from auto_presenter.follower.onnx_scorer import available

        if not available(unpacked):
            raise ScorerDownloadError("the downloaded scoring model is incomplete or out of date")
        if dest.exists():
            shutil.rmtree(dest)
        os.replace(unpacked, dest)
    log.info("scoring model %s downloaded to %s", model.name, dest)
    return dest
