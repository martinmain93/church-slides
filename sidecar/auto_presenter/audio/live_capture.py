"""Live microphone capture via sounddevice → `AudioFrame` stream.

sounddevice is callback-based: PortAudio invokes our callback on a real-time
audio thread every `blocksize` samples. Our job is to translate those
callback invocations into a Python iterator that yields `AudioFrame`s on
the main thread — without dropping samples and without blocking the
callback.

Approach:

- The audio thread pushes incoming numpy chunks into a bounded
  `queue.Queue`. We *intentionally* drop overflow rather than block the
  callback, because blocking the callback causes PortAudio to insert
  silence (a worse failure mode).
- `stream()` is a Python iterator that pulls chunks off the queue and
  yields `AudioFrame`s timestamped against a monotonic clock anchored
  at capture-start. That matches `FileCapture`'s timestamp behavior so
  downstream code is source-agnostic.
- The capture runs until `stop()` is called or the underlying stream
  errors. Tests inject a fake `sounddevice` module via `_sd_module` so
  the queue + iterator logic is testable without PortAudio.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from auto_presenter.audio.devices import (
    PORTAUDIO_LOCK,
    stream_closed,
    stream_closed_locked,
    stream_opening,
)
from auto_presenter.audio.types import (
    DEFAULT_FRAME_LENGTH,
    SAMPLE_RATE,
    AudioFrame,
)

log = logging.getLogger(__name__)


@dataclass
class LiveCapture:
    """Microphone capture backed by sounddevice."""

    device: int | str | None = None
    sample_rate: int = SAMPLE_RATE
    frame_length: int = DEFAULT_FRAME_LENGTH
    #: Bounded queue capacity in chunks. ~50 chunks at 100 ms is 5 s of
    #: headroom — well more than any reasonable scheduling stall.
    queue_capacity: int = 50

    #: Test seam — swap in a fake `sounddevice` module from tests.
    _sd_module: Any = None

    _queue: "queue.Queue[_ChunkOrSentinel]" = field(init=False)
    _t_origin: float = field(default=0.0, init=False)
    _stream: Any = field(default=None, init=False)
    _stopped: bool = field(default=False, init=False)
    _dropped_chunks: int = field(default=0, init=False)
    #: How many channels PortAudio is delivering. Resolved at start time
    #: from the device's `max_input_channels` because some macOS built-in
    #: mics (notably the studio-quality arrays on recent MBPs) report 2+
    #: and refuse to open in mono mode (PaErrorCode -9998).
    _input_channels: int = field(default=1, init=False)
    #: One stopper at a time: the session's worker and the server can both
    #: call `stop()`, and two threads stopping one CoreAudio stream at once is
    #: how a stop deadlocked inside PortAudio (2026-10-22).
    _stop_lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def __post_init__(self) -> None:
        if self.frame_length <= 0:
            raise ValueError("frame_length must be > 0")
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be > 0")
        self._queue = queue.Queue(maxsize=self.queue_capacity)

    # ----------------------------------------------------------- public API

    def start(self) -> None:
        """Open the audio stream and begin queuing frames.

        Idempotent: calling `start()` twice is a no-op once a stream is
        running. Pair with `stop()`.
        """
        if self._stream is not None:
            return
        sd = self._sd_module or _import_sounddevice()
        if sd is None:
            raise RuntimeError(
                "sounddevice is not available — cannot start live capture"
            )

        self._t_origin = time.monotonic()
        self._stopped = False
        self._dropped_chunks = 0
        # Never open a stream while a device rescan is re-initializing
        # PortAudio (see `PORTAUDIO_LOCK`), and count it as live from here
        # until `stop()` so a rescan skips while it is open.
        with PORTAUDIO_LOCK:
            stream_opening()
            try:
                resolved_index, resolved_name, channels = _resolve_input_device(
                    sd, self.device
                )
                self._input_channels = channels
                # Pass the *resolved* integer index to PortAudio. Don't pass the
                # raw user string — substring-matching at the sounddevice level
                # is ambiguous when multiple device names share a prefix, and
                # using the index we just validated removes any further surprise.
                device_for_stream: int | None = resolved_index
                self._stream = sd.InputStream(
                    samplerate=self.sample_rate,
                    channels=self._input_channels,
                    dtype="float32",
                    blocksize=self.frame_length,
                    device=device_for_stream,
                    callback=self._on_audio,
                )
                self._stream.start()
            except BaseException:
                # Failed to open: nothing is live, and `stop()` won't run for
                # a stream that never was.
                if self._stream is not None:
                    try:
                        self._stream.close()
                    except Exception:  # pragma: no cover - defensive
                        pass
                    self._stream = None
                stream_closed_locked()
                raise
        log.info(
            "live audio capture started: requested=%r resolved=%s (idx=%s) rate=%d frame_length=%d channels=%d",
            self.device,
            resolved_name,
            resolved_index,
            self.sample_rate,
            self.frame_length,
            self._input_channels,
        )

    def stop(self) -> None:
        """Stop the audio stream and signal the iterator to terminate.

        Only the first caller stops the stream; a concurrent or later call
        returns at once. PortAudio's stop can block on CoreAudio, so callers
        on the event loop run this in a thread (`server._stop_audio_session`).
        """
        with self._stop_lock:
            stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            stream.stop()
            stream.close()
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("sounddevice stream stop failed: %s", exc)
        finally:
            self._stopped = True
            stream_closed()
            # Wake the iterator. The queue is bounded, so under the very
            # overload that makes stopping urgent it may be full — and
            # swallowing the resulting `Full` would leave the consumer
            # blocked on `get()` forever, hanging the worker thread on
            # shutdown. Make room for the sentinel instead.
            for _ in range(2):
                try:
                    self._queue.put_nowait(_SENTINEL)
                    break
                except queue.Full:
                    try:
                        self._queue.get_nowait()
                    except queue.Empty:
                        pass
            log.info(
                "live audio capture stopped (dropped %d chunks)",
                self._dropped_chunks,
            )

    def stream(self) -> Iterator[AudioFrame]:
        """Yield `AudioFrame`s as samples arrive, blocking when the queue is
        empty. Terminates when `stop()` is called."""
        if self._stream is None:
            self.start()
        return self._iter()

    @property
    def dropped_chunks(self) -> int:
        """Number of incoming audio chunks the queue had to drop because the
        consumer wasn't keeping up. Should always stay at 0 in steady state."""
        return self._dropped_chunks

    # --------------------------------------------------------- internals

    def _on_audio(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: Any,
        status: Any,
    ) -> None:
        """PortAudio callback. Runs on the audio thread — must not block."""
        del frames, time_info
        if status:
            log.warning("sounddevice status flag: %s", status)
        # `indata` is shape (frame_length, channels). Downmix to mono by
        # averaging across channels — better for multi-mic arrays than
        # picking one channel, which would silently drop half the signal
        # on stereo line-in setups.
        if indata.ndim == 2 and indata.shape[1] > 1:
            samples = indata.mean(axis=1).astype(np.float32, copy=False)
        elif indata.ndim == 2:
            samples = np.asarray(indata[:, 0], dtype=np.float32)
        else:
            samples = np.asarray(indata, dtype=np.float32)
        # Copy so PortAudio can reuse the buffer.
        chunk = _Chunk(samples=samples.copy(), arrived_at=time.monotonic())
        try:
            self._queue.put_nowait(chunk)
        except queue.Full:
            # Don't block PortAudio — drop oldest, push newest.
            self._dropped_chunks += 1
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(chunk)
            except queue.Full:
                pass

    def _iter(self) -> Iterator[AudioFrame]:
        frame_idx = 0
        consumed_drops = 0
        seconds_per_frame = self.frame_length / self.sample_rate
        while True:
            item = self._queue.get()
            if item is _SENTINEL:
                return
            assert isinstance(item, _Chunk)
            samples = item.samples
            # Pad / trim to exact frame_length so consumers see a consistent shape.
            if samples.shape[0] != self.frame_length:
                if samples.shape[0] < self.frame_length:
                    samples = np.concatenate(
                        [
                            samples,
                            np.zeros(self.frame_length - samples.shape[0], dtype=np.float32),
                        ]
                    )
                else:
                    samples = samples[: self.frame_length]
            # Account for audio the queue had to drop before this chunk.
            # Timestamps come from a frame counter, so without this every
            # dropped chunk shifts the whole timeline permanently into the
            # past: lose five seconds early in a service and every later
            # frame is stamped five seconds early for the rest of it,
            # cumulatively. Downstream that corrupts the filter's `dt`, the
            # scheduler's timing, and the alignment between a recording's
            # audio and its ground truth (which is stamped from the wall
            # clock, not this one).
            #
            # Skipping the counter forward instead leaves an honest *gap*
            # where audio was lost, and everything after it stays aligned
            # with real elapsed time.
            gap = self._dropped_chunks - consumed_drops
            if gap > 0:
                frame_idx += gap
                consumed_drops += gap
            t_start = frame_idx * seconds_per_frame
            t_end = t_start + seconds_per_frame
            yield AudioFrame(
                samples=samples,
                t_start=t_start,
                t_end=t_end,
                sample_rate=self.sample_rate,
            )
            frame_idx += 1


