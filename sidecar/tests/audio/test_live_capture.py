"""LiveCapture tests with a fake sounddevice module.

The fake stream calls our callback synchronously from a helper, which
lets us assert on the queue/iterator logic without involving PortAudio
or real audio threads.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import numpy as np
import pytest

from auto_presenter.audio.live_capture import LiveCapture
from auto_presenter.audio.types import DEFAULT_FRAME_LENGTH, SAMPLE_RATE


class FakeStream:
    """Minimal stand-in for `sounddevice.InputStream`."""

    def __init__(
        self,
        *,
        callback,
        blocksize: int,
        channels: int,
        samplerate: int,
        device: Any = None,
        **kwargs: Any,
    ) -> None:
        self.callback = callback
        self.blocksize = blocksize
        self.channels = channels
        self.samplerate = samplerate
        self.device = device
        self.started = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def close(self) -> None:
        self.closed = True


class FakeSounddevice:
    """sounddevice-shaped module for dependency injection in tests.

    `devices` is the full list as `query_devices()` would return.
    Each entry is a dict mimicking PortAudio's metadata. When `devices`
    is None, `query_devices` raises (mocks/old sounddevice without the
    call), exercising the defensive-default path.
    """

    def __init__(
        self,
        devices: list[dict] | None = None,
        default_input_index: int | None = None,
    ) -> None:
        self.last_stream: FakeStream | None = None
        self._devices = devices
        self._default_input_index = default_input_index

    def InputStream(self, **kwargs: Any) -> FakeStream:
        s = FakeStream(**kwargs)
        self.last_stream = s
        return s

    def query_devices(self, device: Any = None, kind: str | None = None):
        if self._devices is None:
            raise AttributeError("query_devices not available")
        if device is None and kind == "input":
            # Mimic sounddevice's behaviour: return the default input's
            # info dict, with `index` populated so the caller can pass it
            # straight to InputStream(device=...).
            for idx, d in enumerate(self._devices):
                if (
                    self._default_input_index is not None
                    and idx == self._default_input_index
                ):
                    return {**d, "index": idx}
            for idx, d in enumerate(self._devices):
                if int(d.get("max_input_channels", 0) or 0) > 0:
                    return {**d, "index": idx}
            return {"max_input_channels": 0}
        if device is None:
            return list(self._devices)
        if isinstance(device, int):
            return self._devices[device]
        raise NotImplementedError(f"unexpected device arg: {device!r}")


def _push_callback(cap: LiveCapture, data: np.ndarray) -> None:
    """Invoke the capture's audio callback as PortAudio would."""
    assert cap._stream is not None
    cap._stream.callback(data, data.shape[0], None, None)  # type: ignore[attr-defined]


def test_rejects_invalid_frame_length():
    with pytest.raises(ValueError, match="frame_length"):
        LiveCapture(frame_length=0)


def test_rejects_invalid_sample_rate():
    with pytest.raises(ValueError, match="sample_rate"):
        LiveCapture(sample_rate=0)


def test_start_creates_stream_with_expected_config():
    fake = FakeSounddevice()
    cap = LiveCapture(device=3, _sd_module=fake)
    cap.start()
    s = fake.last_stream
    assert s is not None
    assert s.started is True
    assert s.blocksize == DEFAULT_FRAME_LENGTH
    assert s.channels == 1
    assert s.samplerate == SAMPLE_RATE
    cap.stop()


def test_start_is_idempotent():
    fake = FakeSounddevice()
    cap = LiveCapture(_sd_module=fake)
    cap.start()
    first_stream = fake.last_stream
    cap.start()  # second call should be a no-op
    assert fake.last_stream is first_stream
    cap.stop()


def test_start_opens_with_devices_advertised_channels():
    """The reported MacBook bug: PortAudio rejects mono when the device
    only advertises stereo input. Open with the advertised count and
    downmix in the callback instead."""
    fake = FakeSounddevice(
        devices=[{"name": "Stereo Mic", "max_input_channels": 2}],
    )
    cap = LiveCapture(device=0, _sd_module=fake)
    cap.start()
    assert fake.last_stream is not None
    assert fake.last_stream.channels == 2
    cap.stop()


def test_start_falls_back_to_mono_when_query_devices_unavailable():
    """Older sounddevice / mocks without `query_devices` still work."""
    fake = FakeSounddevice(devices=None)
    cap = LiveCapture(device=0, _sd_module=fake)
    cap.start()
    assert fake.last_stream is not None
    assert fake.last_stream.channels == 1
    cap.stop()


