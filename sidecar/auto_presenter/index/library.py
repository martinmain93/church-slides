"""Build an `IndexedSetlist` from a WS-supplied `SetlistContent` payload.

This is the only entry point the matcher needs. There's deliberately no
caching across sessions — Phase 5's matcher is single-session, and the
"library mutation event subscription" the original plan called out
isn't necessary when the UI hands us a fresh payload each time.

Phase 6 additions:
  - Per-word Double Metaphone codes precomputed once at index time.
  - `IndexedItem.section_index` mapping section keys → slide indices,
    so the particle filter's transition prior can look up "where do I
    jump to repeat the chorus?" in O(1).

Phase 7 addition:
  - `IndexedItem.confusion_groups`: slides that open with the same words,
    and the words that later tell them apart. 46% of the library's songs
    contain such a group; see `index.confusion`.
"""

from __future__ import annotations

from auto_presenter.index.confusion import find_confusion_groups
from auto_presenter.index.types import IndexedItem, IndexedSetlist, IndexedSlide
from auto_presenter.protocol import SetlistContent


def build_index(content: SetlistContent, *, follow_readings: bool = True) -> IndexedSetlist:
    """Translate the WS payload into the matcher's lookup structures.

    Validation: empty slides (zero words) are kept so item/slide indices
    align with what the operator clicks through in the UI. The matcher
    will just produce zero scores on them — that's the right behavior
    for instrumental/section-divider slides.

    `follow_readings` off (the kill switch, `ParticleFilterTunables`) keeps a
    passage read aloud (`spoken`) in place, slide for slide, with no words:
    nothing for the AI to follow or land on.
    """
    items: list[IndexedItem] = []
    for raw_item in content.items:
        silent = raw_item.spoken and not follow_readings
        sections = list(raw_item.slide_sections)
        # Backwards compat: pre-Phase-6 payloads omit slide_sections.
        # Fill with "" so the indexed slide doesn't carry stale data.
        while len(sections) < len(raw_item.slides):
            sections.append("")

        slides_list: list[IndexedSlide] = []
        section_index: dict[str, list[int]] = {}
        for slide_idx, words in enumerate(raw_item.slides):
            section = sections[slide_idx]
            slides_list.append(
                IndexedSlide(
                    item_idx=raw_item.item_idx,
                    slide_idx=slide_idx,
                    words=() if silent else tuple(words),
                    section_key=section,
                )
            )
            if section:
                section_index.setdefault(section, []).append(slide_idx)

        groups = find_confusion_groups(
            [(s.slide_idx, s.words) for s in slides_list], raw_item.item_idx
        )
        by_slide = {
            slide_idx: g for g in groups for slide_idx in g.slide_idxs
        }
        by_text: dict[tuple[str, ...], list[int]] = {}
        for slide in slides_list:
            if slide.words:
                by_text.setdefault(slide.words, []).append(slide.slide_idx)
        identical = {
            idx: tuple(members)
            for members in by_text.values()
            if len(members) > 1
            for idx in members
        }
        items.append(
            IndexedItem(
                item_idx=raw_item.item_idx,
                song_id=raw_item.song_id,
                slides=tuple(slides_list),
                title=raw_item.title,
                spoken=raw_item.spoken,
                section_index={k: tuple(v) for k, v in section_index.items()},
                confusion_groups=tuple(groups),
                confusion_by_slide=by_slide,
                identical_by_slide=identical,
            )
        )
    return IndexedSetlist(items=tuple(items))
