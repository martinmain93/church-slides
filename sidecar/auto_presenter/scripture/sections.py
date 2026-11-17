"""The passage around a verse, for offering more than the verse asked for.

"John 3:16" is usually what someone means — but often the reading is the
rest of the paragraph, or the whole story. Sections come from the Berean
Standard Bible's headings (`_sections.py`), which suit any translation:
section breaks follow the text's sense, not one translation's wording.
"""

from __future__ import annotations

from dataclasses import dataclass

from auto_presenter.scripture._sections import SECTIONS


@dataclass(frozen=True)
class Context:
    first: int
    last: int
    #: The section's heading, when the range is a whole section.
    heading: str | None = None


def section_of(code: str, chapter: int, verse: int, last_in_chapter: int) -> Context | None:
    """The section `verse` sits in, within its chapter, with its heading.

    A chapter whose first heading comes after verse 1 starts by continuing
    the previous chapter's section; that stretch counts as a section of its
    own, without a heading.
    """
    starts = SECTIONS.get(code.upper(), {}).get(chapter)
    if not starts:
        return None
    bounds = [(1, None)] if starts[0][0] > 1 else []
    bounds += [(start, title) for start, title in starts]
    for i, (start, title) in enumerate(bounds):
        end = bounds[i + 1][0] - 1 if i + 1 < len(bounds) else last_in_chapter
        if start <= verse <= end:
            return Context(start, min(end, last_in_chapter), title)
    return None


def contexts(code: str, chapter: int, verse: int, last_in_chapter: int) -> list[Context]:
    """Up to three wider passages for one verse, narrowest first:

    - the verse to the end of its section ("John 3:16–21"),
    - the whole section ("John 3:1–21", "Jesus and Nicodemus"),
    - the whole chapter, when it is more than that section.

    Anything that would only repeat the verse, or another option, is left out.
    """
    out: list[Context] = []
    section = section_of(code, chapter, verse, last_in_chapter)
    if section is not None:
        if section.first < verse:
            out.append(Context(verse, section.last))
        out.append(section)  # from its first verse: the same range, with a heading
    out.append(Context(1, last_in_chapter))
    seen = {(verse, verse)}
    kept: list[Context] = []
    for c in out:
        if (c.first, c.last) in seen:
            continue
        seen.add((c.first, c.last))
        kept.append(c)
    return kept[:3]
