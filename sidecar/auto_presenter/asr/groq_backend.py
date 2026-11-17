"""Groq-hosted Whisper backend.

The ASR analogue of `decision.instructions`'s `GenericLLMBackend`: wrap a
hosted API behind the exact `ASRBackend` Protocol (`asr.backend`) the local
`WhisperBackend` already satisfies, so `SlidingWindowWrapper` — and
everything above it — doesn't know or care which one it's driving.

The two hosted wrappers built this session face very different traffic
shapes, though. `GenericLLMBackend.decide()` runs once per detected
utterance, maybe a handful of times a minute — a network round trip there
is nearly free. `SlidingWindowWrapper` calls a backend's `transcribe()`
roughly once per second, continuously, for the length of a service. This
backend's viability as the *live* engine rests entirely on whether a 6 s
clip's upload-plus-inference round trip fits inside that ~1 s cadence, not
on Groq's advertised speed for one-off calls. See
`scripts/bench_groq_whisper.py` for the measured number before trusting
this as more than an offline/eval backend.
"""

from __future__ import annotations

import io
import logging
import time
import urllib.error
import wave
from collections.abc import Iterable, Iterator

import numpy as np

from auto_presenter.asr.types import TranscriptEvent, Word
from auto_presenter.audio.types import AudioFrame
from auto_presenter.http_client import REQUEST_ERRORS, open_multipart_post, read_json

log = logging.getLogger(__name__)


class GroqWhisperBackend:
    """`ASRBackend` that sends audio to Groq's hosted Whisper endpoint.

    Failures (network, timeout, rate limit) log a warning and yield nothing
    for that pass rather than raising — `SlidingWindowWrapper` treats an
    empty pass as "no new words yet," which is the right degradation for a
    backend on the far side of a network call. Crashing the pipeline over
    one dropped HTTP request would be a worse outcome than a gap in the
    transcript.

    It also keeps to Groq's rate limit. The free tier allows 20 requests a
    minute for Whisper, and the wrapper used to ask once a second: two calls
    in three came back 429, and a live service (2026-10-13) was left with a
    transcript too thin to follow a song after it had switched to it. Asked
    every `min_step_seconds` instead, the 6 s window still overlaps each pass
    by 3 s, so no sung word falls between passes. A 429 anyway (another app
    on the key, the daily cap) pauses calls for the server's `retry-after`.
    """

    #: Read by `SlidingWindowWrapper`: run a pass no more often than this.
    #: 60 s / 20 requests per minute, the free tier's limit (and 6 s x 20 a
    #: minute is its 7,200 audio seconds an hour, exactly).
    min_step_seconds: float = 3.0

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "whisper-large-v3-turbo",
        base_url: str = "https://api.groq.com/openai/v1",
        language: str | None = "en",
        timeout_seconds: float = 8.0,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._language = language
        self._timeout_seconds = timeout_seconds
        #: No calls before this (monotonic seconds): set by a 429.
        self._blocked_until = 0.0

    @property
    def refusing(self) -> bool:
        """Groq is turning requests away (rate limit), so nothing is being
        transcribed: reported in pipeline health, for the operator."""
        return time.monotonic() < self._blocked_until

    def name(self) -> str:
        return f"groq-{self._model}"

    def transcribe(
        self, frames: Iterable[AudioFrame]
    ) -> Iterator[TranscriptEvent]:
        chunks = list(frames)
        if not chunks:
            return iter(())

        audio = np.concatenate([f.samples for f in chunks]).astype(np.float32, copy=False)
        t_origin = chunks[0].t_start
        wav_bytes = _encode_wav(audio, sample_rate=chunks[0].sample_rate)

        now = time.monotonic()
        if now < self._blocked_until:
            return iter(())
        try:
            response = self._call(wav_bytes)
        except urllib.error.HTTPError as exc:
            if exc.code != 429:
                log.warning("groq whisper call failed: %s", exc)
                return iter(())
            wait = _retry_after(exc)
            self._blocked_until = now + wait
            log.warning(
                "groq is refusing requests (rate limit); pausing transcription for %.0f s", wait
            )
            return iter(())
        except REQUEST_ERRORS as exc:
            # Same set as every other client here: a non-JSON body (proxy
            # error page, truncated response) must degrade to "no words this
            # window", not raise into the capture thread.
            log.warning("groq whisper call failed: %s", exc)
            return iter(())

        return _convert_response(response, t_origin=t_origin)

    # ------------------------------------------------------------- internals

    def _call(self, wav_bytes: bytes) -> dict:
        fields = {
            "model": self._model,
            "response_format": "verbose_json",
            "timestamp_granularities[]": "word",
        }
        if self._language:
            fields["language"] = self._language
        with open_multipart_post(
            f"{self._base_url}/audio/transcriptions",
            fields,
            file_field="file", filename="audio.wav",
            file_content_type="audio/wav", file_bytes=wav_bytes,
            api_key=self._api_key, timeout_seconds=self._timeout_seconds,
        ) as resp:
            return read_json(resp)


def _retry_after(exc: urllib.error.HTTPError) -> float:
    """Seconds the server asked us to wait, within reason: a missing or
    unreadable header waits one pacing step, and a long wait (the daily cap)
    is retried every minute rather than abandoned for the service."""
    try:
        wait = float(exc.headers.get("retry-after", ""))
    except (TypeError, ValueError, AttributeError):
        wait = GroqWhisperBackend.min_step_seconds
    return min(max(wait, 1.0), 60.0)


def _encode_wav(samples: np.ndarray, *, sample_rate: int) -> bytes:
    """16 kHz mono float32 in [-1, 1] -> 16-bit PCM WAV bytes."""
    pcm16 = np.clip(samples, -1.0, 1.0)
    pcm16 = (pcm16 * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm16.tobytes())
    return buf.getvalue()


def _convert_response(response: dict, *, t_origin: float) -> Iterator[TranscriptEvent]:
    """Groq's `verbose_json` + word granularity: top-level `words: [{word,
    start, end}]`. No per-segment structure at this granularity, so the
    whole response becomes one event — `SlidingWindowWrapper` only cares
    about the flattened word list anyway."""
    raw_words = response.get("words") or []
    words: list[Word] = []
    for w in raw_words:
        text = str(w.get("word", "")).strip()
        if not text:
            continue
        words.append(
            Word(
                text=text,
                t_start=t_origin + float(w.get("start", 0.0)),
                t_end=t_origin + float(w.get("end", 0.0)),
                confidence=1.0,  # Groq's API doesn't expose per-word confidence.
            )
        )
    if not words:
        return iter(())
    return iter((TranscriptEvent(words=words, is_final=True, segment_id=0),))
