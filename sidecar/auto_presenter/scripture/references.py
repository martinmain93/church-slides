"""Bible references: parsing them, canonicalizing them, refusing bad ones.

Kept separate from the verse store because refusing a malformed reference is
the safety property of this whole feature, and it should be testable without a
database. `docs/done/BIBLE_INTEGRATION.md` §4: a suggestion that cannot be resolved
is a hallucination with a citation, which is worse on a screen in front of a
congregation than showing nothing at all.

Two things here were found by measurement rather than by thinking about it, and
both are the same shape — a *correct* answer rejected by an over-strict parser:

- **Dashes.** A model returned `1 Peter 1:18‑19` using U+2011, a non-breaking
  hyphen. An ASCII-only `[-–]` range calls that unparseable and throws away a
  real reference. `_DASHES` covers the family.
- **Compound references.** `"Psalm 3:3; Psalm 16:5"` is two real passages in
  one string. This module refuses it rather than guessing which was meant;
  the fix belongs upstream, in the tool schema that asks for one passage per
  entry. `split_compound` exists for callers that want to be lenient about it
  at *import* time, where a human is watching — never at display time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Every dash a model or a human might type in a verse range.
_DASHES = "-‐‑‒–—−"

#: Books whose common name differs from the one the verse store holds.
BOOK_ALIASES = {
    "psalm": "psalms",
    "song of songs": "song of solomon",
    "canticles": "song of solomon",
    "revelations": "revelation",
}

#: The chapter/verse separator is a colon or a full stop ("John 3.16" is the
#: common British form), and a book may carry abbreviation dots ("1 Cor.").
#: Book names are letters in any script: an imported Bible can hold a book
#: under its own name ("Ján") when it isn't one the canonical table knows.
_REF_RE = re.compile(
    r"^\s*((?:[1-4]\s*)?[^\W\d_](?:[^\W\d_]|[ .'\u2019])*?)\s*(\d+)\s*[:.]\s*(\d+)"
    rf"(?:\s*[{_DASHES}]\s*(\d+))?\s*$"
)

_COMPOUND_RE = re.compile(r"\s*[;,]\s*")

#: Books whose citation form differs from the name the verse store holds.
#: Nobody writes "Psalms 23:1" on a screen.
CITATION_NAMES = {"psalms": "Psalm"}


def citation_book(book_name: str) -> str:
    """The form a book's name takes when citing it.

    Takes the store's own spelling (which has the right capitalization for
    multi-word books like "Song of Solomon") and adjusts only where the
    citation convention differs.
    """
    return CITATION_NAMES.get(book_name.lower(), book_name)


def format_reference(book_name: str, chapter: int, first: int, last: int) -> str:
    """Render a canonical, displayable reference.

    Every path that puts a reference on a screen goes through here, so the
    same verse reads identically whether it was typed by an operator, returned
    by a search, or suggested by a model. Before this existed, a lookup of
    "Psalm 103:1" displayed the caller's own spelling while the identical
    verse arriving from search displayed the store's ("Psalms 103:1").
    """
    text = f"{citation_book(book_name)} {chapter}:{first}"
    return f"{text}–{last}" if last > first else text  # en dash, typographic


class ReferenceError(ValueError):
    """A reference that cannot be understood. The message is user-facing."""


@dataclass(frozen=True)
class Reference:
    """A parsed, normalized reference to one passage.

    `book` is lowercased for lookup; `display` is the citation form. Both
    matter — one is a key, the other is copy, and they differ in more than
    case ("psalms" vs "Psalm").
    """

    book: str
    chapter: int
    first_verse: int
    last_verse: int
    display: str

    @property
    def is_range(self) -> bool:
        return self.last_verse > self.first_verse

    @property
    def verse_count(self) -> int:
        return self.last_verse - self.first_verse + 1


def parse(reference: str) -> Reference:
    """Parse one reference. Raises `ReferenceError` on anything else.

    Deliberately strict: it accepts exactly one passage, and a caller holding
    two must have split them itself. Guessing here would mean quietly showing
    one of two passages a model asked for.
    """
    if not reference or not reference.strip():
        raise ReferenceError("empty reference")
    match = _REF_RE.match(reference)
    if match is None:
        if _COMPOUND_RE.search(reference):
            raise ReferenceError(
                f"{reference!r} looks like more than one passage — "
                "give each its own reference"
            )
        raise ReferenceError(f"{reference!r} is not a passage reference")

    book_raw = " ".join(match.group(1).replace(".", " ").split())
    book = BOOK_ALIASES.get(book_raw.lower(), book_raw.lower())
    chapter = int(match.group(2))
    first = int(match.group(3))
    last = int(match.group(4)) if match.group(4) else first
    if last < first:
        raise ReferenceError(f"{reference!r} ends before it starts")

    return Reference(
        book=book,
        chapter=chapter,
        first_verse=first,
        last_verse=last,
        display=format_reference(book_raw, chapter, first, last),
    )


def split_compound(reference: str) -> list[str]:
    """Split "Psalm 3:3; Psalm 16:5" into its parts.

    For import paths where a human is reviewing the result. Returns the input
    unchanged (as a single-element list) when there is nothing to split, so
    callers can use it unconditionally.
    """
    parts = [p.strip() for p in _COMPOUND_RE.split(reference) if p.strip()]
    return parts or [reference]
