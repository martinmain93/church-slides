"""Online speech for computers without Apple silicon (docs/roadmap/HOSTED_SPEECH.md).

Sends the sliding window to an OpenAI-compatible transcription endpoint
(`POST {base_url}/audio/transcriptions`, word timestamps). The organization's
server hands out the endpoint, the model and a short-lived bearer token, so
nothing here knows which provider runs it.

Failures never reach the room:

1. A failed call is retried, silently, on the next passes for `RETRY_S`.
2. Then, if there is one, the computer's own engine (`fallback`, Whisper on
   the processor) takes over, and the online service is tried again with
   backoff (2 s, 4 s, 8 s ... capped at `MAX_BACKOFF_S`) until it answers.
3. With no fallback, `offline` turns true: the session blanks the screen and
   the same backoff keeps trying.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass

import numpy as np

from auto_presenter.asr.groq_backend import _convert_response, _encode_wav
from auto_presenter.asr.types import TranscriptEvent
from auto_presenter.audio.types import AudioFrame
from auto_presenter.http_client import REQUEST_ERRORS, open_multipart_post, read_json

log = logging.getLogger(__name__)

#: Silent retries before falling back (seconds of failing calls).
RETRY_S = 3.0
FIRST_BACKOFF_S = 2.0
MAX_BACKOFF_S = 60.0
#: A slow answer is as bad as none: the window has moved on.
TIMEOUT_S = 4.0


@dataclass(frozen=True)
class HostedCredentials:
    base_url: str
    model: str
    token: str


class HostedSpeechBackend:
    """`ASRBackend` over an OpenAI-compatible transcription endpoint."""

    #: Read by `SlidingWindowWrapper`: one call at a time, about one a second.
    min_step_seconds: float = 1.0

    def __init__(
        self,
        credentials: Callable[[], HostedCredentials | None],
        *,
        fallback=None,
        language: str | None = "en",
        timeout_seconds: float = TIMEOUT_S,
        clock: Callable[[], float] = time.monotonic,
        call: Callable[[HostedCredentials, bytes], dict] | None = None,
    ) -> None:
        self._credentials = credentials
        self._fallback = fallback
        self._language = language
        self._timeout_seconds = timeout_seconds
        self._clock = clock
        self._call_impl = call or self._post
        self._failing_since: float | None = None
        self._backoff = 0.0
        self._next_try = 0.0
        self.calls = 0
        self.failures = 0

    # ---------------------------------------------------------------- state

    def _given_up(self, now: float) -> bool:
        return self._failing_since is not None and now - self._failing_since >= RETRY_S

    @property
    def falling_back(self) -> bool:
        """Online is down and the computer's own engine is listening."""
        return self._fallback is not None and self._given_up(self._clock())

    @property
    def offline(self) -> bool:
        """Online is down and nothing else can listen: blank the screen."""
        return self._fallback is None and self._given_up(self._clock())

    @property
    def refusing(self) -> bool:  # pipeline health (`audio_session`)
        return self._given_up(self._clock())

    def name(self) -> str:
        creds = self._credentials()
        return f"hosted-{creds.model if creds else 'none'}"

    def warm(self) -> None:
        if self._fallback is not None and hasattr(self._fallback, "warm"):
            self._fallback.warm()

    # ----------------------------------------------------------- transcribe

    def transcribe(self, frames: Iterable[AudioFrame]) -> Iterator[TranscriptEvent]:
        chunks = list(frames)
        if not chunks:
            return iter(())
        now = self._clock()
        if now >= self._next_try:
            creds = self._credentials()
            if creds is not None:
                audio = np.concatenate([f.samples for f in chunks]).astype(np.float32, copy=False)
                wav = _encode_wav(audio, sample_rate=chunks[0].sample_rate)
                self.calls += 1
                try:
                    response = self._call_impl(creds, wav)
                except REQUEST_ERRORS as exc:
                    self._failed(self._clock(), exc)
                else:
                    if self._failing_since is not None:
                        log.info("online speech is answering again")
                    self._failing_since, self._backoff, self._next_try = None, 0.0, 0.0
                    return _convert_response(response, t_origin=chunks[0].t_start)
            else:
                self._failed(now, "no speech key")
        if self._given_up(self._clock()) and self._fallback is not None:
            return self._fallback.transcribe(iter(chunks))
        return iter(())

    def _failed(self, now: float, why) -> None:
        self.failures += 1
        if self._failing_since is None:
            self._failing_since = now
            log.warning("online speech call failed (%s); retrying", why)
        if now - self._failing_since < RETRY_S:
            self._next_try = now  # retry on the next pass
            return
        if self._backoff == 0.0:
            log.warning(
                "online speech is down; %s",
                "the computer's own engine takes over" if self._fallback else "blanking",
            )
        self._backoff = min(MAX_BACKOFF_S, max(FIRST_BACKOFF_S, self._backoff * 2))
        self._next_try = now + self._backoff * random.uniform(0.8, 1.2)

    def _post(self, creds: HostedCredentials, wav: bytes) -> dict:
        fields = {
            "model": creds.model,
            "response_format": "verbose_json",
            "timestamp_granularities[]": "word",
        }
        if self._language:
            fields["language"] = self._language
        with open_multipart_post(
            f"{creds.base_url.rstrip('/')}/audio/transcriptions",
            fields,
            file_field="file", filename="audio.wav",
            file_content_type="audio/wav", file_bytes=wav,
            api_key=creds.token, timeout_seconds=self._timeout_seconds,
        ) as resp:
            return read_json(resp)
