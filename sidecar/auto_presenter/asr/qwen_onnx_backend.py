"""Qwen3-ASR 0.6B on the processor, through ONNX Runtime (int8 decoder). Research.

The Apple-silicon backend (`qwen_backend`) runs Qwen3-ASR on the GPU with
`mlx_qwen3_asr.streaming`. This runs the same model and the same streaming
loop on any computer's processor, so live AI needn't be Apple-silicon only
(docs/research/QWEN3_ASR_CPU.md). It is `QwenStreamingBackend` with the
model calls swapped: the `QwenStreamer` loop, word agreement and junk
filtering are shared, unchanged.

**What makes it fast enough.** `mlx_qwen3_asr.streaming` re-encodes the live
window and re-prefills its prompt every step, reusing only complete 8 s
encoder blocks. On a 4-core x86 processor that is over a whole step's time.
Here the encoder's blocks are 2 s (`BLOCK_FRAMES`), so a block completes at
every 2 s step and each step encodes and prefills only the newest audio plus
the closing text. Measured on six public songs with the MLX package set the
same way: word error 0.371 -> 0.381 (research/spikes/qwen_cpu/block_eval.py).

**The model files.** The published int4 export, andrewleech/qwen3-asr-0.6b-onnx
(Apache 2.0): `encoder.int4.onnx`, `embed_tokens.bin`, `tokenizer.json`. Its
prefill graph can't continue from a cache, so the decoder is one graph of our
own, `decoder_extend.int8.onnx` (research/spikes/qwen_cpu/export_extend.py,
quantize_extend.py --bits 8 --accuracy-level 4; int4 cost 0.09 word error):
any number of new tokens onto a key/value cache, returning the next token's
logits, the new cache, and the attention of the eight heads word times come
from (`qwen_backend.ATTN_HEADS`). Prefill and every decoded token use it.

**Word times** come from that attention while decoding, the rows the
Apple-silicon backend gets from a second prefill (`_attention_times`), so
they cost nothing extra.

Ported from `mlx_qwen3_asr` 0.4.4 (Apache 2.0): the log-mel features, the
prompt template, the prefix rollback, the repetition check and the
commit-at-silence cut.
"""

from __future__ import annotations

import logging
import math
import os
import string
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from auto_presenter.asr.qwen_backend import QwenStreamingBackend
from auto_presenter.audio.types import SAMPLE_RATE

log = logging.getLogger(__name__)

ENCODER_FILE = "encoder.int4.onnx"
DECODER_FILE = "decoder_extend.int8.onnx"
EMBED_FILE = "embed_tokens.bin"
TOKENIZER_FILE = "tokenizer.json"
FILES = (ENCODER_FILE, DECODER_FILE, DECODER_FILE + ".data", EMBED_FILE, TOKENIZER_FILE)
VOCAB = 151936
HIDDEN = 1024

#: Seconds of new audio per decode. 2 s blocks and 2 s steps: 0.381 word
#: error against 0.392 at 1 s steps, and half the passes.
STEP_SECONDS = 2.0
MAX_CONTEXT_SECONDS = 10.0
UNFIXED_TOKENS = 5
#: Decodes at the start of each window without a text prefix (the package's).
UNFIXED_CHUNKS = 2
#: Encoder attention block, in mel frames (100 a second). The model's own is
#: 800 (8 s); see the module docstring.
BLOCK_FRAMES = 200
#: A block is cached once its frames are all in, though its last frame's
#: window reaches 10 ms past it (padded): with 2 s steps the newest block
#: then completes the step it arrives, and each step prefills 2 s of audio,
#: not 4. False waits for a frame after the block, as `mlx_qwen3_asr` does.
EAGER_BLOCKS = os.environ.get("AUTO_PRESENTER_QWEN_CPU_EAGER", "1") == "1"
N_FFT = 400
HOP = 160
EOS_IDS = (151643, 151645)
REPETITION_THRESHOLD = 20
#: The window is cut at the quietest pause in its last 2 s when it overflows
#: (the package's `commit_at_silence`), so no word straddles two windows.
COMMIT_LOOKBACK = 2 * SAMPLE_RATE
COMMIT_FRAME = 320
COMMIT_MIN_SILENCE = 1920
COMMIT_DIP_RATIO = 0.5
COMMIT_ROLLBACK_TOKENS_PER_S = 10.0


def model_dir() -> Path | None:
    """Where the model files are, if all of them are: the directory named by
    AUTO_PRESENTER_QWEN_ONNX_DIR. (Not downloaded by the app yet: the files
    need a pinned home first.)"""
    env = os.environ.get("AUTO_PRESENTER_QWEN_ONNX_DIR")
    if not env:
        return None
    d = Path(env)
    return d if all((d / f).exists() for f in FILES) else None


