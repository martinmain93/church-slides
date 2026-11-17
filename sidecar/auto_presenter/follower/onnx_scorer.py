"""The span scorer on ONNX Runtime: Intel Macs, Windows PCs, any CPU.

`MLXSpanScorer` (follower.scorer) runs only on Apple silicon's GPU. This is
the same computation for everything else, from graphs exported by
scripts/export_whisper_scorer_onnx.py (OpenAI's Whisper weights, MIT):

* `encoder.onnx`: the 6 s window encoded as it is (positional embedding
  sliced), not padded to 30 s, out to every decoder layer's cross-attention
  keys and values, computed once a window;
* `scorer.onnx`: every span scored in one call per token length against
  those keys and values, with the vocabulary projection only at the
  positions that predict a span's own tokens. Run on the best span alone,
  its alignment heads' cross-attention places it in the window by
  Whisper's DTW word timing (`_place`).

Same scores as MLX's in fp32 (same best span in every benchmark window, rank
correlation 1.00, placement within 0.02 s), on a CPU (ONNX Runtime's
default) or a GPU provider when given one (DirectML on Windows, CUDA). Use
the fp32 export: ONNX Runtime's dynamic int8 quantises activations with one
scale per tensor, so a span's score depends on which spans share its batch
(up to 0.4 apart in log-probability on real songs), which no setting of
ours can keep stable.

The DTW and median filter are ports of `mlx_whisper.timing`'s (MIT) to
plain numpy: that module needs numba and scipy, which the Intel and Windows
engines don't ship.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import numpy as np

from auto_presenter.follower.scorer import SCORE_CHUNK, WINDOW_S, ScoreResult

log = logging.getLogger(__name__)

#: Files a model directory must hold.
FILES = ("encoder.onnx", "scorer.onnx", "tokenizer.json", "meta.json")
#: The export this reads (scripts/export_whisper_scorer_onnx.py FORMAT): 3 has
#: the encoder output every layer's cross-attention keys and values ("kv")
#: and the scorer output the alignment heads too (no align.onnx).
FORMAT = 3


#: Threads for ONNX Runtime by default: scoring is memory-bound, and on an M1
#: 4 threads scored 160 spans fastest (2: 843 ms, 4: 638, 6: 759).
MAX_THREADS = 4


def default_threads() -> int:
    """`MAX_THREADS`, or fewer on a computer with fewer physical cores."""
    try:
        import psutil

        cores = psutil.cpu_count(logical=False) or MAX_THREADS
    except Exception:
        cores = MAX_THREADS
    return max(1, min(MAX_THREADS, cores))


def available(model_dir: Path) -> bool:
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    if not all((model_dir / f).is_file() for f in FILES):
        return False
    try:
        return json.loads((model_dir / "meta.json").read_text()).get("format") == FORMAT
    except (OSError, ValueError):
        return False


class OnnxSpanScorer:
    """Teacher-forced span scoring with ONNX Runtime (any CPU, or a GPU provider)."""

    def __init__(
        self,
        model_dir: Path,
        providers: list[str] | None = None,
        threads: int = 0,
    ) -> None:
        self.model_dir = Path(model_dir)
        #: what `scorer_repo` reports in pipeline health / session config
        self.repo = f"onnx:{self.model_dir.name}"
        self.providers = providers or ["CPUExecutionProvider"]
        self.threads = threads or default_threads()
        self._sessions: tuple | None = None
        self._cache: dict[str, list[int]] = {}

    def active_provider(self) -> str:
        """The provider ONNX Runtime actually chose (a GPU provider that
        can't start falls back to the CPU without saying so)."""
        return self._load()[1].get_providers()[0]

    def _load(self):
        if self._sessions is None:
            import onnxruntime as ort
            from faster_whisper.feature_extractor import FeatureExtractor
            from tokenizers import Tokenizer

            opts = ort.SessionOptions()
            if self.threads:
                opts.intra_op_num_threads = self.threads
            if "DmlExecutionProvider" in self.providers:
                # DirectML's documented requirements: no memory patterns,
                # and nodes run one at a time.
                opts.enable_mem_pattern = False
                opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            d = self.model_dir
            meta = json.loads((d / "meta.json").read_text())
            self._sessions = (
                ort.InferenceSession(str(d / "encoder.onnx"), opts, providers=self.providers),
                ort.InferenceSession(str(d / "scorer.onnx"), opts, providers=self.providers),
            )
            self._tok = Tokenizer.from_file(str(d / "tokenizer.json"))
            self._sot: list[int] = list(meta["prompt"])
            self._eot: int = int(meta["eot"])
            self._fe = FeatureExtractor()
            log.info(
                "ONNX span scorer %s on %s", d.name, self._sessions[1].get_providers()[0]
            )
        return self._sessions

    def tokens(self, text: str) -> list[int]:
        if text not in self._cache:
            self._cache[text] = self._tok.encode(" " + text.strip(), add_special_tokens=False).ids
        return self._cache[text]

    def _encode(self, audio_16k: np.ndarray) -> np.ndarray:
        """The window's cross-attention keys and values, [layers, 2, T, d]."""
        enc = self._load()[0]
        frames = int(WINDOW_S * 100)
        mel = self._fe(audio_16k.astype(np.float32))[:, :frames]
        if mel.shape[-1] < frames:
            mel = np.pad(mel, ((0, 0), (0, frames - mel.shape[-1])))
        return enc.run(None, {"mel": mel[None].astype(np.float32)})[0]

    def score(
        self, audio_16k: np.ndarray, spans: list[str], place_among: int | None = None
    ) -> ScoreResult | None:
        """`place_among`: place the best of the first N spans only (others ride along)."""
        if not spans:
            return None
        t0 = time.perf_counter()
        _, scorer = self._load()
        kv = self._encode(audio_16k)
        toks = [self.tokens(s) for s in spans]
        n0 = len(self._sot)
        # A span whose tokens begin another span's is scored by that one: the
        # decoder is causal, so the longer span's first tokens' log-probabilities
        # are exactly the shorter's. A line's first few spans are prefixes of
        # its sixth (spans_for): 25% fewer token positions on real songs.
        runs, owner = _prefix_runs(toks)
        per_token: dict[tuple[int, ...], np.ndarray] = {}
        by_len: dict[int, list[tuple[int, ...]]] = {}
        for r in runs:
            by_len.setdefault(len(r), []).append(r)
        for n_tok, group in by_len.items():
            width = n0 + n_tok
            for c in range(0, len(group), SCORE_CHUNK):
                chunk = group[c : c + SCORE_CHUNK]
                seqs = np.asarray([self._sot + list(r) for r in chunk], np.int64)
                rows = np.asarray(
                    [b * width + p for b in range(len(chunk)) for p in range(n0 - 1, width - 1)],
                    np.int64,
                )
                lp = scorer.run(
                    None,
                    {
                        "kv": kv,
                        "tokens": seqs,
                        "rows": rows,
                        "targets": seqs[:, n0:].reshape(-1),
                    },
                )[0].reshape(len(chunk), n_tok)
                for r, row in zip(chunk, lp, strict=True):
                    per_token[r] = row
        scores = np.asarray(
            [per_token[owner[tuple(t)]][: len(t)].mean() if t else -20.0 for t in toks],
            np.float32,
        )
        t1 = time.perf_counter()
        best = int(np.argmax(scores[: place_among or len(spans)]))
        place = self._place(kv, toks[best], spans[best])
        t2 = time.perf_counter()
        return ScoreResult(
            scores, best, place, {"score": t1 - t0, "place": t2 - t1, "total": t2 - t0}
        )

    def _place(
        self, kv: np.ndarray, toks: list[int], span: str, medfilt_width: int = 7
    ) -> float:
        """Start of the span's last word in the window, seconds (Whisper's DTW
        timing, as `MLXSpanScorer._place`)."""
        scorer = self._load()[1]
        seq = np.asarray([self._sot + toks + [self._eot]], np.int64)
        # one sequence; the scored position is incidental, the heads are the point
        n0 = len(self._sot)
        w = scorer.run(
            None,
            {
                "kv": kv,
                "tokens": seq,
                "rows": np.asarray([n0 - 1], np.int64),
                "targets": seq[0, n0 : n0 + 1],
            },
        )[1].astype(np.float32)
        mean = w.mean(axis=-2, keepdims=True)
        std = w.std(axis=-2, keepdims=True)
        w = median_filter((w - mean) / np.maximum(std, 1e-6), medfilt_width)
        matrix = w.mean(axis=0)[n0 - 1 : n0 + len(toks)]
        text_idx, time_idx = dtw(-matrix)
        jumps = np.pad(np.diff(text_idx), (1, 0), constant_values=1).astype(bool)
        jump_times = time_idx[jumps] * 0.02
        last = self.tokens(span.split()[-1])
        i_last = max(0, len(toks) - len(last))
        return float(jump_times[min(i_last, len(jump_times) - 1)])


def _prefix_runs(
    toks: list[list[int]],
) -> tuple[list[tuple[int, ...]], dict[tuple[int, ...], tuple[int, ...]]]:
    """The token sequences to run (none a prefix of another) and, for every
    requested sequence, the run that scores it."""
    runs: list[tuple[int, ...]] = []
    owner: dict[tuple[int, ...], tuple[int, ...]] = {}
    for t in sorted({tuple(t) for t in toks if t}, key=len, reverse=True):
        if t in owner:
            continue  # a prefix of a longer run
        runs.append(t)
        for k in range(1, len(t) + 1):
            owner.setdefault(t[:k], t)
    return runs, owner


def median_filter(x: np.ndarray, width: int) -> np.ndarray:
    """Median over a `width`-wide window along the last axis, reflect-padded
    (`mlx_whisper.timing.median_filter`, without scipy)."""
    pad = width // 2
    if x.shape[-1] <= pad:
        return x
    padded = np.pad(x, [(0, 0)] * (x.ndim - 1) + [(pad, pad)], mode="reflect")
    windows = np.lib.stride_tricks.sliding_window_view(padded, width, axis=-1)
    return np.median(windows, axis=-1).astype(np.float32)


def dtw(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Monotonic alignment of rows (tokens) to columns (frames) minimising
    `x`'s summed cost (`mlx_whisper.timing.dtw_cpu`, without numba). Returns
    (row indexes, column indexes) along the path."""
    n, m = x.shape
    cost = np.full((n + 1, m + 1), np.inf, np.float32)
    trace = np.full((n + 1, m + 1), -1, np.int8)
    cost[0, 0] = 0.0
    for j in range(1, m + 1):
        for i in range(1, n + 1):
            c0, c1, c2 = cost[i - 1, j - 1], cost[i - 1, j], cost[i, j - 1]
            if c0 < c1 and c0 < c2:
                c, t = c0, 0
            elif c1 < c0 and c1 < c2:
                c, t = c1, 1
            else:
                c, t = c2, 2
            cost[i, j] = x[i - 1, j - 1] + c
            trace[i, j] = t
    trace[0, :] = 2
    trace[:, 0] = 1
    i, j = n, m
    path = []
    while i > 0 or j > 0:
        path.append((i - 1, j - 1))
        t = trace[i, j]
        if t == 0:
            i, j = i - 1, j - 1
        elif t == 1:
            i -= 1
        else:
            j -= 1
    arr = np.array(path[::-1])
    return arr[:, 0], arr[:, 1]