# --------------------------------------------------------------------------- helpers


@dataclass
class _Chunk:
    samples: np.ndarray
    arrived_at: float


_SENTINEL = object()

_ChunkOrSentinel = "_Chunk | object"


def _import_sounddevice() -> Any | None:
    try:
        import sounddevice as sd

        return sd
    except (ImportError, OSError):
        return None


def _resolve_input_device(
    sd: Any, device: int | str | None
) -> tuple[int | None, str, int]:
    """Pick the actual device + channel count to open the input stream with.

    Returns `(index, name, channels)`. The index is what's passed to
    `InputStream(device=...)`; `None` means "use the PortAudio default
    input". The channel count is the value we open the stream with —
    capped at 2 because we always downmix to mono in the callback.

    Resolution strategy (best-effort, never blow up the session if a
    usable input exists somewhere):

      1. If `device` was specified, look it up in the full device list.
         Integers are treated as indices into the list; strings do a
         case-insensitive substring match against device names.
         If the resolved device is input-capable, use it.
      2. Otherwise (no device, or the requested one isn't input-capable),
         fall back to the PortAudio default input device.
      3. If even that doesn't work, scan the device list for the first
         input-capable device.
      4. If nothing has input channels at all, raise.

    The user can save a stale device prefs from an older session; rather
    than failing the session with a cryptic PortAudio error, we log
    what got picked instead so the operator can re-select in Settings.
    """
    try:
        device_list = list(sd.query_devices())
    except (AttributeError, NotImplementedError, TypeError):
        # Sounddevice version / mock that doesn't expose query_devices().
        # Fall through to PortAudio defaults; assume mono.
        return (device if isinstance(device, int) else None, str(device or "default"), 1)
    except Exception as exc:
        raise RuntimeError(
            f"Couldn't enumerate audio devices: {exc}. "
            f"Check that the system audio service is running."
        ) from exc

    # 1) Try the requested device.
    if device is not None:
        match = _find_input_device(device_list, device)
        if match is not None:
            return match
        log.warning(
            "requested audio device %r is not input-capable; falling back to system default",
            device,
        )

    # 2) Try the OS default input.
    try:
        default_info = sd.query_devices(kind="input")
        default_idx, default_name, default_max = _info_to_tuple(default_info)
        if default_max > 0:
            return default_idx, default_name, _capped_channels(default_max)
    except Exception:  # pragma: no cover - defensive
        pass

    # 3) Scan for the first input-capable device.
    for idx, info in enumerate(device_list):
        max_in = int(info.get("max_input_channels", 0) or 0)
        if max_in > 0:
            return idx, str(info.get("name", f"device {idx}")), _capped_channels(max_in)

    raise RuntimeError(
        "No audio input device available. Connect a microphone or audio "
        "interface, then open Settings → Audio."
    )


