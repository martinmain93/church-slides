"""What a per-frame stage costs: its share of real time, and its slow frame."""

from __future__ import annotations

import pytest

from auto_presenter.pipeline.stage_cost import StageCost


def test_cost_per_audio_second_and_the_slow_frame():
    cost = StageCost()
    for _ in range(19):
        cost.add(1.0, 0.1)  # 1 ms for each 0.1 s frame
    cost.add(40.0, 0.1)  # one read-out frame
    per_s, p95 = cost.take()
    assert per_s == pytest.approx(29.5)  # 59 ms of work over 2 s of audio
    assert p95 == 40.0


def test_each_take_starts_a_new_window():
    cost = StageCost()
    assert cost.take() is None
    cost.add(2.0, 0.1)
    per_s, p95 = cost.take()
    assert (per_s, p95) == (pytest.approx(20.0), 2.0)
    assert cost.take() is None
