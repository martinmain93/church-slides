"""Director unit tests.

These exercise the director with the production engine
(`decision.production.build_engine`), hand-driven, and list-append
broadcast callbacks. The engine is real (not mocked) so the director's
`on_decision`/`on_override` flow exercises the actual
MatcherDecision-shaped interface.
"""

from __future__ import annotations

import pytest

from auto_presenter.decision.director import Director
from auto_presenter.decision.engine import ParticleFilterEngine
from auto_presenter.decision.production import build_engine
from auto_presenter.decision.types import AIStatus
from auto_presenter.index.library import build_index
from auto_presenter.matcher.types import MatcherDecision
from auto_presenter.protocol import SetlistContent, SetlistItemContent


@pytest.fixture()
def matcher() -> ParticleFilterEngine:
    content = SetlistContent(
        items=[
            SetlistItemContent(
                item_idx=0,
                song_id="song_a",
                slides=[["amazing", "grace"]],
            ),
            SetlistItemContent(
                item_idx=1,
                song_id="song_b",
                slides=[["holy", "holy"]],
            ),
        ]
    )
    return build_engine(build_index(content))


def _make_director(matcher: ParticleFilterEngine, *, mode: str = "active"):
    decisions: list[MatcherDecision] = []
    statuses: list[AIStatus] = []
    director = Director(
        matcher=matcher,
        on_decision_broadcast=decisions.append,
        on_status_broadcast=statuses.append,
        mode=mode,  # type: ignore[arg-type]
        cooldown_seconds=5.0,
    )
    return director, decisions, statuses


# --- initial state ----------------------------------------------------------


def test_initial_status_is_idle(matcher):
    _director, _decisions, statuses = _make_director(matcher)
    assert statuses[-1].state == "idle"
    assert statuses[-1].mode == "active"


# --- decisions in each mode -------------------------------------------------


def test_active_mode_broadcasts_decisions(matcher):
    director, decisions, statuses = _make_director(matcher, mode="active")
    decision = MatcherDecision(item_idx=0, slide_idx=0, t_seconds=1.0, confidence=0.9)
    director.on_decision(decision)
    assert decisions == [decision]
    # idle → tracking on the first decision.
    assert statuses[-1].state == "tracking"


def test_assist_mode_sends_moves_to_the_assist_layer_not_the_screen(matcher):
    director, decisions, _statuses = _make_director(matcher, mode="assist")
    moves: list[MatcherDecision] = []
    director.on_assist_move = moves.append
    decision = MatcherDecision(item_idx=1, slide_idx=0, t_seconds=1.0, confidence=0.9)
    director.on_decision(decision)
    assert decisions == []
    assert moves == [decision]


def test_active_mode_never_feeds_the_assist_layer(matcher):
    director, decisions, _statuses = _make_director(matcher, mode="active")
    moves: list[MatcherDecision] = []
    director.on_assist_move = moves.append
    director.on_decision(MatcherDecision(item_idx=0, slide_idx=0, t_seconds=1.0, confidence=0.9))
    assert len(decisions) == 1
    assert moves == []


def test_shadow_mode_does_not_broadcast_decisions(matcher):
    director, decisions, statuses = _make_director(matcher, mode="shadow")
    director.on_decision(
        MatcherDecision(item_idx=0, slide_idx=0, t_seconds=1.0, confidence=0.9)
    )
    assert decisions == []
    # Still transitions to tracking — the pill should reflect that the AI is
    # producing decisions even in shadow.
    assert statuses[-1].state == "tracking"


def test_off_mode_ignores_decisions(matcher):
    director, decisions, statuses = _make_director(matcher, mode="off")
    director.on_decision(
        MatcherDecision(item_idx=0, slide_idx=0, t_seconds=1.0, confidence=0.9)
    )
    assert decisions == []
    # No state change.
    assert statuses[-1].state == "idle"


# --- override flow ----------------------------------------------------------


def test_override_transitions_to_following_human(matcher):
    director, _decisions, statuses = _make_director(matcher, mode="active")
    director.on_decision(
        MatcherDecision(item_idx=0, slide_idx=0, t_seconds=1.0, confidence=0.9)
    )
    director.on_override(item_idx=0, slide_idx=1, t_seconds=2.0)
    assert statuses[-1].state == "following_human"


def test_override_during_active_session_is_not_broadcast_as_decision(matcher):
    """Overrides flow back from the operator; the director doesn't echo them."""
    director, decisions, _statuses = _make_director(matcher, mode="active")
    director.on_override(item_idx=0, slide_idx=1, t_seconds=2.0)
    assert decisions == []


