"""The XML Bible formats: OSIS, USX, USFX, Zefania and OpenSong.

They differ in vocabulary, not in shape — every one is a tree where a book
contains chapters contain verses, with notes and headings mixed into the text
that a screen must not show. So there is one walker (`_walk`) and one small
handler per format saying which elements set the book, chapter and verse,
which to skip with everything inside them, and which to uppercase.

Two verse styles have to be handled everywhere, because real files use both:

- **containers**: `<VERS vnumber="1">text</VERS>` — the verse ends with its
  element;
- **milestones**: `<verse sID="Gen.1.1"/>text<verse eID="Gen.1.1"/>` (OSIS),
  `<verse number="1"/>text` (USX), `<v id="1"/>text<ve/>` (USFX) — the verse
  runs until its end marker, or the next verse, or the end of the chapter.

Parsed with the standard library: each of these files is 5-30 MB, which an
in-memory tree handles in a second or two, and none needs anything a full XML
library adds.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable

from auto_presenter.scripture.books import NON_SCRIPTURE_CODES
from auto_presenter.scripture.importers.model import (
    BibleImportError,
    ParsedBible,
    RawBook,
    parse_verse_number,
)

#: Block-level elements: a word boundary on both sides, since text is
#: appended raw and "...earth.</l><l>And..." must not become "earth.And".
_BLOCKS = frozenset(
    "p l lg lb div chapter item list para table row cell q br BR CHAPTER VERS v c ve".split()
)


def local(tag: object) -> str:
    """An element's tag without its namespace."""
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def parse_tree(data: bytes) -> ET.Element:
    try:
        return ET.fromstring(data)
    except ET.ParseError as err:
        raise BibleImportError(f"This file isn't valid XML ({err}).") from err


class _Cursor:
    """Where the text being read belongs: a book, a chapter, a verse range."""

    def __init__(self, bible: ParsedBible) -> None:
        self.bible = bible
        self.book: RawBook | None = None
        self.chapter = 0
        self.verse: tuple[int, int] | None = None
        self.upper = 0

    def open_book(self, name: str, *, code: str | None = None, number: int | None = None) -> None:
        if code and code.upper() in NON_SCRIPTURE_CODES:
            self.book = None
        else:
            self.book = self.bible.book(name, code=code, number=number)
        self.chapter = 0
        self.verse = None

    def text(self, text: str | None) -> None:
        if not text or self.book is None or self.verse is None or not self.chapter:
            return
        if self.upper:
            text = text.upper()
        self.book.add(self.chapter, self.verse[0], self.verse[1], text)

    def space(self) -> None:
        self.text(" ")


#: start(element) -> True to read inside it, False to skip it entirely.
Start = Callable[[ET.Element, str], bool]
End = Callable[[ET.Element, str], None]


def _walk(root: ET.Element, cursor: _Cursor, start: Start, end: End) -> None:
    def visit(el: ET.Element) -> None:
        tag = local(el.tag)
        if start(el, tag):
            if tag in _BLOCKS:
                cursor.space()
            cursor.text(el.text)
            for child in el:
                visit(child)
                cursor.text(child.tail)
            if tag in _BLOCKS:
                cursor.space()
            end(el, tag)

    visit(root)


def _first_text(root: ET.Element, *paths: str) -> str:
    for path in paths:
        for el in root.iter():
            if local(el.tag) == path and (el.text or "").strip():
                return " ".join(el.text.split())  # type: ignore[union-attr]
    return ""


# --- OSIS ------------------------------------------------------------------

_OSIS_SKIP = frozenset("note title header rdg figure index milestone reference catchWord".split())
_OSIS_REF_RE = re.compile(r"^([1-4]?[A-Za-z]+)\.(\d+)\.(\d+)")


