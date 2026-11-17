"""Open Hymnal ABC scores -> song text.

The fixtures are cut-down copies of the shapes real Open Hymnal files have:
the melody's `w:` lines interleaved verse by verse under each line of music,
a harmony voice whose own `w:` lines are echoes (and must be ignored), a
refrain written under verse 1 only, extra verses as `W:` lines. The converter
was checked against all 351 Open Hymnal files — see `Plan/HYMNS.md`.
"""

# ruff: noqa: E501 — fixtures keep the real files' long lines intact.

from __future__ import annotations

from auto_presenter.hymns.abc import parse_abc, parse_people, to_song, tokenize_lyrics

AMAZING = """\
%OHMETRICAL 8 6 8 6
%OHAUTHOR Newton, John (1725-1807)
%OHTRANSLATOR none
X: 1
T: Amazing Grace
C: Words: John Newton, 1779.  Music: 'New Britain' 1831.
C: copyright: public domain.  This score is a part of the Open Hymnal Project, 2006 Revision.
K: G
[V: S1V1] D | G2 B/G/ | B2 A | G2 E | D2
w: 1.~A- maz- ing * grace! How sweet the sound That
w: 2.~'Twas grace that * taught my heart to fear, And
[V: S2V1] B, | D2 D | D2 D | D2 C | B,2
[V: S1V1] D | G2 B/G/ | B2 A | G2 E | D2
w: saved a * wretch like me! * I once * was * lost, but
w: grace my * fears re- lieved; * How pre- * cious * did that
[V: S1V1] G | B2 A | G2 E | D2
w:   now * am * found; Was blind, but * now I see. *
w:   grace * ap- * pear The hour I * first be- lieved. *
W: 3.Through many dangers, toils and snares,
W: I have already come;
W: 'Tis grace hath brought me safe thus far,
W: And grace will lead me home.
"""


def test_syllables_join_into_words_and_skips_are_dropped():
    syllables = tokenize_lyrics("1.~A- maz- ing * grace! How")
    assert [s.text for s in syllables] == ["A", "maz", "ing", "grace!", "How"]
    assert [s.word_end for s in syllables] == [False, False, True, True, True]


def test_verses_are_reassembled_and_broken_into_lines_by_the_metre():
    song = to_song(parse_abc(AMAZING))
    assert song.body_text.startswith(
        "[verse 1]\n"
        "Amazing grace! How sweet the sound\n"
        "That saved a wretch like me!\n"
        "I once was lost, but now am found;\n"
        "Was blind, but now I see.\n"
    )
    assert (
        "[verse 2]\n'Twas grace that taught my heart to fear,\nAnd grace my fears relieved;"
        in song.body_text
    )
    # The W: verse keeps its own line breaks.
    assert (
        "[verse 3]\nThrough many dangers, toils and snares,\nI have already come;" in song.body_text
    )
    assert song.slide_order == ""


def test_header_gives_title_people_and_copyright():
    hymn = parse_abc(AMAZING)
    assert hymn.title == "Amazing Grace"
    assert hymn.metre == [8, 6, 8, 6]
    assert [(p.name, p.died) for p in hymn.authors] == [("John Newton", 1807)]
    assert hymn.translators == []
    assert hymn.copyright.startswith("copyright: public domain")


IT_IS_WELL = """\
%OHMETRICAL 11 8 11 9 6 9
X: 1
T: It Is Well With My Soul
T: (also known as When Peace Like a River)
K: D
[V: S1V1] A | A2 G F | F2 E F | (G B) A G | F3 A | d2 c
w: 1.~When peace, like a ri- ver, at- ten- * deth my way, When sor- rows
w: 2.~Though Sa- tan should buf- fet, though tri- * als should come, Let this blest
[V: S1V1] B | B2 A =G | A3 A | d2 d c | B2 B B | e2 e d |
w: like sea bil- lows roll; What- ev- er my lot, Thou has taught me to
w: a- ssur- ance con- trol, That Christ has re- gar- ded my help- less es-
[V: S1V1] c2 B A | d2 d d | d2 c3/2 d/ | !fermata!d2 "^Refrain" A A |
w: say, It is well, it is well, with my soul. It is
w: tate, And hath shed His own blood for my soul.  *  *
[V: S1V1] A4- | A2 A A | A4- | A2 F A | B2 B d | d2 c3/2 d/ | d3 |]
w: well,  * with my soul,  * It is well, it is well with my soul.
[V: S2V1] z2 A, D | C2 z2 | z2 C E | D2 A, D | D2 D B, | A,2 A,3/2 A,/ | A,3 |]
w: It is well,  with my soul,  * * * * * * * * *
"""


def test_a_refrain_written_under_verse_one_becomes_a_chorus():
    hymn = parse_abc(IT_IS_WELL)
    assert hymn.also_known_as == ["When Peace Like a River"]
    song = to_song(hymn)
    assert song.has_refrain
    assert song.slide_order == "V1, C, V2, C"
    assert (
        "[chorus]\nIt is well, with my soul,\nIt is well, it is well with my soul."
        in song.body_text
    )
    # Verse 1 ends where verse 2 does; the harmony's echo isn't sung text.
    assert "[verse 1]\nWhen peace, like a river, attendeth my way," in song.body_text
    assert song.body_text.count("It is well, with my soul") == 1


LITANY = """\
%OHMETRICAL 7 7 7 7
X: 1
T: A Litany
K: C
[V: S1V1] C D E F | G A B c |
w: 1.~Sa- vior, when in dust to Thee Low we bow th'a- dor- ing knee,
w: 2.~By Thy help- less in- fant years, By Thy life of want and tears,
[V: S1V1] C D E F | G A B c |
w: Bend- ing from Thy throne on high, Hear our pen- i- ten- tial cry!
w: Turn, O turn a fav'- ring eye, Hear our pen- i- ten- tial cry!
"""


def test_a_line_every_verse_ends_with_is_not_mistaken_for_a_chorus():
    """It ends each verse, but it isn't whole lines of a refrain — cutting it
    off put half a sentence on its own slide."""
    song = to_song(parse_abc(LITANY))
    assert not song.has_refrain
    assert song.body_text.count("Hear our penitential cry!") == 2


def test_a_hyphen_before_a_capital_is_a_dash_and_before_lowercase_a_compound():
    hymn = parse_abc(
        "%OHMETRICAL 8 8\nX: 1\nT: T\nK: C\n[V: S1V1] C D E F G A B c\n"
        "w: Wait- ing the com- ing day\\- Je- sus my Lord! Liv- ing, life\\- im- part- ing Spir- it\n"
    )
    text = to_song(hymn).body_text
    assert "day— Jesus" in text
    assert "life-imparting" in text


def test_people_with_awkward_dates_and_traditions():
    assert [(p.name, p.died) for p in parse_people("Francis of Assisi (1181/1182-1226)")] == [
        ("Francis of Assisi", 1226)
    ]
    assert parse_people("Turner, H. L. (18th Century)")[0].died == 1800
    assert parse_people("Ristad, Ditlef Georgson, 1863-1938")[0].died == 1938
    # A tradition or a source book is not a person.
    assert parse_people("German;anonymous;1650 from the Scottish Psalter") == []
