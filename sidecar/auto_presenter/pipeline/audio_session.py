"""Live audio → ASR → transcript event broadcaster.

`AudioSession` runs the capture-and-transcribe loop on a worker thread,
keeping the asyncio event loop free for serving WebSocket clients.
Transcript events are forwarded by scheduling the supplied async
callback on the loop the session was started with.

Lifecycle is `start()` → broadcasts running state → `stop()` →
broadcasts stopped state. Errors during the loop transition the session
to "error" state with a descriptive message, and the worker thread
terminates cleanly.

The pipeline composition is fixed for Phase 4:
    AudioCapture.stream() → SlidingWindowWrapper(ASRBackend) → events

Slice 2B-1 ships with `make_session` constructing a `MockASRBackend`
(deterministic, no model required). Slice 2B-2 swaps the backend
factory to load `WhisperBackend` when the client requests it.
"""

from __future__ import annotations

import asyncio
import logging
import math
import queue
import threading
import time
from collections.abc import Awaitable, Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import numpy as np

from auto_presenter.asr.backend import ASRBackend, MockASRBackend, schedule_from_words
from auto_presenter.asr.latency import DeliveryLatency, LatencySnapshot
from auto_presenter.asr.mlx_backend import release_cache as release_mlx_cache
from auto_presenter.asr.streaming import SlidingWindowWrapper
from auto_presenter.asr.types import TranscriptEvent
from auto_presenter.audio.types import AudioFrame
from auto_presenter.pipeline.perf import (
    STAGE_ASR,
    STAGE_MATCHER,
    PerfMonitor,
)
from auto_presenter.pipeline.power import PowerState, read_power_state

log = logging.getLogger(__name__)

#: How often to report pipeline health, in seconds. Fast enough that an
#: operator notices the AI falling behind mid-song, slow enough to be free.
HEALTH_INTERVAL_S = 2.0

#: Lag beyond this means the AI's view of the room is stale enough that its
#: slide decisions are no longer trustworthy.
UNHEALTHY_LAG_S = 2.0

#: Frames waiting for the ASR thread. ~5 s at 100 ms, matching LiveCapture
#: so a slow pass drops the oldest audio rather than blocking observers.
ASR_QUEUE_CAPACITY = 50

_ASR_SENTINEL = object()
#: Wakes an idle ASR thread without carrying audio (see `set_transcribing`).
_ASR_WAKE = object()


@dataclass(frozen=True)
class PipelineHealth:
    """How well the capture -> ASR pipeline is keeping up, and what it costs."""

    lag_seconds: float
    #: Chunks discarded since the session began — cumulative, and shown to the
    #: operator as a running total.
    dropped_chunks: int
    #: Chunks discarded *in this reporting window*. Health keys off this rather
    #: than the total: loading the ASR model blocks the first pass for many
    #: seconds and unavoidably drops audio, so a cumulative test latches the
    #: pipeline "unhealthy" for the rest of the service. Observed live: 180
    #: chunks dropped at startup, then "falling behind" every two seconds for
    #: the whole session with a reported lag of 0.0s.
    dropped_recent: int = 0
    #: Share of one core this process used over the reporting window.
    cpu_percent: float = 0.0
    #: Peak resident memory, MB.
    rss_mb: float = 0.0
    #: Mean wall time of one ASR pass over the window.
    asr_ms: float = 0.0
    #: Mean wall time of one matcher update over the window.
    matcher_ms: float = 0.0
    #: Seconds of audio captured so far. This keeps advancing while the
    #: transcript does not, which is what lets the freeze watchdog notice a
    #: stall caused by evidence *stopping* — see `LeadTimeScheduler.tick`.
    audio_seconds: float = 0.0
    #: A hosted speech service is refusing requests (Groq's rate limit), so
    #: the transcript has stopped while the pipeline itself keeps up.
    asr_refused: bool = False
    #: The computer's power state, read only when the window is unhealthy
    #: (`pipeline.power`): Low Power Mode or a near-empty battery slows the
    #: engine several-fold, and the operator's fix is not a faster computer.
    power: PowerState | None = None

    @property
    def healthy(self) -> bool:
        return self.lag_seconds < UNHEALTHY_LAG_S and self.dropped_recent == 0


