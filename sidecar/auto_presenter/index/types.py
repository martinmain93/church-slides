"""Indexed setlist types — what the matcher actually scans over.

Built fresh from the `SetlistContent` payload each time an audio session
starts; discarded on stop. No DB access, no caching across sessions —
the matcher is stateless w.r.t. the broader library, and the WS message
is the single source of truth for "which words am I matching against."

Word lists arrive pre-tokenized from the UI (via `parseSong.ts`'s parser,
which mirrors Python's `library.parser.tokenize` byte-for-byte — that
parity is what makes this safe).

Phase 6 adds:
  - `IndexedItem.section_index`: maps a section-name key (e.g. `chorus:1`)
    to the slide indices that belong to that section. The transition
    prior reads this for "repeat the chorus" and "jump to bridge"
    moves.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from auto_presenter.index.confusion import ConfusionGroup


@dataclass(frozen=True)
class IndexedSlide:
    """One slide in the index — tokenized word list plus locator."""

    item_idx: int
    slide_idx: int
    words: tuple[str, ...]
    #: Section label for this slide, e.g. `"chorus:1"`. Empty when the
    #: section is unknown (Phase 5 callers that don't pass section
    #: information leave this empty). Used by transition priors.
    section_key: str = ""

    @property
    def text(self) -> str:
        """Space-joined words — what rapidfuzz scores against."""
        return " ".join(self.words)


@dataclass(frozen=True)
class IndexedItem:
    """One setlist item (a song) and its slides."""

    item_idx: int
    song_id: str
    slides: tuple[IndexedSlide, ...]
    #: The song's title, for spoken instructions (`decision.instructions`) to
    #: describe it to a model. Nothing else here needs a name — matching is
    #: entirely on `words`. Empty on an older protocol payload.
    title: str = ""
    #: A passage read aloud (`protocol.SetlistItemContent.spoken`).
    spoken: bool = False
    #: section_key → list of slide indices in that section. Built once
    #: in `build_index`; consumed by `tracker.transitions`.
    section_index: dict[str, tuple[int, ...]] = field(default_factory=dict)
    #: Slides that open with the same words, and the words that tell them
    #: apart. Precomputed because the ambiguity is a property of the song, not
    #: of any particular performance — see `index.confusion`.
    confusion_groups: tuple[ConfusionGroup, ...] = field(default_factory=tuple)
    #: slide_idx → the group it belongs to. O(1); a slide is in at most one
    #: group because groups are keyed on a fixed-length prefix.
    confusion_by_slide: dict[int, ConfusionGroup] = field(default_factory=dict)
    #: slide_idx → every slide with byte-identical text, itself included.
    #:
    #: `find_confusion_groups` deliberately skips these — showing either copy
    #: displays the right words, so they are not a *confusion*. But they are
    #: still a problem for any threshold read off a per-slide marginal: a
    #: chorus written as four identical slides splits the cloud four ways, and
    #: the top marginal caps near 0.25. Measured on King of My Heart, the
    #: cloud sat at 0.34 while the pooled mass over the identical copies was
    #: 0.79 — under a 0.40 commit threshold and a 0.50 found threshold, so the
    #: tracker was permanently lost and the screen froze for two minutes.
    identical_by_slide: dict[int, tuple[int, ...]] = field(default_factory=dict)

    def slide(self, slide_idx: int) -> IndexedSlide | None:
        if 0 <= slide_idx < len(self.slides):
            return self.slides[slide_idx]
        return None


@dataclass(frozen=True)
class IndexedSetlist:
    """The matcher's view of an entire setlist."""

    items: tuple[IndexedItem, ...]

    def item(self, item_idx: int) -> IndexedItem | None:
        if 0 <= item_idx < len(self.items):
            return self.items[item_idx]
        return None

    @property
    def item_count(self) -> int:
        return len(self.items)