def _find_input_device(
    device_list: list, device: int | str
) -> tuple[int | None, str, int] | None:
    """Look up an input-capable device by index or name. None if no match."""
    if isinstance(device, int):
        if 0 <= device < len(device_list):
            info = device_list[device]
            max_in = int(info.get("max_input_channels", 0) or 0)
            if max_in > 0:
                return (
                    device,
                    str(info.get("name", f"device {device}")),
                    _capped_channels(max_in),
                )
        return None
    if isinstance(device, str) and device:
        # Case-insensitive substring match, but ONLY consider input-capable
        # devices — avoids the bug where a saved name substring-matches
        # an output device (e.g. "MacBook Air" matching Speakers).
        query = device.lower()
        for idx, info in enumerate(device_list):
            max_in = int(info.get("max_input_channels", 0) or 0)
            if max_in <= 0:
                continue
            name = str(info.get("name", "")).lower()
            if query in name:
                return (
                    idx,
                    str(info.get("name", f"device {idx}")),
                    _capped_channels(max_in),
                )
    return None


def _info_to_tuple(info: dict) -> tuple[int | None, str, int]:
    idx = info.get("index")
    return (
        int(idx) if isinstance(idx, int) else None,
        str(info.get("name", "default")),
        int(info.get("max_input_channels", 0) or 0),
    )


def _capped_channels(max_in: int) -> int:
    """Open at most 2 channels even when the device offers more — we
    downmix to mono anyway and a cheaper callback is worth it."""
    return 2 if max_in >= 2 else 1
