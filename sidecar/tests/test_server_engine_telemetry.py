"""The engine-telemetry broadcast: what the matcher is about to do.

These assert the *broadcast*, not the arithmetic — `tests/decision/test_scheduler.py`
covers the projection itself. What can only break here is the wiring: reading
the numbers off the live objects, and going out on every belief update rather
than only when a decision fires. A readout that stops updating exactly when the
engine gets stuck is worse than no readout.
"""

from __future__ import annotations

import pytest

from auto_presenter import server as server_mod
from auto_presenter.asr.latency import DeliveryLatency
from auto_presenter.asr.types import TranscriptEvent, Word
from auto_presenter.decision.production import build_engine
from auto_presenter.index.library import build_index
from auto_presenter.protocol import (
    EngineTelemetryMessage,
    SetlistContent,
    SetlistItemContent,
)


@pytest.fixture
def sent(monkeypatch):
    """Collect everything the server broadcasts."""
    out: list[object] = []

    async def capture(msg):
        out.append(msg)

    monkeypatch.setattr(server_mod, "_broadcast", capture)
    return out


def _telemetry(sent) -> EngineTelemetryMessage:
    msgs = [m for m in sent if isinstance(m, EngineTelemetryMessage)]
    assert msgs, "no engine telemetry was broadcast"
    return msgs[-1]


def _engine():
    content = SetlistContent(
        items=[
            SetlistItemContent(
                item_idx=0,
                song_id="a",
                slides=[
                    [],
                    ["amazing", "grace", "how", "sweet", "the", "sound"],
                    ["that", "saved", "a", "wretch", "like", "me"],
                ],
            )
        ]
    )
    return build_engine(build_index(content))


@pytest.mark.asyncio
async def test_telemetry_goes_out_on_every_belief_even_with_no_decision(sent):
    """The case worth seeing is the engine *not* committing. If telemetry only
    rode along with decisions it would go quiet during precisely the runs an
    operator opens the panel to diagnose."""
    engine = _engine()
    server_mod._state.matcher = engine

    await server_mod._broadcast_belief_for_engine(engine)
    before = len([m for m in sent if isinstance(m, EngineTelemetryMessage)])

    engine.update(
        TranscriptEvent(
            words=[Word(text="amazing", t_start=0.0, t_end=0.5, conf=0.9)],
            is_final=True,
            segment_id=0,
        )
    )
    await server_mod._broadcast_belief_for_engine(engine)

    assert len([m for m in sent if isinstance(m, EngineTelemetryMessage)]) == before + 1


@pytest.mark.asyncio
async def test_countdown_and_rate_come_from_the_live_scheduler(sent):
    engine = _engine()
    server_mod._state.matcher = engine
    for i, text in enumerate(["amazing", "grace", "how", "sweet"]):
        engine.update(
            TranscriptEvent(
                words=[Word(text=text, t_start=i * 0.5, t_end=i * 0.5 + 0.5, conf=0.9)],
                is_final=True,
                segment_id=i,
            )
        )

    await server_mod._broadcast_belief_for_engine(engine)
    msg = _telemetry(sent)

    # The lead is derived, and reported as its parts so a reader can see which
    # term moved: lag + gap + margin.
    assert msg.lead_time_ms == pytest.approx(
        engine.scheduler.effective_lead_seconds() * 1000.0
    )
    assert msg.lead_lag_ms > 0.0  # falls back to the measured median
    assert msg.target_margin_ms == float(engine.scheduler.config.target_margin_ms)
    assert msg.seconds_remaining is not None
    assert msg.rate_wps is not None and msg.rate_wps > 0
    assert msg.words_on_slide == 6
    assert 0.0 <= msg.position <= 1.0


@pytest.mark.asyncio
async def test_latency_is_reported_from_the_session_that_measured_it(sent):
    engine = _engine()
    server_mod._state.matcher = engine

    class FakeSession:
        def __init__(self):
            self.latency = DeliveryLatency()

        def latency_snapshot(self):
            return self.latency.snapshot()

    session = FakeSession()
    session.latency.observe(
        TranscriptEvent(
            words=[Word(text="x", t_start=0.0, t_end=1.0, conf=0.9)],
            is_final=True,
            segment_id=0,
        ),
        available_at=4.0,
    )
    server_mod._state.audio_session = session

    await server_mod._broadcast_belief_for_engine(engine)
    msg = _telemetry(sent)
    assert msg.latency_median_ms == 3000.0
    assert msg.latency_samples == 1


@pytest.mark.asyncio
async def test_an_unmeasured_lag_is_reported_as_nothing_not_as_zero(sent):
    """Zero milliseconds of lag reads as "instant", which is the opposite of
    the truth before any audio has been transcribed. It stays None so the UI
    shows a dash."""
    engine = _engine()
    server_mod._state.matcher = engine
    server_mod._state.audio_session = None

    await server_mod._broadcast_belief_for_engine(engine)
    msg = _telemetry(sent)
    assert msg.latency_median_ms is None
    assert msg.latency_p90_ms is None
    assert msg.latency_samples == 0


