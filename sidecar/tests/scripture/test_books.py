"""The canonical book table: every spelling of a book lands on one identity.

The cases here are the ones real files and real operators produced — a VPL
file's "SOL" and "JOH", Zefania's numbering, a Slovak "3. Jánov" that is one
chapter of 15 verses like the Prayer of Manasseh — not a tour of the table.
"""

from __future__ import annotations

import pytest

from auto_presenter.scripture.books import (
    BOOKS,
    BY_CODE,
    by_code,
    canon_index,
    identify,
    match_shape,
    normalize_name,
    shape_similarity,
)


@pytest.mark.parametrize(
    "spelling, code",
    [
        ("Genesis", "GEN"),
        ("gen", "GEN"),
        ("Song of Songs", "SNG"),
        ("Canticles", "SNG"),
        ("Song of Solomon", "SNG"),
        ("Psalm", "PSA"),
        ("I John", "1JN"),
        ("First John", "1JN"),
        ("1st John", "1JN"),
        ("1John", "1JN"),
        ("1. John", "1JN"),
        ("Revelations", "REV"),
        ("Ecclesiasticus", "SIR"),
        ("Wisdom of Solomon", "WIS"),
        ("Tobias", "TOB"),
        # eBible.org's VPL codes, which are not USFM's.
        ("SOL", "SNG"),
        ("JOH", "JHN"),
        ("MAR", "MRK"),
        ("JAM", "JAS"),
        ("PHI", "PHP"),
        ("3JO", "3JN"),
    ],
)
def test_identify_accepts_the_spellings_files_use(spelling, code):
    book = identify(spelling)
    assert book is not None and book.code == code


def test_codes_and_osis_ids_are_both_accepted():
    assert by_code("EXO").name == "Exodus"
    assert by_code("Exod").name == "Exodus"
    assert by_code("1Kgs").code == "1KI"
    assert by_code("nonsense") is None


def test_prefixes_resolve_only_when_unambiguous():
    assert identify("jer", prefix=True).code == "JER"
    assert identify("philip", prefix=True).code == "PHP"
    assert identify("jo", prefix=True) is None  # John, Job, Joel, Jonah, Joshua
    assert identify("jerem") is None  # without prefix, an exact spelling only


def test_normalize_spells_ordinals_as_digits():
    assert normalize_name("II Kings") == "2 kings"
    assert normalize_name("Second  Kings.") == "2 kings"
    assert normalize_name("2Kings") == "2 kings"


def test_every_book_has_a_reference_shape():
    """`match_shape` and the import report need a shape for every book the
    reference Bible carries; the few it doesn't (Odes, Laodiceans…) are the
    rare ones."""
    missing = [b.code for b in BOOKS if not b.verse_counts]
    assert set(missing) <= {"ODA", "PSS", "LAO", "S3Y", "SUS", "BEL", "LJE"}


def test_a_book_matches_its_own_shape_exactly():
    genesis = BY_CODE["GEN"]
    assert shape_similarity(list(genesis.verse_counts), genesis) == pytest.approx(1.0)
    assert match_shape(list(genesis.verse_counts)).code == "GEN"


def test_catholic_versification_still_matches():
    """Malachi in three chapters (Hebrew/Catholic) is still Malachi."""
    after_zechariah = canon_index("ZEC")
    assert match_shape([14, 17, 24], after=after_zechariah).code == "MAL"


def test_order_and_a_leading_number_break_one_chapter_ties():
    """3 John has 14 or 15 verses depending on the Bible; so does the Prayer
    of Manasseh. Reading order and the "3." in the name decide it."""
    after_2john = canon_index("2JN")
    assert match_shape([15], after=after_2john, name="3. Jánov").code == "3JN"
    # Philemon and Jude are both one chapter of 25 verses.
    assert match_shape([25], after=canon_index("TIT")).code == "PHM"
    assert match_shape([25], after=canon_index("3JN")).code == "JUD"
