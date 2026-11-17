"""The matcher stack the app actually runs, in one place.

Every eval harness needs to build the same engine the server builds, and each
one that rebuilds it by hand is a copy that silently stops matching. That has
now happened four times: `trace.py` hardcoded its own windowing and
`snap_window`, `perf_check.py` kept a comment saying it mirrors `server.py`
while it no longer did, the integration suite pinned `snap_window=1`, and
`run_realtime_eval.py` went on constructing a particle filter under the heading
"Particle filter (production default)" for a week after the default became the
forward filter — reporting 0.844 wrong-slide for a matcher production does not
run.

The failure is quiet in the worst way: the harness runs, produces a number, and
the number is about something else. So the construction lives here, and callers
ask for it rather than describe it.
"""

from __future__ import annotations

from auto_presenter.decision.engine import ParticleFilterEngine
from auto_presenter.decision.plan_gate import PlanGate
from auto_presenter.decision.scheduler import (
    LeadTimeScheduler,
    LeadTimeSchedulerConfig,
)
from auto_presenter.index.library import IndexedSetlist
from auto_presenter.protocol import ParticleFilterTunables
from auto_presenter.tracker.forward import ForwardFilter, ForwardTracker


def build_engine(
    indexed: IndexedSetlist,
    tunables: ParticleFilterTunables | None = None,
) -> ParticleFilterEngine:
    """The production matcher: dense forward filter behind the lead-time scheduler.

    The forward filter replaces the particle cloud *and* the three components
    built around it — ConfidenceMonitor, RelocalizeController, LostHoldPolicy —
    because structural jumps evaluated inside the recursion do what those
    approximate after a detection delay.

    **Two harnesses, and the difference between them matters more than the
    tuning did.** Most of this project's numbers come from replaying a cached
    *offline* faster-whisper pass (`eval.trace.real_asr_schedule`) through a
    mock backend: realistic timing, but every word transcribed with unlimited
    right context. `research/eval/run_realtime_eval.py` instead runs the real
    `SlidingWindowWrapper(WhisperBackend)` the app runs — a 6 s window with
    1.5 s of right context — and that is what production sounds like.

    Wrong-slide rate, six approved goldens, 2026-08-29:

                             cached offline words    live windowed ASR
        particle                     0.327                 0.844
        forward                      0.296                 0.641
        naive (Phase 5)                  —                 0.675

    The ordering holds and the margin grows — forward wins on all six
    recordings live, by 0.203 mean, against 0.031 on the cached transcript. But
    the absolute rate is roughly double, and 0.641 is the number that matches
    what the app feels like to use. Any tuning done on the cached harness is
    tuning against words that are cleaner than the ones the matcher will get.

    Phase 7 is not merely unnecessary here, it is harmful: its thresholds are
    calibrated against a particle cloud's marginal, and with the monitor on and
    the hold off the screen freezes outright (0.835).
    """
    t = tunables or ParticleFilterTunables()
    return ParticleFilterEngine(
        filter=ForwardTracker(filter=ForwardFilter(setlist=indexed)),
        scheduler=LeadTimeScheduler(
            setlist=indexed,
            config=LeadTimeSchedulerConfig(
                target_margin_ms=t.target_margin_ms,
                spoken_margin_ms=t.spoken_margin_ms,
                marginal_threshold=t.marginal_threshold,
            ),
            plan_gate=PlanGate(min_words=t.off_plan_min_words) if t.off_plan_min_words > 0 else None,
        ),
    )
