"""The engine learns what the screen shows from the screen (engine review
2026-11-10, findings 1, 2 and 4).

The scheduler notes a move as shown when it decides it. A move the director
drops (cooldown), the server holds (talk), or the app declines must put that
record back; a mid-service setlist edit must not wipe it; and an operator's
click starts the cooldown from the click, not from the AI's last move.
"""

from __future__ import annotations

import time

import pytest

from auto_presenter import server
from auto_presenter.asr.types import TranscriptEvent, Word
from auto_presenter.decision.director import Director
from auto_presenter.decision.production import build_engine
from auto_presenter.index.library import build_index
from auto_presenter.matcher.types import MatcherDecision
from auto_presenter.protocol import (
    RecordSlideChangeNotification,
    SetlistContent,
    SetlistItemContent,
)
from auto_presenter.server_state import (
    AI_MOVE_ECHO_S,
    _state,
    check_ai_move_landed,
    deliver_decision,
)

SONGS = [
    ["long", "before", "the", "dawn", "you", "were", "calling", "out", "to", "me"],
    ["all", "to", "jesus", "i", "surrender", "all", "to", "him", "i", "freely", "give"],
    ["we", "gather", "here", "to", "lift", "our", "voices", "in", "the", "light"],
    ["holy", "holy", "holy", "lord", "god", "almighty", "early", "in", "the", "morning"],
    ["on", "a", "hill", "far", "away", "stood", "an", "old", "rugged", "cross", "high"],
    ["alleluia", "alleluia", "for", "the", "lord", "god", "almighty", "reigns"],
    ["amazing", "grace", "how", "sweet", "the", "sound", "that", "saved", "a", "wretch"],
]


def _content() -> SetlistContent:
    return SetlistContent(
        items=[
            SetlistItemContent(
                item_idx=i,
                song_id=f"s{i}",
                slides=[[], words[:5], words[5:], ["verse", "two", "words", "here"]],
            )
            for i, words in enumerate(SONGS)
        ]
    )


def _event(words: list[str], t0: float) -> TranscriptEvent:
    return TranscriptEvent(
        words=[
            Word(text=w, t_start=t0 + i * 0.4, t_end=t0 + i * 0.4 + 0.3)
            for i, w in enumerate(words)
        ],
        is_final=True,
    )


@pytest.fixture()
def session(monkeypatch):
    """A live session's server state: engine, director (active), screen."""
    engine = build_engine(build_index(_content()))
    shown: list[MatcherDecision] = []
    director = Director(
        matcher=engine,
        on_decision_broadcast=shown.append,
        on_status_broadcast=lambda _s: None,
        mode="active",
    )
    monkeypatch.setattr(_state, "matcher", engine)
    monkeypatch.setattr(_state, "director", director)
    monkeypatch.setattr(_state, "last_slide", None)
    monkeypatch.setattr(_state, "ai_move_pending", None)
    monkeypatch.setattr(_state, "audio_clock", None)
    return engine, director, shown


# --- 1. a move that never reached the screen --------------------------------


def test_a_move_dropped_in_the_cooldown_puts_the_record_back(session):
    engine, director, shown = session
    _state.last_slide = (0, 1)
    director.on_override(0, 1, t_seconds=10.0)
    engine.note_displayed(0, 2)  # the scheduler decided slide 2 and noted it
    delivered = deliver_decision(
        MatcherDecision(item_idx=0, slide_idx=2, t_seconds=11.0, confidence=1.0)
    )
    assert not delivered and not shown
    assert engine._displayed == (0, 1)


def test_a_move_the_app_never_shows_puts_the_record_back(session):
    engine, _director, shown = session
    _state.last_slide = (0, 1)
    engine.note_displayed(0, 2)
    assert deliver_decision(
        MatcherDecision(item_idx=0, slide_idx=2, t_seconds=11.0, confidence=1.0)
    )
    assert shown and _state.ai_move_pending is not None
    check_ai_move_landed()  # too soon to tell
    assert engine._displayed == (0, 2)
    item, slide, _sent = _state.ai_move_pending
    _state.ai_move_pending = (item, slide, time.monotonic() - AI_MOVE_ECHO_S - 0.1)
    check_ai_move_landed()  # the app never reported it: declined
    assert engine._displayed == (0, 1) and _state.ai_move_pending is None


def test_a_move_the_app_reports_back_stands(session, monkeypatch):
    engine, _director, _shown = session
    monkeypatch.setattr(server, "learning_slide", lambda *a: None)
    _state.last_slide = (0, 1)
    engine.note_displayed(0, 2)
    deliver_decision(MatcherDecision(item_idx=0, slide_idx=2, t_seconds=11.0, confidence=1.0))
    server._handle_slide_change(RecordSlideChangeNotification(item_idx=0, slide_idx=2, source="ai"))
    assert _state.ai_move_pending is None
    check_ai_move_landed()
    assert engine._displayed == (0, 2)


