"""SongProgress — when is slide order still a reliable predictor?"""

from __future__ import annotations

from auto_presenter.tracker.progress import SongProgress


def test_order_predicts_on_a_fresh_song() -> None:
    p = SongProgress()
    p.note_commit(0, 1)
    assert p.order_is_predictive(1, at_last_slide=False) is True
    assert p.deviated is False


def test_straight_run_stays_predictive() -> None:
    p = SongProgress()
    for slide in range(1, 8):
        p.note_commit(0, slide)
    assert p.order_is_predictive(7, at_last_slide=False) is True


def test_a_repeat_makes_order_unpredictive() -> None:
    """Once a band has gone back once, they may do it again — order stops
    being a safe assumption and the next slide must be earned by evidence."""
    p = SongProgress()
    for slide in (1, 2, 3, 4):
        p.note_commit(0, slide)
    p.note_commit(0, 2)
    assert p.deviated is True
    # Slide 4 is where the band jumped from — expect ambiguity there again.
    assert p.order_is_predictive(4, at_last_slide=False) is False
    # ...but the slides inside the repeated run are still ordered.
    assert p.order_is_predictive(2, at_last_slide=False) is True


def test_repeating_the_same_slide_counts_as_deviation() -> None:
    p = SongProgress()
    p.note_commit(0, 3)
    p.note_commit(0, 3)
    assert p.deviated is True


def test_a_forward_skip_is_not_a_deviation() -> None:
    """Skipping a verse is common and order still predicts what follows it."""
    p = SongProgress()
    p.note_commit(0, 1)
    p.note_commit(0, 4)
    assert p.deviated is False
    assert p.order_is_predictive(4, at_last_slide=False) is True


def test_the_last_slide_has_no_next_in_order() -> None:
    p = SongProgress()
    p.note_commit(0, 1)
    assert p.order_is_predictive(1, at_last_slide=True) is False


def test_a_new_song_restores_predictiveness() -> None:
    """Deviation is a property of the song being played, not of the session."""
    p = SongProgress()
    p.note_commit(0, 3)
    p.note_commit(0, 1)
    assert p.deviated is True
    p.note_commit(1, 1)
    assert p.deviated is False
    assert p.order_is_predictive(1, at_last_slide=False) is True


def test_reset_clears_history() -> None:
    p = SongProgress()
    p.note_commit(0, 3)
    p.note_commit(0, 1)
    p.reset()
    assert p.deviated is False
    assert p.slides_seen == 0
