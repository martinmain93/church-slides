"""Phonetic helper tests — Double Metaphone + scoring tiers."""

from __future__ import annotations

import pytest

from auto_presenter.index.phonetic import (
    metaphone_match_score,
    metaphone,
    normalize,
    phonetic_match_score,
)


# --- normalize --------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Hosanna!", "hosanna"),
        ("  GRACE,  ", "grace"),
        ("don't", "don't"),
        ("'glory'", "glory"),
        ("“love”", "love"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_normalize_strips_outer_punct_preserves_interior_apostrophes(raw, expected):
    assert normalize(raw) == expected


# --- metaphone caching ------------------------------------------------------


def test_metaphone_returns_pair():
    primary, secondary = metaphone("hosanna")
    assert primary == "HSN"
    assert secondary == ""


def test_metaphone_empty_input_is_safe():
    assert metaphone("") == ("", "")
    assert metaphone("   ") == ("", "")


def test_metaphone_normalizes_before_encoding():
    # Punctuation shouldn't change the code.
    assert metaphone("HOSANNA!") == metaphone("hosanna")


# --- phonetic_match_score ---------------------------------------------------


def test_identical_normalized_words_score_one():
    assert phonetic_match_score("grace", "grace") == 1.0
    assert phonetic_match_score("Grace!", "grace") == 1.0


def test_hosanna_homophones_score_high():
    """The motivating worship-music case — operator hears `hosanna`,
    Whisper hallucinates `hose-anna`. Both encode to `HSN`."""
    assert phonetic_match_score("hosanna", "hose-anna") >= 0.9
    # `savior` / `saviour` — common spelling drift Whisper produces.
    assert phonetic_match_score("savior", "saviour") >= 0.9


def test_unrelated_words_score_low():
    assert phonetic_match_score("apple", "grace") < 0.5


def test_empty_inputs_score_zero():
    assert phonetic_match_score("", "grace") == 0.0
    assert phonetic_match_score("grace", "") == 0.0
    assert phonetic_match_score("", "") == 0.0


def test_score_is_in_unit_interval():
    """A broad fuzz test: nothing should escape [0, 1]."""
    pairs = [
        ("savior", "saviour"),
        ("savior", "savor"),
        ("amazing", "amzing"),
        ("through", "thru"),
        ("forever", "forevr"),
        ("hallelujah", "halleluiah"),
        ("light", "night"),
    ]
    for a, b in pairs:
        s = phonetic_match_score(a, b)
        assert 0.0 <= s <= 1.0, (a, b, s)


def test_phonetic_match_outranks_edit_distance_fallback():
    """A pair with a metaphone primary hit must score > pair that only
    has letter-level proximity."""
    phonetic = phonetic_match_score("hosanna", "hose-anna")
    levenshtein_only = phonetic_match_score("grace", "trace")
    assert phonetic > levenshtein_only


def test_levenshtein_fallback_is_capped():
    """The letter-distance fallback can't beat the lowest phonetic tier (0.7).

    Asserted against `metaphone_match_score` specifically. `phonetic_match_score`
    now prefers `index.phonemic`, which compares actual pronunciations, and
    there the cap does not apply and should not: "light" and "night" differ by a
    single consonant and score 0.70, which is a phonemic judgement rather than a
    consolation prize for looking alike on the page. The cap exists to stop
    *spelling* proximity masquerading as sound proximity, and that is still what
    it does in the fallback path.
    """
    # Words that don't share metaphone codes but are close letter-wise.
    s = metaphone_match_score("light", "night")
    assert s <= 0.6


def test_score_is_symmetric():
    pairs = [("hosanna", "hose-anna"), ("grace", "amazing"), ("forever", "ever")]
    for a, b in pairs:
        assert phonetic_match_score(a, b) == phonetic_match_score(b, a)
