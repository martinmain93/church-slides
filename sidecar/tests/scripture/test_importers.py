"""Bible-file importers: every format, read from a small handwritten fixture.

Each fixture carries the markup that a real file of that format has and that
must never reach a screen — footnotes, cross-references, section headings,
Strong's numbers — next to the verse text that must. The real-file check (the
full eBible.org, open-bibles and OpenSong Bibles these were developed against)
is in `Plan/BIBLE_INTEGRATION.md`; these keep the behaviour pinned without a
40 MB download.
"""

# ruff: noqa: E501 — the fixtures keep real files' long marked-up lines intact.

from __future__ import annotations

import io
import json
import zipfile

import pytest

from auto_presenter.scripture import store as store_mod
from auto_presenter.scripture.importers import BibleImportError, parse_file, plan
from auto_presenter.scripture.store import VerseStore, install_imported


def _verses(data: bytes | str, name: str = "") -> dict[tuple[str | None, int, int], str]:
    raw = data.encode() if isinstance(data, str) else data
    report = plan(parse_file(raw, name))
    return {
        (p.code, c, v): text
        for p in report.books
        for c, verses in p.raw.chapters.items()
        for v, text in verses.items()
    }


USFM = r"""\id JHN World English Bible
\h John
\toc2 John
\mt1 The Good News According to John
\c 3
\s1 Jesus and Nicodemus
\p
\v 16 \w For|strong="G1063"\w* God so \w loved|strong="G25"\w* the world,\f + \fr 3:16 \ft a footnote\f* that he gave his only Son;
\v 17 \x - \xo 3:17 \xt Luke 9:56\x*For God didn’t send his Son\wj into the world\wj*.
\q1
\v 18-19 Merged verses here.
\c 4
\p
\v 1 \nd Lord\nd* of all.
"""


def test_usfm_keeps_words_and_drops_notes_headings_and_attributes():
    verses = _verses(USFM)
    assert verses[("JHN", 3, 16)] == ("For God so loved the world, that he gave his only Son;")
    assert verses[("JHN", 3, 17)] == "For God didn’t send his Son into the world."
    assert verses[("JHN", 4, 1)] == "LORD of all."  # \nd is the divine name


def test_usfm_merged_verses_resolve_by_either_number_and_show_once(tmp_path, monkeypatch):
    monkeypatch.setattr(store_mod, "scripture_dir", lambda: tmp_path)
    report = plan(parse_file(USFM.encode()))
    assert report.merged_verses == 1
    translation = install_imported(report, name="Test", abbreviation="TST", attribution="x")
    with VerseStore(translation) as store:
        assert store.resolve("John 3:19").text == "Merged verses here."
        assert store.resolve("John 3:18-19").text == "Merged verses here."
        assert store.resolve("John 3:17-19").text.count("Merged") == 1


def test_usfm_zip_of_books_skips_front_matter():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("00-FRTeng.usfm", "\\id FRT\n\\c 1\n\\v 1 Preface text.")
        archive.writestr("73-JHNeng.usfm", USFM)
        archive.writestr(
            "copr.htm",
            "<html><title>Test Bible</title><body><p>Public Domain</p></body></html>",
        )
    parsed = parse_file(buffer.getvalue(), "test_usfm.zip")
    report = plan(parsed)
    assert [p.code for p in report.books] == ["JHN"]
    assert report.title == "Test Bible"
    assert report.rights == "Public Domain"


OSIS = """<?xml version="1.0" encoding="UTF-8"?>
<osis xmlns="http://www.bibletechnologies.net/2003/OSIS/namespace">
<osisText osisIDWork="KJV" xml:lang="en">
<header><work osisWork="KJV"><title>King James Version</title></work></header>
<div type="book" osisID="Ps">
<chapter osisID="Ps.23" sID="Ps.23"/>
<title type="psalm">A Psalm of David.</title>
<verse osisID="Ps.23.1" sID="Ps.23.1"/>The <divineName>Lord</divineName> is my shepherd;
<note type="study">a note</note>I shall not want.<verse eID="Ps.23.1"/>
<verse osisID="Ps.23.2" sID="Ps.23.2"/><l>He maketh me to lie down</l><l>in green pastures.</l><verse eID="Ps.23.2"/>
<chapter eID="Ps.23"/>
</div>
<div type="book" osisID="John"><chapter osisID="John.11">
<verse osisID="John.11.35">Jesus wept.</verse>
</chapter></div>
</osisText></osis>
"""