def test_multi_channel_input_is_downmixed_to_mono():
    """Stereo input → callback delivers a mono samples vector that is
    the average of the channels."""
    fake = FakeSounddevice(
        devices=[{"name": "Stereo Mic", "max_input_channels": 2}],
    )
    cap = LiveCapture(device=0, _sd_module=fake, frame_length=4)
    cap.start()
    stereo = np.array(
        [
            [1.0, 3.0],
            [2.0, 4.0],
            [0.0, 0.0],
            [-1.0, 1.0],
        ],
        dtype=np.float32,
    )
    _push_callback(cap, stereo)
    frames = []
    it = cap.stream()
    cap.stop()  # ensure iterator terminates after one chunk
    for f in it:
        frames.append(f)
    assert len(frames) == 1
    np.testing.assert_array_almost_equal(
        frames[0].samples,
        np.array([2.0, 3.0, 0.0, 0.0], dtype=np.float32),
    )


# --- input-device resolution -------------------------------------------------


def test_string_lookup_skips_output_devices_with_matching_substring():
    """The reported bug: a saved device name substring-matching an
    output device shouldn't pull that output in — we always need an
    input-capable target. Even if the name matches, skip output
    devices and find the next input device."""
    fake = FakeSounddevice(
        devices=[
            {"name": "MacBook Air Speakers", "max_input_channels": 0},
            {"name": "MacBook Air Microphone", "max_input_channels": 1},
        ],
    )
    # User saved "MacBook Air" (ambiguous substring across both).
    # Resolver must skip the speakers (no input channels) and land
    # on the microphone.
    cap = LiveCapture(device="MacBook Air", _sd_module=fake)
    cap.start()
    assert fake.last_stream is not None
    assert fake.last_stream.device == 1  # the mic's index
    cap.stop()


def test_unknown_device_index_falls_back_to_system_default_input():
    """If a stale device_index points at the wrong slot, fall back to
    the OS default input rather than blowing up."""
    fake = FakeSounddevice(
        devices=[
            {"name": "MacBook Air Speakers", "max_input_channels": 0},
            {"name": "MacBook Air Microphone", "max_input_channels": 1},
        ],
        default_input_index=1,
    )
    # Index 0 is the speakers (no input channels) — should NOT be opened.
    cap = LiveCapture(device=0, _sd_module=fake)
    cap.start()
    assert fake.last_stream is not None
    # Resolver should have fallen back to the system default input (idx 1).
    assert fake.last_stream.device == 1
    cap.stop()


def test_string_lookup_finds_microphone_among_outputs():
    """The exact MacBook Air case: list contains speakers + microphone,
    saved name is the exact microphone name — must resolve to the mic."""
    fake = FakeSounddevice(
        devices=[
            {"name": "MacBook Air Speakers", "max_input_channels": 0},
            {"name": "External USB Output", "max_input_channels": 0},
            {"name": "MacBook Air Microphone", "max_input_channels": 1},
        ],
    )
    cap = LiveCapture(device="MacBook Air Microphone", _sd_module=fake)
    cap.start()
    assert fake.last_stream is not None
    assert fake.last_stream.device == 2
    cap.stop()


def test_no_input_devices_at_all_raises_clear_error():
    fake = FakeSounddevice(
        devices=[{"name": "Some Speaker", "max_input_channels": 0}],
    )
    cap = LiveCapture(_sd_module=fake)
    with pytest.raises(RuntimeError, match="No audio input device"):
        cap.start()


def test_stop_without_start_is_safe():
    cap = LiveCapture(_sd_module=FakeSounddevice())
    cap.stop()  # should not raise


def test_callback_chunks_become_frames():
    fake = FakeSounddevice()
    cap = LiveCapture(_sd_module=fake)
    cap.start()

    samples = np.ones(DEFAULT_FRAME_LENGTH, dtype=np.float32) * 0.5
    _push_callback(cap, samples.reshape(-1, 1))

    it = cap.stream()
    frame = next(it)
    assert frame.samples.shape == (DEFAULT_FRAME_LENGTH,)
    assert frame.samples.dtype == np.float32
    assert np.allclose(frame.samples, 0.5)
    cap.stop()


