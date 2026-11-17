"""faster-whisper backend — batch mode.

Slice 1B ships *batch* transcription: accumulate every `AudioFrame` from
the input iterator, run `WhisperModel.transcribe()` once, and emit one
`TranscriptEvent` per Whisper segment with word-level timestamps.

This is the right shape for offline eval (replay a recording, then
matcher gets the full transcript at the end). The *streaming* wrapper
that turns this into incremental sliding-window transcription — what the
live matcher needs — lands in Slice 1C as `SlidingWindowWrapper`. That
wrapper composes any `ASRBackend`, so the streaming logic is testable
without a real model.

The model is dependency-injected so tests can pass a mock with no
network or filesystem access. The `.load()` factory is the production
path (downloads the model from Hugging Face on first call, caches under
the platform-default `~/.cache/huggingface/hub`).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Any, Protocol

import numpy as np

from auto_presenter.asr.types import TranscriptEvent, Word
from auto_presenter.audio.types import SAMPLE_RATE, AudioFrame

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass

#: Most new tokens one pass may decode, per second of audio (timestamp tokens
#: included). Whisper loops on sung audio ("please, please, ...") until its
#: 448-token limit: on the live set, small.en int8 on CPU took 12.7 s for one
#: such 6 s window (7.4 s for another) against ~1.6 s normally, far past the
#: ASR queue's 5 s. At 96 per 6 s they take 4.7 s and 3.4 s, and every normal
#: window decoded identically. Higher than MLX's cap (`mlx_backend`) because a
#: chunk cut short here is decoded again from its last timestamp: at 60, normal
#: windows came back with different words and took longer.
MAX_TOKENS_PER_S = 16.0
MIN_TOKENS = 48
#: `_transcribe_short`: seconds of silence after the window (exactly the
#: window loops on its last phrase; 2 s stopped most of it on the bench).
SHORT_CONTEXT_PAD_S = 2.0
#: `_transcribe_short`: a word starting this close to the window's end, or
#: later, is in the padding, not the singing.
SILENT_TAIL_TOLERANCE_S = 0.1


class _WhisperModelLike(Protocol):
    """Subset of faster-whisper's WhisperModel surface that we actually use."""

    def transcribe(
        self,
        audio: np.ndarray,
        *,
        word_timestamps: bool = ...,
        language: str | None = ...,
        beam_size: int = ...,
        vad_filter: bool = ...,
        temperature: float | list[float] = ...,
        max_new_tokens: int | None = ...,
    ) -> tuple[Any, Any]: ...