def test_osis_milestone_and_container_verses():
    verses = _verses(OSIS)
    assert verses[("PSA", 23, 1)] == "The LORD is my shepherd; I shall not want."
    assert verses[("PSA", 23, 2)] == "He maketh me to lie down in green pastures."
    assert verses[("JHN", 11, 35)] == "Jesus wept."


def test_osis_metadata_suggests_a_name():
    report = plan(parse_file(OSIS.encode()))
    assert report.format == "OSIS"
    assert report.title == "King James Version"
    assert report.language == "en"


USX = """<?xml version="1.0" encoding="utf-8"?>
<usx version="3.0">
<book code="ROM" style="id">Romans</book>
<para style="h">Romans</para>
<chapter number="8" style="c" sid="ROM 8"/>
<para style="s1">Life in the Spirit</para>
<para style="p"><verse number="28" style="v" sid="ROM 8:28"/>We know that all things
<char style="w" strong="G4903">work</char> together for good<note caller="+" style="f">a note</note>.<verse eid="ROM 8:28"/></para>
<chapter eid="ROM 8"/>
</usx>
"""


def test_usx():
    assert _verses(USX)[("ROM", 8, 28)] == "We know that all things work together for good."


USFX = """<?xml version="1.0" encoding="utf-8"?>
<usfx xml:lang="en">
<book id="GEN"><id id="GEN">World English Bible</id><h>Genesis</h>
<c id="1"/><s>The Creation</s>
<p><v id="1"/>In the beginning, God created the heavens and the earth.<f>a note</f><ve/>
<v id="2"/>The earth was formless.<ve/></p>
</book>
</usfx>
"""


def test_usfx():
    verses = _verses(USFX)
    assert verses[("GEN", 1, 1)] == "In the beginning, God created the heavens and the earth."
    assert verses[("GEN", 1, 2)] == "The earth was formless."


ZEFANIA = """<?xml version="1.0" encoding="utf-8"?>
<XMLBIBLE biblename="Douay Rheims">
<INFORMATION><title>Douay Rheims</title><identifier>DRA</identifier><language>ENG</language></INFORMATION>
<BIBLEBOOK bnumber="14" bname="2 Chronicles" bsname="1Chr">
<CHAPTER cnumber="7"><CAPTION>A heading</CAPTION>
<VERS vnumber="14">And my people<NOTE>a note</NOTE> shall pray.</VERS>
</CHAPTER></BIBLEBOOK>
<BIBLEBOOK bnumber="69" bname="Tobit" bsname="Tob">
<CHAPTER cnumber="1"><VERS vnumber="1">Tobias of the tribe.</VERS></CHAPTER>
</BIBLEBOOK>
</XMLBIBLE>
"""


def test_zefania_trusts_the_number_over_a_wrong_short_name():
    """A real Douay-Rheims file labels 2 Chronicles "1Chr"."""
    verses = _verses(ZEFANIA)
    assert verses[("2CH", 7, 14)] == "And my people shall pray."
    assert verses[("TOB", 1, 1)] == "Tobias of the tribe."


OPENSONG = """<?xml version="1.0" encoding="UTF-8"?>
<bible>
<b n="Rút"><c n="1"><v n="16">Kam ty pôjdeš, pôjdem aj ja.</v></c></b>
<b n="Ján"><c n="3"><v n="16">Veď Boh tak miloval svet.</v></c></b>
</bible>
"""


def test_opensong_keeps_books_it_cannot_identify_under_their_own_names():
    """Two short books in Slovak: too little shape to identify, so they are
    kept — and listed — rather than guessed at or dropped."""
    report = plan(parse_file(OPENSONG.encode()))
    assert report.format == "OpenSong"
    assert sorted(report.unmatched) == ["Ján", "Rút"]


