"""What every Bible-file parser produces, before books are identified.

Parsers only *read*: each turns its format into a `ParsedBible` of books as the
file names them, chapters, and verse text. Deciding which canonical book a
"Genezis" or a `bnumber="69"` is happens afterwards, in one place
(`importers.plan`), so every format gets the same identification rules and the
same report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class RawBook:
    #: The book's name as the file gives it ("Genesis", "Genezis", "GEN").
    name: str
    #: A code the format states outright (USFM `\id GEN`, OSIS `Gen`). Trusted
    #: over the name when present.
    code: str | None = None
    #: Zefania's `bnumber`. Trusted for 1-66, cross-checked by name above.
    number: int | None = None
    #: chapter -> verse -> text.
    chapters: dict[int, dict[int, str]] = field(default_factory=dict)
    #: Other names the file gives the book (Zefania's `bsname`). Hints only.
    alt_names: list[str] = field(default_factory=list)
    #: Verse ranges the file merged into one ("\v 1-2"): counted for the report.
    merged: int = 0

    def add(self, chapter: int, first: int, last: int, text: str) -> None:
        """Append raw text to a verse (or to a merged range of verses).

        Text is appended as-is — markup boundaries fall mid-sentence
        ("shepherd\\w*;") and a separator would put a space before the
        semicolon — and cleaned once the whole file is read (`finish`).

        A merged range stores the same text under every verse it spans; the
        verse store drops adjacent duplicates when it builds a passage, so
        "Romans 1:1-2" shows the text once and "Romans 1:2" alone still
        finds it.
        """
        verses = self.chapters.setdefault(chapter, {})
        if last > first and first not in verses:
            self.merged += 1
        for verse in range(first, max(first, last) + 1):
            verses[verse] = verses.get(verse, "") + text

    def finish(self) -> int:
        """Clean every verse's text; drop the empty ones. Returns how many.

        Empty verses are real: a translation that omits a verse on textual
        grounds still marks it (`\\v 15-16` with only a footnote). Storing
        it empty would put a blank passage on a screen.
        """
        dropped = 0
        for chapter in list(self.chapters):
            verses = self.chapters[chapter]
            for verse in list(verses):
                text = clean_text(verses[verse])
                if text:
                    verses[verse] = text
                else:
                    del verses[verse]
                    dropped += 1
            if not verses:
                del self.chapters[chapter]
        return dropped

    @property
    def verse_count(self) -> int:
        return sum(len(v) for v in self.chapters.values())

    def shape(self) -> list[int]:
        """Verses per chapter, chapter 1 upward (a missing chapter counts 0)."""
        if not self.chapters:
            return []
        last = max(self.chapters)
        return [len(self.chapters.get(c, {})) for c in range(1, last + 1)]


@dataclass
class ParsedBible:
    format: str
    books: list[RawBook] = field(default_factory=list)
    title: str = ""
    abbreviation: str = ""
    language: str = ""
    rights: str = ""
    warnings: list[str] = field(default_factory=list)

    def book(self, name: str, **kwargs: object) -> RawBook:
        """The book named `name`, created (in file order) on first sight."""
        for book in self.books:
            if book.name == name and book.code == kwargs.get("code", book.code):
                return book
        book = RawBook(name=name, **kwargs)  # type: ignore[arg-type]
        self.books.append(book)
        return book


class BibleImportError(ValueError):
    """A file that can't be imported. The message is shown to the operator."""


_WS_RE = re.compile(r"\s+")
#: Pilcrows and similar paragraph marks some KJV files embed in verse text.
_MARKS_RE = re.compile(r"[¶§]")
#: A space that crept in before punctuation when markup was removed.
_SPACE_PUNCT_RE = re.compile(r"\s+([,.;:!?’”)\]])")


def clean_text(text: str) -> str:
    text = _MARKS_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return _SPACE_PUNCT_RE.sub(r"\1", text)


_RANGE_RE = re.compile(r"^\s*(\d+)\s*(?:[a-z])?\s*(?:[-–,]\s*(\d+)\s*[a-z]?)?\s*$")


def parse_verse_number(text: str) -> tuple[int, int] | None:
    """`"3"` -> (3, 3); `"1-2"` -> (1, 2); `"4a"` -> (4, 4); junk -> None."""
    match = _RANGE_RE.match(text or "")
    if not match:
        return None
    first = int(match.group(1))
    last = int(match.group(2)) if match.group(2) else first
    return (first, last) if last >= first else (first, first)
