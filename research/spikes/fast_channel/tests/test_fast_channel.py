"""The low-latency second listener.

It is a diagnostic, not a decision-maker — it must never be able to break the
app by being absent, broken, or wrong.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from auto_presenter.asr.fast_channel import FastChannel, FastWord, _model_stem


def test_latency_is_the_gap_between_sung_and_known():
    """The number the whole feature exists to show. Whisper's is 2-3 s."""
    assert FastWord("grace", t_start=10.0, t_emitted=10.42).latency == pytest.approx(0.42)


def test_latency_never_goes_negative():
    """Clock skew must not produce a nonsense figure in the panel."""
    assert FastWord("grace", t_start=10.0, t_emitted=9.8).latency == 0.0


def test_a_missing_model_is_not_an_error(tmp_path):
    """The model is a large optional download. Its absence disables the panel
    and must not touch the audio pipeline."""
    channel = FastChannel(model_dir=tmp_path / "nope")
    assert channel.start() is False
    assert channel.available is False


def test_feeding_an_unavailable_channel_is_harmless(tmp_path):
    channel = FastChannel(model_dir=tmp_path)
    channel.start()
    assert channel.feed(np.zeros(1600, dtype=np.float32)) == []
    assert channel.realtime_factor == 0.0


def test_model_stem_is_discovered_from_the_encoder_filename(tmp_path):
    """Releases name their files differently — `epoch-99-avg-1` in one, and
    `epoch-99-avg-1-chunk-16-left-128` in another — so the stem is read off
    disk rather than hardcoded."""
    (tmp_path / "encoder-epoch-99-avg-1-chunk-16-left-128.int8.onnx").touch()
    assert _model_stem(tmp_path) == "epoch-99-avg-1-chunk-16-left-128"


def test_no_stem_when_the_directory_holds_no_model(tmp_path):
    (tmp_path / "readme.txt").touch()
    assert _model_stem(tmp_path) is None


def test_status_says_why_it_is_off(tmp_path):
    """The panel shows an instruction based on this, so "which of the three
    things is missing" has to be distinguishable — "not configured" sends
    people hunting through settings that do not exist."""
    from auto_presenter.asr.fast_channel import STATUS_NO_MODEL

    # Without the optional runtime (the `fast` extra) that's what it reports first.
    pytest.importorskip("sherpa_onnx")
    channel = FastChannel(model_dir=tmp_path / "absent")
    channel.start()
    assert channel.status == STATUS_NO_MODEL


def test_probe_does_not_load_a_missing_model(tmp_path):
    from auto_presenter.asr.fast_channel import STATUS_NO_MODEL

    pytest.importorskip("sherpa_onnx")
    channel = FastChannel(model_dir=tmp_path / "absent")
    assert channel.probe() == STATUS_NO_MODEL
    assert channel.available is False


def test_the_default_model_location_is_under_app_data():
    """Beside the library and the recordings, so it survives a rebuild and
    needs no environment variable."""
    from auto_presenter.asr.fast_channel import default_model_dir

    path = default_model_dir()
    assert "models" in path.parts
    assert "fast-channel" in path.parts


def test_a_capture_frame_is_split_before_feeding(tmp_path, monkeypatch):
    """Capture delivers 100 ms frames; the recogniser is fed 50 ms at a time so
    a word surfaces sooner. Measured, chunk size changes only *when* a word
    appears (0.38 s at 50 ms against 0.60 s at 500 ms) and not what is heard —
    word count and error rate were identical across the whole range."""
    from auto_presenter.asr.fast_channel import FEED_SAMPLES

    fed: list[int] = []

    class _Stream:
        def accept_waveform(self, rate, samples):
            fed.append(len(samples))

    class _Recognizer:
        def is_ready(self, stream):
            return False

        def get_result_as_json_string(self, stream):
            return '{"tokens": [], "timestamps": []}'

    channel = FastChannel(model_dir=tmp_path)
    object.__setattr__(channel, "_recognizer", _Recognizer())
    object.__setattr__(channel, "_stream", _Stream())

    channel.feed(np.zeros(1600, dtype=np.float32))       # one 100 ms frame
    assert fed == [FEED_SAMPLES, FEED_SAMPLES]