SessionState = Literal["starting", "running", "stopping", "stopped", "error"]


class _CaptureLike(Protocol):
    """The subset of LiveCapture / FileCapture we care about here."""

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def stream(self) -> Iterator[AudioFrame]: ...


@dataclass
class StateUpdate:
    state: SessionState
    error: str | None = None


@dataclass(frozen=True)
class AudioLevel:
    """One audio-level sample: RMS in dBFS plus the absolute peak.

    Both fields are derived from a single `AudioFrame`. `rms_dbfs` is
    -infinity when the frame is exactly silent — UI code should clamp
    to a display floor (e.g. -60 dB) before rendering a bar.
    """

    t_seconds: float
    rms_dbfs: float
    peak: float


EventCallback = Callable[[TranscriptEvent], Awaitable[None]]
StateCallback = Callable[[StateUpdate], Awaitable[None]]


FrameObserver = Callable[[AudioFrame], None]
LevelObserver = Callable[[AudioLevel], None]
HealthObserver = Callable[["PipelineHealth"], None]


def compute_audio_level(frame: AudioFrame) -> AudioLevel:
    """Pure helper. RMS in dBFS + peak amplitude in [0, 1].

    `dBFS` is `20 * log10(rms / 1.0)` — the reference is full-scale
    float32 audio. A sine at 0.1 amplitude is roughly -23 dBFS.
    """
    samples = frame.samples
    if samples.size == 0:
        return AudioLevel(t_seconds=frame.t_end, rms_dbfs=-math.inf, peak=0.0)
    rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
    peak = float(np.max(np.abs(samples)))
    rms_dbfs = -math.inf if rms <= 0 else 20.0 * math.log10(rms)
    return AudioLevel(t_seconds=frame.t_end, rms_dbfs=rms_dbfs, peak=peak)


