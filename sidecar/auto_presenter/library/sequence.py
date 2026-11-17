"""Playback slide sequence — the Python twin of `src/lib/slideSequence.ts`.

The UI and the matcher must agree on slide indices. The TypeScript side
prepends a synthetic blank and expands `slide_order`; if the sidecar ranked a
library song against document order it would land on the wrong slide once
that song was imported. This module is the same expansion, so a hit here is
a hit there.

Parity with the TS expander is enforced by `tests/library/test_sequence.py`
against the same cases `src/__tests__/slideSequence.test.ts` covers.
"""

from __future__ import annotations

from auto_presenter.library.parser import parse_body_text, parse_section_tag
from auto_presenter.library.types import Section, Slide

BLANK_SECTION = Section(type="other", number=0)


def parse_slide_order(spec: str | None) -> list[Section]:
    """`"V1, V2, C"` → section refs. Empty / missing → []."""
    if not spec:
        return []
    tokens = [t for t in spec.replace(",", " ").split() if t]
    return [parse_section_tag(t) for t in tokens]


def _section_key(section: Section) -> str:
    return f"{section.type}:{section.number}"


def blank_slide(song_id: str) -> Slide:
    return Slide(
        id="blank",
        song_id=song_id,
        section=BLANK_SECTION,
        order_in_song=0,
        order_in_section=0,
        lyrics="",
        words=[],
    )


def resolve_slide_sequence(
    body: str,
    song_id: str,
    slide_order: str | None = None,
    *,
    leading_blank: bool = True,
) -> list[Slide]:
    """Concrete play sequence, matching `resolveSlideSequence` in TypeScript.

    A missing/empty `slide_order` plays each section once in document order.
    Unknown order tokens are skipped. `leading_blank` prepends the synthetic
    song-start slide the operator and audience both see as index 0.
    """
    parsed, _ = parse_body_text(body, song_id)
    by_section: dict[str, list[Slide]] = {}
    for slide in parsed:
        by_section.setdefault(_section_key(slide.section), []).append(slide)

    order = parse_slide_order(slide_order)
    out: list[Slide] = []
    if not order:
        # Each block once, as written: walking the section order through the
        # buckets would show a section written twice (two [Chorus] blocks)
        # twice over.
        out = [s.model_copy(update={"order_in_song": i}) for i, s in enumerate(parsed)]
    for ref in order:
        bucket = by_section.get(_section_key(ref))
        if not bucket:
            continue
        for slide in bucket:
            out.append(
                slide.model_copy(update={"order_in_song": len(out)})
            )

    if not leading_blank:
        return out
    shifted = [
        s.model_copy(update={"order_in_song": i + 1}) for i, s in enumerate(out)
    ]
    return [blank_slide(song_id), *shifted]


def section_key_of(slide: Slide) -> str:
    return _section_key(slide.section)


def first_lyric_slide_idx(slides: list[Slide]) -> int:
    """Index of the first slide that has words, else 0."""
    for i, slide in enumerate(slides):
        if slide.words:
            return i
    return 0
