"""Qwen3-ASR 0.6B, streamed incrementally on Apple silicon. Opt-in.

Off by default: `ParticleFilterTunables.asr_qwen_streaming` turns it on for an
MLX session. Needs the `qwen` extra (`mlx-qwen3-asr`, Apache 2.0).

**Why it exists.** The research spike (docs/research/QWEN3_ASR_IMPROVEMENTS.md)
measured Qwen3-ASR 0.6B, run through the package's own incremental streaming,
against the app's MLX Whisper small.en on six public sung songs: WER 0.406 vs
0.452, and words final 2.14 s after they were sung vs 3.06 s, at ~70% of an M1
busy. Those are speech-recognition numbers; whether slides land sooner is the
real-time gate's question (`research/eval/run_follower_eval.py`).

**How it differs from Whisper's path.** `SlidingWindowWrapper` re-runs a
whole 6 s window from scratch every step and calls words stable once they are
1.5 s from the window's end. `mlx_qwen3_asr.streaming` instead grows one
window (up to `max_context_seconds`), keeps the encoder output of each
finished 8 s encoder block and the decoder's cache, and re-decodes only the
tail with the previous text forced in as a prefix. So it emits words itself,
through `QwenStreamer`, which stands where `SlidingWindowWrapper` stands in
`AudioSession`:

- every step (1 s of audio, longer when a step ran slow) the new audio is fed
  in and the live window is re-aligned with Qwen3-ForcedAligner for word times;
- every word the aligner returns past the final ones is emitted as a partial;
- a word is final once two consecutive steps agree on it and on every word
  before it (LocalAgreement-2), or when the package commits the window.

That loop is `research/spikes/qwen_improve/incremental_eval.py` (`run_clip`),
measured there.

**The follower still uses Whisper.** The score follower's span scorer, the
song-openings listener and the next-line check are built on Whisper small.en
(the next-line check's phone model was trained on its encoder), so this
backend carries the session's `MLXWhisperBackend` as `whisper` and they use
that. Both models share the GPU under `mlx_backend.GPU_LOCK`.

The aligner's Korean and Japanese word splitting needs `soynlp` (GPL-3.0) and
`nagisa`, the package's `[aligner]` extra; English needs neither, and this
only ever aligns English, so the extra is not installed.
"""

from __future__ import annotations

import logging
import os
import string
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from auto_presenter.asr.junk import JunkFilter
from auto_presenter.asr.mlx_backend import (
    GPU_LOCK,
    MLXWhisperBackend,
    _configure_mlx_memory,
    local_snapshot,
)
from auto_presenter.asr.types import TranscriptEvent, Word
from auto_presenter.audio.types import SAMPLE_RATE, AudioFrame

log = logging.getLogger(__name__)

QWEN_REPO = "Qwen/Qwen3-ASR-0.6B"
ALIGNER_REPO = "Qwen/Qwen3-ForcedAligner-0.6B"

#: Steps of new audio between decodes. 0.5 s was worse (WER 0.448) and fell
#: behind real time at fp16 on an M1; 1 s is the spike's measured setting.
STEP_SECONDS = 1.0
#: Longest live window before the package commits it and starts a new one.
MAX_CONTEXT_SECONDS = 10.0
#: Tokens at the end of the previous text left unforced on the next decode.
UNFIXED_TOKENS = 5
#: A gap this long in the frames' clock (transcription paused, then resumed)
#: ends the stream: the old window's words are flushed and a new one starts,
#: rather than stitching audio from either side of the pause into one window.
GAP_RESET_SECONDS = 0.5
#: Audio kept for the side pass (the follower's span scorer), the same 6 s
#: window it gets from `SlidingWindowWrapper`.
SIDE_WINDOW_SECONDS = 6.0
#: A word goes final only once its aligned end is at least this far behind the
#: newest audio. Qwen3-ForcedAligner squeezes words it has barely heard
#: against the window's end: in the app's loop on the six spike songs, 29% of
#: final words came out under 50 ms long and 43% of those sat >1 s from where
#: they were sung; words finalised within 1.5 s of their end were 47% off.
#: 0 is the spike's rule (text agreement alone). Read from the environment for
#: the real-time eval only, until it picks a value.
EDGE_MARGIN_SECONDS = float(os.environ.get("AUTO_PRESENTER_QWEN_EDGE_MARGIN", "0") or 0)

