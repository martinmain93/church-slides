"""The assist interpreter's rules, one test each (see docs/done/AI_ASSISTED_MODE.md)."""

from auto_presenter.decision.assist import AssistConfig, AssistInterpreter
from auto_presenter.index.library import build_index
from auto_presenter.protocol import SetlistContent, SetlistItemContent

# Song 0: blank, verse 1 (two slides), chorus, verse 2, chorus again.
SONG_A = [
    [],
    "i once was lost".split(),
    "but now am found".split(),
    "how sweet the sound".split(),
    "twas grace that taught".split(),
    "how sweet the sound".split(),
]
SONG_B = [[], "holy holy holy".split(), "lord god almighty".split()]


def _assist(**cfg: float) -> AssistInterpreter:
    content = SetlistContent(
        items=[
            SetlistItemContent(item_idx=0, song_id="a", slides=SONG_A),
            SetlistItemContent(item_idx=1, song_id="b", slides=SONG_B),
        ]
    )
    return AssistInterpreter(setlist=build_index(content), config=AssistConfig(**cfg))


def _kinds(events) -> list[str]:
    return [e.kind for e in events]


def test_agreement_says_nothing():
    a = _assist()
    a.displayed(0.0, 0, 1)
    assert a.engine_move(1.0, 0, 1) == []
    assert a.tick(20.0) == []


def test_identical_slides_are_the_same_place():
    a = _assist()
    a.displayed(0.0, 0, 3)  # the first chorus is up
    a.engine_move(1.0, 0, 5)  # the engine says the second chorus
    assert a.tick(20.0) == []


def test_next_tier_waits_for_the_operator_to_be_late():
    a = _assist(next_late_s=1.0)
    a.displayed(0.0, 0, 1)
    assert a.engine_move(5.0, 0, 2) == []
    assert a.tick(5.5) == []
    (ev,) = a.tick(6.0)
    assert ev.kind == "raise" and ev.suggestion.tier == "next"
    assert (ev.suggestion.item_idx, ev.suggestion.slide_idx) == (0, 2)
    (ev,) = a.displayed(6.4, 0, 2)
    assert ev.kind == "clear" and ev.reason == "operator_followed"


def test_without_the_next_tier_a_late_operator_gets_no_pulse():
    a = _assist(next_late_s=1.0, next_tier=False)
    a.displayed(0.0, 0, 1)
    a.engine_move(5.0, 0, 2)
    assert a.tick(6.0) == [] and a.tick(30.0) == []
    # a wrong place is still caught
    a.engine_move(31.0, 0, 4)
    assert a.tick(31.5) == []
    (ev,) = a.tick(34.5)
    assert ev.kind == "raise" and ev.suggestion.tier == "place"


def test_place_tier_waits_out_the_dwell():
    a = _assist(place_dwell_s=3.0)
    a.displayed(0.0, 0, 1)
    a.engine_move(5.0, 1, 1)  # a different song
    assert a.tick(7.9) == []
    (ev,) = a.tick(8.0)
    assert ev.kind == "raise" and ev.suggestion.tier == "place"
    assert (ev.suggestion.item_idx, ev.suggestion.slide_idx) == (1, 1)


def test_an_operator_move_never_creates_a_suggestion():
    a = _assist()
    a.engine_move(0.0, 0, 1)
    a.displayed(1.0, 0, 1)
    # The operator goes to the next song before the engine hears it.
    assert a.displayed(10.0, 1, 1) == []
    assert a.tick(30.0) == []


def test_engine_one_slide_behind_is_the_operator_being_early():
    a = _assist()
    a.displayed(0.0, 0, 2)
    a.engine_move(1.0, 0, 1)
    assert a.tick(30.0) == []


def test_engine_changing_its_mind_clears_and_starts_a_fresh_dwell():
    a = _assist(place_dwell_s=3.0, place_gap_s=0.0)
    a.displayed(0.0, 0, 1)
    a.engine_move(1.0, 1, 1)
    a.tick(4.0)
    assert a.current is not None
    (clear,) = a.engine_move(5.0, 0, 4)
    assert clear.kind == "clear" and clear.reason == "engine_moved"
    assert a.tick(7.9) == []
    assert _kinds(a.tick(8.0)) == ["raise"]


def test_the_suggestion_follows_the_band_forward():
    a = _assist(place_dwell_s=3.0)
    a.displayed(0.0, 0, 1)
    a.engine_move(1.0, 1, 1)
    (raised,) = a.tick(4.0)
    (ev,) = a.engine_move(6.0, 1, 2)
    assert ev.kind == "update"
    assert ev.suggestion.id == raised.suggestion.id
    assert (ev.suggestion.item_idx, ev.suggestion.slide_idx) == (1, 2)


def test_engine_coming_round_to_the_screen_clears():
    a = _assist(place_dwell_s=3.0)
    a.displayed(0.0, 0, 1)
    a.engine_move(1.0, 1, 1)
    a.tick(4.0)
    events = a.engine_move(6.0, 0, 1)
    assert [(e.kind, e.reason) for e in events] == [("clear", "engine_moved")]
    assert a.tick(20.0) == []


