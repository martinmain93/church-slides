"""Visible error: placing sung words in the lyrics and scoring the screen."""

from auto_presenter.eval.types import Decision, GroundTruthEvent, SetlistItemSnapshot
from auto_presenter.eval.visible import (
    merge_windows,
    normalize_word,
    place_sung_words,
    slide_tokens,
    visible_error,
    words_match,
)

CONTENT = [
    SetlistItemSnapshot(
        item_idx=0,
        song_id="s",
        slides=[
            [],
            ["amazing", "grace", "how", "sweet", "the", "sound"],
            ["nothing", "but", "the", "blood", "of", "jesus"],
            "oh precious is the flow nothing but the blood of jesus".split(),
        ],
    )
]


def words(text: str, t0: float, dt: float = 0.5):
    return [(t0 + i * dt, t0 + (i + 1) * dt, w) for i, w in enumerate(text.split())]


def gt(*cues):
    return [GroundTruthEvent(t_seconds=t, item_idx=0, slide_idx=s) for t, s in cues]


def dec(*cues):
    return [Decision(t_seconds=t, item_idx=0, slide_idx=s, source="ai") for t, s in cues]


def test_normalisation_folds_contractions_and_hyphens():
    assert normalize_word("Don\u2019t") == normalize_word("dont") == "dont"
    assert slide_tokens(["ever-present", "I'm"]) == ["ever", "present", "im"]
    assert words_match("amazing", "amazin")
    assert not words_match("the", "he")  # short words must match exactly


def test_shared_line_is_at_home_on_both_slides():
    pl = place_sung_words(words("nothing but the blood", 10), CONTENT)
    assert {w.word for w in pl.placed} == {"nothing", "but", "the", "blood"}
    assert all(w.homes == frozenset({(0, 2), (0, 3)}) for w in pl.placed)


def test_noise_and_stop_word_runs_are_not_placed():
    pl = place_sung_words(words("banana phone xylophone the of the", 0), CONTENT)
    assert pl.placed == []


def test_visible_error_scores_only_singing():
    pl = place_sung_words(words("amazing grace how sweet", 2), CONTENT)  # sung 2..4 s
    g = gt((0, 1), (20, 2))
    assert visible_error(dec((0, 1)), g, pl).rate == 0.0
    late = visible_error(dec((0, 0), (3, 1)), g, pl)  # blank until 3 s
    assert abs(late.hidden - 1.0) < 0.06 and abs(late.scored - 2.0) < 0.06
    # excused windows leave both numerator and denominator
    ex = visible_error(dec((0, 0), (3, 1)), g, pl, excused=[(2, 3)])
    assert ex.hidden < 0.06 and abs(ex.scored - 1.0) < 0.06


def test_golden_can_be_scored_as_a_screen():
    pl = place_sung_words(words("nothing but the blood of jesus", 5), CONTENT)
    g = gt((0, 1), (4, 3), (20, 1))
    assert visible_error(g, g, pl).rate == 0.0  # shared line: slide 3 carries it


def test_merge_windows():
    assert merge_windows([(0, 1), (1.5, 2), (5, 6)], gap=1.0) == [(0, 2), (5, 6)]