def test_shadow_and_assist_keep_their_free_running_record(session):
    engine, director, _shown = session
    director.set_mode("assist")
    _state.last_slide = (0, 1)
    engine.note_displayed(0, 2)
    assert not deliver_decision(
        MatcherDecision(item_idx=0, slide_idx=2, t_seconds=11.0, confidence=1.0)
    )
    assert engine._displayed == (0, 2)


# --- 2. a setlist edit mid-service ------------------------------------------


@pytest.mark.parametrize("reanchor", [False, True])
def test_a_setlist_edit_keeps_the_screen_and_the_song_change_bar(session, reanchor):
    engine, _director, _shown = session
    engine.commit_external(3, 2)
    _state.last_slide = (3, 2)
    index = build_index(_content())
    engine.reindex(index, 3)
    if reanchor:
        server._reanchor_after_edit(engine, index, 3)
    decision = engine.update(_event(["amazing", "grace"], 50.0))
    jumped = decision is not None and decision.item_idx != 3
    # without the re-anchor two words of another song's first line jump song
    # (the review's reproduction); with it they don't
    assert jumped is not reanchor
    if reanchor:
        assert engine._displayed == (3, 2)


# --- 4. the cooldown runs from the click ------------------------------------


def test_the_cooldown_runs_from_the_operators_click(session, monkeypatch):
    _engine, director, shown = session
    monkeypatch.setattr(server, "learning_slide", lambda *a: None)
    monkeypatch.setattr(server, "note_operator_move", lambda *_a: False)
    director.on_decision(MatcherDecision(item_idx=0, slide_idx=1, t_seconds=7.4, confidence=1.0))
    assert len(shown) == 1
    # 33 s later the operator clicks; the health tick knows the audio clock
    _state.audio_clock = (40.4, time.monotonic())
    server._handle_slide_change(
        RecordSlideChangeNotification(item_idx=0, slide_idx=2, source="manual")
    )
    director.on_decision(MatcherDecision(item_idx=0, slide_idx=3, t_seconds=41.7, confidence=1.0))
    assert len(shown) == 1, "the AI moved the screen 1.3 s after the operator's click"


def test_the_cooldown_ends_on_the_health_tick_without_a_decision(session):
    _engine, director, _shown = session
    statuses = []
    director.on_status_broadcast = statuses.append
    director.on_override(0, 1, t_seconds=10.0)
    assert director._state == "following_human"
    director.note_session_time(12.0)
    assert director._state == "following_human"
    director.note_session_time(16.0)
    assert director._state == "tracking"
    assert statuses[-1].state == "tracking"


# --- 6. the watchdog never undoes an early advance --------------------------

BREAK_SONG = [
    [],
    "you part the waves so i can walk upon the shore".split(),
    "my heart was lost in deepest night".split(),
    "you lifted me so i can stand and sing".split(),
    "i am held by your grace".split(),
]


def _sing_through(engine, slides, t: float = 0.4):
    out = []
    for sl in slides:
        for w in BREAK_SONG[sl]:
            d = engine.update(
                TranscriptEvent(words=[Word(text=w, t_start=t, t_end=t + 0.3)], is_final=True)
            )
            if d is not None:
                out.append(d.slide_idx)
            t += 0.6
    return out, t


def test_the_watchdog_does_not_step_back_into_a_break():
    engine = build_engine(
        build_index(
            SetlistContent(items=[SetlistItemContent(item_idx=0, song_id="a", slides=BREAK_SONG)])
        )
    )
    engine.note_displayed(0, 0)
    moves, t = _sing_through(engine, [1, 2])
    assert moves[-1] == 3, "the scheduler advances before slide 2's line ends"
    # an instrumental: no words for 20 s
    back = [d.slide_idx for k in range(1, 41) if (d := engine.tick(t + k * 0.5)) is not None]
    assert back == [], "the watchdog put the finished line back up mid-break"


def test_the_band_really_going_back_is_still_followed():
    engine = build_engine(
        build_index(
            SetlistContent(items=[SetlistItemContent(item_idx=0, song_id="a", slides=BREAK_SONG)])
        )
    )
    engine.note_displayed(0, 0)
    _moves, t = _sing_through(engine, [1, 2])
    _more, t = _sing_through(engine, [3, 2], t)
    # the tracker left slide 2 and came back to it: the watchdog may follow
    back = [d.slide_idx for k in range(1, 41) if (d := engine.tick(t + k * 0.5)) is not None]
    assert engine._displayed == (0, 2) or back == [2]
