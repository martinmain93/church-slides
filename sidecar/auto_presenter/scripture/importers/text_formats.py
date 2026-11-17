"""The plain-text Bible formats: verse-per-line, CSV/TSV, and JSON.

These are the fallback formats — what a church has when its Bible came out of
a spreadsheet, a database dump or another program's export. None of them has
a standard, so each reader accepts the handful of shapes seen in the wild and
refuses anything else with a message saying what it expected.

- **Verse per line** (eBible.org's VPL, and most "plain text" Bibles):
  `GEN 1:1 In the beginning…` or `Genesis 1:1 In the beginning…`.
- **CSV / TSV** with a header naming the columns (`book, chapter, verse,
  text`, or the short `b, c, v, t`), or four unlabelled columns in that order.
- **JSON**: getbible.net's `{"books": [{"name", "chapters": [...]}]}`; a list
  of verse records with the same column names as CSV; or the nested
  `{"Genesis": {"1": {"1": "In the beginning…"}}}`.
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any

from auto_presenter.scripture.importers.model import (
    BibleImportError,
    ParsedBible,
    parse_verse_number,
)

#: `GEN 1:1 text`, `Genesis 1:1 text`, `1 John 3.16 text`, `Song of Songs 2:1-2 text`.
_LINE_RE = re.compile(
    r"^\s*(?P<book>(?:[1-4]\s?)?[^\W\d_][^\d:]*?)\s+(?P<c>\d+)\s*[:.]\s*"
    r"(?P<v>\d+[a-z]?(?:\s*[-–]\s*\d+[a-z]?)?)\s+(?P<text>\S.*)$"
)


def parse_verse_lines(text: str, bible: ParsedBible) -> bool:
    """Parse `Book C:V text` lines. False if this isn't that format."""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    matches = [_LINE_RE.match(line) for line in lines]
    hits = sum(1 for m in matches if m)
    # A header line or two, or a stray blank-ish line, is fine; a file that is
    # mostly something else is not this format.
    if hits < max(10, int(len(lines) * 0.9)) and hits != len(lines):
        return False
    skipped = 0
    for match in matches:
        if not match:
            skipped += 1
            continue
        verse = parse_verse_number(match.group("v"))
        if verse is None:
            skipped += 1
            continue
        book = bible.book(" ".join(match.group("book").split()))
        book.add(int(match.group("c")), verse[0], verse[1], match.group("text"))
    if skipped:
        bible.warnings.append(f"{skipped} line(s) didn't look like a verse and were skipped.")
    return True


_COLUMN_NAMES = {
    "book": ("book", "b", "book_name", "bookname", "book_id", "bookid", "book name"),
    "chapter": ("chapter", "c", "chapter_number", "chapternumber", "ch"),
    "verse": ("verse", "v", "verse_number", "versenumber", "vs"),
    "text": ("text", "t", "verse_text", "versetext", "content", "scripture"),
}


def _columns(header: list[str]) -> dict[str, int] | None:
    lowered = [h.strip().lower() for h in header]
    found: dict[str, int] = {}
    for role, names in _COLUMN_NAMES.items():
        for index, name in enumerate(lowered):
            if name in names:
                found[role] = index
                break
    return found if len(found) == 4 else None


def parse_table(text: str, bible: ParsedBible) -> bool:
    """CSV or TSV (or `;`/`|` separated). False if it isn't a table."""
    sample = text[:20000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
    except csv.Error:
        return False
    rows = [row for row in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in row)]
    if len(rows) < 2:
        return False
    columns = _columns(rows[0])
    if columns is not None:
        rows = rows[1:]
    elif len(rows[0]) >= 4 and rows[0][1].strip().isdigit() and rows[0][2].strip().isdigit():
        columns = {"book": 0, "chapter": 1, "verse": 2, "text": 3}
    else:
        return False
    _add_records(
        ({role: row[i] if i < len(row) else "" for role, i in columns.items()} for row in rows),
        bible,
    )
    return True


def _add_records(records: Any, bible: ParsedBible) -> None:
    skipped = 0
    for record in records:
        raw_book = str(record.get("book", "")).strip()
        verse = parse_verse_number(str(record.get("verse", "")))
        chapter = str(record.get("chapter", "")).strip()
        if not raw_book or verse is None or not chapter.isdigit():
            skipped += 1
            continue
        if raw_book.isdigit():
            # A numbered book column: Genesis = 1, the Zefania numbering.
            book = bible.book(f"Book {raw_book}", number=int(raw_book))
        else:
            book = bible.book(raw_book)
        book.add(int(chapter), verse[0], verse[1], str(record.get("text", "")))
    if skipped:
        bible.warnings.append(
            f"{skipped} row(s) were missing a book, chapter or verse and were skipped."
        )


def parse_json(text: str, bible: ParsedBible) -> None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as err:
        raise BibleImportError(f"This file isn't valid JSON ({err.msg}).") from err
    parse_json_data(data, bible)


def parse_json_data(data: Any, bible: ParsedBible) -> None:
    """The JSON layouts, already decoded. getbible.net downloads arrive here."""
    if isinstance(data, dict) and isinstance(data.get("books"), list):
        bible.title = str(data.get("translation") or data.get("name") or "")
        bible.abbreviation = str(data.get("abbreviation") or "")
        bible.language = str(data.get("lang") or data.get("language") or "")
        for entry in data["books"]:
            number = entry.get("nr") or entry.get("number")
            book = bible.book(
                str(entry.get("name") or f"Book {number}"),
                number=int(number) if str(number or "").isdigit() else None,
            )
            for chapter in entry.get("chapters", []):
                c = int(chapter.get("chapter") or chapter.get("number") or 0)
                for verse in chapter.get("verses", []):
                    span = parse_verse_number(str(verse.get("verse") or verse.get("number") or ""))
                    if c and span:
                        book.add(c, span[0], span[1], str(verse.get("text", "")))
        return

    records = data.get("verses") if isinstance(data, dict) else data
    if isinstance(records, list) and records and isinstance(records[0], dict):
        _add_records(
            ({role: _pick(r, names) for role, names in _COLUMN_NAMES.items()} for r in records),
            bible,
        )
        return

    if isinstance(data, dict) and data and all(isinstance(v, dict) for v in data.values()):
        # {"Genesis": {"1": {"1": "In the beginning…"}}}
        for name, chapters in data.items():
            book = bible.book(str(name))
            for c, verses in chapters.items():
                if not str(c).isdigit() or not isinstance(verses, dict):
                    continue
                for v, verse_text in verses.items():
                    span = parse_verse_number(str(v))
                    if span:
                        book.add(int(c), span[0], span[1], str(verse_text))
        return

    raise BibleImportError(
        "This JSON isn't in a Bible layout we recognise (expected a list of "
        "books with chapters and verses, or a list of verse records)."
    )


def _pick(record: dict[str, Any], names: tuple[str, ...]) -> Any:
    for key, value in record.items():
        if key.lower() in names:
            return value
    return ""