# ------------------------------------------------------------- features


_HANN = np.hanning(N_FFT + 1)[:-1].astype(np.float32)
_FILTERS: np.ndarray | None = None


def _mel_filters() -> np.ndarray:
    global _FILTERS
    if _FILTERS is None:
        # Whisper's 128-bin filterbank, the same one Qwen3-ASR uses (checked
        # equal to mlx_qwen3_asr's assets/mel_filters.npz).
        from faster_whisper.feature_extractor import FeatureExtractor

        _FILTERS = FeatureExtractor(feature_size=128).mel_filters.astype(np.float32)
    return _FILTERS


def log_mel(audio: np.ndarray) -> np.ndarray:
    """(128, len(audio) // 160) log-mel, as `mlx_qwen3_asr.audio.log_mel_spectrogram`."""
    x = np.asarray(audio, dtype=np.float32)
    pad = N_FFT // 2
    x = np.concatenate([x[1 : pad + 1][::-1], x, x[-(pad + 1) : -1][::-1]])
    n = 1 + (len(x) - N_FFT) // HOP
    frames = np.lib.stride_tricks.as_strided(
        x, shape=(n, N_FFT), strides=(x.strides[0] * HOP, x.strides[0])
    )
    power = np.abs(np.fft.rfft(frames * _HANN, axis=-1)) ** 2
    spec = np.log10(np.maximum(_mel_filters() @ power.T.astype(np.float32), 1e-10))[:, :-1]
    spec = np.maximum(spec, spec.max() - 8.0)
    return ((spec + 4.0) / 4.0).astype(np.float32)


# ------------------------------------------------------------- tokens


class _Tokenizer:
    """The Qwen tokenizer (`tokenizers`, already a faster-whisper dependency)
    and the ASR prompt template."""

    def __init__(self, path: Path) -> None:
        from tokenizers import Tokenizer

        self._tok = Tokenizer.from_file(str(path))
        sid = self._tok.token_to_id
        self.im_start, self.im_end = sid("<|im_start|>"), sid("<|im_end|>")
        self.audio_start, self.audio_end = sid("<|audio_start|>"), sid("<|audio_end|>")
        self.audio_pad = sid("<|audio_pad|>")
        self.newline = self.encode("\n")[0]

    def encode(self, text: str) -> list[int]:
        return self._tok.encode(text, add_special_tokens=False).ids

    def decode(self, ids: list[int]) -> str:
        return self._tok.decode(ids, skip_special_tokens=False)

    def head(self, context: str) -> list[int]:
        """The prompt up to and including <|audio_start|>."""
        ids = [self.im_start, *self.encode("system\n")]
        if context:
            ids += self.encode(context)
        return [*ids, self.im_end, self.newline, self.im_start, *self.encode("user\n"),
                self.audio_start]

    def tail(self) -> list[int]:
        """The prompt after the audio, with English forced."""
        return [self.audio_end, self.im_end, self.newline, self.im_start,
                *self.encode("assistant\n"), *self.encode("language English<asr_text>")]


