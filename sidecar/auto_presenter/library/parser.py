"""Body-text → Slides parser.

Canonical implementation. The TypeScript port at `src/lib/parseSong.ts`
must produce byte-identical results; `tests/test_parser_parity.py`
enforces this.

See `docs/SONG_FORMAT.md` for the full format spec.
"""

from __future__ import annotations

import hashlib
import re
import string
from typing import get_args

from auto_presenter.library.types import Section, SectionType, Slide

_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
_SMART_QUOTES = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
    }
)

_KNOWN_TYPES: set[SectionType] = set(get_args(SectionType))

# Tag aliases — keyed by normalized form (lowercase, hyphens stripped).
_TAG_ALIASES: dict[str, SectionType] = {
    "v": "verse",
    "verse": "verse",
    "c": "chorus",
    "chorus": "chorus",
    "b": "bridge",
    "bridge": "bridge",
    "p": "pre-chorus",
    "prechorus": "pre-chorus",
    "pre-chorus": "pre-chorus",
    "t": "tag",
    "tag": "tag",
    "i": "intro",
    "intro": "intro",
    "o": "outro",
    "outro": "outro",
    "ending": "outro",
    "instrumental": "instrumental",
    "other": "other",
}


def parse_section_tag(raw: str) -> Section:
    """Parse the inside of `[...]` into a Section.

    Examples:
        "verse 1" -> Section(type="verse", number=1)
        "Chorus"  -> Section(type="chorus", number=1)
        "v2"      -> Section(type="verse", number=2)
        "asdf"    -> Section(type="other", number=1)
    """
    text = raw.strip().lower()

    # Trailing number — capture optional digits at end.
    m = re.match(r"^(.*?)\s*(\d+)?\s*$", text)
    body = (m.group(1) if m else text).strip()
    number = int(m.group(2)) if m and m.group(2) else 1

    # Normalize: collapse spaces, strip hyphens for lookup, but keep
    # the canonical "pre-chorus" key form.
    key = re.sub(r"\s+", "", body)  # remove spaces
    key_no_hyphen = key.replace("-", "")

    section_type: SectionType = "other"
    if key in _TAG_ALIASES:
        section_type = _TAG_ALIASES[key]
    elif key_no_hyphen in _TAG_ALIASES:
        section_type = _TAG_ALIASES[key_no_hyphen]
    elif key in _KNOWN_TYPES:
        section_type = key  # type: ignore[assignment]

    return Section(type=section_type, number=max(1, number))


def tokenize(lyrics: str) -> list[str]:
    """Slide.words for the AI matcher: lowercase, punctuation-stripped, split on ws."""
    text = lyrics.translate(_SMART_QUOTES).lower()
    # Strip punctuation but keep apostrophes inside words (it's, don't).
    cleaned: list[str] = []
    for raw_word in text.split():
        word = raw_word.strip(string.punctuation.replace("'", "") + "\"")
        # Strip leading/trailing apostrophes too — only keep interior ones.
        word = word.strip("'")
        if word:
            cleaned.append(word)
    return cleaned


def parse_body_text(body: str, song_id: str) -> tuple[list[Slide], list[Section]]:
    """Parse a body text into (slides, section_order)."""
    normalized = body.translate(_SMART_QUOTES).replace("\r\n", "\n").replace("\r", "\n")

    # Split into "blocks" separated by blank lines, but emit a new block when
    # a section tag is encountered (even mid-paragraph).
    current_section: Section = Section(type="other", number=1)
    slides_raw: list[tuple[Section, str]] = []
    section_order: list[Section] = []

    buffer_lines: list[str] = []

    def flush() -> None:
        if not buffer_lines:
            return
        text = "\n".join(line.rstrip() for line in buffer_lines).strip("\n")
        text = text.strip()
        if text:
            slides_raw.append((current_section, text))
        buffer_lines.clear()

    section_seen_for_current_song = False

    for line in normalized.split("\n"):
        m = _SECTION_RE.match(line)
        if m:
            flush()
            current_section = parse_section_tag(m.group(1))
            # Track distinct section appearances (in order).
            if not section_order or section_order[-1] != current_section:
                section_order.append(current_section)
            section_seen_for_current_song = True
            continue

        if line.strip() == "":
            flush()
            continue

        # Pre-section content goes under (other, 1) by default.
        if not section_seen_for_current_song and not section_order:
            section_order.append(current_section)
            section_seen_for_current_song = True

        buffer_lines.append(line)

    flush()

    # Build Slide objects with deterministic IDs and per-section ordering.
    slides: list[Slide] = []
    section_counts: dict[tuple[SectionType, int], int] = {}
    for order_in_song, (section, lyrics) in enumerate(slides_raw):
        key = (section.type, section.number)
        order_in_section = section_counts.get(key, 0)
        section_counts[key] = order_in_section + 1
        slide_id = _slide_id(song_id, order_in_song, lyrics)
        slides.append(
            Slide(
                id=slide_id,
                song_id=song_id,
                section=section,
                order_in_song=order_in_song,
                order_in_section=order_in_section,
                lyrics=lyrics,
                words=tokenize(lyrics),
            )
        )

    return slides, section_order


def _slide_id(song_id: str, order_in_song: int, lyrics: str) -> str:
    payload = f"{song_id}:{order_in_song}:{lyrics}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