class AudioSession:
    """Owns capture and transcription, on two threads.

    Capture pulls the microphone, fans frames out to observers (recording,
    music, VU), and queues a copy for ASR. Transcription
    runs on a sibling thread so a Whisper pass cannot stall those
    observers — previously they shared one worker, and a 0.3 s pass froze
    the VU, the music analyser, and the recording sink together.

    Use `start()` and `stop()`; both are safe to call from the asyncio
    thread. The session is single-use: once stopped, instantiate a new
    one rather than restarting.

    `frame_observer`, when supplied, receives every captured `AudioFrame`
    before it is queued for ASR. The observer runs on the capture thread
    — it must not block.
    """

    def __init__(
        self,
        capture: _CaptureLike,
        backend: ASRBackend,
        *,
        on_event: EventCallback,
        on_state: StateCallback,
        wrapper_kwargs: dict[str, Any] | None = None,
        frame_observer: FrameObserver | None = None,
        level_observer: LevelObserver | None = None,
        health_observer: HealthObserver | None = None,
        perf: PerfMonitor | None = None,
    ) -> None:
        self._capture = capture
        self._backend = backend
        self.perf = perf or PerfMonitor()
        self._on_event = on_event
        self._on_state = on_state
        self._frame_observer = frame_observer
        self._level_observer = level_observer
        self._health_observer = health_observer
        # How stale the transcript is when it lands. `perf` already times the
        # ASR *pass*, which is a different and smaller number: it misses the
        # stability margin and the step, which together dominate the wait.
        self.latency = DeliveryLatency()
        # The timer closes over the monitor, not `self`: a lambda holding the
        # session, held by the session's own wrapper, made every finished
        # session a reference cycle — its observers, music analyser and matcher
        # (tens of MB) then waited for a full collection, which a heap this big
        # rarely triggers. The Windows soak measured it as 67 MB a restart.
        perf_monitor = self.perf
        hooks: dict[str, Any] = dict(
            pass_timer=lambda: perf_monitor.stage(STAGE_ASR),
            emission_observer=self.latency.observe,
            **(wrapper_kwargs or {}),
        )
        # A backend that streams its own words (`QwenStreamingBackend`) takes
        # the wrapper's place, with the same hooks; every other one is windowed.
        streamer = getattr(backend, "streamer", None)
        self._wrapper: Any = (
            streamer(**hooks) if callable(streamer) else SlidingWindowWrapper(backend, **hooks)
        )

        self._thread: threading.Thread | None = None
        self._asr_thread: threading.Thread | None = None
        self._stopping = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._asr_queue: queue.Queue[Any] = queue.Queue(maxsize=ASR_QUEUE_CAPACITY)
        self._asr_lag_seconds = 0.0
        self._asr_dropped = 0
        # A file read faster than real time (evals, the build's soak run in
        # scripts/perf_report.py) has no live audio to keep up with, so ASR
        # gets every frame and the reader waits for it. Dropping the oldest,
        # the live policy, transcribed only the last ~5 s of each file.
        self._lossless = getattr(capture, "realtime", True) is False
        # Whether captured audio goes to ASR at all. Capture, recording and
        # the level meter run regardless; this only decides whether anyone
        # needs a transcript (see `set_transcribing`).
        self._transcribing = threading.Event()
        self._transcribing.set()

        # Public, for tests / introspection. Treat as read-only.
        self.state: SessionState = "stopped"
        self.last_error: str | None = None

    def latency_snapshot(self) -> LatencySnapshot | None:
        """Recent delivery lag, or `None` before the first committed word."""
        return self.latency.snapshot()

    # ----------------------------------------------------------- lifecycle

    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Begin capturing and transcribing.

        `loop` is the asyncio loop on which `on_event` / `on_state` will
        be scheduled; defaults to the currently-running loop.
        """
        if self._thread is not None:
            raise RuntimeError("AudioSession already started")
        self._loop = loop or asyncio.get_event_loop()
        self._stopping.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="AudioSession")
        self._thread.start()

    def stop(self) -> None:
        """Signal the worker to terminate and stop the capture.

        Idempotent. Returns immediately; join the thread separately if
        you need a confirmation.
        """
        if self._thread is None:
            return
        self._stopping.set()
        try:
            self._capture.stop()
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("capture.stop() failed: %s", exc)

    def join(self, timeout: float | None = None) -> None:
        if self._thread is None:
            return
        self._thread.join(timeout=timeout)

    def set_transcribing(self, on: bool) -> None:
        """Start or stop feeding audio to ASR, without touching capture.

        Off, the ASR thread idles on an empty queue: no passes, no GPU, no
        matcher updates. Turning it back on needs nothing special — the
        rolling window trims by audio time, so the first frame after the
        pause pushes all the old audio out rather than stitching across the
        gap.
        """
        if on:
            self._transcribing.set()
            return
        self._transcribing.clear()
        # Between passes the ASR thread sits in `queue.get()` waiting for the
        # next frame — and paused, none will come. Wake it so it notices it
        # has gone quiet and releases its GPU buffers. A full queue needs no
        # nudge: the thread has work to finish first and checks after.
        try:
            self._asr_queue.put_nowait(_ASR_WAKE)
        except queue.Full:
            pass

    @property
    def transcribing(self) -> bool:
        return self._transcribing.is_set()

    @property
    def is_running(self) -> bool:
        return self.state == "running"

    @property
    def is_live(self) -> bool:
        """Whether this session owns the microphone *or is about to*.

        `is_running` only goes true once the worker thread has opened the
        capture device, which happens well after `start()` returns — opening
        PortAudio takes hundreds of milliseconds. Anything asking "is a
        session already in place?" must include `"starting"`, or it gets a
        False for a session that exists and is seconds away from running.

        That gap is not theoretical: the WS server's "one microphone, one
        session" guard read `is_running`, so the debug panel's
        transcript-only start landing just after a service start passed the
        guard and replaced the service's matcher. Transcripts kept flowing,
        the belief panel sat on "waiting for the matcher", and the AI never
        moved a slide.
        """
        return self.state in ("starting", "running")

    @property
    def capture_thread_alive(self) -> bool:
        """Whether the capture thread may still hold (or be opening) a stream.

        Stricter than `not is_live`: a session that failed or was stopped
        flips its state before its thread has finished closing the device.
        """
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------ internals

    def _run(self) -> None:
        """Capture thread: open the mic, fan frames out, feed the ASR queue."""
        self._set_state("starting")
        try:
            self._capture.start()
        except Exception as exc:
            self._fail(f"capture start failed: {exc}")
            return

        self._asr_thread = threading.Thread(
            target=self._run_asr, daemon=True, name="AudioSessionASR"
        )
        self._asr_thread.start()
        self._set_state("running")
        try:
            for frame in self._guarded_frames():
                if self._stopping.is_set():
                    break
                if self._transcribing.is_set():
                    self._enqueue_asr(frame)
        except Exception as exc:
            self._fail(f"capture loop failed: {exc}")
        finally:
            self._close_asr_queue()
            if self._asr_thread is not None:
                self._asr_thread.join(timeout=8.0)
            # Every way a session ends passes here (an explicit stop, a file
            # running out, a failure), so this is where the GPU buffers the
            # ASR thread cached go back. A no-op off the MLX backend.
            release_mlx_cache()
            try:
                self._capture.stop()
            except Exception:  # pragma: no cover - defensive
                pass
            if self.state != "error":
                self._set_state("stopping")
                self._set_state("stopped")

    def _run_asr(self) -> None:
        """ASR thread: consume queued frames without blocking capture."""
        try:
            for event in self._wrapper.transcribe(self._asr_frames()):
                if self._stopping.is_set():
                    break
                self._dispatch_event(event)
        except Exception as exc:
            self._fail(f"transcription loop failed: {exc}")

    def _enqueue_asr(self, frame: AudioFrame) -> None:
        if self._lossless:
            while not self._stopping.is_set():
                try:
                    self._asr_queue.put(frame, timeout=0.1)
                    return
                except queue.Full:
                    continue
            return
        try:
            self._asr_queue.put_nowait(frame)
            return
        except queue.Full:
            pass
        # Drop the oldest queued audio so observers stay realtime and ASR
        # sees the most recent window rather than a growing backlog.
        try:
            self._asr_queue.get_nowait()
        except queue.Empty:
            pass
        self._asr_dropped += 1
        try:
            self._asr_queue.put_nowait(frame)
        except queue.Full:  # pragma: no cover - raced with ASR consumer
            self._asr_dropped += 1

    def _close_asr_queue(self) -> None:
        """Guarantee the ASR iterator sees a sentinel, even if the queue is full."""
        while True:
            try:
                self._asr_queue.put_nowait(_ASR_SENTINEL)
                return
            except queue.Full:
                try:
                    self._asr_queue.get_nowait()
                except queue.Empty:
                    time.sleep(0.001)

    def _asr_frames(self) -> Iterator[AudioFrame]:
        origin: float | None = None
        released = False
        while True:
            if not self._transcribing.is_set() and self._asr_queue.empty():
                # Paused, and the last pass has finished (this generator is
                # only pulled between passes): nothing will use the GPU until
                # transcription resumes, so don't sit on ~1 GB of cached
                # buffers for the rest of the service. Releasing from
                # `set_transcribing` instead raced the pass in flight, whose
                # buffers went straight back into the cache.
                if not released:
                    release_mlx_cache()
                    released = True
            else:
                released = False
            item = self._asr_queue.get()
            if item is _ASR_SENTINEL:
                return
            if item is _ASR_WAKE:
                continue
            now = time.monotonic()
            if origin is None:
                origin = now - item.t_end
            self._asr_lag_seconds = max(0.0, (now - origin) - item.t_end)
            yield item

    def _guarded_frames(self) -> Iterable[AudioFrame]:
        """Wrap `capture.stream()` so we exit promptly on stop. Also fans
        each frame out to the optional `frame_observer` (used by the
        recording subsystem to write audio to disk in parallel), the
        optional `level_observer` (used by the UI for the VU meter), and the
        optional `health_observer` (how far behind real time we are running).
        """
        frame_observer = self._frame_observer
        level_observer = self._level_observer
        health_observer = self._health_observer
        # Anchor the clock on the FIRST FRAME, not on entry.
        #
        # `capture.stream()` opens the audio device, and on macOS that can
        # take seconds (device init, permission checks). Anchoring before it
        # charges that startup to the pipeline: the first frame arrives with
        # t_end=0.1 while several seconds of wall clock have passed, so the
        # very first health report claims multiple seconds of lag on a
        # pipeline that has not yet processed anything. Lag is about keeping
        # up with audio once it is flowing.
        origin: float | None = None
        last_health = 0.0
        dropped_reported = 0
        for frame in self._capture.stream():
            if self._stopping.is_set():
                return
            if health_observer is not None:
                # Capture lag is "did we pull this frame on time". ASR lag is
                # "how stale is the transcript thread" — the number the
                # operator actually cares about, now that the two are split.
                now = time.monotonic()
                if origin is None:
                    origin = now - frame.t_end
                if now - last_health >= HEALTH_INTERVAL_S:
                    last_health = now
                    try:
                        snap = self.perf.snapshot()
                        capture_dropped = int(
                            getattr(self._capture, "dropped_chunks", 0) or 0
                        )
                        dropped_total = capture_dropped + self._asr_dropped
                        capture_lag = max(0.0, (now - origin) - frame.t_end)
                        lag = max(capture_lag, self._asr_lag_seconds)
                        behind = (
                            lag >= UNHEALTHY_LAG_S or dropped_total > dropped_reported
                        )
                        health_observer(
                            PipelineHealth(
                                lag_seconds=lag,
                                audio_seconds=frame.t_end,
                                dropped_chunks=dropped_total,
                                dropped_recent=dropped_total - dropped_reported,
                                cpu_percent=snap.cpu_percent,
                                rss_mb=snap.rss_mb,
                                asr_ms=snap.stage_mean_ms(STAGE_ASR),
                                matcher_ms=snap.stage_mean_ms(STAGE_MATCHER),
                                asr_refused=bool(getattr(self._backend, "refusing", False)),
                                power=read_power_state() if behind else None,
                            )
                        )
                        dropped_reported = dropped_total
                    except Exception:
                        log.exception("health_observer raised; continuing")
            if frame_observer is not None:
                try:
                    frame_observer(frame)
                except Exception:
                    log.exception("frame_observer raised; dropping frame")
            if level_observer is not None:
                try:
                    level_observer(compute_audio_level(frame))
                except Exception:
                    log.exception("level_observer raised; continuing")
            yield frame

    def _dispatch_event(self, event: TranscriptEvent) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        asyncio.run_coroutine_threadsafe(self._on_event(event), loop)

    def _dispatch_state(self, update: StateUpdate) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        asyncio.run_coroutine_threadsafe(self._on_state(update), loop)

    def _set_state(self, state: SessionState) -> None:
        self.state = state
        self._dispatch_state(StateUpdate(state=state))

    def _fail(self, message: str) -> None:
        log.error("audio session error: %s", message)
        self.state = "error"
        self.last_error = message
        self._stopping.set()
        try:
            self._capture.stop()
        except Exception:  # pragma: no cover - defensive
            pass
        self._dispatch_state(StateUpdate(state="error", error=message))


# --------------------------------------------------------------------- factory


def make_mock_backend() -> ASRBackend:
    """A demo backend that emits canned worship-y words on a schedule.

    Used by the Settings "Test microphone" button so the user can see
    the pipeline working without downloading a Whisper model. Slice 2B-2
    replaces this with `WhisperBackend.load(...)` once the model
    download UX lands.
    """
    return MockASRBackend(
        schedule=schedule_from_words(
            [
                ("amazing", 0.5, 1.0),
                ("grace", 1.2, 1.6),
                ("how", 2.0, 2.3),
                ("sweet", 2.4, 2.8),
                ("the", 2.9, 3.0),
                ("sound", 3.1, 3.5),
            ]
        ),
        backend_name="mock-demo",
    )