def _repeating(tokens: list[int], threshold: int = REPETITION_THRESHOLD) -> bool:
    """`mlx_qwen3_asr.generate._detect_repetition`."""
    if len(tokens) < threshold:
        return False
    count = 1
    for i in range(len(tokens) - 1, 0, -1):
        if tokens[i] != tokens[i - 1]:
            break
        count += 1
        if count >= threshold:
            return True
    for n in range(2, min(11, len(tokens) // 2 + 1)):
        pattern, repeats, pos = tokens[-n:], 1, len(tokens) - 2 * n
        while pos >= 0 and tokens[pos : pos + n] == pattern:
            repeats += 1
            pos -= n
        if repeats >= max(2, threshold // n):
            return True
    return False


def _budget(window_s: float) -> int:
    """`mlx_qwen3_asr.generate.resolve_max_new_tokens`, adaptive."""
    return min(512, max(128, math.ceil(window_s * 12.0) + 32))


def _frame_rms(x: np.ndarray, frame: int, hop: int) -> np.ndarray:
    if len(x) < frame:
        return np.array([], dtype=np.float32)
    starts = range(0, len(x) - frame + 1, hop)
    return np.array([np.sqrt(np.mean(x[s : s + frame] ** 2)) for s in starts], np.float32)


def _commit_cut(audio: np.ndarray) -> int:
    """`mlx_qwen3_asr.streaming._select_commit_boundary`: the centre of the
    quietest pause in the window's last 2 s, else its end."""
    total, hop = len(audio), COMMIT_FRAME // 2
    start = max(0, total - COMMIT_LOOKBACK)
    if total - start < COMMIT_FRAME:
        return total
    seg = _frame_rms(audio[start:], COMMIT_FRAME, hop)
    ref = _frame_rms(audio, COMMIT_FRAME, hop)
    if not seg.size or not ref.size or float(np.median(ref)) <= 1e-8:
        return total
    quiet = seg <= float(np.median(ref)) * COMMIT_DIP_RATIO
    min_frames = max(1, math.ceil(COMMIT_MIN_SILENCE / hop))
    best: tuple[float, int, int] | None = None
    run: int | None = None
    for i in range(len(quiet) + 1):
        if i < len(quiet) and quiet[i]:
            run = i if run is None else run
            continue
        if run is not None and i - run >= min_frames:
            m = float(np.mean(seg[run:i]))
            if best is None or m <= best[0]:
                best = (m, run, i)
        run = None
    if best is None:
        return total
    centre = (best[1] + best[2] - 1) / 2.0
    return max(0, min(total, start + round(centre * hop) + COMMIT_FRAME // 2))


# ------------------------------------------------------------- the model


class QwenOnnxModel:
    """The ONNX sessions, the embeddings and the tokenizer. Holds no stream state."""

    def __init__(self, d: Path, threads: int = 0) -> None:
        import onnxruntime as ort

        so = ort.SessionOptions()
        if threads:
            so.intra_op_num_threads = threads
        cpu = ["CPUExecutionProvider"]
        self.encoder = ort.InferenceSession(str(d / ENCODER_FILE), so, providers=cpu)
        self.decoder = ort.InferenceSession(str(d / DECODER_FILE), so, providers=cpu)
        self.embed = np.memmap(d / EMBED_FILE, dtype=np.float16, mode="r", shape=(VOCAB, HIDDEN))
        self.tokenizer = _Tokenizer(d / TOKENIZER_FILE)
        self.layers, self.kv_heads, self.head_dim = 28, 8, 128
        if len(self.decoder.get_outputs()) < 4:
            raise RuntimeError(f"{DECODER_FILE} has no attention output")

    def encode(self, mel: np.ndarray) -> np.ndarray:
        """(tokens, 1024) audio features for one block of mel frames."""
        return self.encoder.run(None, {"mel": mel[None]})[0][0]

    def embeds(self, ids: list[int]) -> np.ndarray:
        return np.asarray(self.embed[ids], dtype=np.float32)

    def empty_cache(self) -> tuple[np.ndarray, np.ndarray]:
        z = np.zeros((self.layers, 1, self.kv_heads, 0, self.head_dim), np.float32)
        return z, z.copy()

    def extend(
        self, embeds: np.ndarray, start: int, keys: np.ndarray, values: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """(next-token logits, keys, values, attention [8, S, P+S]) after
        appending `embeds` (S, 1024) at positions start.. onto the cache."""
        s = embeds.shape[0]
        logits, k, v, attn = self.decoder.run(None, {
            "input_embeds": embeds[None],
            "position_ids": np.arange(start, start + s, dtype=np.int64)[None],
            "past_keys": keys,
            "past_values": values,
        })
        return logits[0, -1], k, v, attn


@dataclass
class OnnxStreamState:
    """One stream's state. Carries the attributes `QwenStreamer` and
    `qwen_backend._word_probabilities` read from `mlx_qwen3_asr`'s
    `StreamingState`."""

    context: str = ""
    buffer: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float32))
    audio_accum: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float32))
    window_text: str = ""
    committed_text: str = ""
    _tokenizer: Any = None
    _window_raw_ids: list[int] = field(default_factory=list)
    _ap_probs: list[float] = field(default_factory=list)
    #: Attention rows over the window's audio, one per `_window_raw_ids` entry.
    _rows: list[np.ndarray] = field(default_factory=list)
    _window_chunk_id: int = 0
    _extra_rollback: int = 0
    #: The block cache: (mel max, encoded blocks, keys, values, positions), or
    #: None. Reset by `QwenStreamer` when the context changes.
    _prefix_cache: Any = None


@dataclass
class _BlockCache:
    mel_max: float
    blocks: list[np.ndarray]
    keys: np.ndarray
    values: np.ndarray
    #: Positions the cache covers: the prompt head and every block's audio.
    positions: int


class QwenOnnxBackend(QwenStreamingBackend):
    """`QwenStreamingBackend` on the processor (see the module docstring)."""

    def __init__(self, whisper: Any = None, *, directory: Path | None = None,
                 threads: int = 0, **kwargs: Any) -> None:
        kwargs.setdefault("step_seconds", STEP_SECONDS)
        kwargs.setdefault("max_context_seconds", MAX_CONTEXT_SECONDS)
        kwargs.setdefault("unfixed_tokens", UNFIXED_TOKENS)
        super().__init__(whisper, **kwargs)  # type: ignore[arg-type]
        self._dir = directory
        self._threads = threads
        self._onnx: QwenOnnxModel | None = None
        #: The prompt head's cache, by context: (ids, keys, values).
        self._head: dict[str, tuple[list[int], np.ndarray, np.ndarray]] = {}
        self._onnx_lock = threading.Lock()
        #: The stream `align` reads: the one decoded last.
        self._aligning: OnnxStreamState | None = None

    @staticmethod
    def is_available() -> bool:
        try:
            import onnxruntime  # noqa: F401
        except Exception:
            return False
        return model_dir() is not None

    def name(self) -> str:
        return "onnx-qwen3-asr-0.6b-int8"

    def load(self) -> None:
        with self._onnx_lock:
            if self._onnx is not None:
                return
            d = self._dir or model_dir()
            if d is None:
                raise FileNotFoundError("Qwen3-ASR ONNX files are not on this computer")
            self._onnx = QwenOnnxModel(d, self._threads)

    # ------------------------------------------------------------- streaming

    def new_state(self) -> OnnxStreamState:
        assert self._onnx is not None
        return OnnxStreamState(context=self.context, _tokenizer=self._onnx.tokenizer)

    def feed(self, state: OnnxStreamState, audio: np.ndarray) -> None:
        """Decode `audio` as one step (`QwenStreamingBackend.feed`)."""
        cap = int(self.max_context_seconds * SAMPLE_RATE)
        audio = np.asarray(audio, dtype=np.float32)[-cap:]
        if len(state.audio_accum) and len(state.audio_accum) + len(audio) > cap:
            cut = _commit_cut(state.audio_accum)
            carry = state.audio_accum[cut:]
            if 0 < cut < len(state.audio_accum):
                state.audio_accum = state.audio_accum[:cut]
                state._extra_rollback = math.ceil(
                    len(carry) / SAMPLE_RATE * COMMIT_ROLLBACK_TOKENS_PER_S
                )
                self._decode(state)
                state._extra_rollback = 0
            state.committed_text = f"{state.committed_text} {state.window_text}".strip()
            state.window_text = ""
            state.audio_accum = carry
            state._window_raw_ids, state._ap_probs, state._rows = [], [], []
            state._window_chunk_id = 0
            state._prefix_cache = None
        state.audio_accum = np.concatenate([state.audio_accum, audio])
        self._decode(state)

    def _head_cache(self, context: str) -> tuple[list[int], np.ndarray, np.ndarray]:
        m = self._onnx
        assert m is not None
        if context not in self._head:
            ids = m.tokenizer.head(context)
            k, v = m.empty_cache()
            _, k, v, _ = m.extend(m.embeds(ids), 0, k, v)
            self._head.clear()  # one context at a time
            self._head[context] = (ids, k, v)
        return self._head[context]

    def _decode(self, state: OnnxStreamState) -> None:
        m = self._onnx
        assert m is not None
        tok = m.tokenizer
        audio = state.audio_accum
        if len(audio) < 2 * HOP:
            return
        mel = log_mel(audio)
        n_frames = mel.shape[1]
        n_complete = max(0, (n_frames if EAGER_BLOCKS else n_frames - 1) // BLOCK_FRAMES)
        mel_max = float(mel.max())
        head_ids, head_k, head_v = self._head_cache(state.context)
        cache: _BlockCache | None = state._prefix_cache
        if cache is None or cache.mel_max != mel_max or len(cache.blocks) > n_complete:
            cache = _BlockCache(mel_max, [], head_k, head_v, len(head_ids))
            state._prefix_cache = cache

        # Encode what isn't cached: the newly complete blocks and the tail.
        first = len(cache.blocks)
        new_blocks = [
            m.encode(mel[:, b * BLOCK_FRAMES : (b + 1) * BLOCK_FRAMES])
            for b in range(first, n_complete)
        ]
        rest = mel[:, n_complete * BLOCK_FRAMES :]
        tail = m.encode(rest) if rest.shape[1] else np.zeros((0, HIDDEN), np.float32)
        n_audio = sum(len(b) for b in cache.blocks) + sum(len(b) for b in new_blocks) + len(tail)

        if state._window_chunk_id < UNFIXED_CHUNKS:
            prefix: list[int] = []
        else:
            end = max(0, len(state._window_raw_ids) - self.unfixed_tokens - state._extra_rollback)
            while end > 0 and "�" in tok.decode(state._window_raw_ids[:end]):
                end -= 1
            prefix = state._window_raw_ids[:end]

        closing = tok.tail()
        embeds = np.concatenate(
            [*new_blocks, tail, m.embeds(closing + prefix)], axis=0
        ).astype(np.float32)
        start = cache.positions
        logits, keys, values, attn = m.extend(embeds, start, cache.keys, cache.values)

        # The blocks complete now are final: keep their audio's cache.
        if new_blocks:
            keep = start + sum(len(b) for b in new_blocks)
            cache.blocks.extend(new_blocks)
            cache.keys = np.ascontiguousarray(keys[:, :, :, :keep])
            cache.values = np.ascontiguousarray(values[:, :, :, :keep])
            cache.positions = keep

        a0 = len(head_ids)
        rows: list[np.ndarray] = (
            [attn[:, -len(prefix) + i, a0 : a0 + n_audio] for i in range(len(prefix))]
            if prefix else []
        )
        old = state._ap_probs
        probs = list(old[: len(prefix)]) if state._window_raw_ids[: len(prefix)] == prefix else []
        probs += [1.0] * (len(prefix) - len(probs))
        generated: list[int] = []
        pos = start + len(embeds)
        for _ in range(_budget(len(audio) / SAMPLE_RATE)):
            z = logits.astype(np.float64)
            token = int(np.argmax(z))
            if token in EOS_IDS:
                break
            generated.append(token)
            probs.append(float(np.exp(z[token] - (z.max() + np.log(np.exp(z - z.max()).sum())))))
            if _repeating(generated):
                rows.append(rows[-1] if rows else np.zeros((8, n_audio), np.float32))
                break
            logits, keys, values, attn = m.extend(m.embeds([token]), pos, keys, values)
            rows.append(attn[:, 0, a0 : a0 + n_audio])
            pos += 1
        raw = prefix + generated
        state._window_raw_ids, state._ap_probs, state._rows = raw, probs, rows
        state._window_chunk_id += 1
        text = tok.decode(raw)
        if "<asr_text>" in text:
            text = text.split("<asr_text>", 1)[1]
        for t in ("<|im_end|>", "<|endoftext|>"):
            text = text.replace(t, "")
        state.window_text = text.strip()
        # `QwenStreamer._step` aligns right after feeding, under the same lock
        self._aligning = state

    def align(self, audio: np.ndarray, text: str) -> list[tuple[str, float, float]]:
        """(word, start, end) within the window, from the attention rows the
        last decode kept (`_attention_times`' rule: a word starts at its
        tokens' mean attention peak)."""
        state = self._aligning
        if state is None or not state._rows:
            return []
        tok = state._tokenizer
        groups: list[tuple[str, list[int]]] = []
        for i, t in enumerate(state._window_raw_ids):
            piece = tok.decode([t])
            if not groups or (piece[:1].isspace() and groups[-1][0].strip()):
                groups.append(("", []))
            word, idx = groups[-1]
            groups[-1] = (word + piece, [*idx, i])
        words = [(w.strip().strip(string.punctuation), idx) for w, idx in groups]
        words = [(w, idx) for w, idx in words if any(c.isalnum() for c in w)]
        if not words:
            return []
        n_a = state._rows[0].shape[-1]
        spt = len(audio) / SAMPLE_RATE / max(1, n_a)
        starts = []
        for _, idx in words:
            m = sum(state._rows[i] for i in idx if i < len(state._rows))
            m = (m / (m.sum(-1, keepdims=True) + 1e-9)).mean(0)
            starts.append(float(np.argmax(m)) * spt)
        dur = len(audio) / SAMPLE_RATE
        out = []
        for i, ((w, _), st) in enumerate(zip(words, starts, strict=True)):
            nxt = starts[i + 1] if i + 1 < len(starts) else min(dur, st + 0.4)
            out.append((w, st, min(st + 1.0, max(st + 0.1, nxt))))
        return out

    def warm(self) -> None:
        self.load()
        rng = np.random.default_rng(0)
        noise = (rng.standard_normal(4 * SAMPLE_RATE) * 1e-3).astype(np.float32)
        state = self.new_state()
        self.feed(state, noise)
        if self.whisper is not None and hasattr(self.whisper, "warm"):
            self.whisper.warm()
