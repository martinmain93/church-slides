"""The passage around a verse (`scripture.sections`)."""

from __future__ import annotations

from auto_presenter.scripture._sections import SECTIONS
from auto_presenter.scripture.sections import Context, contexts, section_of


def test_the_table_covers_the_bible():
    assert len(SECTIONS) == 66
    assert SECTIONS["JHN"][3] == ((1, "Jesus and Nicodemus"), (22, "John’s Testimony about Jesus"))


def test_john_3_16_offers_the_rest_of_the_section_then_all_of_it_then_the_chapter():
    assert contexts("JHN", 3, 16, 36) == [
        Context(16, 21),
        Context(1, 21, "Jesus and Nicodemus"),
        Context(1, 36),
    ]


def test_a_verse_that_opens_its_section_is_not_offered_twice():
    # 3:22 starts "John's Testimony about Jesus", which runs to the chapter's end.
    assert contexts("JHN", 3, 22, 36) == [
        Context(22, 36, "John’s Testimony about Jesus"),
        Context(1, 36),
    ]


def test_the_last_verse_of_a_section_offers_the_section_and_the_chapter():
    assert contexts("JHN", 3, 21, 36) == [
        Context(1, 21, "Jesus and Nicodemus"),
        Context(1, 36),
    ]


def test_a_chapter_that_continues_the_last_ones_section():
    first = SECTIONS["ROM"][8][0][0]
    if first > 1:
        assert section_of("ROM", 8, 1, 39) == Context(1, first - 1, None)


def test_a_book_the_table_doesnt_know_still_offers_its_chapter():
    assert contexts("XYZ", 1, 3, 10) == [Context(1, 10)]