def test_frame_timestamps_are_monotonic_and_correctly_spaced():
    fake = FakeSounddevice()
    cap = LiveCapture(_sd_module=fake)
    cap.start()
    for _ in range(3):
        _push_callback(cap, np.zeros((DEFAULT_FRAME_LENGTH, 1), dtype=np.float32))

    it = cap.stream()
    frames = [next(it) for _ in range(3)]
    cap.stop()

    expected_step = DEFAULT_FRAME_LENGTH / SAMPLE_RATE
    assert frames[0].t_start == pytest.approx(0.0)
    assert frames[1].t_start == pytest.approx(expected_step)
    assert frames[2].t_start == pytest.approx(2 * expected_step)
    for f in frames:
        assert f.t_end > f.t_start
        assert f.duration_seconds == pytest.approx(expected_step)


def test_short_chunks_are_padded_to_frame_length():
    """Sometimes PortAudio invokes the callback with fewer than `blocksize`
    samples at the tail. We pad rather than reshape so consumers see a
    consistent shape."""
    fake = FakeSounddevice()
    cap = LiveCapture(_sd_module=fake)
    cap.start()
    short = np.ones((100, 1), dtype=np.float32) * 0.7
    _push_callback(cap, short)
    frame = next(cap.stream())
    assert frame.samples.shape == (DEFAULT_FRAME_LENGTH,)
    assert np.all(frame.samples[:100] == 0.7)
    assert np.all(frame.samples[100:] == 0.0)
    cap.stop()


def test_long_chunks_are_trimmed_to_frame_length():
    fake = FakeSounddevice()
    cap = LiveCapture(_sd_module=fake)
    cap.start()
    long = np.full((DEFAULT_FRAME_LENGTH * 2, 1), 0.3, dtype=np.float32)
    _push_callback(cap, long)
    frame = next(cap.stream())
    assert frame.samples.shape == (DEFAULT_FRAME_LENGTH,)
    cap.stop()


def test_queue_overflow_drops_oldest_but_keeps_running():
    """If the consumer falls behind, the callback should drop instead of
    blocking PortAudio (which would insert silence)."""
    fake = FakeSounddevice()
    cap = LiveCapture(_sd_module=fake, queue_capacity=2)
    cap.start()
    for _ in range(10):
        _push_callback(cap, np.zeros((DEFAULT_FRAME_LENGTH, 1), dtype=np.float32))
    assert cap.dropped_chunks >= 8
    cap.stop()


def test_stream_terminates_after_stop_unblocks_consumer():
    """Calling `stop()` should wake a consumer blocked on `next(it)` and
    raise StopIteration."""
    fake = FakeSounddevice()
    cap = LiveCapture(_sd_module=fake)
    cap.start()

    it = cap.stream()
    received: list = []

    def consumer():
        for frame in it:
            received.append(frame)

    t = threading.Thread(target=consumer, daemon=True)
    t.start()
    time.sleep(0.05)  # let the consumer block on empty queue
    cap.stop()
    t.join(timeout=1.0)
    assert not t.is_alive(), "consumer thread should exit after stop()"


def test_raises_when_sounddevice_unavailable():
    cap = LiveCapture(_sd_module=None)
    # Force the lazy import path to also fail by patching sys.modules
    import sys
    from unittest.mock import patch

    with patch.dict(sys.modules, {"sounddevice": None}):
        with pytest.raises(RuntimeError, match="sounddevice"):
            cap.start()


def test_dropped_audio_leaves_a_gap_rather_than_shifting_the_timeline():
    """Timestamps must stay aligned with real elapsed time when audio is lost.

    A bare frame counter drifts: every dropped chunk moves the whole timeline
    permanently into the past, so a service that overloads once is
    mis-timestamped for the rest of its run — corrupting the filter's `dt`,
    the scheduler's timing, and the alignment between a recording's audio and
    its wall-clock ground truth.
    """
    import itertools

    fake = FakeSounddevice()
    cap = LiveCapture(_sd_module=fake, queue_capacity=3)
    cap.start()
    frames_iter = cap.stream()
    # Four chunks into a queue that holds three: the first is dropped.
    for i in range(4):
        _push_callback(cap, np.full((DEFAULT_FRAME_LENGTH, 1), float(i), dtype=np.float32))
    assert cap.dropped_chunks == 1

    frames = list(itertools.islice(frames_iter, 3))
    cap.stop()

    spacing = DEFAULT_FRAME_LENGTH / SAMPLE_RATE
    # Chunks 1, 2 and 3 survived; chunk 0 was dropped. Their timestamps must
    # be where those chunks really fell — 1, 2 and 3 frames in — not
    # collapsed to 0, 1, 2 as a bare counter would report.
    assert [f.t_start for f in frames] == pytest.approx(
        [spacing, 2 * spacing, 3 * spacing]
    )


