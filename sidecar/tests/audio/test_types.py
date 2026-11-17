"""AudioFrame data-type invariants."""

from __future__ import annotations

import numpy as np
import pytest

from auto_presenter.audio.types import AudioFrame, SAMPLE_RATE


def test_frame_basic_construction():
    samples = np.zeros(1600, dtype=np.float32)
    f = AudioFrame(samples=samples, t_start=0.0, t_end=0.1)
    assert f.duration_seconds == pytest.approx(0.1)
    assert f.n_samples == 1600
    assert f.sample_rate == SAMPLE_RATE


def test_rejects_non_float32():
    samples = np.zeros(100, dtype=np.float64)
    with pytest.raises(ValueError, match="float32"):
        AudioFrame(samples=samples, t_start=0.0, t_end=0.1)


def test_rejects_2d():
    samples = np.zeros((100, 2), dtype=np.float32)
    with pytest.raises(ValueError, match="1-D mono"):
        AudioFrame(samples=samples, t_start=0.0, t_end=0.1)


def test_rejects_inverted_timespan():
    samples = np.zeros(100, dtype=np.float32)
    with pytest.raises(ValueError, match="t_end"):
        AudioFrame(samples=samples, t_start=0.5, t_end=0.1)
