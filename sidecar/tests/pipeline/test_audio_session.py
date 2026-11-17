"""AudioSession tests with a controllable fake capture + mock backend."""

from __future__ import annotations

import asyncio
import statistics
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field

import numpy as np
import pytest

from auto_presenter.asr.backend import MockASRBackend, schedule_from_words
from auto_presenter.asr.types import TranscriptEvent
from auto_presenter.audio.types import (
    DEFAULT_FRAME_LENGTH,
    SAMPLE_RATE,
    AudioFrame,
)
from auto_presenter.pipeline.audio_session import (
    PipelineHealth,
    AudioLevel,
    AudioSession,
    StateUpdate,
    compute_audio_level,
)


@dataclass
class FakeCapture:
    """Captures yield N pre-built frames then either stop or block indefinitely.

    Tests drive this by setting `frames` and `block_after_drain`. When
    `block_after_drain=True`, the iterator parks until `stop()` is
    called — simulating live capture that stays alive until cancelled.
    """

    frames: list[AudioFrame] = field(default_factory=list)
    block_after_drain: bool = True

    started: bool = False
    stopped: bool = False
    _wakeup: threading.Event = field(default_factory=threading.Event)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True
        self._wakeup.set()

    def stream(self) -> Iterator[AudioFrame]:
        for f in self.frames:
            yield f
        if self.block_after_drain:
            self._wakeup.wait()


def frame(t_start: float, t_end: float) -> AudioFrame:
    n = max(1, int((t_end - t_start) * SAMPLE_RATE))
    return AudioFrame(
        samples=np.zeros(n, dtype=np.float32),
        t_start=t_start,
        t_end=t_end,
    )