#: Trial: a word goes final the first step it is heard, without waiting for a
#: second step to agree, once its aligned end is at least this far behind the
#: newest audio. The matcher reads final words only, and agreement costs a
#: whole step (Qwen first hears a word ~1.1 s before it agrees). `None` keeps
#: LocalAgreement-2. Read from the environment for quick trials only.
_FIRST = os.environ.get("AUTO_PRESENTER_QWEN_FIRST_HEARING", "")
FIRST_HEARING_SECONDS: float | None = float(_FIRST) if _FIRST else None

#: Trial: when a window's unconfirmed words are flushed (commit, pause, end),
#: drop the trailing run the aligner squeezed under this many seconds each:
#: text the decoder wrote past what it heard. 0 is off.
SQUEEZED_SECONDS = float(os.environ.get("AUTO_PRESENTER_QWEN_DROP_SQUEEZED", "0") or 0)


def _drop_squeezed(words: list[_Aligned]) -> list[_Aligned]:
    if SQUEEZED_SECONDS <= 0:
        return words
    end = len(words)
    while end > 0 and words[end - 1].t_end - words[end - 1].t_start < SQUEEZED_SECONDS:
        end -= 1
    return words[:end]


_SENTENCE_END = (".", "?", "!")


def _norm(text: str) -> str:
    """Casefold and drop punctuation, so step-to-step agreement ignores it."""
    return "".join(c for c in text.lower() if c.isalnum() or c == "'")


@dataclass(frozen=True)
class _Aligned:
    text: str
    t_start: float
    t_end: float
    #: The raw decoded token ended a sentence (".", "?", "!").
    sentence_end: bool = False


def _sentence_ends(text: str, n_words: int) -> list[bool]:
    """Which aligned words end a sentence, from the decoded text's punctuation.

    The aligner returns bare words; the text it aligned carries the
    punctuation. English splits on spaces the same way, so when the counts
    match each word lines up with its raw token. When they don't (a token
    that was all punctuation, say), nothing is marked: an unmarked boundary
    is what every other backend without segments reports too.
    """
    raw = [t for t in text.split() if any(c.isalnum() for c in t)]
    if len(raw) != n_words:
        return [False] * n_words
    return [t.rstrip(string.whitespace + "\"')]").endswith(_SENTENCE_END) for t in raw]