def test_unidentified_books_can_still_be_looked_up_by_their_own_name(tmp_path, monkeypatch):
    monkeypatch.setattr(store_mod, "scripture_dir", lambda: tmp_path)
    report = plan(parse_file(OPENSONG.encode()))
    translation = install_imported(report, name="SK", abbreviation="SK", attribution="x")
    with VerseStore(translation) as store:
        assert store.resolve("Ján 3:16").text == "Veď Boh tak miloval svet."


def test_verse_per_line():
    text = "\n".join(
        [f"GEN 1:{v} Verse {v} of Genesis." for v in range(1, 12)]
        + ["SOL 2:1 I am a rose of Sharon.", "1JO 4:8 God is love."]
    )
    verses = _verses(text)
    assert verses[("GEN", 1, 3)] == "Verse 3 of Genesis."
    assert verses[("SNG", 2, 1)] == "I am a rose of Sharon."
    assert verses[("1JN", 4, 8)] == "God is love."


def test_csv_with_a_header():
    text = (
        "Book,Chapter,Verse,Text\n"
        'John,3,16,"For God so loved the world, that"\n'
        "1 John,4,8,God is love.\n"
    )
    verses = _verses(text, "bible.csv")
    assert verses[("JHN", 3, 16)] == "For God so loved the world, that"
    assert verses[("1JN", 4, 8)] == "God is love."


def test_tsv_with_numbered_books():
    text = "b\tc\tv\tt\n43\t3\t16\tFor God so loved\n1\t1\t1\tIn the beginning\n"
    verses = _verses(text, "bible.tsv")
    assert verses[("JHN", 3, 16)] == "For God so loved"
    assert verses[("GEN", 1, 1)] == "In the beginning"


@pytest.mark.parametrize(
    "payload",
    [
        {
            "books": [
                {
                    "nr": 43,
                    "name": "John",
                    "chapters": [
                        {"chapter": 3, "verses": [{"verse": 16, "text": "For God so loved"}]}
                    ],
                }
            ]
        },
        [{"book": "John", "chapter": 3, "verse": 16, "text": "For God so loved"}],
        {"John": {"3": {"16": "For God so loved"}}},
    ],
)
def test_json_layouts(payload):
    assert _verses(json.dumps(payload))[("JHN", 3, 16)] == "For God so loved"


def test_a_file_that_is_not_a_bible_says_so():
    with pytest.raises(BibleImportError, match="doesn't look like a Bible"):
        parse_file(b"just some notes\nnothing here\n")
    with pytest.raises(BibleImportError, match="isn't a Bible format"):
        parse_file(b"<?xml version='1.0'?><html><body/></html>")
    with pytest.raises(BibleImportError, match="valid XML"):
        parse_file(b"<osis><broken></osis>")


def test_a_bible_with_no_verses_is_refused():
    with pytest.raises(BibleImportError, match="No verses"):
        plan(parse_file(b"<bible><b n='Genesis'></b></bible>"))


def test_report_lists_missing_books_and_latin_psalm_numbering():
    """A file with Psalms numbered the Greek/Latin way (Psalm 9 of ~39
    verses) and an Old Testament short of most of its books."""
    lines = [f"Psalms 9:{v} Verse." for v in range(1, 40)]
    report = plan(parse_file("\n".join(lines).encode()))
    assert any("Greek/Latin numbering" in w for w in report.warnings)
    assert "Genesis" in report.missing
    assert "Matthew" not in report.missing  # no New Testament at all: not "missing"
    assert any("Old Testament only" in w for w in report.warnings)


def test_windows_1252_text_is_read():
    """Older Bible files are often Windows-1252: the en dash is byte 0x96."""
    lines = ["John 3:16 For God so loved the world – truly."]
    lines += [f"John 3:{v} Filler." for v in range(1, 11)]
    data = "\n".join(lines).encode("cp1252")
    assert b"\x96" in data
    assert _verses(data)[("JHN", 3, 16)] == "For God so loved the world – truly."