def test_dismiss_mutes_the_place_and_reanchors_on_the_screen():
    a = _assist(place_dwell_s=3.0, mute_s=30.0, place_gap_s=0.0)
    a.displayed(0.0, 0, 1)
    a.engine_move(1.0, 1, 1)
    (raised,) = a.tick(4.0)
    clear, reanchor = a.dismiss(5.0, raised.suggestion.id)
    assert clear.kind == "clear" and clear.reason == "dismissed"
    assert (reanchor.kind, reanchor.item_idx, reanchor.slide_idx) == ("reanchor", 0, 1)
    # The engine insists again inside the mute: still quiet.
    a.engine_move(10.0, 1, 1)
    assert a.tick(20.0) == []
    # After it, the engine still insisting on the same place brings it back:
    # it has disagreed with the screen all along.
    assert _kinds(a.engine_move(40.0, 1, 1)) == ["raise"]


def test_place_suggestions_are_rate_limited():
    a = _assist(place_dwell_s=1.0, place_gap_s=10.0)
    a.displayed(0.0, 0, 1)
    a.engine_move(1.0, 1, 1)
    a.tick(2.0)
    a.engine_move(3.0, 0, 4)  # clears, and a new place
    assert a.tick(5.0) == []
    assert _kinds(a.tick(12.0)) == ["raise"]


def test_no_place_suggestion_while_the_engine_is_lost():
    a = _assist(place_dwell_s=1.0)
    a.displayed(0.0, 0, 1)
    a.candidates(0.5, [], lost=True)
    a.engine_move(1.0, 1, 1)
    assert a.tick(10.0) == []
    assert _kinds(a.candidates(11.0, [], lost=False)) == ["raise"]


def test_never_suggests_a_blank_slide():
    a = _assist(place_dwell_s=1.0)
    a.displayed(0.0, 0, 1)
    a.engine_move(1.0, 1, 0)
    assert a.tick(10.0) == []


def test_alternatives_are_distinct_places_with_words():
    a = _assist(place_dwell_s=1.0)
    a.displayed(0.0, 0, 1)
    a.candidates(0.5, [(1, 1), (0, 5), (0, 3), (0, 1), (1, 0), (1, 2), (0, 4)])
    a.engine_move(1.0, 1, 1)
    (ev,) = a.tick(2.0)
    # (0,5) and (0,3) are one chorus; (0,1) is on screen; (1,0) is blank.
    assert ev.suggestion.alternatives == ((0, 5), (1, 2), (0, 4))


def test_low_confidence_moves_are_not_evidence():
    a = _assist(place_dwell_s=1.0, min_confidence=0.5)
    a.displayed(0.0, 0, 1)
    a.engine_move(1.0, 1, 1, confidence=0.3)
    assert a.tick(10.0) == []


def test_short_slides_dont_reset_the_wait():
    # Seen live: screen left on verse 1 while the band went on through slide
    # after slide, each shorter than the wait. One disagreement, one wait.
    a = _assist(place_dwell_s=3.0, next_late_s=1.0)
    a.displayed(0.0, 0, 1)
    a.engine_move(1.0, 0, 2)  # next slide: pulses
    assert _kinds(a.tick(2.0)) == ["raise"]
    events = a.engine_move(3.0, 0, 3)  # on again, before 3 s at any one slide
    assert _kinds(events) == ["clear"]
    assert _kinds(a.tick(4.0)) == ["raise"]  # 3 s since the screen went wrong
    assert a.current.tier == "place" and a.current.slide_idx == 3


def test_going_back_is_not_being_early():
    # The operator clicks back to verse 1 while the engine has the band on
    # the chorus: that's the mistake assist is for, so it isn't held back
    # waiting for the engine's next move — only the click's own grace.
    a = _assist(place_dwell_s=3.0)
    a.displayed(0.0, 0, 3)
    a.engine_move(1.0, 0, 3)
    a.displayed(5.0, 0, 1)
    assert a.tick(7.9) == []
    assert _kinds(a.tick(8.0)) == ["raise"]


def test_going_ahead_of_the_engine_still_waits_for_it():
    a = _assist(place_dwell_s=3.0)
    a.displayed(0.0, 0, 1)
    a.engine_move(1.0, 0, 1)
    a.displayed(5.0, 0, 4)  # the operator jumps ahead
    assert a.tick(30.0) == []


def test_next_steps_over_a_repeated_slide():
    # Two identical slides in a row are one place; the slide after both is
    # still the next one, so a suggestion follows the band across them.
    content = SetlistContent(items=[SetlistItemContent(item_idx=0, song_id="a", slides=[
        [], "verse one".split(), "so good to me".split(), "so good to me".split(), "verse two".split(),
    ])])
    a = AssistInterpreter(setlist=build_index(content), config=AssistConfig(place_dwell_s=1.0))
    assert a._is_successor((0, 1), (0, 2))
    assert a._is_successor((0, 2), (0, 4))
    a.displayed(0.0, 0, 0)
    a.engine_move(1.0, 0, 2)
    (raised,) = a.tick(2.0)
    a.engine_move(3.0, 0, 3)
    (ev,) = a.engine_move(4.0, 0, 4)
    assert ev.kind == "update" and ev.suggestion.id == raised.suggestion.id