def test_following_human_suppresses_decisions_during_cooldown(matcher):
    director, decisions, _statuses = _make_director(matcher, mode="active")
    # Inject a deterministic clock.
    director.set_now_provider(lambda: 100.0)
    director.on_override(item_idx=0, slide_idx=1, t_seconds=100.0)
    # AI tries to commit during the cooldown.
    director.on_decision(
        MatcherDecision(item_idx=0, slide_idx=0, t_seconds=101.0, confidence=0.95)
    )
    assert decisions == []  # suppressed


def test_cooldown_expires_on_the_session_clock(matcher):
    """Both ends of the cooldown are session seconds — the clock every decision
    already carries.

    It used to compare an override stamped with the audio clock against
    `time.monotonic()`, which is a difference of millions of seconds measured
    against five. A wall clock would also make replays depend on how fast they
    run: a faster-than-real-time eval would suppress commits a live service
    allows.
    """
    director, decisions, _statuses = _make_director(matcher, mode="active")
    director.cooldown_seconds = 1.0
    director.on_override(item_idx=0, slide_idx=1, t_seconds=100.0)

    director.on_decision(
        MatcherDecision(item_idx=0, slide_idx=0, t_seconds=100.5, confidence=0.9)
    )
    assert not decisions, "inside the cooldown, the AI must not move the screen"

    director.on_decision(
        MatcherDecision(item_idx=0, slide_idx=0, t_seconds=101.5, confidence=0.9)
    )
    assert decisions, "past the cooldown, the AI takes over again"


def test_the_cooldown_ends_without_anyone_asking_for_the_state(matcher):
    """The live freeze. Expiry is applied lazily inside the `state` property,
    `on_decision` read the raw field, and nothing in the class read the
    property — so the only thing that could end a cooldown was an outside
    caller happening to ask. The one caller sits behind a short-circuit
    (`"uncertain" if is_lost else director.state`), so a lost engine never
    asked. A spoken song import routes through `on_override`, and the screen
    then stayed frozen for the rest of the service.
    """
    director, decisions, _statuses = _make_director(matcher, mode="active")
    director.cooldown_seconds = 5.0
    director.on_override(item_idx=4, slide_idx=1, t_seconds=153.21)

    for t in (156.0, 160.0, 170.0, 200.0):
        director.on_decision(
            MatcherDecision(item_idx=4, slide_idx=2, t_seconds=t, confidence=0.95)
        )
    assert decisions, "the screen never recovered after a spoken import"


def test_override_advances_matcher_current_item(matcher):
    director, _decisions, _statuses = _make_director(matcher, mode="active")
    director.on_override(item_idx=1, slide_idx=0, t_seconds=1.0)
    assert matcher.current_item_idx == 1


def test_off_mode_override_is_informational_only(matcher):
    director, _decisions, statuses = _make_director(matcher, mode="off")
    director.on_override(item_idx=1, slide_idx=0, t_seconds=1.0)
    # State stays idle; no transition.
    assert statuses[-1].state == "idle"
    # But the matcher's current item still updated — useful for late
    # mode-flip to `shadow` mid-service.
    assert matcher.current_item_idx == 1


# --- mode changes -----------------------------------------------------------


def test_set_mode_off_resets_to_idle(matcher):
    director, _decisions, statuses = _make_director(matcher, mode="active")
    director.on_decision(
        MatcherDecision(item_idx=0, slide_idx=0, t_seconds=1.0, confidence=0.9)
    )
    director.set_mode("off")
    assert statuses[-1].state == "idle"
    assert statuses[-1].mode == "off"


def test_set_mode_back_on_returns_to_tracking(matcher):
    director, _decisions, statuses = _make_director(matcher, mode="off")
    director.set_mode("shadow")
    assert statuses[-1].state == "tracking"
    assert statuses[-1].mode == "shadow"


def test_set_mode_unchanged_is_noop(matcher):
    director, _decisions, statuses = _make_director(matcher, mode="active")
    count = len(statuses)
    director.set_mode("active")
    assert len(statuses) == count  # no new broadcast


def test_an_override_before_any_decision_still_lets_go(matcher):
    """An operator clicking a slide at the top of a service has no session
    clock to start the cooldown from — decisions are what carry one.

    Seeding it from the first session time we learn about, rather than leaving
    it unset, is the difference between a five second cooldown and a permanent
    one. Caught while fixing the permanent freeze, by re-introducing it.
    """
    director, decisions, _statuses = _make_director(matcher, mode="active")
    director.cooldown_seconds = 5.0
    director.on_override(item_idx=0, slide_idx=1)  # no t_seconds

    for t in (10.0, 20.0, 60.0):
        director.on_decision(
            MatcherDecision(item_idx=0, slide_idx=2, t_seconds=t, confidence=0.9)
        )
    assert decisions, "the AI never took over again after an operator click"
