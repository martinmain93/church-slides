"""Body-text parser tests."""

from __future__ import annotations

import pytest

from auto_presenter.library.parser import (
    parse_body_text,
    parse_section_tag,
    tokenize,
)


# --- parse_section_tag ---------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "type_", "number"),
    [
        ("verse 1", "verse", 1),
        ("Verse 1", "verse", 1),
        ("VERSE 1", "verse", 1),
        ("verse", "verse", 1),
        ("v", "verse", 1),
        ("V1", "verse", 1),
        ("v 2", "verse", 2),
        ("chorus", "chorus", 1),
        ("Chorus 2", "chorus", 2),
        ("c", "chorus", 1),
        ("c3", "chorus", 3),
        ("bridge", "bridge", 1),
        ("b", "bridge", 1),
        ("pre-chorus", "pre-chorus", 1),
        ("pre chorus", "pre-chorus", 1),
        ("prechorus", "pre-chorus", 1),
        ("Pre-Chorus 2", "pre-chorus", 2),
        ("p", "pre-chorus", 1),
        ("tag", "tag", 1),
        ("intro", "intro", 1),
        ("outro", "outro", 1),
        ("ending", "outro", 1),
        ("instrumental", "instrumental", 1),
        ("something weird", "other", 1),
        ("verse 0", "verse", 1),  # zero clamps to 1
    ],
)
def test_parse_section_tag(raw: str, type_: str, number: int) -> None:
    section = parse_section_tag(raw)
    assert section.type == type_
    assert section.number == number


# --- tokenize -----------------------------------------------------------------


def test_tokenize_strips_punctuation_keeps_apostrophes():
    assert tokenize("My God, my Shepherd has carried me!") == [
        "my",
        "god",
        "my",
        "shepherd",
        "has",
        "carried",
        "me",
    ]
    assert tokenize("It's don't can't won't") == ["it's", "don't", "can't", "won't"]


def test_tokenize_handles_smart_quotes():
    assert tokenize("‘Tis grace") == ["tis", "grace"]
    assert tokenize("“Amazing grace”") == ["amazing", "grace"]


# --- parse_body_text ----------------------------------------------------------


def test_parses_basic_song():
    body = """[verse 1]
Amazing grace how sweet the sound
That saved a wretch like me

[chorus]
Grace upon grace now covers me
My shepherd and my Savior leads me home

[verse 2]
'Twas grace that taught my heart to fear
And grace my fears relieved
"""
    slides, sections = parse_body_text(body, "song1")
    assert len(slides) == 3
    assert slides[0].section.type == "verse"
    assert slides[0].section.number == 1
    assert "Amazing grace" in slides[0].lyrics
    assert slides[1].section.type == "chorus"
    assert slides[2].section.type == "verse"
    assert slides[2].section.number == 2

    assert sections == [
        slides[0].section,
        slides[1].section,
        slides[2].section,
    ]


def test_blank_lines_split_slides_within_section():
    body = """[verse 1]
Slide one line one
Slide one line two

Slide two line one
Slide two line two
"""
    slides, _ = parse_body_text(body, "song1")
    assert len(slides) == 2
    assert slides[0].lyrics.split("\n") == [
        "Slide one line one",
        "Slide one line two",
    ]
    assert slides[1].lyrics.split("\n") == [
        "Slide two line one",
        "Slide two line two",
    ]


def test_implicit_slide_break_on_section_tag():
    body = """[verse 1]
Verse line
[chorus]
Chorus line
"""
    slides, _ = parse_body_text(body, "song1")
    assert len(slides) == 2
    assert slides[0].section.type == "verse"
    assert slides[0].lyrics == "Verse line"
    assert slides[1].section.type == "chorus"
    assert slides[1].lyrics == "Chorus line"


def test_empty_section_tags_produce_no_slide():
    body = """[verse 1]
[chorus]
Chorus content
"""
    slides, sections = parse_body_text(body, "song1")
    assert len(slides) == 1
    assert slides[0].section.type == "chorus"
    # Section_order still contains both since they were declared.
    assert sections[0].type == "verse"
    assert sections[1].type == "chorus"


def test_content_before_any_tag_becomes_other():
    body = """Some intro text
that nobody tagged

[verse 1]
Verse content
"""
    slides, _ = parse_body_text(body, "song1")
    assert len(slides) == 2
    assert slides[0].section.type == "other"
    assert slides[1].section.type == "verse"


def test_smart_quotes_normalized():
    body = """[verse 1]
‘Tis grace
"""
    slides, _ = parse_body_text(body, "song1")
    assert "'Tis grace" in slides[0].lyrics


def test_windows_line_endings():
    body = "[verse 1]\r\nLine one\r\nLine two\r\n"
    slides, _ = parse_body_text(body, "song1")
    assert len(slides) == 1
    assert slides[0].lyrics.split("\n") == ["Line one", "Line two"]


def test_deterministic_ids():
    body = "[verse 1]\nFoo\n"
    a, _ = parse_body_text(body, "song1")
    b, _ = parse_body_text(body, "song1")
    assert a[0].id == b[0].id


def test_id_changes_when_lyrics_change():
    a, _ = parse_body_text("[v]\nFoo\n", "song1")
    b, _ = parse_body_text("[v]\nBar\n", "song1")
    assert a[0].id != b[0].id


def test_id_changes_when_song_id_changes():
    a, _ = parse_body_text("[v]\nFoo\n", "song1")
    b, _ = parse_body_text("[v]\nFoo\n", "song2")
    assert a[0].id != b[0].id


def test_section_numbering_within_section():
    body = """[verse 1]
A

B

[verse 1]
C
"""
    slides, _ = parse_body_text(body, "song1")
    assert len(slides) == 3
    # Three slides all in (verse, 1); order_in_section should increment.
    assert [s.order_in_section for s in slides] == [0, 1, 2]


def test_repeated_chorus_in_different_positions():
    body = """[verse 1]
Verse
[chorus]
Chorus
[verse 2]
Verse 2
[chorus]
Chorus again
"""
    slides, sections = parse_body_text(body, "song1")
    assert len(slides) == 4
    assert slides[1].section == slides[3].section  # same chorus identity
    assert slides[1].order_in_section == 0
    assert slides[3].order_in_section == 1


def test_words_field_populated():
    slides, _ = parse_body_text("[v]\nAmazing grace, how sweet!\n", "song1")
    assert slides[0].words == ["amazing", "grace", "how", "sweet"]


def test_empty_body_produces_no_slides():
    slides, sections = parse_body_text("", "song1")
    assert slides == []
    assert sections == []


def test_whitespace_only_body():
    slides, _ = parse_body_text("\n\n   \n\n", "song1")
    assert slides == []


def test_multiple_blank_lines_between_slides():
    body = """[verse 1]
A



B
"""
    slides, _ = parse_body_text(body, "song1")
    assert len(slides) == 2
