"""Device enumeration tests.

These tests exercise the wrapper logic over `sounddevice.query_devices()`;
they don't require real audio hardware. The wrapper has to swallow
PortAudio failures gracefully because the CI runner often lacks an
input device.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import patch

from auto_presenter.audio.devices import list_input_devices


def test_returns_empty_when_sounddevice_unavailable():
    """If `import sounddevice` raises, return [] rather than crashing."""
    # Block the import. importlib will raise ImportError when the loader
    # returns None — we simulate that by clearing the module and using a
    # finder that always fails.
    with patch.dict(sys.modules, {"sounddevice": None}):
        # `sys.modules[name] = None` makes import raise ImportError.
        out = list_input_devices()
    assert out == []


def test_filters_out_output_only_devices():
    fake_sd = SimpleNamespace(
        query_devices=lambda: [
            {"name": "Built-in Output", "max_input_channels": 0, "default_samplerate": 48000.0},
            {"name": "USB Mic", "max_input_channels": 1, "default_samplerate": 16000.0},
            {"name": "Some other", "max_input_channels": 2, "default_samplerate": 44100.0},
        ],
        default=SimpleNamespace(device=[1, 0]),
    )
    with patch.dict(sys.modules, {"sounddevice": fake_sd}):
        out = list_input_devices()
    names = [d.name for d in out]
    assert names == ["USB Mic", "Some other"]
    # The USB Mic was index 1 in the original list, marked default.
    assert out[0].is_default is True
    assert out[1].is_default is False


def test_default_device_index_handles_scalar_form():
    """`sd.default.device` can be either `[in, out]` or a single int."""
    fake_sd = SimpleNamespace(
        query_devices=lambda: [
            {"name": "Mic A", "max_input_channels": 1, "default_samplerate": 16000.0},
            {"name": "Mic B", "max_input_channels": 1, "default_samplerate": 16000.0},
        ],
        default=SimpleNamespace(device=0),  # scalar
    )
    with patch.dict(sys.modules, {"sounddevice": fake_sd}):
        out = list_input_devices()
    assert out[0].is_default is True
    assert out[1].is_default is False


def test_handles_no_default_device():
    fake_sd = SimpleNamespace(
        query_devices=lambda: [
            {"name": "Mic A", "max_input_channels": 1, "default_samplerate": 16000.0},
        ],
        default=SimpleNamespace(device=None),
    )
    with patch.dict(sys.modules, {"sounddevice": fake_sd}):
        out = list_input_devices()
    assert out == [
        out[0]
    ]
    assert out[0].is_default is False


def test_returns_empty_when_query_raises():
    """sounddevice can raise on PortAudio init errors; we should not crash."""

    def boom():
        raise RuntimeError("PortAudio not available")

    fake_sd = SimpleNamespace(
        query_devices=boom,
        default=SimpleNamespace(device=None),
    )
    with patch.dict(sys.modules, {"sounddevice": fake_sd}):
        out = list_input_devices()
    assert out == []


def test_exposes_max_input_channels_and_default_samplerate():
    fake_sd = SimpleNamespace(
        query_devices=lambda: [
            {"name": "Studio", "max_input_channels": 8, "default_samplerate": 48000.0},
        ],
        default=SimpleNamespace(device=[0, 0]),
    )
    with patch.dict(sys.modules, {"sounddevice": fake_sd}):
        out = list_input_devices()
    assert out[0].max_input_channels == 8
    assert out[0].default_samplerate == 48000.0


def test_a_rescan_reinitializes_portaudio_under_the_lock():
    from auto_presenter.audio.devices import PORTAUDIO_LOCK, rescan_input_devices

    seen: list[str] = []
    fake_sd = SimpleNamespace(
        _terminate=lambda: seen.append(f"terminate locked={PORTAUDIO_LOCK.locked()}"),
        _initialize=lambda: seen.append("initialize"),
        query_devices=lambda: [],
        default=SimpleNamespace(device=[0, 0]),
    )
    with patch.dict(sys.modules, {"sounddevice": fake_sd}):
        rescan_input_devices()
        list_input_devices(rescan=True)
    assert seen == ["terminate locked=True", "initialize"] * 2
