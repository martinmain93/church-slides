"""The continuous-mode broadcast: a `flow_position` track per belief update.

`tests/decision/test_flow.py` covers the follower's policy. What can only
break here is the wiring — reading the live posterior, stamping "now" from
the measured delivery time, and holding back while an operator has the
screen — and whether anything reaches the socket at all.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from auto_presenter import server as server_mod
from auto_presenter.asr.latency import DeliveryLatency
from auto_presenter.asr.types import TranscriptEvent, Word
from auto_presenter.decision.production import build_engine
from auto_presenter.index.library import build_index
from auto_presenter.protocol import FlowPositionMessage, SetlistContent, SetlistItemContent

VERSE = ["amazing", "grace", "how", "sweet", "the", "sound"]


@pytest.fixture
def sent(monkeypatch):
    out: list[object] = []

    async def capture(msg):
        out.append(msg)

    monkeypatch.setattr(server_mod, "_broadcast", capture)
    monkeypatch.setattr(server_mod._state, "flow_follower", None)
    monkeypatch.setattr(server_mod._state, "clients", set())
    return out


class _Session:
    def __init__(self):
        self.latency = DeliveryLatency()

        class _Perf:
            @contextmanager
            def stage(self, _name):
                yield

        self.perf = _Perf()

    def latency_snapshot(self):
        return self.latency.snapshot()


class _Director:
    state = "tracking"

    def on_decision(self, decision):
        pass


def _engine(*, spoken: bool = False):
    content = SetlistContent(
        items=[
            SetlistItemContent(
                item_idx=0,
                song_id="a",
                slides=[[], VERSE, ["that", "saved", "a", "wretch", "like", "me"]],
                spoken=spoken,
            )
        ]
    )
    return build_engine(build_index(content))


def _sing(words: list[str], t0: float) -> TranscriptEvent:
    return TranscriptEvent(
        words=[
            Word(text=w, t_start=t0 + 0.5 * i, t_end=t0 + 0.5 * (i + 1), conf=0.9)
            for i, w in enumerate(words)
        ],
        is_final=True,
        segment_id=int(t0),
    )


def _flows(sent) -> list[FlowPositionMessage]:
    return [m for m in sent if isinstance(m, FlowPositionMessage)]


@pytest.mark.asyncio
async def test_singing_broadcasts_a_track_ahead_of_the_stale_belief(sent, monkeypatch):
    engine = _engine()
    session = _Session()
    monkeypatch.setattr(server_mod._state, "audio_session", session)
    monkeypatch.setattr(server_mod._state, "matcher", engine)
    handler = server_mod._make_transcript_handler(engine, _Director())

    event = _sing(VERSE[:4], 0.0)
    # Delivered 2 s after the last word was sung.
    session.latency.observe(event, available_at=4.0)
    await handler(event)

    flows = _flows(sent)
    assert flows, "no flow_position was broadcast"
    msg = flows[-1]
    assert msg.item_idx == 0
    assert msg.jump  # first placement cuts rather than scrolls
    # The belief sits around word 4 of the verse; the track makes up the
    # delivery lag, but never runs past the end of the slide it is on.
    assert 3.0 <= msg.word <= msg.limit == len(VERSE)


@pytest.mark.asyncio
async def test_no_track_while_the_operator_has_the_screen(sent, monkeypatch):
    engine = _engine()
    session = _Session()
    monkeypatch.setattr(server_mod._state, "audio_session", session)
    monkeypatch.setattr(server_mod._state, "matcher", engine)
    director = _Director()
    director.state = "following_human"
    handler = server_mod._make_transcript_handler(engine, director)

    await handler(_sing(VERSE[:4], 0.0))
    assert _flows(sent) == []

    # Once the cooldown lapses the follower starts afresh — a cut, not a
    # correction measured against a track nobody was showing.
    director.state = "tracking"
    await handler(_sing(VERSE[4:], 2.0))
    assert _flows(sent) and _flows(sent)[-1].jump


@pytest.mark.asyncio
async def test_a_reading_is_followed_while_the_operator_has_the_screen(sent, monkeypatch):
    engine = _engine(spoken=True)
    session = _Session()
    monkeypatch.setattr(server_mod._state, "audio_session", session)
    monkeypatch.setattr(server_mod._state, "matcher", engine)
    monkeypatch.setattr(server_mod._state, "flow_override_t", None)
    director = _Director()
    director.state = "following_human"
    director.last_override_t = 1.0
    handler = server_mod._make_transcript_handler(engine, director)

    # Put up by voice: the reader starts straight away, and so does the column.
    await handler(_sing(VERSE[:4], 0.0))
    assert _flows(sent) and _flows(sent)[-1].jump

    # A later override restarts the track from where it re-anchored, rather
    # than measuring against the old one.
    sent.clear()
    director.last_override_t = 5.0
    await handler(_sing(VERSE[4:], 2.0))
    assert _flows(sent) and _flows(sent)[-1].jump
