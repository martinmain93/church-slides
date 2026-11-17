"""Keep the speech models in the app's own folder.

huggingface_hub (behind both faster-whisper and mlx-whisper) downloads into
a cache shared by every tool on the computer, `~/.cache/huggingface/hub`. So
the app's speech model — hundreds of MB — lived outside the app's data:
deleting the app left it behind, and so would an uninstaller that clears the
app's own folder, because nothing tied it to Church Slides.

The shell passes `--models-dir` (a folder beside the library) in a release
build, and `use_models_dir` points huggingface_hub there before anything
imports it, then moves across any speech model an earlier version left in
the shared cache, so nobody downloads it twice. Development runs and the
build's own tests pass no folder and keep the shared cache.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

#: The repos the app downloads speech models from (`asr.mlx_backend.repo_for`
#: and faster-whisper's model names), as huggingface_hub names their folders.
SPEECH_REPO_PREFIXES = ("models--mlx-community--whisper-", "models--Systran--faster-whisper-")


def use_models_dir(models_dir: Path, *, shared_cache: Path) -> list[str]:
    """Download speech models into `models_dir` from now on, and move the
    ones already in `shared_cache` there. Must run before huggingface_hub is
    imported: it reads its cache folder once, on import. Returns the repos
    moved."""
    models_dir.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HUB_CACHE"] = str(models_dir)
    try:
        return migrate_speech_models(shared_cache, models_dir)
    except OSError:
        # Never a reason not to start: the worst case is a fresh download.
        log.exception("couldn't move speech models out of %s", shared_cache)
        return []


def migrate_speech_models(old: Path, new: Path) -> list[str]:
    """Move each speech-model repo from `old` to `new`, skipping any that is
    still downloading or already there. A repo's snapshot links point into
    its own `blobs/` by relative path, so the folder moves whole."""
    if not old.is_dir() or old.resolve() == new.resolve():
        return []
    moved = []
    for repo in sorted(old.iterdir()):
        if not repo.name.startswith(SPEECH_REPO_PREFIXES) or not repo.is_dir():
            continue
        target = new / repo.name
        if target.exists():
            continue
        if any((repo / "blobs").glob("*.incomplete")):
            log.info("not moving %s: a download into it isn't finished", repo.name)
            continue
        _move(repo, target)
        moved.append(repo.name)
        log.info("moved speech model %s into the app's folder", repo.name)
    return moved


def _move(src: Path, dst: Path) -> None:
    try:
        os.rename(src, dst)  # same volume: instant, and all or nothing
        return
    except OSError:
        pass
    # Another volume: copy under a temporary name, so an interrupted copy is
    # never mistaken for a model, then let go of the original.
    partial = dst.with_name(dst.name + ".moving")
    shutil.rmtree(partial, ignore_errors=True)
    shutil.copytree(src, partial, symlinks=True)
    os.rename(partial, dst)
    shutil.rmtree(src, ignore_errors=True)