@pytest.mark.asyncio
async def test_a_cold_engine_still_reports_a_countdown(sent):
    """The cold-start prior sits on the songs' opening slides rather than on
    the blank lead-in, so there is a real projection to report before a word
    has been heard — and it should be visible, since an opening that never
    fires is one of the failures this panel is for."""
    engine = _engine()
    server_mod._state.matcher = engine

    await server_mod._broadcast_belief_for_engine(engine)
    msg = _telemetry(sent)
    assert msg.target_slide_idx == 1  # slide 0 is the blank lead-in
    assert msg.seconds_remaining == pytest.approx(6.0)  # 6 words at the 1.0 w/s prior
    assert msg.position == 0.0
    assert msg.advancing is False


# --- the freeze watchdog reaching the display ---------------------------------


@pytest.mark.asyncio
async def test_a_watchdog_decision_reaches_the_director(sent, monkeypatch):
    """The tick is driven by pipeline health, which arrives on a cadence
    whether or not any words do — that is the whole point, since the stall it
    exists for is caused by words *stopping*. A decision from there has to
    take the same route as one from a transcript event, or the watchdog fires
    into nothing."""
    from auto_presenter.pipeline.audio_session import PipelineHealth

    engine = _engine()
    server_mod._state.matcher = engine

    seen: list[object] = []

    class FakeDirector:
        def on_decision(self, d):
            seen.append(d)

    server_mod._state.director = FakeDirector()

    # A belief the screen disagrees with, too unconfident to commit normally.
    engine.update(
        TranscriptEvent(
            words=[Word(text="amazing", t_start=0.0, t_end=0.5, conf=0.9)],
            is_final=True,
            segment_id=0,
        )
    )
    engine.scheduler.on_position(
        server_mod.TrackedPosition(
            item_idx=0, slide_idx=2, confidence=0.25, position=0.5,
            rate=1.0, t_seconds=1.0,
        )
    )

    health = PipelineHealth(lag_seconds=0.1, dropped_chunks=0, audio_seconds=2.0)
    await server_mod._broadcast_pipeline_health(health)
    assert not seen, "fired before the stall window elapsed"

    # No transcript at all in between — only the audio clock moves.
    health = PipelineHealth(lag_seconds=0.1, dropped_chunks=0, audio_seconds=40.0)
    await server_mod._broadcast_pipeline_health(health)
    assert seen, "the watchdog never reached the director"
    assert seen[-1].slide_idx == 2


@pytest.mark.asyncio
async def test_the_tick_is_harmless_without_a_matcher(sent):
    from auto_presenter.pipeline.audio_session import PipelineHealth

    server_mod._state.matcher = None
    server_mod._state.director = None
    await server_mod._broadcast_pipeline_health(
        PipelineHealth(lag_seconds=0.1, dropped_chunks=0, audio_seconds=99.0)
    )
@pytest.mark.asyncio
async def test_the_lead_gets_its_lag_with_nobody_watching(monkeypatch):
    """The measured lag must reach the scheduler off the *belief* path.

    `effective_lead_seconds` is `lag + gap + margin`, so this feeds every
    commit decision. It used to be assigned inside the engine-telemetry
    broadcast, which happens to run before `_broadcast`'s "no clients, no
    work" early return — so it worked, but only by accident of ordering.
    Skipping telemetry when no debug panel is open is an obvious future
    optimization, and it would have dropped the lead back to
    `fallback_lag_seconds` on a live service with nothing failing anywhere.

    So: no `_broadcast` stub here, and `_state.clients` deliberately empty —
    the real early return fires, and the lag still has to land.
    """
    from contextlib import contextmanager

    engine = _engine()

    class FakePerf:
        @contextmanager
        def stage(self, name):
            yield

    class FakeSession:
        def __init__(self):
            self.latency = DeliveryLatency()
            self.perf = FakePerf()

        def latency_snapshot(self):
            return self.latency.snapshot()

    session = FakeSession()
    session.latency.observe(
        TranscriptEvent(
            words=[Word(text="x", t_start=0.0, t_end=1.0, conf=0.9)],
            is_final=True,
            segment_id=0,
        ),
        available_at=3.5,
    )

    monkeypatch.setattr(server_mod._state, "audio_session", session)
    monkeypatch.setattr(server_mod._state, "matcher", engine)
    monkeypatch.setattr(server_mod._state, "clients", set())

    assert engine.scheduler.observed_lag_seconds is None

    class _Director:
        def on_decision(self, decision):
            pass

    handler = server_mod._make_transcript_handler(engine, _Director())
    await handler(
        TranscriptEvent(
            words=[Word(text="amazing", t_start=0.0, t_end=0.5, conf=0.9)],
            is_final=True,
            segment_id=1,
        )
    )

    assert engine.scheduler.observed_lag_seconds == pytest.approx(2.5)
    # And the lead the commit rule uses reflects it, rather than the fallback.
    assert engine.scheduler.effective_lead_seconds() == pytest.approx(
        max(
            engine.scheduler.config.min_lead_seconds,
            min(
                engine.scheduler.config.max_lead_seconds,
                2.5 + engine.scheduler.config.target_margin_ms / 1000.0,
            ),
        )
    )