def test_stop_wakes_the_consumer_even_when_the_queue_is_full():
    """The sentinel that terminates `stream()` is pushed onto the same bounded
    queue. Under the overload that makes stopping urgent that queue is full,
    and silently swallowing the resulting `Full` left the consumer blocked on
    `get()` forever — hanging the worker thread on shutdown."""
    fake = FakeSounddevice()
    cap = LiveCapture(_sd_module=fake, queue_capacity=2)
    cap.start()
    frames_iter = cap.stream()
    for _ in range(6):
        _push_callback(cap, np.zeros((DEFAULT_FRAME_LENGTH, 1), dtype=np.float32))
    cap.stop()

    done = threading.Event()

    def drain():
        list(frames_iter)
        done.set()

    threading.Thread(target=drain, daemon=True).start()
    assert done.wait(timeout=2.0), "consumer never terminated after stop()"


def test_a_stream_never_opens_during_a_device_rescan():
    """Re-initializing PortAudio under a stream that is part-way through
    opening can crash natively; the rescan and the open share a lock."""
    from auto_presenter.audio.devices import PORTAUDIO_LOCK

    held_during_open: list[bool] = []

    class Recording(FakeSounddevice):
        def InputStream(self, **kwargs: Any) -> FakeStream:
            held_during_open.append(PORTAUDIO_LOCK.locked())
            return super().InputStream(**kwargs)

    cap = LiveCapture(device=0, _sd_module=Recording(devices=[{"max_input_channels": 1}]))
    cap.start()
    assert held_during_open == [True]
    assert not PORTAUDIO_LOCK.locked()
    cap.stop()


def test_no_rescan_while_any_capture_stream_is_open():
    """The server forgets a session after a bounded join, so a capture thread
    still holding (or opening) a stream can outlive it. The rescan checks the
    streams themselves."""
    import sys
    from types import SimpleNamespace
    from unittest.mock import patch

    from auto_presenter.audio import devices

    reinits: list[str] = []
    fake_sd = SimpleNamespace(
        _terminate=lambda: reinits.append("terminate"),
        _initialize=lambda: reinits.append("initialize"),
    )
    before = devices.live_stream_count()
    cap = LiveCapture(device=0, _sd_module=FakeSounddevice(devices=[{"max_input_channels": 1}]))
    cap.start()
    assert devices.live_stream_count() == before + 1
    with patch.dict(sys.modules, {"sounddevice": fake_sd}):
        devices.rescan_input_devices()
        assert reinits == []
        cap.stop()
        assert devices.live_stream_count() == before
        devices.rescan_input_devices()
    assert reinits == ["terminate", "initialize"]


def test_a_stream_that_fails_to_open_is_not_counted_as_live():
    from auto_presenter.audio import devices

    class Broken(FakeSounddevice):
        def InputStream(self, **kwargs: Any) -> FakeStream:
            raise RuntimeError("Error opening InputStream")

    before = devices.live_stream_count()
    cap = LiveCapture(device=0, _sd_module=Broken(devices=[{"max_input_channels": 1}]))
    with pytest.raises(RuntimeError):
        cap.start()
    assert devices.live_stream_count() == before


def test_two_threads_stopping_at_once_stop_the_stream_once():
    """The session worker and the server can both stop the capture. Two
    PortAudio stops on one CoreAudio stream is how a stop deadlocked."""
    sd = FakeSounddevice()
    cap = LiveCapture(_sd_module=sd)
    cap.start()
    stream = sd.last_stream
    assert stream is not None
    calls: list[str] = []
    entered = threading.Event()
    release = threading.Event()

    def slow_stop() -> None:
        calls.append("stop")
        entered.set()
        release.wait(2)

    stream.stop = slow_stop  # type: ignore[method-assign]
    first = threading.Thread(target=cap.stop)
    first.start()
    assert entered.wait(2)
    cap.stop()  # arrives while the first stop is still inside PortAudio
    release.set()
    first.join(2)
    assert calls == ["stop"]
    assert stream.closed
