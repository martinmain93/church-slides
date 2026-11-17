"""A clear line-scorer pass counts as the song's words heard
(`live.START_ON_CLEAR_LINE`)."""

from __future__ import annotations

from auto_presenter.follower import live
from auto_presenter.follower.talk import TalkGate

WORDS = [
    [],
    "amazing grace how sweet the sound".split(),
    "that saved a wretch like me".split(),
    "amazing grace how sweet the sound".split(),
]


def test_on_by_default():
    assert live.START_ON_CLEAR_LINE


def test_one_line_well_ahead_is_clear():
    assert live.clear_line(WORDS, {(1, 3): -1.2, (2, 3): -3.9, (3, 3): -1.2})


def test_a_copy_of_the_line_is_not_a_rival():
    # slides 1 and 3 are the same words: the lead is over slide 2
    assert live.clear_line(WORDS, {(1, 3): -1.2, (3, 3): -1.3, (2, 3): -4.0})


def test_a_faint_or_close_line_is_not_clear():
    assert not live.clear_line(WORDS, {(1, 3): -2.4, (2, 3): -5.0})
    assert not live.clear_line(WORDS, {(1, 3): -1.2, (2, 3): -1.9})


def test_one_word_spans_and_a_lone_line_do_not_count():
    assert not live.clear_line(WORDS, {(1, 0): -0.5, (2, 0): -5.0})
    assert not live.clear_line(WORDS, {(1, 3): -1.0})


def test_note_lyrics_keeps_the_earliest_arrival():
    gate = TalkGate(frozenset())
    assert not gate.lyrics_heard(10.0)
    gate.note_lyrics(8.0)
    gate.note_lyrics(9.0)
    assert gate.lyrics_heard(8.0) and not gate.lyrics_heard(7.9)
