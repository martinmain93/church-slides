"""`asr.junk` — which Whisper output is not a word at all."""

from __future__ import annotations

from auto_presenter.asr.junk import JunkFilter
from auto_presenter.asr.types import Word


def _w(text: str, conf: float = 0.9, no_speech: float | None = None) -> Word:
    return Word(text=text, t_start=0.0, t_end=0.5, confidence=conf, no_speech=no_speech)


def test_tokens_with_no_letters_or_digits_are_junk():
    f = JunkFilter()
    for text in ("!", "...", "♪", "-", "'", '"'):
        assert f.is_junk(_w(text)), text


def test_real_words_with_punctuation_are_kept():
    f = JunkFilter()
    for text in ("grace,", "it's", "Me.", "ninety-nine", "3"):
        assert not f.is_junk(_w(text)), text


def test_a_low_probability_sung_word_is_kept():
    """Sung words often score low; probability alone is never enough."""
    f = JunkFilter()
    assert not f.is_junk(_w("grace", conf=0.01, no_speech=0.05))


def test_a_low_probability_word_in_a_no_speech_segment_is_junk():
    f = JunkFilter(min_no_speech=0.5)
    assert f.is_junk(_w("you", conf=0.01, no_speech=0.9))


def test_without_a_no_speech_reading_only_the_content_rule_applies():
    f = JunkFilter(min_no_speech=0.5)
    assert not f.is_junk(_w("you", conf=0.0, no_speech=None))


def test_the_no_speech_rule_is_off_by_default():
    """Sung words read no-speech 0.5-0.74 on real audio; see `JunkFilter`."""
    assert not JunkFilter().is_junk(_w("a", conf=0.05, no_speech=0.54))


# --- DoubtfulWordFilter --------------------------------------------------------

from auto_presenter.asr.junk import DoubtfulWordFilter  # noqa: E402


def _keep(words, *, talking=0.0, silent=False, established=True):
    return [
        w.text for w in DoubtfulWordFilter().keep(
            words, talking_score=talking, silent=silent,
            singing_established=established,
        )
    ]


def test_near_zero_words_survive_while_people_are_singing():
    assert _keep([_w("Music", conf=0.006)], talking=0.1) == ["Music"]


def test_near_zero_words_go_when_it_sounds_like_talking():
    words = [_w("Thank", conf=0.004), _w("you", conf=0.97)]
    assert _keep(words, talking=0.6) == ["you"]


def test_near_zero_words_go_before_anyone_has_been_heard_singing():
    assert _keep([_w("Thank", conf=0.004)], established=False) == []


def test_low_but_not_near_zero_words_are_always_kept():
    """0.02-0.1 is real sung words ("a", "of", "Jesus'") on the goldens."""
    assert _keep([_w("a", conf=0.05)], talking=0.9) == ["a"]