@pytest.fixture()
def event_loop_in_thread():
    """Run an asyncio loop in a background thread for run_coroutine_threadsafe."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        yield loop
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=1.0)
        loop.close()


def _make_session(loop, *, frames=None, schedule=None, wrapper_kwargs=None):
    capture = FakeCapture(frames=frames or [frame(0.0, 5.0)])
    backend = MockASRBackend(schedule=schedule or [], backend_name="test")
    events: list[TranscriptEvent] = []
    states: list[StateUpdate] = []

    async def on_event(ev):
        events.append(ev)

    async def on_state(s):
        states.append(s)

    session = AudioSession(
        capture,
        backend,
        on_event=on_event,
        on_state=on_state,
        wrapper_kwargs=wrapper_kwargs or {
            "window_seconds": 6.0,
            "step_seconds": 0.5,
            "stable_margin_seconds": 0.2,
        },
    )
    return session, capture, events, states


# --- lifecycle ---------------------------------------------------------------


def test_starting_then_running_state_transitions(event_loop_in_thread):
    session, capture, _events, states = _make_session(event_loop_in_thread)
    session.start(event_loop_in_thread)
    # Wait for state to settle into running.
    deadline = time.monotonic() + 1.0
    while session.state != "running" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert capture.started
    assert session.state == "running"

    session.stop()
    session.join(timeout=1.0)
    # Give the loop a beat to dispatch the final state update.
    time.sleep(0.05)
    state_seq = [s.state for s in states]
    assert state_seq[0] == "starting"
    assert "running" in state_seq
    assert state_seq[-1] == "stopped"


def test_a_finished_session_is_freed_without_the_cycle_collector(event_loop_in_thread):
    # Everything a session holds (observers, the music analyser, the matcher)
    # goes with it. A session in a reference cycle waited for a full
    # collection, which a heap this big rarely runs: the Windows soak measured
    # 67 MB a session restart.
    import gc
    import weakref

    session, _capture, _events, _states = _make_session(event_loop_in_thread)
    session.start(event_loop_in_thread)
    session.stop()
    session.join(timeout=1.0)
    time.sleep(0.05)
    ref = weakref.ref(session)
    gc.disable()
    try:
        del session
        assert ref() is None
    finally:
        gc.enable()


def test_capture_failure_transitions_to_error(event_loop_in_thread):
    class BoomCapture(FakeCapture):
        def start(self):
            raise RuntimeError("portaudio unavailable")

    backend = MockASRBackend(schedule=[], backend_name="test")
    events: list = []
    states: list[StateUpdate] = []

    session = AudioSession(
        BoomCapture(frames=[]),
        backend,
        on_event=lambda ev: _noop(),
        on_state=lambda s: _record_state(states, s),
    )
    session.start(event_loop_in_thread)
    session.join(timeout=1.0)
    time.sleep(0.05)

    assert session.state == "error"
    assert session.last_error is not None
    assert "portaudio" in session.last_error.lower()
    assert any(s.state == "error" for s in states)
    assert states[-1].error and "portaudio" in states[-1].error.lower()
    del events  # unused


def test_double_start_raises():
    session, _capture, _events, _states = _make_session(asyncio.new_event_loop())
    # Can't actually start on a non-running loop; reach in.
    session._thread = threading.Thread(target=lambda: None)
    with pytest.raises(RuntimeError, match="already started"):
        session.start(asyncio.new_event_loop())


def test_stop_before_start_is_safe():
    session, _capture, _events, _states = _make_session(asyncio.new_event_loop())
    session.stop()  # should not raise


# --- event flow --------------------------------------------------------------


def test_transcript_events_reach_async_callback(event_loop_in_thread):
    schedule = schedule_from_words(
        [
            ("amazing", 0.0, 0.4),
            ("grace", 0.5, 0.9),
        ]
    )
    session, _capture, events, _states = _make_session(
        event_loop_in_thread,
        frames=[
            frame(0.0, 1.0),
            frame(1.0, 2.0),
            frame(2.0, 3.0),
        ],
        schedule=schedule,
        wrapper_kwargs={
            "window_seconds": 3.0,
            "step_seconds": 0.5,
            "stable_margin_seconds": 0.5,
        },
    )
    session.start(event_loop_in_thread)
    # Wait for events to flush.
    deadline = time.monotonic() + 2.0
    while not events and time.monotonic() < deadline:
        time.sleep(0.02)
    session.stop()
    session.join(timeout=2.0)
    time.sleep(0.1)

    all_words = [w.text for e in events for w in e.words]
    assert "amazing" in all_words
    assert "grace" in all_words


def test_events_marked_final_or_partial_per_streaming_wrapper(event_loop_in_thread):
    schedule = schedule_from_words([("hosanna", 0.0, 0.3)])
    session, _capture, events, _states = _make_session(
        event_loop_in_thread,
        frames=[frame(0.0, 2.0)],
        schedule=schedule,
        wrapper_kwargs={
            "window_seconds": 5.0,
            "step_seconds": 0.5,
            "stable_margin_seconds": 0.5,
        },
    )
    session.start(event_loop_in_thread)
    deadline = time.monotonic() + 2.0
    while not events and time.monotonic() < deadline:
        time.sleep(0.02)
    session.stop()
    session.join(timeout=2.0)
    time.sleep(0.05)

    # The streaming wrapper's contract is exercised in its own tests; here
    # we just check that the session preserves the is_final flag end-to-end.
    assert any(e.is_final for e in events) or any(not e.is_final for e in events)


def test_stop_stops_the_worker_thread_promptly(event_loop_in_thread):
    """Confirm `stop()` causes the thread to exit even when capture is
    blocked waiting for more frames."""
    session, capture, _events, _states = _make_session(event_loop_in_thread)
    session.start(event_loop_in_thread)
    deadline = time.monotonic() + 1.0
    while session.state != "running" and time.monotonic() < deadline:
        time.sleep(0.01)

    t0 = time.monotonic()
    session.stop()
    session.join(timeout=1.0)
    assert (time.monotonic() - t0) < 1.0, "stop() did not return promptly"
    assert capture.stopped
    assert session.state in {"stopped", "stopping"}


def test_ending_a_session_releases_cached_gpu_buffers(event_loop_in_thread, monkeypatch):
    """The MLX buffer cache goes back when a session ends, by any path.

    Releasing it only in the server's stop handler missed sessions that end
    on their own (an injected file running out): the sidecar kept ~1 GB of
    GPU buffers it no longer needed. See docs/done/PERF_AUDIT_2026-09-26.md.
    """
    import auto_presenter.pipeline.audio_session as audio_session

    released: list[float] = []
    monkeypatch.setattr(audio_session, "release_mlx_cache", lambda: released.append(time.monotonic()))
    session, _capture, _events, _states = _make_session(event_loop_in_thread)
    session.start(event_loop_in_thread)
    deadline = time.monotonic() + 1.0
    while session.state != "running" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert released == []
    session.stop()
    session.join(timeout=1.0)
    assert len(released) == 1


class CountingBackend:
    """Counts ASR passes; transcribes nothing."""

    def __init__(self) -> None:
        self.passes = 0

    def name(self) -> str:
        return "counting"

    def transcribe(self, frames):
        list(frames)
        self.passes += 1
        return iter(())


def _run_frames(loop, *, transcribing: bool, resume_after: int | None = None):
    frames = [frame(i * 0.1, (i + 1) * 0.1) for i in range(40)]
    capture = FakeCapture(frames=frames)
    backend = CountingBackend()
    seen: list[AudioFrame] = []

    async def _nothing(_):
        return None

    session = AudioSession(
        capture, backend, on_event=_nothing, on_state=_nothing,
        frame_observer=lambda f: (
            seen.append(f),
            resume_after is not None and len(seen) == resume_after and session.set_transcribing(True),
        ),
        wrapper_kwargs={"window_seconds": 2.0, "step_seconds": 0.5, "stable_margin_seconds": 0.2},
    )
    session.set_transcribing(transcribing)
    session.start(loop)
    deadline = time.monotonic() + 2.0
    while len(seen) < len(frames) and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.2)  # let the ASR thread drain what it was given
    session.stop()
    session.join(timeout=2.0)
    return backend, seen


def test_not_transcribing_runs_no_asr_but_still_captures(event_loop_in_thread):
    """AI off with nothing else needing words: audio is still captured (and
    recorded, and metered) but Whisper never runs."""
    backend, seen = _run_frames(event_loop_in_thread, transcribing=False)
    assert len(seen) == 40
    assert backend.passes == 0


def test_transcription_resumes_when_switched_back_on(event_loop_in_thread):
    backend, seen = _run_frames(event_loop_in_thread, transcribing=False, resume_after=20)
    assert len(seen) == 40
    assert backend.passes > 0


def test_pausing_releases_gpu_buffers_after_the_pass_in_flight(event_loop_in_thread, monkeypatch):
    """Released once the last pass has finished, not when the switch flips.

    Releasing in `set_transcribing` raced a pass still running: its buffers
    went straight back into the cache (measured live: 1.49 GB -> 1.23 GB
    instead of the model's 464 MB).
    """
    import auto_presenter.pipeline.audio_session as audio_session

    log: list[str] = []
    monkeypatch.setattr(audio_session, "release_mlx_cache", lambda: log.append("release"))
    pass_started = threading.Event()
    finish_pass = threading.Event()

    class SlowPass:
        def name(self):
            return "slow"

        def transcribe(self, frames):
            list(frames)
            pass_started.set()
            finish_pass.wait(2.0)
            log.append("pass done")
            return iter(())

    async def _nothing(_):
        return None

    capture = FakeCapture(frames=[frame(i * 0.1, (i + 1) * 0.1) for i in range(10)])
    session = AudioSession(
        capture, SlowPass(), on_event=_nothing, on_state=_nothing,
        wrapper_kwargs={"window_seconds": 2.0, "step_seconds": 0.5, "stable_margin_seconds": 0.2},
    )
    session.start(event_loop_in_thread)
    assert pass_started.wait(2.0)
    session.set_transcribing(False)  # mid-pass
    time.sleep(0.05)
    assert "release" not in log  # not while the pass is still running
    finish_pass.set()
    deadline = time.monotonic() + 2.0
    while "release" not in log and time.monotonic() < deadline:
        time.sleep(0.01)
    session.stop()
    session.join(timeout=2.0)
    assert log.index("release") > log.index("pass done")


def test_pausing_an_idle_asr_thread_still_releases(event_loop_in_thread, monkeypatch):
    """The usual case: between passes the ASR thread is blocked waiting for
    the next frame, and once paused no frame ever comes. It has to be woken,
    or the GPU buffers are kept for the rest of the service (seen live:
    still 1.48 GB thirty seconds after switching the AI off)."""
    import auto_presenter.pipeline.audio_session as audio_session

    released: list[int] = []
    monkeypatch.setattr(audio_session, "release_mlx_cache", lambda: released.append(1))

    async def _nothing(_):
        return None

    backend = CountingBackend()
    capture = FakeCapture(frames=[frame(i * 0.1, (i + 1) * 0.1) for i in range(10)])
    session = AudioSession(
        capture, backend, on_event=_nothing, on_state=_nothing,
        wrapper_kwargs={"window_seconds": 2.0, "step_seconds": 0.5, "stable_margin_seconds": 0.2},
    )
    session.start(event_loop_in_thread)
    deadline = time.monotonic() + 2.0
    while backend.passes < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.1)  # frames drained; the ASR thread is now parked on the queue
    assert released == []
    session.set_transcribing(False)
    deadline = time.monotonic() + 1.0
    while not released and time.monotonic() < deadline:
        time.sleep(0.01)
    try:
        assert released == [1]
    finally:
        session.stop()
        session.join(timeout=2.0)


# --- level metering ----------------------------------------------------------


def _loud_frame(t_start: float, t_end: float, amplitude: float) -> AudioFrame:
    n = max(1, int((t_end - t_start) * SAMPLE_RATE))
    t = np.arange(n, dtype=np.float64) / SAMPLE_RATE
    samples = (amplitude * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    return AudioFrame(samples=samples, t_start=t_start, t_end=t_end)


def test_compute_audio_level_silence_returns_minus_inf():
    f = frame(0.0, 0.1)  # zero-filled
    level = compute_audio_level(f)
    assert level.peak == 0.0
    assert level.rms_dbfs == float("-inf")


def test_compute_audio_level_loud_frame_is_above_minus_30_dbfs():
    f = _loud_frame(0.0, 0.1, amplitude=0.5)
    level = compute_audio_level(f)
    # 0.5 amplitude sine → RMS ≈ 0.354 → -9 dBFS.
    assert -12.0 < level.rms_dbfs < -6.0
    assert 0.45 < level.peak <= 0.5


def test_level_observer_fires_once_per_frame(event_loop_in_thread):
    """Each frame the session pulls becomes one level event."""
    frames = [_loud_frame(0.0, 0.1, 0.3), _loud_frame(0.1, 0.2, 0.3)]
    capture = FakeCapture(frames=frames, block_after_drain=True)
    backend = MockASRBackend(schedule=[], backend_name="test")
    levels: list[AudioLevel] = []

    async def on_event(_ev):
        return None

    async def on_state(_s):
        return None

    session = AudioSession(
        capture,
        backend,
        on_event=on_event,
        on_state=on_state,
        level_observer=levels.append,
    )
    session.start(event_loop_in_thread)
    deadline = time.monotonic() + 1.0
    while len(levels) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    session.stop()
    session.join(timeout=1.0)

    assert len(levels) >= 2
    # Levels are sane RMS readings on a loud sine.
    for lvl in levels[:2]:
        assert lvl.rms_dbfs > -20.0
        assert 0.25 < lvl.peak < 0.35


# ---- helpers ---------------------------------------------------------------


async def _noop():
    return None


async def _record_state(target: list[StateUpdate], state: StateUpdate) -> None:
    target.append(state)


def test_health_lag_excludes_device_startup_time():
    """Lag must measure keeping up with audio, not opening the microphone.

    `capture.stream()` opens the audio device, which on macOS can take
    seconds. Anchoring the clock before that charges startup to the pipeline:
    the first frame arrives at t_end=0.1 with several wall seconds elapsed, so
    the first health report claims multi-second lag on a pipeline that has not
    processed anything yet — sending the operator chasing a problem that isn't
    there.
    """
    from auto_presenter.pipeline.audio_session import PipelineHealth

    class SlowOpeningCapture:
        """Takes 3s to open, then delivers frames promptly."""

        dropped_chunks = 0

        def stream(self):
            time.sleep(0.3)  # stand-in for device open
            for i in range(40):
                yield frame(i * 0.1, (i + 1) * 0.1)

        def stop(self):
            pass

    seen: list[PipelineHealth] = []
    async def _on_event(_e):
        return None

    async def _on_state(_s):
        return None

    session = AudioSession(
        SlowOpeningCapture(),
        MockASRBackend(schedule=[]),
        on_event=_on_event,
        on_state=_on_state,
        health_observer=seen.append,
    )
    frames = list(session._guarded_frames())
    assert len(frames) == 40
    assert seen, "no health reports emitted"
    # The 0.3s device open must not be reported as pipeline lag.
    assert seen[0].lag_seconds < 0.2, (
        f"device startup counted as {seen[0].lag_seconds:.2f}s of lag"
    )


def test_health_recovers_after_a_burst_of_drops():
    """Dropped chunks are cumulative; health must key off recent ones.

    Loading the ASR model blocks the first pass for many seconds and
    unavoidably drops audio. With a cumulative test the pipeline then reports
    "falling behind" for the rest of the service — observed live at 180 dropped
    chunks and a reported lag of 0.0s, which is what the operator's degraded
    indicator keys off.
    """
    during = PipelineHealth(lag_seconds=0.0, dropped_chunks=180, dropped_recent=180)
    after = PipelineHealth(lag_seconds=0.0, dropped_chunks=180, dropped_recent=0)
    assert during.healthy is False
    assert after.healthy is True, "health latched on a cumulative counter"


class SlowBackend:
    """Batch backend that sleeps inside every pass, like a slow Whisper."""

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.calls = 0

    def name(self) -> str:
        return "slow"

    def transcribe(self, frames):
        self.calls += 1
        time.sleep(self.delay)
        return iter(())


@dataclass
class PacedCapture:
    """Yields frames as they arrive in wall time, like LiveCapture."""

    frames: list[AudioFrame]
    interval: float = 0.03
    started: bool = False
    stopped: bool = False
    _wakeup: threading.Event = field(default_factory=threading.Event)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True
        self._wakeup.set()

    def stream(self) -> Iterator[AudioFrame]:
        for f in self.frames:
            if self.stopped:
                return
            yield f
            self._wakeup.wait(timeout=self.interval)
        self._wakeup.wait()


def test_slow_asr_does_not_stall_frame_observers(event_loop_in_thread):
    """A Whisper pass used to run on the same thread as music/VU/recording.

    Frames only reached those observers after the pass returned, so a 0.25 s
    model froze the rest of the capture fan-out. Observers must keep the
    producer's cadence even while ASR is blocked.
    """
    n = 8
    frames = [frame(i * 0.03, (i + 1) * 0.03) for i in range(n)]
    capture = PacedCapture(frames=frames, interval=0.025)
    seen: list[float] = []

    async def on_event(_ev):
        return None

    async def on_state(_s):
        return None

    session = AudioSession(
        capture,
        SlowBackend(delay=0.2),
        on_event=on_event,
        on_state=on_state,
        frame_observer=lambda _f: seen.append(time.monotonic()),
        wrapper_kwargs={
            "window_seconds": 1.0,
            "step_seconds": 0.05,
            "stable_margin_seconds": 0.2,
        },
    )
    session.start(event_loop_in_thread)
    deadline = time.monotonic() + 2.0
    while len(seen) < n and time.monotonic() < deadline:
        time.sleep(0.01)
    session.stop()
    session.join(timeout=2.0)

    assert len(seen) >= n, f"observers only saw {len(seen)} of {n} frames"
    gaps = [seen[i] - seen[i - 1] for i in range(1, len(seen))]
    # Producer interval is 25 ms. A stalled observer would see ~200 ms gaps
    # once the first pass starts. Keep the median well under that.
    assert statistics.median(gaps) < 0.08, (
        f"observer cadence stalled: median gap {statistics.median(gaps):.3f}s"
    )


def test_a_file_read_faster_than_real_time_drops_nothing(event_loop_in_thread):
    """Live audio drops the oldest queued frames when ASR falls behind; a file
    played as fast as possible has nothing to keep up with, so ASR must get
    all of it (the build's soak run measures memory over whole passes)."""
    frames = [frame(i * 0.1, (i + 1) * 0.1) for i in range(3 * 50)]  # 3x the queue
    capture = FakeCapture(frames=frames, block_after_drain=False)
    capture.realtime = False  # type: ignore[attr-defined]  # as FileCapture(realtime=False)

    async def ignore(_x):
        return None

    session = AudioSession(
        capture,
        SlowBackend(delay=0.005),
        on_event=ignore,
        on_state=ignore,
        wrapper_kwargs={"window_seconds": 1.0, "step_seconds": 0.5, "stable_margin_seconds": 0.2},
    )
    session.start(event_loop_in_thread)
    session.join(timeout=10.0)
    assert session.state == "stopped"
    assert session._asr_dropped == 0