def parse_osis(root: ET.Element, bible: ParsedBible) -> None:
    cursor = _Cursor(bible)
    work = next((el for el in root.iter() if local(el.tag) == "osisText"), None)
    if work is not None:
        bible.language = (
            work.get("{http://www.w3.org/XML/1998/namespace}lang") or work.get("lang") or ""
        )
        bible.abbreviation = (work.get("osisIDWork") or "").split(".")[-1]
    header = next((el for el in root.iter() if local(el.tag) == "header"), None)
    if header is not None:
        bible.title = _first_text(header, "title")
        bible.rights = _first_text(header, "rights")

    def set_verse(osis_id: str) -> None:
        refs = [_OSIS_REF_RE.match(part) for part in osis_id.split()]
        refs = [r for r in refs if r]
        if not refs:
            cursor.verse = None
            return
        code = refs[0].group(1)
        if cursor.book is None or cursor.book.code != code:
            cursor.open_book(code, code=code)
        cursor.chapter = int(refs[0].group(2))
        cursor.verse = (int(refs[0].group(3)), int(refs[-1].group(3)))

    def start(el: ET.Element, tag: str) -> bool:
        if tag in _OSIS_SKIP:
            return False
        if tag == "div" and el.get("type") == "book" and el.get("osisID"):
            cursor.open_book(el.get("osisID", ""), code=el.get("osisID"))
        elif tag == "verse":
            if el.get("eID"):
                cursor.verse = None
            elif el.get("osisID"):
                set_verse(el.get("osisID", ""))
        elif tag == "chapter" and el.get("eID"):
            cursor.verse = None
        elif tag == "divineName":
            cursor.upper += 1
        return True

    def end(el: ET.Element, tag: str) -> None:
        if tag == "divineName":
            cursor.upper -= 1
        elif tag == "verse" and el.get("osisID") and not el.get("sID"):
            cursor.verse = None  # a container verse ends with its element

    _walk(root, cursor, start, end)


# --- USX (and DBL bundles) -------------------------------------------------

#: Paragraph styles that are not scripture text (USFM's skip list).
_USX_SKIP_STYLES = re.compile(
    r"^(h|toc\d*|toca\d*|mt\d*|mte\d*|ms\d*|mr|s\d*|sr|r|d|sp|cl|cd|rem|sts|"
    r"imt\d*|imte\d*|is\d*|ip|ipi|im|imi|ipq|imq|ipr|iq\d*|ib|ili\d*|iot|io\d*|"
    r"iex|ie|qa|lit|restore)$"
)


def parse_usx(root: ET.Element, bible: ParsedBible) -> None:
    cursor = _Cursor(bible)

    def start(el: ET.Element, tag: str) -> bool:
        style = el.get("style", "")
        if tag == "book":
            code = el.get("code", "")
            cursor.open_book(code, code=code)
            return False
        if tag in ("note", "figure", "sidebar", "ms"):
            return False
        if tag == "para":
            if style == "h" and cursor.book is not None and (el.text or "").strip():
                cursor.book.name = el.text.strip()  # type: ignore[union-attr]
            if _USX_SKIP_STYLES.match(style):
                return False
        if tag == "chapter":
            if el.get("number"):
                cursor.chapter = int(re.match(r"\d+", el.get("number", "0")).group(0))  # type: ignore[union-attr]
            cursor.verse = None
        elif tag == "verse":
            if el.get("eid"):
                cursor.verse = None
            elif el.get("number"):
                cursor.verse = parse_verse_number(el.get("number", ""))
        elif tag == "char" and style == "nd":
            cursor.upper += 1
        return True

    def end(el: ET.Element, tag: str) -> None:
        if tag == "char" and el.get("style") == "nd":
            cursor.upper -= 1

    _walk(root, cursor, start, end)


def read_dbl_metadata(root: ET.Element, bible: ParsedBible) -> None:
    """A Digital Bible Library bundle's `metadata.xml`: name, abbreviation, rights."""
    ident = next((el for el in root.iter() if local(el.tag) == "identification"), root)
    bible.title = bible.title or _first_text(ident, "name", "nameLocal")
    bible.abbreviation = bible.abbreviation or _first_text(
        ident, "abbreviation", "abbreviationLocal"
    )
    bible.language = bible.language or _first_text(root, "iso")
    bible.rights = bible.rights or _first_text(root, "statement")


# --- USFX (eBible.org's XML) -----------------------------------------------

_USFX_SKIP = frozenset(
    "f x fe fig h toc id ide rem s d cl ms mt cp ca va vp periph generated".split()
)


