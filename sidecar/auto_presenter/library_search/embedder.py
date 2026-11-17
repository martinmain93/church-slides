"""The sentence model behind library search: e5-small-v2 (MIT), int8 ONNX.

34 MB, ~2 ms a query and ~5 ms a passage on one CPU core of an M-series Mac.
Chosen over bge-small (same size) and the fp32 export (4x the size, no more
accurate) on the spike's labelled queries; see research/spikes/semantic_search.

Where it loads from, first match wins:
    AUTO_PRESENTER_SEARCH_MODEL   a folder holding model.onnx + tokenizer.json
    the frozen engine             `_internal/models/search` (build_sidecar.py)
    the app data folder           `models/search`, where
                                  scripts/setup_search_model.py puts it in development
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

#: Bumped whenever the model or how text is fed to it changes: the on-disk
#: index is keyed by it, so a new model never compares against old vectors.
MODEL_ID = "e5-small-v2-int8/1"
MAX_TOKENS = 256
_QUERY = "query: "
_PASSAGE = "passage: "


def bundled_model_dir() -> Path | None:
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) / "models" / "search" if base else None


def default_model_dir() -> Path:
    override = os.environ.get("AUTO_PRESENTER_SEARCH_MODEL")
    if override:
        return Path(override)
    bundled = bundled_model_dir()
    if bundled is not None and (bundled / "model.onnx").is_file():
        return bundled
    data_dir = os.environ.get("AUTO_PRESENTER_DATA_DIR")
    if data_dir:
        base = Path(data_dir)
    else:
        from auto_presenter.config import app_data_dir

        base = app_data_dir()
    return base / "models" / "search"


class Embedder:
    """Unit-length vectors for queries and passages. Thread-safe: ONNX
    Runtime sessions may be run from several threads, and the tokenizer is
    only read."""

    def __init__(self, model_dir: Path) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.tok = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self.tok.enable_truncation(MAX_TOKENS)
        self.tok.enable_padding()
        opts = ort.SessionOptions()
        # Half the cores, at most four: a first index of ~300 songs takes
        # ~15 s on four cores of an M-series Mac (~50 s on two), and the
        # other half stays free for a live service running alongside.
        opts.intra_op_num_threads = max(1, min(4, (os.cpu_count() or 2) // 2))
        self.sess = ort.InferenceSession(
            str(model_dir / "model.onnx"), opts, providers=["CPUExecutionProvider"]
        )
        self._inputs = {i.name for i in self.sess.get_inputs()}

    def queries(self, texts: list[str]) -> np.ndarray:
        return self._embed([_QUERY + t for t in texts])

    def passages(self, texts: list[str]) -> np.ndarray:
        return self._embed([_PASSAGE + t for t in texts])

    def _embed(self, texts: list[str], batch: int = 32) -> np.ndarray:
        if not texts:
            return np.zeros((0, 384), dtype=np.float32)
        # Similar lengths batched together pad less: ~3x faster on a library.
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        out = np.empty((len(texts), 384), dtype=np.float32)
        for start in range(0, len(order), batch):
            idx = order[start : start + batch]
            enc = self.tok.encode_batch([texts[i] for i in idx])
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self._inputs:
                feed["token_type_ids"] = np.zeros_like(ids)
            hidden = self.sess.run(None, feed)[0]
            m = mask[..., None].astype(np.float32)
            vec = (hidden * m).sum(1) / np.maximum(m.sum(1), 1e-9)
            out[idx] = vec / np.maximum(np.linalg.norm(vec, axis=1, keepdims=True), 1e-9)
        return out


_LOADED: Embedder | None = None
_LOAD_ERROR: str | None = None
_LOCK = threading.Lock()


def get_embedder() -> Embedder | None:
    """The process's one embedder, loaded on first use; None if the model
    isn't installed or won't load (search then falls back to keywords)."""
    global _LOADED, _LOAD_ERROR
    with _LOCK:
        if _LOADED is not None or _LOAD_ERROR is not None:
            return _LOADED
        model_dir = default_model_dir()
        if not (model_dir / "model.onnx").is_file() or not (model_dir / "tokenizer.json").is_file():
            _LOAD_ERROR = f"search model not installed at {model_dir}"
            log.info("library search: %s", _LOAD_ERROR)
            return None
        try:
            _LOADED = Embedder(model_dir)
            log.info("library search: model loaded from %s", model_dir)
        except Exception as exc:  # a broken install must not take the engine down
            _LOAD_ERROR = f"search model failed to load: {exc}"
            log.exception("library search: model failed to load")
        return _LOADED


def load_error() -> str | None:
    return _LOAD_ERROR
