"""Matching on pronunciation rather than spelling.

The tracker's emission score is how it decides which slide the band is on, so
these pin the properties that decision depends on: that sound beats spelling,
that a near-miss scores better than an unrelated word, and that the fallback
for unknown words never scores worse than what it replaced.
"""

from __future__ import annotations

import pytest

from auto_presenter.index.phonemic import (
    in_dictionary,
    phonemes,
    phonemic_match_score,
    substitution_cost,
)
from auto_presenter.index.phonetic import metaphone_match_score


class TestPronunciationNotSpelling:
    def test_silent_letters_do_not_break_a_match(self):
        """Double Metaphone gets this right by luck of its rule set; a
        dictionary gets it right because it knows how the word sounds."""
        assert phonemic_match_score("knight", "night") == 1.0

    def test_words_spelled_differently_but_said_the_same_match_exactly(self):
        assert phonemic_match_score("through", "threw") == 1.0
        assert phonemic_match_score("praise", "prays") == 1.0

    def test_phonemes_are_stripped_of_stress_markers(self):
        """Stress is not something a mishearing preserves, so comparing it
        would only add noise."""
        assert phonemes("jesus") == ("JH", "IY", "Z", "AH", "S")


class TestConfusableSounds:
    def test_a_voicing_swap_is_cheap(self):
        """p/b, t/d, s/z are the commonest ASR confusions and differ only in
        whether the voice is on."""
        assert substitution_cost("P", "B") == pytest.approx(0.2)
        assert substitution_cost("T", "D") == pytest.approx(0.2)

    def test_one_vowel_for_another_is_cheap(self):
        """Sung vowels are the least reliable thing an ASR reports — the
        phoneme spike found them wrong throughout."""
        assert substitution_cost("AE", "EH") == pytest.approx(0.3)

    def test_a_vowel_for_a_consonant_is_not(self):
        assert substitution_cost("P", "IY") == pytest.approx(1.0)

    def test_related_consonants_cost_less_than_unrelated_ones(self):
        assert substitution_cost("M", "N") < substitution_cost("M", "K")


class TestInsertionsAndDeletions:
    """The failure that motivated this. The shipped scorer dropped every
    inserted sound to a capped letter-distance of 0.6."""

    def test_a_near_miss_beats_an_unrelated_word(self):
        near = phonemic_match_score("fight", "fights")
        far = phonemic_match_score("fight", "mountain")
        assert near > far
        assert near > 0.6

    def test_a_dropped_sound_still_scores_well(self):
        assert phonemic_match_score("surrender", "surrenders") > 0.6

    def test_real_asr_confusions_score_better_than_before(self):
        """Measured cases from the fast channel: it heard "says" for "chases"
        and "and" for "found", and the shipped scorer gave those 0.33 and 0.40
        by falling through to letter distance."""
        for heard, sung in (("says", "chases"), ("and", "found")):
            assert phonemic_match_score(heard, sung) > metaphone_match_score(
                heard, sung
            )

    def test_an_unrelated_word_still_scores_low(self):
        """The point is a graded distance, not a generous one."""
        assert phonemic_match_score("mountain", "jesus") < 0.35


class TestUnknownWords:
    def test_asr_nonsense_is_not_in_the_dictionary(self):
        assert in_dictionary("samulay") is False
        assert in_dictionary("jesus") is True

    def test_a_plural_falls_back_to_its_stem(self):
        """Lyrics inflect words the dictionary lists in one form only."""
        assert phonemes("mountains")[:3] == phonemes("mountain")[:3]

    def test_a_misspelling_is_scored_by_spelling_not_by_guesswork(self):
        """Guessing a pronunciation from letters made this *worse*: an early
        version scored "graace" at 0.25 against "grace" where metaphone gave
        0.90. For a word no dictionary knows, spelling is all the evidence
        there is."""
        assert phonemic_match_score("graace", "grace") == metaphone_match_score(
            "graace", "grace"
        )

    def test_the_fallback_can_never_do_worse_than_what_it_replaced(self):
        for heard, sung in (("graace", "grace"), ("mountainn", "mountain"),
                            ("enig", "jesus"), ("samulay", "surrender")):
            assert phonemic_match_score(heard, sung) == metaphone_match_score(
                heard, sung
            )


class TestContract:
    """Properties the tracker's emission kernel assumes."""

    def test_identical_words_score_one(self):
        assert phonemic_match_score("grace", "grace") == 1.0

    def test_empty_input_is_no_evidence(self):
        assert phonemic_match_score("", "grace") == 0.0
        assert phonemic_match_score("grace", "") == 0.0

    def test_the_score_stays_in_range(self):
        pairs = [("jesus", "surrender"), ("a", "hallelujah"), ("oh", "oh"),
                 ("999", "grace"), ("'", "praise")]
        for a, b in pairs:
            assert 0.0 <= phonemic_match_score(a, b) <= 1.0

    def test_case_and_punctuation_do_not_matter(self):
        """The fast channel emits uppercase; Whisper emits punctuation."""
        assert phonemic_match_score("JESUS", "jesus") == 1.0
        assert phonemic_match_score("Jesus,", "jesus") == 1.0


def test_the_tracker_actually_gets_the_phonemic_scorer():
    """The gap a mutation test found: every call site imports
    `phonetic_match_score`, so if that stopped delegating, the tracker would
    silently fall back to spelling and nothing here would notice."""
    from auto_presenter.index.phonetic import phonetic_match_score

    # knight/night is identical in sound and unequal in spelling, so only the
    # phonemic path can score it 1.0.
    assert phonetic_match_score("knight", "night") == 1.0


def test_the_dictionary_loads_once_however_many_ask_at_once(monkeypatch):
    """A session warms it while the first sung word may already be asking:
    both must share one load, not each pay for their own."""
    import threading
    import time

    from auto_presenter.index import phonemic

    loads = []

    def slow_load():
        loads.append(1)
        time.sleep(0.05)
        return {"grace": [["G", "R", "EY1", "S"]]}

    monkeypatch.setattr(phonemic, "_loaded", None)
    monkeypatch.setattr(phonemic, "load_cmudict", slow_load)
    got = []
    threads = [threading.Thread(target=lambda: got.append(phonemic._dictionary())) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(loads) == 1
    assert all(g is got[0] for g in got)
