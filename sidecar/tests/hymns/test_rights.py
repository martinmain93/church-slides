"""Copyright status of a hymn's words — the cases real Open Hymnal files raised.

The rule is stricter than the Open Hymnal's own (US law): words count as
public domain only when they were published 95+ years ago *and* every writer
died 70+ years ago. Every case where a date is missing has to land on the
cautious side.
"""

from __future__ import annotations

import datetime as dt

from auto_presenter.hymns.rights import (
    FREE_FOR_WORSHIP,
    PUBLIC_DOMAIN,
    PUBLIC_DOMAIN_US,
    Contributor,
    classify,
    words_years,
)

TODAY = dt.date(2026, 10, 17)
PD_LINE = (
    "copyright: public domain.  This score is a part of the Open Hymnal Project, 2006 Revision."
)


def test_old_words_by_long_dead_writers_are_public_domain_everywhere():
    rights = classify(PD_LINE, [Contributor("John Newton", 1807)], [1779], today=TODAY)
    assert rights.status == PUBLIC_DOMAIN


def test_great_is_thy_faithfulness_is_us_only():
    """Published 1923 (US public domain), but its author died in 1960."""
    rights = classify(PD_LINE, [Contributor("Thomas O. Chisholm", 1960)], [1923], today=TODAY)
    assert rights.status == PUBLIC_DOMAIN_US
    assert "2030" in rights.explanation


def test_the_life_rule_moves_with_the_calendar():
    chisholm = [Contributor("Thomas O. Chisholm", 1960)]
    assert classify(PD_LINE, chisholm, [1923], today=dt.date(2031, 1, 1)).status == PUBLIC_DOMAIN


def test_a_writer_with_no_death_date_is_cautious_unless_the_text_is_ancient():
    unknown = [Contributor("Edward W. Blandy", None)]
    assert classify(PD_LINE, unknown, [1890], today=TODAY).status == PUBLIC_DOMAIN_US
    assert classify(PD_LINE, unknown, [1700], today=TODAY).status == PUBLIC_DOMAIN


def test_anonymous_old_words_are_public_domain():
    assert classify(PD_LINE, [], [1900], anonymous=True, today=TODAY).status == PUBLIC_DOMAIN
    # Not marked anonymous and nobody named: we can't say — unless the words
    # are so old that whoever wrote them is certainly long dead.
    assert classify(PD_LINE, [], [1900], today=TODAY).status == PUBLIC_DOMAIN_US
    assert classify(PD_LINE, [], [1850], today=TODAY).status == PUBLIC_DOMAIN


def test_recent_words_are_not_public_domain_everywhere_even_if_the_source_says_so():
    rights = classify(PD_LINE, [Contributor("A. Writer", 1950)], [1954], today=TODAY)
    assert rights.status == PUBLIC_DOMAIN_US


def test_a_copyrighted_setting_does_not_make_the_words_copyrighted():
    line = (
        "Words and Music, public domain.  Setting: Copyright 2009 Brian J. Dumont. "
        "This setting may be freely reproduced or published for Christian worship."
    )
    rights = classify(line, [Contributor("Robert Campbell", 1868)], [1849], today=TODAY)
    assert rights.status == PUBLIC_DOMAIN


def test_free_for_worship_texts():
    line = (
        "Words: Copyright 2009, Brian J. Dumont. These lyrics may be freely reproduced or "
        "published for Christian worship, provided they are not altered, and this notice "
        "is on each copy. All other rights reserved."
    )
    assert classify(line, [], [2009], today=TODAY).status == FREE_FOR_WORSHIP
    no_copyright_word = (
        "This hymn (lyrics, music, and setting) may be freely reproduced or used for "
        "Christian worship or personal use. All other rights reserved."
    )
    assert classify(no_copyright_word, [], [2011], today=TODAY).status == FREE_FOR_WORSHIP


def test_words_years_ignore_the_music_and_read_centuries():
    assert words_years("Words: Horatio G. Spafford, 1873.  Music and Setting: Bliss, 1876.") == [
        1873
    ]
    assert words_years(["Words: Aurelius Prudentius, 4th Century."]) == [400]