class QwenStreamingBackend:
    """Qwen3-ASR 0.6B + Qwen3-ForcedAligner on MLX, streamed incrementally.

    `transcribe()` satisfies `ASRBackend` for one-off use (it streams the
    frames it is given and returns the final words); a live session uses
    `streamer()` in place of `SlidingWindowWrapper`.
    """

    def __init__(
        self,
        whisper: MLXWhisperBackend | None = None,
        *,
        step_seconds: float = STEP_SECONDS,
        max_context_seconds: float = MAX_CONTEXT_SECONDS,
        unfixed_tokens: int = UNFIXED_TOKENS,
        edge_margin_seconds: float = EDGE_MARGIN_SECONDS,
        first_hearing_seconds: float | None = FIRST_HEARING_SECONDS,
    ) -> None:
        #: The session's Whisper, for the follower's scorer and next-line check.
        self.whisper = whisper
        self.step_seconds = step_seconds
        self.max_context_seconds = max_context_seconds
        self.unfixed_tokens = unfixed_tokens
        self.edge_margin_seconds = edge_margin_seconds
        self.first_hearing_seconds = first_hearing_seconds
        #: Text Qwen3-ASR is told to expect (its system prompt): the words the
        #: service will sing. Read by every new stream; "" for none.
        self.context = ""
        self._model: Any = None
        self._model_path: str = QWEN_REPO
        self._aligner: Any = None
        self._load_lock = threading.Lock()

    @staticmethod
    def is_available() -> bool:
        """Whether `mlx_qwen3_asr` imports here (Apple silicon, the `qwen` extra)."""
        if os.environ.get("AUTO_PRESENTER_DISABLE_MLX") == "1":
            return False
        try:
            import mlx_qwen3_asr  # noqa: F401
        except Exception:
            return False
        return True

    def name(self) -> str:
        return "mlx-qwen3-asr-0.6b"

    # ------------------------------------------------------------- loading

    def load(self) -> None:
        """Load the speech model and the aligner (once; downloads if missing)."""
        with self._load_lock:
            if self._model is not None and self._aligner is not None:
                return
            from mlx_qwen3_asr import ForcedAligner
            from mlx_qwen3_asr.load_models import load_model

            _configure_mlx_memory()
            with GPU_LOCK:
                self._model_path = local_snapshot(QWEN_REPO)
                self._model, _ = load_model(self._model_path)
                aligner = ForcedAligner(model_path=local_snapshot(ALIGNER_REPO))
                aligner._ensure_loaded()
                self._aligner = aligner

    def warm(self) -> None:
        """Load both models and run one step, off the sung-word path.

        Also warms the session's Whisper: its first pass sets `FIRST_PASS`,
        which the next-line check waits for, and compiles its kernels for the
        span scorer.
        """
        self.load()
        rng = np.random.default_rng(0)
        noise = (rng.standard_normal(2 * SAMPLE_RATE) * 1e-3).astype(np.float32)
        frames = [
            AudioFrame(samples=noise[i : i + SAMPLE_RATE], t_start=i / SAMPLE_RATE,
                       t_end=(i + SAMPLE_RATE) / SAMPLE_RATE)
            for i in range(0, len(noise), SAMPLE_RATE)
        ]
        list(self.streamer().transcribe(frames))
        if self.whisper is not None:
            self.whisper.warm()

    # ------------------------------------------------------------- streaming

    def new_state(self) -> Any:
        from mlx_qwen3_asr.streaming import init_streaming

        return init_streaming(
            model=self._model_path,
            chunk_size_sec=self.step_seconds,
            max_context_sec=self.max_context_seconds,
            language="English",
            unfixed_token_num=self.unfixed_tokens,
            context=self.context,
        )

    def feed(self, state: Any, audio: np.ndarray) -> None:
        """Decode `audio` as ONE step, however long it is.

        The package decodes its buffer one `chunk_size` at a time, so 2 s fed
        after a slow step would cost two decodes and fall further behind. A
        step that ran slow is followed by a longer one instead, as
        `SlidingWindowWrapper` paces Whisper.
        """
        from mlx_qwen3_asr.streaming import feed_audio

        cap = int(self.max_context_seconds * SAMPLE_RATE)
        if len(audio) > cap:  # never more than one window at once
            audio = audio[-cap:]
        state.chunk_size_samples = max(1, len(audio))
        feed_audio(audio, state, model=self._model)

    def align(self, audio: np.ndarray, text: str) -> list[tuple[str, float, float]]:
        return [
            (w.text, float(w.start_time), float(w.end_time))
            for w in self._aligner.align(audio, text, "English")
            if w.text.strip()
        ]

    def streamer(self, **kwargs: Any) -> QwenStreamer:
        """What `AudioSession` runs in place of `SlidingWindowWrapper`."""
        return QwenStreamer(self, **kwargs)

    def transcribe(self, frames: Iterable[AudioFrame]) -> Iterator[TranscriptEvent]:
        return (e for e in self.streamer().transcribe(frames) if e.is_final)


