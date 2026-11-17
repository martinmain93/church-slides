"""USFM: the plain-text markup Bible translators work in (Paratext, eBible.org).

One book per file, `\\id GEN` on the first line, `\\c 1` and `\\v 1` for chapter
and verse, and a few dozen other markers for everything else. What matters for
display is short to state:

- **drop** notes (`\\f…\\f*`, `\\x…\\x*`), figures, alternate verse numbers,
  and every paragraph that isn't scripture text (titles, section headings,
  introductions, Psalm superscriptions);
- **keep** the words inside character markup (`\\w grace|strong="G5485"\\w*`
  is just "grace");
- **uppercase** `\\nd` (the divine name, printed LORD in small capitals).

Everything else — poetry indents, paragraph breaks — flattens to a space,
because the verse store holds plain text per verse.
"""

from __future__ import annotations

import re

from auto_presenter.scripture.books import NON_SCRIPTURE_CODES
from auto_presenter.scripture.importers.model import (
    ParsedBible,
    RawBook,
    parse_verse_number,
)

#: Paragraph markers whose content is not scripture text. Matched on the
#: marker name with any trailing level digit removed ("s1" -> "s").
_SKIP_PARAGRAPHS = frozenset(
    """
    id ide h toc toca mt mte ms mr s sr r d sp cl cd rem sts usfm restore periph
    imt imte is ip ipi im imi ipq imq ipr iq ib ili iot io iex ie qa lit
    """.split()
)

#: Notes and other inline spans removed together with their content.
_SPAN_RE = re.compile(
    r"\\(f|fe|ef|x|ex|fig|rq|va|vp|ca|cat|jmp)\b.*?\\\1\*",
    re.DOTALL,
)
#: USFM 3 milestones (`\qt-s |sid="..."\*`, `\zaln-s |...\*`, `\ts\*`).
_MILESTONE_RE = re.compile(r"\\[a-z0-9]+(?:-[se])?\s*(?:\|[^\\]*)?\\\*")
#: Word attributes: `\w grace|strong="G5485"\w*` -> `\w grace\w*`.
_ATTR_RE = re.compile(r"\|[^\\|]*(?=\\\+?[a-z0-9]+\*)")
#: The divine name, LORD in small capitals.
_ND_RE = re.compile(r"\\\+?nd\s(.*?)\\\+?nd\*", re.DOTALL)
#: Any marker: `\v`, `\q1`, `\+w`, `\w*`.
_MARKER_RE = re.compile(r"\\(\+?)([a-zA-Z]+\d*)(\*?)")
_DIGIT_TAIL_RE = re.compile(r"\d+$")
_NUMBER_RE = re.compile(r"^\s*(\d+(?:[a-z])?(?:\s*[-–]\s*\d+[a-z]?)?)\s?")


def clean_markup(text: str) -> str:
    """Remove the spans, milestones and attributes that never reach a screen."""
    text = _SPAN_RE.sub(" ", text)
    text = _MILESTONE_RE.sub("", text)
    text = _ATTR_RE.sub("", text)
    return _ND_RE.sub(lambda m: m.group(1).upper(), text)


def parse_usfm(text: str, bible: ParsedBible) -> None:
    """Parse one USFM book into `bible`. Non-scripture books are skipped."""
    text = clean_markup(text.lstrip("\ufeff"))
    state = _State(bible)
    marker: str | None = None
    pos = 0
    for match in _MARKER_RE.finditer(text):
        state.handle(marker, text[pos : match.start()])
        pos = match.end()
        plus, name, closing = match.groups()
        if closing or plus or name in _CHARACTER_MARKERS:
            # Character markup opening or closing: the words around it carry
            # on whatever paragraph they were in. After `\v 3 …` that is the
            # verse's own text, which the pseudo-marker "p" stands for.
            if marker in ("v", "c"):
                marker = "p"
            continue
        marker = name
    state.handle(marker, text[pos:])
    state.finish()


class _State:
    def __init__(self, bible: ParsedBible) -> None:
        self.bible = bible
        self.code = ""
        self.name = ""
        self.book: RawBook | None = None
        self.chapter = 0
        self.verse: tuple[int, int] | None = None

    def handle(self, marker: str | None, content: str) -> None:
        if marker is None:
            return
        if marker == "id":
            words = content.split()
            self.code = words[0].upper() if words else ""
        elif marker in ("h", "toc2"):
            if content.strip() and not self.name:
                self.name = content.strip()
        elif marker == "c":
            number = _NUMBER_RE.match(content)
            if number:
                self.chapter = int(re.match(r"\d+", number.group(1)).group(0))  # type: ignore[union-attr]
                self.verse = None
        elif marker == "v":
            number = _NUMBER_RE.match(content)
            parsed = parse_verse_number(number.group(1)) if number else None
            if parsed is None or not self.chapter:
                self.verse = None
                return
            self.verse = parsed
            self._add(content[number.end() :])  # type: ignore[union-attr]
        elif _DIGIT_TAIL_RE.sub("", marker) in _SKIP_PARAGRAPHS:
            return
        elif self.verse is not None:
            self._add(content)

    def _add(self, content: str) -> None:
        if self.code in NON_SCRIPTURE_CODES or self.verse is None:
            return
        if self.book is None:
            self.book = self.bible.book(self.name or self.code, code=self.code or None)
        self.book.add(self.chapter, self.verse[0], self.verse[1], content)

    def finish(self) -> None:
        if self.book is not None and self.name:
            self.book.name = self.name


_CHARACTER_MARKERS = frozenset(
    """
    w wj add bk qt k tl it bd em sc no pn png addpn ord sig sls dc bdit lit qs
    qac ior iqt rb wg wh wa sup ndx nd rq fr fq fqa fk ft fl fw fp fv fdc xo xk
    xq xt xta xop xot xnt xdc
    """.split()
)
