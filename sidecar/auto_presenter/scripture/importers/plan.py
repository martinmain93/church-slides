"""Decide which canonical book each of a file's books is, and report on it.

One set of rules for every format, in order of how much they can be trusted:

    code     the file states a USFM code or OSIS id outright
    number   Zefania-style book number (1-66 are universal)
    name     an English name or abbreviation (`books.identify`)
    shape    chapter and verse counts (`books.match_shape`) — any language

A name match is double-checked against the shape, because a name in another
language can collide with an English abbreviation. A book that matches nothing
is kept under its own name: it can still be looked up by that name, and the
report says so, rather than the import failing over one unusual book.

The report is what the operator sees before anything is installed. It exists
so that a bad file — half a Bible, a New Testament with a mangled book table,
Psalms numbered the Latin way — shows up as a readable list on the import
screen, and not as a missing verse in the middle of a service.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from auto_presenter.scripture.books import (
    BOOKS,
    PROTESTANT,
    ZEFANIA_NUMBERS,
    Book,
    by_code,
    canon_index,
    identify,
    match_shape,
    shape_similarity,
)
from auto_presenter.scripture.importers.model import BibleImportError, ParsedBible, RawBook


@dataclass
class PlannedBook:
    raw: RawBook
    book: Book | None
    matched_by: str  # "code" | "number" | "name" | "shape" | "" (unmatched)

    @property
    def name(self) -> str:
        """The name the verse store files this book under."""
        return self.book.name if self.book else self.raw.name

    @property
    def code(self) -> str | None:
        return self.book.code if self.book else None


@dataclass
class ImportReport:
    format: str
    title: str
    abbreviation: str
    language: str
    rights: str
    verse_count: int
    books: list[PlannedBook]
    unmatched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    deuterocanon: list[str] = field(default_factory=list)
    incomplete: list[str] = field(default_factory=list)
    merged_verses: int = 0
    empty_verses: int = 0
    warnings: list[str] = field(default_factory=list)


#: A name match whose shape is this poor, while another book's shape is
#: excellent, is taken to be a collision and overridden.
_NAME_DOUBT = 0.4
_SHAPE_SURE = 0.9


def plan(bible: ParsedBible) -> ImportReport:
    empty = 0
    for raw in bible.books:
        empty += raw.finish()
    raws = [raw for raw in bible.books if raw.verse_count]
    if not raws:
        raise BibleImportError(
            f"No verses found. The file looks like {bible.format}, but nothing "
            "in it was recognised as chapter-and-verse text."
        )

    planned: list[PlannedBook] = []
    assigned: set[str] = set()
    after = -1
    for raw in raws:
        book, how = _identify(raw, assigned, after)
        planned.append(PlannedBook(raw, book, how))
        if book is not None:
            assigned.add(book.code)
            after = canon_index(book.code)

    return _report(bible, planned, empty)


def _identify(raw: RawBook, assigned: set[str], after: int) -> tuple[Book | None, str]:
    shape = raw.shape()

    def free(book: Book | None) -> Book | None:
        return book if book is not None and book.code not in assigned else None

    if raw.code and (book := free(by_code(raw.code))):
        return book, "code"

    if raw.number is not None and raw.number in ZEFANIA_NUMBERS:
        book = free(by_code(ZEFANIA_NUMBERS[raw.number]))
        if book is not None:
            if raw.number <= 66:
                return book, "number"
            # 67+ is less settled across files: accept only with a second
            # opinion from the name or the shape.
            named = _by_names(raw)
            if (named and named.code == book.code) or shape_similarity(shape, book) >= 0.6:
                return book, "number"

    named = free(_by_names(raw))
    if named is not None:
        if shape and shape_similarity(shape, named) < _NAME_DOUBT:
            better = match_shape(
                shape, exclude=assigned, after=after, name=raw.name, threshold=_SHAPE_SURE
            )
            if better is not None:
                return better, "shape"
        return named, "name"

    shaped = match_shape(shape, exclude=assigned, after=after, name=raw.name)
    if shaped is not None:
        return shaped, "shape"
    return None, ""


def _by_names(raw: RawBook) -> Book | None:
    for name in (raw.name, *raw.alt_names):
        book = identify(name)
        if book is not None:
            return book
    return None


def _report(bible: ParsedBible, planned: list[PlannedBook], empty: int) -> ImportReport:
    codes = {p.code for p in planned if p.code}
    has_ot = any(p.book and p.book.section == "ot" for p in planned)
    has_nt = any(p.book and p.book.section == "nt" for p in planned)
    missing = [
        b.name
        for b in PROTESTANT
        if b.code not in codes
        and ((b.section == "ot" and has_ot) or (b.section == "nt" and has_nt))
    ]
    incomplete = []
    for p in planned:
        if p.book is None or not p.book.verse_counts:
            continue
        expected = sum(p.book.verse_counts)
        # The deuterocanon's versification varies far more between Bibles
        # (KJV's "Additions to Esther" is the additions alone; some Baruchs
        # include the Letter of Jeremiah), so only a book that is mostly
        # missing is worth a warning there.
        floor = 0.4 if p.book.section == "dc" else 0.9
        if p.raw.verse_count < expected * floor:
            incomplete.append(f"{p.book.name}: {p.raw.verse_count:,} of {expected:,} verses")

    warnings = list(bible.warnings)
    if not has_ot and has_nt:
        warnings.append("New Testament only.")
    elif has_ot and not has_nt:
        warnings.append("Old Testament only.")
    psalms = next((p for p in planned if p.code == "PSA"), None)
    if psalms is not None and len(psalms.raw.chapters.get(9, {})) > 30:
        # Hebrew Psalm 9 has 20 verses; the Greek/Latin numbering joins 9 and
        # 10 into one psalm of ~39 and is one behind from there to 147.
        warnings.append(
            "Psalms use the Greek/Latin numbering (as in the Douay-Rheims): "
            "from Psalm 10 to 147, a psalm here is numbered one lower than in "
            "most modern Bibles — Psalm 22 here is Psalm 23 elsewhere."
        )
    shaped = [p for p in planned if p.matched_by == "shape"]
    if shaped:
        warnings.append(
            f"{len(shaped)} book name(s) weren't recognised and were matched by "
            "their chapter and verse layout — check the list below."
        )

    return ImportReport(
        format=bible.format,
        title=bible.title,
        abbreviation=bible.abbreviation,
        language=bible.language,
        rights=bible.rights,
        verse_count=sum(p.raw.verse_count for p in planned),
        books=sorted(planned, key=lambda p: canon_index(p.code) if p.code else len(BOOKS)),
        unmatched=[p.raw.name for p in planned if p.book is None],
        missing=missing,
        deuterocanon=[p.book.name for p in planned if p.book and p.book.section == "dc"],
        incomplete=incomplete,
        merged_verses=sum(p.raw.merged for p in planned),
        empty_verses=empty,
        warnings=warnings,
    )