def parse_usfx(root: ET.Element, bible: ParsedBible) -> None:
    cursor = _Cursor(bible)
    bible.language = bible.language or (
        root.get("{http://www.w3.org/XML/1998/namespace}lang") or ""
    )

    def start(el: ET.Element, tag: str) -> bool:
        if tag == "book":
            code = el.get("id", "")
            name = next(
                (
                    (c.text or "").strip()
                    for c in el
                    if local(c.tag) == "h" and (c.text or "").strip()
                ),
                code,
            )
            cursor.open_book(name, code=code)
            return True
        if tag in _USFX_SKIP:
            return False
        if tag == "c" and el.get("id"):
            cursor.chapter = int(re.match(r"\d+", el.get("id", "0")).group(0))  # type: ignore[union-attr]
            cursor.verse = None
        elif tag == "v" and el.get("id"):
            cursor.verse = parse_verse_number(el.get("id", ""))
        elif tag == "ve":
            cursor.verse = None
        elif tag == "nd":
            cursor.upper += 1
        return True

    def end(el: ET.Element, tag: str) -> None:
        if tag == "nd":
            cursor.upper -= 1

    _walk(root, cursor, start, end)


# --- Zefania ---------------------------------------------------------------

_ZEF_SKIP = frozenset("NOTE CAPTION XREF INFORMATION REMARK MEDIA PROLOG".split())


def parse_zefania(root: ET.Element, bible: ParsedBible) -> None:
    cursor = _Cursor(bible)
    bible.title = root.get("biblename", "")
    info = next((el for el in root if local(el.tag).upper() == "INFORMATION"), None)
    if info is not None:
        bible.title = _first_text(info, "title") or bible.title
        bible.abbreviation = _first_text(info, "identifier")
        bible.language = _first_text(info, "language")
        bible.rights = _first_text(info, "rights")

    def start(el: ET.Element, tag: str) -> bool:
        tag = tag.upper()
        if tag in _ZEF_SKIP:
            return False
        if tag == "BIBLEBOOK":
            number = el.get("bnumber")
            cursor.open_book(
                el.get("bname") or el.get("bsname") or f"Book {number}",
                number=int(number) if number and number.isdigit() else None,
            )
            if cursor.book is not None and el.get("bsname"):
                # A hint for identification, not trusted as a code: one real
                # file labels 2 Chronicles "1Chr".
                cursor.book.alt_names.append(el.get("bsname", ""))
        elif tag == "CHAPTER":
            cursor.chapter = _int(el.get("cnumber"))
            cursor.verse = None
        elif tag == "VERS":
            cursor.verse = parse_verse_number(el.get("vnumber", ""))
        return True

    def end(el: ET.Element, tag: str) -> None:
        if tag.upper() == "VERS":
            cursor.verse = None

    _walk(root, cursor, start, end)


# --- OpenSong --------------------------------------------------------------


def parse_opensong(root: ET.Element, bible: ParsedBible) -> None:
    cursor = _Cursor(bible)

    def start(el: ET.Element, tag: str) -> bool:
        if tag == "b":
            cursor.open_book(el.get("n", "").strip() or f"Book {len(bible.books) + 1}")
        elif tag == "c":
            cursor.chapter = _int(el.get("n"))
            cursor.verse = None
        elif tag == "v":
            cursor.verse = parse_verse_number(el.get("n", ""))
        return True

    def end(el: ET.Element, tag: str) -> None:
        if tag == "v":
            cursor.verse = None

    _walk(root, cursor, start, end)


def _int(value: str | None) -> int:
    match = re.match(r"\s*(\d+)", value or "")
    return int(match.group(1)) if match else 0


#: Root tag -> parser. Matched case-insensitively.
PARSERS: dict[str, tuple[str, Callable[[ET.Element, ParsedBible], None]]] = {
    "osis": ("OSIS", parse_osis),
    "usx": ("USX", parse_usx),
    "usfx": ("USFX", parse_usfx),
    "xmlbible": ("Zefania", parse_zefania),
    "bible": ("OpenSong", parse_opensong),
}
