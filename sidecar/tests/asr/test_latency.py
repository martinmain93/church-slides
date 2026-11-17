"""Delivery lag: how stale a word is by the time the matcher can act on it."""

from auto_presenter.asr.latency import DeliveryLatency
from auto_presenter.asr.types import TranscriptEvent, Word


def word(text: str, t_start: float, t_end: float) -> Word:
    return Word(text=text, t_start=t_start, t_end=t_end, conf=0.9)


def final(words: list[Word], segment_id: int = 0) -> TranscriptEvent:
    return TranscriptEvent(words=words, is_final=True, segment_id=segment_id)


def test_nothing_is_reported_before_the_first_word():
    assert DeliveryLatency().snapshot() is None


def test_lag_is_measured_per_word_not_per_event():
    """One pass commits several words at once, and the *earliest* of them has
    waited longest. Averaging over events instead of words would understate
    exactly the case that hurts — a slow pass delivering a long backlog."""
    lat = DeliveryLatency()
    lat.observe(final([word("a", 0.0, 1.0), word("b", 1.0, 2.0)]), available_at=5.0)

    snap = lat.snapshot()
    assert snap is not None
    assert snap.samples == 2
    # Lags are 4.0 s and 3.0 s; the median of the two is 3.5 s.
    assert snap.median_ms == 3500.0


def test_partials_are_ignored():
    """A partial is revised on the next pass, so the lag of a word that has
    not settled is not the lag of anything the matcher acts on."""
    lat = DeliveryLatency()
    lat.observe(
        TranscriptEvent(words=[word("a", 0.0, 1.0)], is_final=False, segment_id=0),
        available_at=9.0,
    )
    assert lat.snapshot() is None


def test_the_window_forgets_so_a_pipeline_falling_behind_is_visible():
    """The interesting failure is a machine that keeps up and then stops. A
    lifetime average would bury a lag that has just tripled; this must not."""
    lat = DeliveryLatency(window=4)
    for i in range(4):
        lat.observe(final([word("x", float(i), float(i))], i), available_at=i + 1.0)
    assert lat.snapshot().median_ms == 1000.0

    for i in range(4, 8):
        lat.observe(final([word("x", float(i), float(i))], i), available_at=i + 6.0)
    assert lat.snapshot().median_ms == 6000.0
    assert lat.snapshot().samples == 4


def test_p90_is_reported_because_the_tail_is_not_the_median():
    """Measured on the goldens, Whisper's median lag was 2.85 s against a p90
    of 8.78 s — a tail three times the middle. A median alone would say the
    pipeline was fine."""
    lat = DeliveryLatency()
    for i in range(9):
        lat.observe(final([word("x", float(i), float(i))], i), available_at=i + 1.0)
    lat.observe(final([word("x", 9.0, 9.0)], 9), available_at=19.0)

    snap = lat.snapshot()
    assert snap.median_ms == 1000.0
    assert snap.p90_ms == 10000.0
