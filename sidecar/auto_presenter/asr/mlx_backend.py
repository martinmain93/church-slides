"""mlx-whisper backend — Whisper on Apple silicon's GPU, via Metal.

Same `ASRBackend` shape as `WhisperBackend`, and composes with
`SlidingWindowWrapper` unchanged. It exists for one measured reason: on Apple
silicon, `faster-whisper` cannot use the GPU at all. CTranslate2 has no Metal
backend, so it runs CPU float32 (it even logs the fp16 -> fp32 downgrade), and
on an M1 a `small.en` pass over a 6 s window takes ~1076 ms.

**Why that specific number matters.** `SlidingWindowWrapper` will not start a
pass until `last_pass_duration * pace_safety` (1.5) seconds of audio have
accumulated, so it can never fall behind realtime. At 1076 ms the effective
step is `max(1.0, 1.61) = 1.61 s` — *pass time*, not the configured 1 s step,
is setting the cadence, and no configuration change fixes it: a true 1 s step
would consume 1 s of audio every 1.08 s and fall behind forever. Only a faster
pass shortens the step.

Measured on M1 / 16 GB, same 6 s buffer of real worship audio:

    faster-whisper small.en (CPU fp32)   1076 ms   -> effective step 1.61 s
    mlx-whisper    small.en (Metal)       694 ms   -> effective step 1.04 s

So ~0.38 s off the pass and ~0.57 s off the step quantisation, against a
measured median cue delay of 1.4 s.

Apple silicon only. That is deliberate and allowed: the roadmap's hardware floor
was relaxed on 2026-09-05 (see `Plan/ROADMAP.md`, "Shipping is a constraint"),
because the old "volunteer's Windows/Intel laptop" target was ruling out the
whole class of fix that the evidence — 89% of cues correct but ~1.4 s late —
points at. This is an *optional* backend; `WhisperBackend` remains the portable
default.

`mlx-whisper` declares a `torch` dependency but never imports it on the
inference path (only `torch_whisper.py`, a checkpoint-conversion utility, does).
Uninstalling torch leaves this working at identical speed and saves 526 MB, so
the no-PyTorch-at-runtime rule is satisfied.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import numpy as np

from auto_presenter.asr.types import TranscriptEvent, Word
from auto_presenter.audio.types import SAMPLE_RATE, AudioFrame

log = logging.getLogger(__name__)

#: One MLX caller at a time: Whisper's pass, the score follower's span scorer
#: and its alignments, and a setlist edit's warm-up (another thread) share the
#: model and the GPU. The ASR thread takes it in turn for each; re-entrant.
GPU_LOCK = threading.RLock()

#: Set once a transcription pass has finished in this process. MLX arrays
#: not yet evaluated belong to the thread that made them (0.32): mlx_whisper's
#: cached mel filters and window, and the model's positional tables, become
#: safe to read from another thread (the next-line check's, `follower.
#: next_line`) only after a pass has evaluated them.
FIRST_PASS = threading.Event()

#: Most tokens one pass may decode, per second of audio. Whisper can loop on
#: sung audio ("please, please, please, ..." until its 224-token default):
#: on the live set two 6 s windows decoded 112 and 160 words in 6.0 s and
#: 4.3 s against ~0.7 s for a normal pass, long enough to overflow the ASR
#: queue (5 s), drop audio and trip the overload guard, which switched the
#: score follower's scorer off for 47 s across a song change. Capped at 64
#: they take 2.3-2.5 s; every other window decoded identically (the most real
#: words in one 6 s window: 24).
MAX_TOKENS_PER_S = 10.0
MIN_TOKENS = 32

#: Bare Whisper sizes -> the community MLX conversions. Anything containing a
#: "/" is passed through untouched, so a caller can name any HF repo directly.
_REPO_FOR_SIZE = {
    "tiny.en": "mlx-community/whisper-tiny.en-mlx",
    "tiny": "mlx-community/whisper-tiny-mlx",
    "base.en": "mlx-community/whisper-base.en-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "small.en": "mlx-community/whisper-small.en-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "medium.en": "mlx-community/whisper-medium.en-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
}


#: Ceiling on MLX's cache of freed Metal buffers. MLX keeps every buffer a
#: pass frees for reuse and sets no limit by default, so over a service the
#: cache grew without bound while the model's live memory stayed flat:
#: small.en is ~460 MB active and ~1.2 GB peak, but the cache reached 2.2 GB
#: after 300 passes (five minutes of audio) and the sidecar ~4 GB live after
#: twenty. 1 GiB covers a pass's transient buffers (~0.7 GB) with room to
#: spare; measured +4% pass time against no limit, versus +9% at 256 MB.
#: See Plan/PERF_AUDIT_2026-09-26.md.
MLX_CACHE_LIMIT_BYTES = 1 << 30

_memory_configured = False


def _configure_mlx_memory() -> None:
    global _memory_configured
    if _memory_configured:
        return
    try:
        import mlx.core as mx

        mx.set_cache_limit(MLX_CACHE_LIMIT_BYTES)
    except Exception as exc:  # an older mlx without the API: run uncapped
        log.warning("could not cap the MLX buffer cache: %s", exc)
    _memory_configured = True


def release_cache() -> None:
    """Hand MLX's cached buffers back to the system (e.g. when a session ends).

    A no-op unless MLX is already loaded, so a machine that never used this
    backend doesn't import it just to clear nothing.
    """
    mx = sys.modules.get("mlx.core")
    if mx is None:
        return
    try:
        mx.clear_cache()
    except Exception as exc:
        log.warning("could not clear the MLX buffer cache: %s", exc)


def repo_for(model_size: str) -> str:
    """Resolve a Whisper size to an MLX repo id (pass-through for repo ids)."""
    if "/" in model_size:
        return model_size
    try:
        return _REPO_FOR_SIZE[model_size]
    except KeyError:
        raise ValueError(
            f"no known MLX conversion for {model_size!r}; "
            f"pass a full HF repo id instead. Known: {sorted(_REPO_FOR_SIZE)}"
        ) from None


def local_snapshot(repo: str) -> str:
    """The cached snapshot folder for `repo` if it's downloaded, else `repo`.

    Given a repo id, `mlx_whisper` calls `snapshot_download` on the first
    pass, which asks the Hub for updates even when the model is on disk — on a
    network that accepts connections but never answers, that stalled a cached
    model for 90 s and then failed it. A local path skips the Hub entirely.
    """
    if "/" not in repo or Path(repo).exists():
        return repo
    try:
        from huggingface_hub import snapshot_download

        return snapshot_download(repo_id=repo, local_files_only=True)
    except Exception:  # not cached (or no huggingface_hub): download on use
        return repo


class MLXWhisperBackend:
    """ASRBackend backed by `mlx_whisper`, running on the Metal GPU."""

    def __init__(
        self,
        model_size: str = "small.en",
        *,
        language: str | None = "en",
        #: Matches `WhisperBackend`'s reasoning exactly: one decode attempt, not
        #: a fallback cascade. Whisper re-decodes a segment at the next
        #: temperature whenever it trips its compression-ratio or logprob
        #: thresholds, which sung audio over instrumental backing does
        #: routinely — so what looks like one pass is sometimes six. The tail is
        #: what hurts, because the wrapper paces the *next* pass off how long
        #: the last one took: one slow pass costs a hole in the transcript, not
        #: just a slow pass.
        temperature: float | tuple[float, ...] = 0.0,
    ) -> None:
        self._repo = local_snapshot(repo_for(model_size))
        self._language = language
        self._temperature = temperature
        self._model_name = f"mlx-whisper-{model_size}"

    @staticmethod
    def is_available() -> bool:
        """Whether `mlx_whisper` imports here — false off Apple silicon.

        AUTO_PRESENTER_DISABLE_MLX=1 makes it false anyway: development only,
        to run an Apple-silicon Mac down the Intel/Windows path."""
        if os.environ.get("AUTO_PRESENTER_DISABLE_MLX") == "1":
            return False
        try:
            import mlx_whisper  # noqa: F401
        except Exception:
            return False
        return True

    def name(self) -> str:
        return self._model_name

    def warm(self) -> None:
        """Compile Metal kernels before the first live window.

        The first `mlx_whisper.transcribe` call on a process pays weight load
        plus kernel compile — measured 1–5 s depending on size. Calling this
        at session start keeps that off the sung-word path. Failures are
        swallowed by `transcribe` (empty iterator); the next live pass will
        retry.
        """
        silence = np.zeros(SAMPLE_RATE, dtype=np.float32)
        frame = AudioFrame(samples=silence, t_start=0.0, t_end=1.0)
        list(self.transcribe([frame]))

    def transcribe(self, frames: Iterable[AudioFrame]) -> Iterator[TranscriptEvent]:
        chunks = list(frames)
        if not chunks:
            return iter(())

        audio = np.concatenate([f.samples for f in chunks]).astype(np.float32, copy=False)
        t_origin = chunks[0].t_start

        # Imported lazily so `import auto_presenter` costs nothing on a machine
        # that will never use this, and so a missing install is a runtime
        # message rather than an import error at startup.
        import mlx_whisper

        _configure_mlx_memory()
        max_tokens = max(MIN_TOKENS, int(MAX_TOKENS_PER_S * len(audio) / SAMPLE_RATE))
        try:
            with GPU_LOCK:
                result = mlx_whisper.transcribe(
                    audio,
                    path_or_hf_repo=self._repo,
                    word_timestamps=True,
                    language=self._language,
                    temperature=self._temperature,
                    sample_len=max_tokens,
                    # None, not False: whisper *shows* its progress bar at False,
                    # which printed a line to stderr on every pass.
                    verbose=None,
                )
        except Exception as exc:
            # Same contract every other backend here honours: a failed pass is
            # "no words this window", never an exception into the ASR thread.
            # See `GroqWhisperBackend`.
            log.warning("mlx-whisper pass failed: %s", exc)
            return iter(())
        FIRST_PASS.set()

        return _convert_segments(result.get("segments") or [], t_origin=t_origin)


def _convert_segments(
    segments: Iterable[dict[str, Any]], *, t_origin: float
) -> Iterator[TranscriptEvent]:
    """Translate mlx-whisper's dict segments into `TranscriptEvent`s.

    Timestamps arrive relative to the supplied buffer, so they are offset by
    `t_origin` onto the same clock the frames (and the ground truth) use —
    identical to `whisper_backend._convert_segments`, but reading dict keys
    where faster-whisper exposes attributes.
    """
    for segment_idx, segment in enumerate(segments):
        words: list[Word] = []
        for w in segment.get("words") or []:
            text = (w.get("word") or "").strip()
            if not text:
                continue
            words.append(
                Word(
                    text=text,
                    t_start=t_origin + float(w.get("start", 0.0)),
                    t_end=t_origin + float(w.get("end", 0.0)),
                    # mlx-whisper reports this as `probability`, same as
                    # faster-whisper; default to 1.0 when a build omits it.
                    confidence=float(w.get("probability", 1.0)),
                    no_speech=(
                        float(segment["no_speech_prob"])
                        if segment.get("no_speech_prob") is not None
                        else None
                    ),
                )
            )
        if not words:
            continue
        yield TranscriptEvent(words=words, is_final=True, segment_id=segment_idx)
