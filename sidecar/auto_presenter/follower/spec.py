"""What the score follower follows, independent of where it came from.

A `ReferenceSpec` is a song as the follower needs it:

* one segment per slide it can place (counted loops add passes when the
  reference is built), each a run of cells (eighth notes)
  carrying the slide's words at their cells, and the melody and chords where
  the source knows them;
* the slide orders the band has been seen to sing (a lead sheet's roadmap,
  past services);
* which sung slides the reference covers.

`SegmentReference` builds the follower's state space from a spec and never
looks behind it. Builders:

* `reference.sheet_spec`: a lead sheet. Rhythm, melody, chords, roadmap.
* `library_spec` (here): the library's slides alone. Library order, a word a
  beat, no music. Any song can be followed with it. Replayed over the 8 live
  traces it read 0.290 tuning / 0.358 held-out, against roughly 0.38 / 0.455
  for the production engine and 0.168 / 0.252 for a lead sheet
  (docs/done/LEARNED_REFERENCES.md).
* learned from past services: later (`auto_presenter.learning`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from auto_presenter.follower import score as sc
from auto_presenter.follower.sheet import CELL

#: No melody / no chord at a cell: neutral to that source's evidence.
NONE = -1


@dataclass
class SegmentSpec:
    """One slide's place in the reference: `cells` long, from its first sung
    word up to the next line's first word."""

    slide: int
    cells: int
    #: (normalised word, cell offset in the segment), in the slide's word order.
    words: list[tuple[str, int]]
    #: MIDI note per cell, NONE where unknown or silent.
    midi: np.ndarray
    #: Chord class per cell (`sheet.chord_class`), NONE where unknown.
    chord: np.ndarray

    def __post_init__(self) -> None:
        if len(self.midi) != self.cells or len(self.chord) != self.cells:
            raise ValueError(f"slide {self.slide}: music tables must be {self.cells} cells long")
        if any(not 0 <= c < self.cells for _w, c in self.words):
            raise ValueError(f"slide {self.slide}: a word lies outside its segment")


@dataclass
class ReferenceSpec:
    #: Where it came from: "lead_sheet", "library", ...
    source: str
    #: In the order their cells are laid out (library slide order).
    segments: list[SegmentSpec]
    #: Sung slides the reference covers, in library order. Includes a slide
    #: shown by another one's segment (a tag inside a verse), which is covered
    #: without a segment of its own.
    covered: list[int]
    #: Slide orders the band was seen to sing, mixed into the order prior
    #: (`reference.order_prior`). Empty: library order alone.
    sequences: list[list[int]] = field(default_factory=list)
    #: Slide orders learned from past services, each with its weight
    #: (`auto_presenter.learning.profile`): mixed into the order prior on top
    #: of the base the `sequences` give (`reference.order_prior`).
    learned: list[tuple[list[int], float]] = field(default_factory=list)
    #: The first slide sung after the intro; None: the first segment.
    first_slide: int | None = None
    #: A lead sheet's spans (`score.Span` per slide), for diagnostics.
    spans: dict | None = None
    #: Slides to treat as looped (sung again straight after themselves), each
    #: a counted loop (`reference.LOOPS`). Nothing sets it in production:
    #: marking slides the look-back heard looped measured worse (0.170 ->
    #: 0.178); loops come from the song's structure.
    loops: list[int] = field(default_factory=list)


#: The library reference's rhythm: a word a beat, and a bar's gap after each
#: line before the next one's first word.
LIBRARY_BEATS_PER_WORD = 1.0
LIBRARY_GAP_BEATS = 4.0
#: A tag — a slide whose words all sit inside at least this many other
#: slides, as a line that closes several verses does — gets no segment of its
#: own; it stays covered, shown by the slides that contain it. As a segment it
#: took the follower off the verse at every verse's end: Nothing But The
#: Blood's tag slide (inside 3 verses, never shown by the operator) was 37 s
#: of the library follower's 154 s of mistakes neither human nor lag on the 8
#: live traces. Not a slide inside just one other: Build My Life's "worthy of
#: every breath ... we live for you" is sung in its own place, and slide 4
#: repeats it with one more "we live for you" (dropping it: 0.148 -> 0.218).
#: The lead-sheet path drops any contained slide (`reference.
#: contained_slides`), but a sheet that prints the line in its own place
#: binds it there anyway. None: every slide a segment.
LIBRARY_TAG_MIN_HOLDERS: int | None = 2


def tag_slides(slides: list[list[str]], min_holders: int) -> set[int]:
    """Slides whose words all appear, in order, inside `min_holders` or more
    other (longer) slides."""
    norm = [" " + " ".join(w for w in (sc.norm(t) for t in sw) if w) + " " for sw in slides]
    out = set()
    for a, wa in enumerate(norm):
        if not wa.strip():
            continue
        holders = sum(1 for b, wb in enumerate(norm) if b != a and len(wb) > len(wa) and wa in wb)
        if holders >= min_holders:
            out.add(a)
    return out


def library_spec(slides: list[list[str]]) -> ReferenceSpec:
    """The song as the library has it: every sung slide a segment, in library
    order, a word a beat, no melody or chords."""
    inside = tag_slides(slides, LIBRARY_TAG_MIN_HOLDERS) if LIBRARY_TAG_MIN_HOLDERS else set()
    segments: list[SegmentSpec] = []
    for si, sw in enumerate(slides):
        mine = [w for w in (sc.norm(x) for x in sw) if w]
        if not mine or si in inside:
            continue
        cells = round((len(mine) * LIBRARY_BEATS_PER_WORD + LIBRARY_GAP_BEATS) / CELL)
        words = [(w, int(j * LIBRARY_BEATS_PER_WORD / CELL)) for j, w in enumerate(mine)]
        segments.append(
            SegmentSpec(
                slide=si,
                cells=cells,
                words=words,
                midi=np.full(cells, NONE, np.int16),
                chord=np.full(cells, NONE, np.int16),
            )
        )
    covered = sorted({g.slide for g in segments} | inside)
    return ReferenceSpec(source="library", segments=segments, covered=covered)