class WhisperBackend:
    """ASRBackend that wraps a `faster_whisper.WhisperModel`."""

    def __init__(
        self,
        model: _WhisperModelLike,
        *,
        language: str | None = "en",
        #: Greedy, not beam search. Measured 2026-08-29 on two goldens (base.en,
        #: 6 s window, int8, M1), beam 1 against the beam 5 that shipped before:
        #: pass time 737 ms -> 649 ms and **WER 0.357 -> 0.220**. Better on both
        #: axes, which is unusual enough to explain.
        #:
        #: Beam search scores whole candidate sequences under the decoder's
        #: implicit language model, so it prefers fluent English. Sung worship
        #: lyrics are repetitive, often archaic, and arrive over instrumental
        #: backing — the fluency prior pulls the transcript toward plausible
        #: prose and away from what was actually sung. Greedy decoding keeps the
        #: acoustic model's own answer, which is the one the matcher wants.
        #:
        #: The saving compounds: `SlidingWindowWrapper` paces passes off measured
        #: pass time, so a millisecond of pass time costs about 1.75 ms of
        #: end-to-end lag. See Plan/ENCODER_ALIGNMENT.md.
        beam_size: int = 1,
        vad_filter: bool = False,
        #: One decode attempt, not up to six.
        #:
        #: faster-whisper defaults to `temperature=[0, .2, .4, .6, .8, 1.0]` and
        #: silently re-decodes a segment at the next temperature whenever it
        #: trips `compression_ratio_threshold` or `log_prob_threshold`. Sung
        #: audio over instrumental backing trips them routinely, so what looks
        #: like one pass is sometimes six. Measured over 40 windows (int8, M1):
        #:
        #:                        p50      p90      max    words
        #:   tiny.en default    542 ms  1521 ms  4240 ms    213
        #:   tiny.en temp 0     473 ms   553 ms   648 ms    212
        #:   base.en default    903 ms  1219 ms  1854 ms    218
        #:   base.en temp 0     880 ms  1080 ms  1422 ms    215
        #:
        #: Essentially the same words for a 6.5x smaller tail. The tail is what
        #: matters, because `SlidingWindowWrapper` paces the next pass off how
        #: long the last one took (`_adaptive_step = elapsed * pace_safety`), so
        #: a single 4.2 s pass blocks the following pass for 6.4 s of audio.
        #: One spike does not cost one spike; it costs a hole in the transcript.
        temperature: float | list[float] = 0.0,
        model_name: str = "whisper",
    ) -> None:
        self._model = model
        self._language = language
        self._beam_size = beam_size
        self._vad_filter = vad_filter
        self._temperature = temperature
        self._model_name = model_name
        #: Encode the window plus this many seconds of silence instead of
        #: padding it to 30 s (None: pad, as faster-whisper does). See
        #: `_transcribe_short`. Set per session (`asr_short_context`).
        self.short_context_s: float | None = None

    @staticmethod
    def load(
        model_size: str = "tiny.en",
        *,
        device: str = "auto",
        compute_type: str = "default",
        language: str | None = "en",
        beam_size: int = 1,
        vad_filter: bool = False,
        temperature: float | list[float] = 0.0,
    ) -> "WhisperBackend":
        """Construct a backend with a freshly-loaded faster-whisper model.

        This is the production entrypoint; on first call faster-whisper
        downloads the model from Hugging Face (~75 MB for `tiny.en`, up to
        ~3 GB for `large-v3`). Subsequent calls hit the local cache.
        Tests should construct `WhisperBackend(mock)` directly instead.
        """
        # Local import so `import auto_presenter` doesn't pay the
        # faster-whisper import cost when only types are needed.
        from faster_whisper import WhisperModel as _WhisperModel

        model = _WhisperModel(model_size, device=device, compute_type=compute_type)
        return WhisperBackend(
            model,
            language=language,
            beam_size=beam_size,
            vad_filter=vad_filter,
            temperature=temperature,
            model_name=f"whisper-{model_size}",
        )

    def name(self) -> str:
        return self._model_name

    def transcribe(
        self, frames: Iterable[AudioFrame]
    ) -> Iterator[TranscriptEvent]:
        chunks = list(frames)
        if not chunks:
            return iter(())

        # Splice frames into a single mono float32 array. Whisper expects
        # samples in [-1, 1] at 16 kHz, which is exactly the AudioFrame
        # contract from `auto_presenter.audio.types`.
        audio = np.concatenate([f.samples for f in chunks]).astype(np.float32, copy=False)
        t_origin = chunks[0].t_start

        if self.short_context_s is not None and hasattr(self._model, "feature_extractor"):
            return self._transcribe_short(audio, t_origin)

        segments, _info = self._model.transcribe(
            audio,
            word_timestamps=True,
            language=self._language,
            beam_size=self._beam_size,
            vad_filter=self._vad_filter,
            temperature=self._temperature,
            max_new_tokens=max(MIN_TOKENS, int(MAX_TOKENS_PER_S * len(audio) / SAMPLE_RATE)),
        )

        return _convert_segments(segments, t_origin=t_origin)

    def _transcribe_short(self, audio: np.ndarray, t_origin: float) -> Iterator[TranscriptEvent]:
        """One pass over the window alone, not a 30 s pad of it.

        faster-whisper pads every window to Whisper's 30 s, so the encoder,
        the heavy part, spends 80% of a 6 s window's pass on silence; the
        CTranslate2 encoder takes the short input as it is. Measured on an
        M1's CPU (scripts/bench_whisper.py): small.en 1265 -> 366 ms a pass,
        base.en 400 -> 124 ms. Decoding from exactly the window repeats its
        last phrase (the decoder never hears the audio end), so it gets
        `short_context_s` of trailing silence and a token cap. Greedy and
        without timestamps; word times come from the same alignment
        faster-whisper uses for its own word timestamps.
        """
        from faster_whisper.tokenizer import Tokenizer

        fw = self._model
        padded = np.concatenate(
            [audio, np.zeros(int(self.short_context_s * SAMPLE_RATE), np.float32)]
        )
        n_frames = round(len(padded) / SAMPLE_RATE * 100)
        features = fw.feature_extractor(padded)[:, :n_frames]
        encoded = fw.encode(features)
        tok = Tokenizer(
            fw.hf_tokenizer, fw.model.is_multilingual, task="transcribe", language=self._language
        )
        prompt = [*tok.sot_sequence, tok.no_timestamps]
        n_new = max(MIN_TOKENS, int(MAX_TOKENS_PER_S * len(audio) / SAMPLE_RATE))
        result = fw.model.generate(
            encoded,
            [prompt],
            beam_size=1,
            max_length=len(prompt) + n_new,
            suppress_blank=True,
            return_no_speech_prob=True,
        )[0]
        tokens = [t for t in result.sequences_ids[0] if t < tok.eot]
        if not tokens:
            return iter(())
        aligned = fw.find_alignment(tok, [tokens], encoded, n_frames)[0]
        audio_s = len(audio) / SAMPLE_RATE
        no_speech = _float_or_none(getattr(result, "no_speech_prob", None))
        words = [
            Word(
                text=w["word"].strip(),
                t_start=t_origin + float(w["start"]),
                t_end=t_origin + min(float(w["end"]), audio_s),
                confidence=float(w["probability"]),
                no_speech=no_speech,
            )
            for w in aligned
            # a word aligned into the trailing silence was never sung: it is
            # the decoder repeating the window's last phrase
            if w["word"].strip() and float(w["start"]) < audio_s - SILENT_TAIL_TOLERANCE_S
        ]
        if not words:
            return iter(())
        return iter((TranscriptEvent(words=words, is_final=True, segment_id=0),))


def _float_or_none(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _convert_segments(
    segments: Iterable[Any], *, t_origin: float
) -> Iterator[TranscriptEvent]:
    """Translate faster-whisper segments into our `TranscriptEvent` shape.

    Whisper produces timestamps relative to the start of the supplied
    audio buffer. We offset by `t_origin` so the events sit on the same
    monotonic clock as the input frames (and therefore the eval ground
    truth).
    """
    for segment_idx, segment in enumerate(segments):
        words: list[Word] = []
        for w in getattr(segment, "words", None) or []:
            text = (getattr(w, "word", "") or "").strip()
            if not text:
                continue
            words.append(
                Word(
                    text=text,
                    t_start=t_origin + float(getattr(w, "start", 0.0)),
                    t_end=t_origin + float(getattr(w, "end", 0.0)),
                    confidence=float(getattr(w, "probability", 1.0)),
                    no_speech=_float_or_none(getattr(segment, "no_speech_prob", None)),
                )
            )
        if not words:
            continue
        yield TranscriptEvent(
            words=words,
            is_final=True,
            segment_id=segment_idx,
        )
