"""The machine check's verdicts, with its timings stubbed: what it passes,
what it fails, and when it won't judge."""

from __future__ import annotations

import pytest

from auto_presenter.asr import machine_check as mc
from auto_presenter.pipeline.power import PowerState


@pytest.fixture
def timings(monkeypatch):
    def use(transcribe_ms: float, score_ms: float, power: PowerState | None = None):
        monkeypatch.setattr(
            mc, "time_transcription", lambda model, pad: [transcribe_ms] * mc.REPEATS
        )
        monkeypatch.setattr(mc, "time_scoring", lambda scorer: [score_ms] * mc.REPEATS)
        monkeypatch.setattr(mc, "time_chords", lambda: 60.0)
        monkeypatch.setattr(mc, "RETRY_PAUSE_S", 0.0)
        monkeypatch.setattr(
            "auto_presenter.pipeline.power.read_power_state", lambda: power or PowerState()
        )

    return use


def test_the_m1_cpu_measurement_passes(timings):
    # what the check measured on the M1 (2026-10-15), which ran clean in real time
    timings(434.3, 570.6)
    a = mc.assess_cpu(object(), object(), 2.0)
    assert a.verdict == "full"
    assert a.speech_load == pytest.approx(0.72, abs=0.01)


def test_the_small_scorer_on_that_m1_fails(timings):
    # it overloaded from the first recording in real time
    timings(480.6, 1930.8)
    # too slow to follow; a pass every 2 s (0.24) still keeps up for Assist
    assert mc.assess_cpu(object(), object(), 2.0).verdict == "assist"


def test_a_slower_computer_is_offered_assist(timings):
    # the 2-thread Windows runner: 634 ms + 1453 ms (base.en fp32, unshared):
    # too slow to follow (1.36), but a pass every 2 s is a third of real time
    timings(634.0, 1453.0)
    a = mc.assess_cpu(object(), object(), 2.0)
    assert a.verdict == "assist"
    assert a.assist_load == pytest.approx(0.317, abs=0.001)
    assert "Assist" in a.reason


def test_too_slow_even_for_assist(timings):
    # a transcription pass that takes most of 2 s: no headroom for anything
    timings(2000.0, 3000.0)
    a = mc.assess_cpu(object(), object(), 2.0)
    assert a.verdict == "insufficient"
    assert a.assist_load == pytest.approx(1.0)
    assert "falls behind" in a.reason


def test_assist_needs_its_own_bar_not_the_full_one(timings):
    # just inside Assist's bar (0.95 at a pass every 2 s)
    timings(1880.0, 0.0)
    assert mc.assess_cpu(object(), object(), 2.0).verdict == "assist"
    timings(1920.0, 0.0)
    assert mc.assess_cpu(object(), object(), 2.0).verdict == "insufficient"


def test_it_wont_judge_a_computer_saving_power(timings):
    timings(100.0, 100.0, PowerState(low_power_mode=True))
    a = mc.assess_cpu(object(), object(), 2.0)
    assert a.verdict == "unknown"
    assert "Low Power Mode" in a.reason


def test_the_hymn_spans_are_a_realistic_set():
    spans = mc.assessment_spans()
    # a setlist's worth, like the 160 of What A Beautiful Name
    assert 120 <= len(spans) <= 260
    assert all(1 <= len(s.split()) <= 6 for s in spans)


def test_the_noise_is_the_same_everywhere():
    assert (mc.noise(1.0) == mc.noise(1.0)).all()


def test_a_busy_moment_is_retried_before_failing(monkeypatch):
    """Something else running only adds time, so a slow first attempt is
    measured again: an M1 that measures 0.72 alone measured 0.94 with a
    second check running beside it."""
    attempts = iter([(900.0, 900.0), (434.3, 570.6)])
    current = {}

    def tr(model, pad):
        current["t"], current["s"] = next(attempts)
        return [current["t"]] * mc.REPEATS

    monkeypatch.setattr(mc, "time_transcription", tr)
    monkeypatch.setattr(mc, "time_scoring", lambda scorer: [current["s"]] * mc.REPEATS)
    monkeypatch.setattr(mc, "time_chords", lambda: 60.0)
    monkeypatch.setattr(mc, "RETRY_PAUSE_S", 0.0)
    monkeypatch.setattr("auto_presenter.pipeline.power.read_power_state", lambda: PowerState())
    a = mc.assess_cpu(object(), object(), 2.0)
    assert a.verdict == "full"
    assert a.speech_load == pytest.approx(0.72, abs=0.01)


def test_a_slow_computer_fails_every_attempt(timings, monkeypatch):
    calls = []
    timings(634.0, 1453.0)
    original = mc.time_transcription
    monkeypatch.setattr(mc, "time_transcription", lambda m, p: calls.append(1) or original(m, p))
    # every attempt is tried for the full bar before settling for Assist
    assert mc.assess_cpu(object(), object(), 2.0).verdict == "assist"
    assert len(calls) == mc.ATTEMPTS


def test_one_slow_pass_does_not_flip_the_verdict():
    """The M1's saved check: one 498 ms transcription among ~413 ms ones. By
    the slowest pass the load read 0.78 (a fail); by the median, 0.67."""
    transcribe = [497.6, 411.8, 402.4, 412.6, 421.5]
    score = [478.1, 542.3, 466.7, 519.5, 554.5]
    assert mc.speech_load(transcribe, score) == pytest.approx(0.672, abs=0.005)
    assert mc.speech_load(transcribe, score) <= mc.LOAD_LIMIT
