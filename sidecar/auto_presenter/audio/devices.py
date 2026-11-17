"""Audio input device enumeration.

`list_input_devices()` returns the system's input-capable devices in a
serializable shape so the React UI can render a picker. The shape is
intentionally narrow — we only expose what the UI actually needs.

The sounddevice import is lazy so importing `auto_presenter` doesn't
require PortAudio to be loadable. That matters in CI / tests where
PortAudio isn't installed.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:  # pragma: no cover
    pass

log = logging.getLogger(__name__)


#: Held while PortAudio is re-initialized and while a capture stream opens.
#: Re-initializing it under a stream that is part-way through opening can
#: crash natively, so the two never overlap — even when a device list and a
#: session start race each other.
PORTAUDIO_LOCK = threading.Lock()

#: Capture streams open or being opened, across every `LiveCapture`. Guarded
#: by `PORTAUDIO_LOCK`. The server's own idea of "no capture alive" is read
#: off its current session, which it forgets after a bounded join — a capture
#: thread still stuck opening a slow device outlives that. A rescan checks
#: this as well and skips while anything holds a stream.
_live_streams = 0


def stream_opening() -> None:
    """Count a stream about to open. Call with `PORTAUDIO_LOCK` held."""
    global _live_streams
    _live_streams += 1


def stream_closed() -> None:
    """Uncount a closed stream. Takes `PORTAUDIO_LOCK` itself."""
    with PORTAUDIO_LOCK:
        stream_closed_locked()


def stream_closed_locked() -> None:
    """Uncount a stream, with `PORTAUDIO_LOCK` already held."""
    global _live_streams
    _live_streams = max(0, _live_streams - 1)


def live_stream_count() -> int:
    return _live_streams


class AudioInputDevice(BaseModel):
    """A single input-capable audio device, as the UI sees it."""

    index: int
    name: str
    is_default: bool
    max_input_channels: int
    default_samplerate: float


def list_input_devices(*, rescan: bool = False) -> list[AudioInputDevice]:
    """Enumerate input-capable audio devices via PortAudio (sounddevice).

    Returns an empty list if PortAudio isn't available or no input devices
    exist; the UI surfaces this as "no microphone configured" rather than
    crashing.

    PortAudio scans devices once, when it initializes, so an interface
    plugged in after the engine started never showed up until the app was
    restarted. `rescan` re-initializes it first — only safe with no stream
    open, which the caller has to know.
    """
    try:
        import sounddevice as sd
    except (ImportError, OSError):
        # OSError covers "PortAudio library not found" on systems where
        # the wheel installed but the runtime lib is missing.
        return []

    try:
        if rescan:
            with PORTAUDIO_LOCK:
                _rescan(sd)
                all_devices = sd.query_devices()
                default_input_idx = _default_input_index(sd)
        else:
            all_devices = sd.query_devices()
            default_input_idx = _default_input_index(sd)
    except Exception:
        # sounddevice raises sd.PortAudioError on driver issues. Treat any
        # failure as "no devices" — better than crashing the sidecar.
        return []

    out: list[AudioInputDevice] = []
    for idx, dev in enumerate(all_devices):
        max_in = int(dev.get("max_input_channels", 0))
        if max_in <= 0:
            continue
        out.append(
            AudioInputDevice(
                index=idx,
                name=str(dev.get("name", f"device {idx}")),
                is_default=(idx == default_input_idx),
                max_input_channels=max_in,
                default_samplerate=float(dev.get("default_samplerate", 0.0) or 0.0),
            )
        )
    return out


def rescan_input_devices() -> None:
    """Re-initialize PortAudio so devices plugged in since are visible.

    Only safe with no capture stream open — the caller has to know. Blocks
    (PortAudio's scan can take a while on some drivers); run it off the
    event loop.
    """
    try:
        import sounddevice as sd
    except (ImportError, OSError):
        return
    with PORTAUDIO_LOCK:
        _rescan(sd)


def _rescan(sd) -> None:  # type: ignore[no-untyped-def]
    """Call with `PORTAUDIO_LOCK` held."""
    if _live_streams > 0:
        log.info("device rescan skipped: %d capture stream(s) open", _live_streams)
        return
    try:
        sd._terminate()
        sd._initialize()
    except Exception:
        log.warning("couldn't rescan audio devices", exc_info=True)


def _default_input_index(sd) -> int | None:  # type: ignore[no-untyped-def]
    """sd.default.device is `[input_idx, output_idx]` or a single value."""
    default = getattr(sd.default, "device", None)
    if default is None:
        return None
    if isinstance(default, (list, tuple)) and len(default) >= 1:
        d = default[0]
        return int(d) if isinstance(d, int) and d >= 0 else None
    if isinstance(default, int) and default >= 0:
        return int(default)
    return None
