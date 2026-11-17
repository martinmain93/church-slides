"""Playback sequence expansion — Python twin of `src/lib/slideSequence.ts`."""

from __future__ import annotations

from auto_presenter.library.sequence import (
    first_lyric_slide_idx,
    parse_slide_order,
    resolve_slide_sequence,
)

SONG = """[verse 1]
Verse one slide one

[verse 1]
Verse one slide two

[chorus]
Chorus line

[verse 2]
Verse two line

[bridge]
Bridge line
"""


def test_leading_blank_is_slide_zero():
    slides = resolve_slide_sequence(SONG, "s1", leading_blank=True)
    assert slides[0].words == []
    assert slides[0].section.type == "other"
    assert slides[0].section.number == 0
    assert first_lyric_slide_idx(slides) == 1


def test_missing_order_is_document_order():
    slides = resolve_slide_sequence(SONG, "s1", leading_blank=False)
    texts = [s.lyrics for s in slides]
    assert texts[0] == "Verse one slide one"
    assert texts[2] == "Chorus line"


def test_a_section_written_twice_shows_once_per_block():
    body = "[Chorus]\nC one\n\nC two\n\n[Verse 1]\nV one\n\n[Chorus]\nC one\n\nC two"
    slides = resolve_slide_sequence(body, "s1", leading_blank=False)
    assert [s.lyrics for s in slides] == ["C one", "C two", "V one", "C one", "C two"]
    assert [s.order_in_song for s in slides] == [0, 1, 2, 3, 4]


def test_slide_order_repeats_the_chorus():
    slides = resolve_slide_sequence(
        SONG, "s1", "V1, C, V2, C", leading_blank=False
    )
    keys = [f"{s.section.type}:{s.section.number}" for s in slides]
    assert keys == [
        "verse:1", "verse:1", "chorus:1", "verse:2", "chorus:1",
    ]


def test_parse_slide_order_accepts_aliases():
    refs = parse_slide_order("V1, C, B")
    assert [r.type for r in refs] == ["verse", "chorus", "bridge"]
