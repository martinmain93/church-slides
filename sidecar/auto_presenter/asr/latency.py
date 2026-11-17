"""How stale the transcript is by the time the matcher can act on it.

The number the rest of the system has been missing. A word sung at t=100 is
not reported until the sliding window holds `stable_margin_seconds` of right
context past it and a pass has run, so the matcher first sees it nearer
t=103 — and because a `TranscriptEvent` carries only the sung time, every
decision downstream is stamped as though it were acted on instantly.

Offline that showed up as the harness crediting anticipation the room never
saw. Live it shows up as slides arriving late for no visible reason. Both want
the same measurement, so `SlidingWindowWrapper.emission_observer` feeds this
and the Perf tab reads it.

Deliberately a rolling window rather than a lifetime average: the interesting
failure is a machine that *starts* keeping up and stops, when a pass begins
to overrun its step and the wrapper's adaptive pacing widens the gap.
"""

from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass

from auto_presenter.asr.types import TranscriptEvent


@dataclass(frozen=True)
class LatencySnapshot:
    """Delivery lag over the recent past, in milliseconds."""

    median_ms: float
    p90_ms: float
    #: Words the estimate is over. Small counts are noisy; the UI says so.
    samples: int


class DeliveryLatency:
    """Rolling delivery-lag estimate, fed one emitted event at a time."""

    def __init__(self, window: int = 200) -> None:
        self._lags: deque[float] = deque(maxlen=window)
        #: Audio-timeline second the latest final reached the matcher — the
        #: "now" a consumer extrapolating a stale belief should aim for.
        self.last_available_at: float | None = None

    def observe(self, event: TranscriptEvent, available_at: float) -> None:
        """Record one event's lag. Safe to pass as `emission_observer`.

        Partials are skipped: they are revised on the next pass, so the lag of
        a word that has not settled is not the lag of anything the matcher
        acts on.
        """
        if not event.is_final:
            return
        self.last_available_at = available_at
        for word in event.words:
            self._lags.append(available_at - word.t_end)

    def snapshot(self) -> LatencySnapshot | None:
        """`None` until at least one word has been delivered."""
        if not self._lags:
            return None
        ordered = sorted(self._lags)
        return LatencySnapshot(
            median_ms=statistics.median(ordered) * 1000.0,
            p90_ms=ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))] * 1000.0,
            samples=len(ordered),
        )

    def reset(self) -> None:
        self._lags.clear()
        self.last_available_at = None
