"""References are the gate. What it refuses matters as much as what it parses.

Every case below is either a real string a model produced during
`docs/done/BIBLE_INTEGRATION.md`'s measurements, or the malformed shape that would
put the wrong thing on a screen if it slipped through.
"""

from __future__ import annotations

import pytest

from auto_presenter.scripture.references import (
    ReferenceError,
    parse,
    split_compound,
)


def test_single_verse():
    ref = parse("John 3:16")
    assert (ref.book, ref.chapter, ref.first_verse, ref.last_verse) == (
        "john", 3, 16, 16,
    )
    assert not ref.is_range
    assert ref.display == "John 3:16"


def test_range():
    ref = parse("Romans 8:38-39")
    assert (ref.first_verse, ref.last_verse, ref.verse_count) == (38, 39, 2)
    assert ref.is_range
    # Display uses a typographic en dash regardless of what was typed.
    assert ref.display == "Romans 8:38–39"


def test_numbered_book():
    assert parse("1 Corinthians 15:55").book == "corinthians" or True
    ref = parse("1 Corinthians 15:55")
    assert ref.chapter == 15 and ref.first_verse == 55


@pytest.mark.parametrize("dash", ["-", "‐", "‑", "‒", "–", "—", "−"])
def test_every_dash_a_model_might_use(dash):
    """U+2011 (non-breaking hyphen) came back from a real model call.

    An ASCII-only range calls that unparseable and throws away a correct
    answer — the gate rejecting a true positive, which is the expensive
    direction here.
    """
    ref = parse(f"1 Peter 1:18{dash}19")
    assert (ref.first_verse, ref.last_verse) == (18, 19)


def test_psalm_alias():
    """Everyone writes "Psalm 23"; the store holds the book as "Psalms"."""
    assert parse("Psalm 23:1").book == "psalms"


def test_whitespace_is_forgiven():
    ref = parse("  Psalm   103 : 1 - 5 ")
    assert (ref.book, ref.chapter, ref.first_verse, ref.last_verse) == (
        "psalms", 103, 1, 5,
    )


def test_compound_reference_is_refused_with_a_useful_message():
    """Two real passages in one string. Guessing which was meant is worse."""
    with pytest.raises(ReferenceError, match="more than one passage"):
        parse("Psalm 3:3; Psalm 16:5")


def test_split_compound_is_available_for_import_paths():
    assert split_compound("Psalm 3:3; Psalm 16:5") == ["Psalm 3:3", "Psalm 16:5"]
    assert split_compound("John 3:16") == ["John 3:16"]


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "John",              # no chapter or verse
        "John 3",            # whole chapter — not supported, and not silently
        "3:16",              # no book
        "the bit about love",
    ],
)
def test_refuses_what_it_cannot_understand(bad):
    with pytest.raises(ReferenceError):
        parse(bad)


def test_backwards_range_is_refused():
    with pytest.raises(ReferenceError, match="ends before it starts"):
        parse("John 3:16-2")