@dataclass
class QwenStreamer:
    """Streams `QwenStreamingBackend`'s words; `SlidingWindowWrapper`'s stand-in.

    Takes the same hooks the wrapper does (`side_pass`, `pass_timer`,
    `emission_observer`) and paces itself the same way: a step that took
    longer than the audio it covered makes the next step longer, never a
    queue of steps.
    """

    backend: QwenStreamingBackend
    emission_observer: Callable[[TranscriptEvent, float], None] | None = None
    pass_timer: Callable[[], AbstractContextManager[object]] | None = None
    side_pass: Callable[[list[AudioFrame], float], None] | None = None
    side_pass_seconds: float = 2.0
    pace_safety: float = 1.5
    junk: JunkFilter = field(default_factory=JunkFilter)

    def name(self) -> str:
        return f"streaming({self.backend.name()})"

    def transcribe(self, frames: Iterable[AudioFrame]) -> Iterator[TranscriptEvent]:
        self._reset()
        side: list[AudioFrame] = []
        pending: list[np.ndarray] = []
        pending_n = 0
        expected_t: float | None = None
        last_side_t = float("-inf")
        side_debt = 0.0
        adaptive = 0.0
        for frame in frames:
            if expected_t is not None and frame.t_start - expected_t > GAP_RESET_SECONDS:
                # paused and resumed: finish the old stream, start a new one
                if pending_n:
                    yield from self._step(np.concatenate(pending), expected_t)
                    pending, pending_n = [], 0
                yield from self._flush(expected_t)
                self._reset()
                side = []
            if self._origin is None:
                self._origin = frame.t_start
            expected_t = frame.t_end
            side.append(frame)
            while side and frame.t_end - side[0].t_start > SIDE_WINDOW_SECONDS:
                side.pop(0)
            pending.append(np.asarray(frame.samples, dtype=np.float32))
            pending_n += len(frame.samples)

            if self.side_pass is not None and frame.t_end - last_side_t >= self.side_pass_seconds:
                last_side_t = frame.t_end
                started = time.monotonic()
                try:
                    self.side_pass(side, frame.t_end)
                except Exception:
                    log.exception("side pass failed; transcription carries on")
                side_debt += time.monotonic() - started

            if pending_n / SAMPLE_RATE < max(self.backend.step_seconds, adaptive):
                continue
            started = time.monotonic()
            yield from self._step(np.concatenate(pending), frame.t_end)
            pending, pending_n = [], 0
            adaptive = (time.monotonic() - started + side_debt) * self.pace_safety
            side_debt = 0.0
        if expected_t is not None:
            if pending_n:
                yield from self._step(np.concatenate(pending), expected_t)
            yield from self._flush(expected_t)

    # ------------------------------------------------------------- internals

    def _reset(self) -> None:
        self._state: Any = None
        self._origin: float | None = None
        self._fed = 0  # samples fed into this stream
        self._prev: list[_Aligned] = []
        self._final = 0  # words of `_prev` already emitted final
        self._segment_id = getattr(self, "_segment_id", 0)
        self._last_pass_seconds = 0.0

    def _window_start(self) -> float:
        st = self._state
        assert self._origin is not None
        return self._origin + (self._fed - len(st.buffer) - len(st.audio_accum)) / SAMPLE_RATE

    def _step(self, audio: np.ndarray, end_t: float) -> Iterator[TranscriptEvent]:
        b = self.backend
        started = time.monotonic()
        timer = self.pass_timer() if self.pass_timer is not None else nullcontext()
        try:
            with timer, GPU_LOCK:
                if self._state is None:
                    self._state = b.new_state()
                elif self._state.context != b.context:
                    # a new prompt invalidates the cached prompt + audio prefix
                    self._state.context = b.context
                    self._state._prefix_cache = None
                before = self._window_start()
                b.feed(self._state, audio)
                self._fed += len(audio)
                start = self._window_start()
                committed = start != before
                text = self._state.window_text.strip()
                aligned = b.align(self._state.audio_accum, text) if text else []
        except Exception as exc:
            # a failed step is a gap, never an exception into the ASR thread
            log.warning("qwen step failed: %s", exc)
            return
        self._last_pass_seconds = time.monotonic() - started
        available_at = end_t + self._last_pass_seconds

        if committed:
            # the package froze the old window: what it still held is final
            yield from self._emit(
                _drop_squeezed(self._prev[self._final:]), True, available_at, ends_segment=True
            )
            self._prev, self._final = [], 0

        ends = _sentence_ends(text, len(aligned))
        words = [
            _Aligned(t, start + s, start + e, end)
            for (t, s, e), end in zip(aligned, ends, strict=True)
        ]
        settled = start + len(self._state.audio_accum) / SAMPLE_RATE - b.edge_margin_seconds
        agree = 0
        while (
            agree < min(len(words), len(self._prev))
            and _norm(words[agree].text) == _norm(self._prev[agree].text)
            and (b.edge_margin_seconds <= 0 or words[agree].t_end <= settled)
        ):
            agree += 1
        if b.first_hearing_seconds is not None:
            heard = start + len(self._state.audio_accum) / SAMPLE_RATE - b.first_hearing_seconds
            agree = max(agree, self._final)
            while agree < len(words) and words[agree].t_end <= heard:
                agree += 1
        if agree > self._final:
            yield from self._emit(words[self._final : agree], True, available_at)
            self._final = agree
        # Words before `_final` were emitted from the last step's alignment;
        # keep those, so a final word's time never moves under the matcher.
        self._prev = self._prev[: self._final] + words[self._final :]
        if len(words) > self._final:
            yield from self._emit(words[self._final :], False, available_at)

    def _flush(self, end_t: float) -> Iterator[TranscriptEvent]:
        if self._state is not None:
            yield from self._emit(
                _drop_squeezed(self._prev[self._final:]), True, end_t + self._last_pass_seconds,
                ends_segment=True,
            )
        self._prev, self._final = [], 0

    def _emit(
        self, words: list[_Aligned], final: bool, available_at: float, *, ends_segment: bool = False
    ) -> Iterator[TranscriptEvent]:
        out = self.junk.keep([Word(text=w.text, t_start=w.t_start, t_end=w.t_end) for w in words])
        if not out:
            return
        event = TranscriptEvent(
            words=out,
            is_final=final,
            segment_id=self._segment_id,
            ends_segment=final and (ends_segment or words[-1].sentence_end),
        )
        if self.emission_observer is not None:
            self.emission_observer(event, available_at)
        yield event
        if final:
            self._segment_id += 1
