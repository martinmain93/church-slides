from __future__ import annotations

import pytest

from auto_presenter.decision.flow import FlowConfig, FlowFollower, FlowTrack
from auto_presenter.index.library import build_index
from auto_presenter.protocol import SetlistContent, SetlistItemContent


def _setlist(slides_per_item: list[list[list[str]]]):
    content = SetlistContent(
        items=[
            SetlistItemContent(item_idx=i, song_id=f"s{i}", slides=slides)
            for i, slides in enumerate(slides_per_item)
        ]
    )
    return build_index(content)


def _words(n: int) -> list[str]:
    return [f"w{i}" for i in range(n)]


#: Blank lead slide, then three 10-word slides: global words 0..30.
SONG = [[], _words(10), _words(10), _words(10)]


def _at(item: int, slide: int, word: int, mass: float = 1.0):
    return {(item, slide, word, 0): mass}


def _follower(**cfg) -> FlowFollower:
    f = FlowFollower(setlist=_setlist([SONG, SONG]), config=FlowConfig(**cfg))
    # Two words a second, steadily, so the rate is known.
    f.observe_words([t * 0.5 for t in range(40)])
    return f


def test_offsets_count_words_across_slides():
    f = _follower()
    assert f.slide_offsets(0) == [0, 0, 10, 20, 30]


def test_first_update_is_a_jump_that_makes_up_the_delivery_lag():
    f = _follower(rate_window_seconds=10.0)
    track = f.update(_at(0, 1, 2), t_observed=10.0, t_now=11.5)
    assert track is not None and track.jump
    assert track.rate_wps == pytest.approx(2.0)
    # 1.5 s late at 2 w/s is three words further on than the belief.
    assert track.word0 == pytest.approx(5.0)
    assert track.limit == 10.0


def test_extrapolation_stops_at_the_end_of_the_slide():
    f = _follower(rate_window_seconds=10.0)
    track = f.update(_at(0, 1, 6), t_observed=10.0, t_now=10.0)
    assert track is not None
    assert track.at(10.0 + 60.0) == 10.0


def test_small_errors_inside_the_deadband_do_not_correct():
    f = _follower(rate_window_seconds=10.0)
    f.update(_at(0, 1, 2), t_observed=10.0, t_now=10.0)
    # Screen predicts word 4 at t=11; the belief says 5.
    track = f.update(_at(0, 1, 5), t_observed=11.0, t_now=11.0)
    assert track is not None and not track.jump
    assert track.correction == 0.0
    assert track.word0 == pytest.approx(4.0)


def test_forward_error_is_paid_back_smoothly():
    f = _follower(rate_window_seconds=10.0, settle_seconds=2.0)
    f.update(_at(0, 1, 0), t_observed=10.0, t_now=10.0)
    track = f.update(_at(0, 2, 5), t_observed=11.0, t_now=11.0)
    assert track is not None and not track.jump
    assert track.correction == pytest.approx(13.0)
    # Continuous: no snap at the moment of the update.
    assert track.at(11.0) == pytest.approx(2.0)
    assert track.at(12.0) == pytest.approx(2.0 + 2.0 + 6.5)
    assert track.at(13.0) == pytest.approx(2.0 + 4.0 + 13.0)


def test_screen_ahead_of_the_singing_stops_rather_than_retreats():
    f = _follower(rate_window_seconds=10.0)
    f.update(_at(0, 2, 5), t_observed=10.0, t_now=10.0)
    track = f.update(_at(0, 2, 3), t_observed=11.0, t_now=11.0)
    assert track is not None
    assert track.rate_wps == 0.0
    assert track.at(20.0) == pytest.approx(17.0)


def test_confident_large_backward_error_eases_back():
    f = _follower(rate_window_seconds=10.0)
    f.update(_at(0, 3, 5), t_observed=10.0, t_now=10.0)
    track = f.update(_at(0, 2, 0), t_observed=10.0, t_now=10.0)
    assert track is not None and not track.jump
    assert track.correction == pytest.approx(-15.0)
    assert track.at(10.0) == pytest.approx(25.0)


def test_low_confidence_leaves_the_track_alone():
    f = _follower()
    first = f.update(_at(0, 1, 2), t_observed=10.0, t_now=10.0)
    split = {(0, 1, 5, 0): 0.3, (1, 3, 5, 0): 0.4, (0, 3, 1, 0): 0.3}
    assert f.update(split, t_observed=11.0, t_now=11.0) is None
    assert f.track is first


def test_a_different_song_is_a_jump():
    f = _follower()
    f.update(_at(0, 1, 2), t_observed=10.0, t_now=10.0)
    track = f.update(_at(1, 1, 2), t_observed=11.0, t_now=11.0)
    assert track is not None and track.jump and track.item_idx == 1


def test_mode_is_not_averaged_across_repeated_sections():
    f = _follower()
    # Two copies of a chorus, 20 words apart: the mean would sit on neither.
    split = {(0, 1, 3, 0): 0.55, (0, 3, 3, 0): 0.45}
    track = f.update(split, t_observed=10.0, t_now=10.0)
    assert track is not None
    assert track.word0 == pytest.approx(3.0)


def test_track_formula_clamps_to_limit():
    t = FlowTrack(
        item_idx=0, word0=0.0, rate_wps=1.0, correction=4.0, settle_seconds=2.0,
        limit=3.0, t0=0.0, jump=False, confidence=1.0,
    )
    assert t.at(1.0) == pytest.approx(3.0)
    assert t.at(-5.0) == 0.0


def _reading(**cfg) -> FlowFollower:
    content = SetlistContent(
        items=[SetlistItemContent(item_idx=0, song_id="r", slides=SONG, spoken=True)]
    )
    f = FlowFollower(setlist=build_index(content), config=FlowConfig(**cfg))
    f.observe_words([t * 0.5 for t in range(40)])
    return f


def test_a_reading_guesses_only_a_little_past_the_last_word_heard():
    f = _reading(rate_window_seconds=10.0, spoken_lead_seconds=1.0, spoken_coast_seconds=2.0)
    # Heard 3 s ago at 2 w/s: a song would place word 2 + 6; a reading, 2 + 2.
    track = f.update(_at(0, 1, 2), t_observed=10.0, t_now=13.0)
    assert track is not None
    assert track.word0 == pytest.approx(4.0)
    # Runs on through the slide break at 10, but stops 2 s on with no new words.
    assert track.at(13.0 + 60.0) == pytest.approx(8.0)


def test_a_reading_runs_on_past_the_end_of_a_slide():
    f = _reading(rate_window_seconds=10.0)
    track = f.update(_at(0, 1, 9), t_observed=10.0, t_now=10.0)
    assert track is not None
    assert track.at(11.0) > 10.0


def test_a_reading_slows_rather_than_stops_when_a_little_ahead():
    f = _reading(rate_window_seconds=10.0, settle_seconds=2.0)
    f.update(_at(0, 2, 5), t_observed=10.0, t_now=10.0)
    # Screen at word 17 by t=11; the belief says 14. A song would stop there.
    track = f.update(_at(0, 2, 4), t_observed=11.0, t_now=11.0)
    assert track is not None
    assert track.rate_wps == pytest.approx(2.0 - 3.0 / 2.0)


def test_a_reading_ignores_a_doubtful_belief():
    f = _reading(spoken_min_confidence=0.8)
    first = f.update(_at(0, 1, 2), t_observed=10.0, t_now=10.0)
    doubtful = {(0, 2, 5, 0): 0.7, (0, 3, 9, 0): 0.3}
    assert f.update(doubtful, t_observed=11.0, t_now=11.0) is None
    assert f.track is first
