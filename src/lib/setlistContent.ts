/**
 * Build the `SetlistContent` payload the sidecar matcher consumes.
 *
 * The TS parser (`src/lib/parseSong.ts`) produces tokenized
 * `Slide.words` that are byte-identical to Python's
 * `library.parser.tokenize` — parity is enforced by
 * `tests/test_parser_parity.py`. So the words we ship here are exactly
 * what the matcher expects to score against.
 */

import { isSongItem, itemKind, spokenWords } from "@/lib/serviceItems";
import { parseSlidesOverride, resolveSlideSequence } from "@/lib/slideSequence";
import type { SetlistDetail } from "@/lib/tauri";
import type { SetlistContent, SetlistItemContent } from "@/types/generated";

export function buildSetlistContent(
  setlist: SetlistDetail,
  { readAlong = true }: { readAlong?: boolean } = {},
): SetlistContent {
  const items = setlist.items.map((item, idx): SetlistItemContent => {
    // A Bible passage: its words as they are read aloud, marked `spoken` so
    // the engine times it as speech and never jumps into it on its own
    // (docs/done/SERVICE_ITEMS.md phase 2). The engine's `follow_readings` kill
    // switch silences it there, keeping the slides in place.
    if (readAlong && item.song && itemKind(item) === "scripture") {
      const slides = resolveSlideSequence(
        item.song.body_text,
        null,
        parseSlidesOverride(item.slides_override),
        { leadingBlank: true },
      );
      return {
        item_idx: idx,
        song_id: item.id,
        title: item.song.title,
        spoken: true,
        slides: slides.map((s, i) => (i === 0 ? s.words : spokenWords(s.lyrics))),
        slide_sections: slides.map((s) => `${s.section.type}:${s.section.number}`),
      };
    }
    // The engine finds items by position, so every item takes its place
    // and `item_idx` is the index the operator sees. One that can't be
    // matched — a text item, or a slot whose
    // song is missing — goes as one wordless slide: the tracker only ever
    // moves to a slide with words, so the AI can't land there. While one is
    // on screen the AI waits on the next song instead (`serviceItems.aiAnchor`).
    if (!item.song || !isSongItem(item)) {
      return { item_idx: idx, song_id: item.id, slides: [[]], slide_sections: ["other:0"] };
    }
    // Resolve through the same expander the live presentation uses so the
    // matcher's slide indices line up with what the operator sees —
    // including any repeated sections from the song's default slide order.
    const slides = resolveSlideSequence(
      item.song.body_text,
      item.song.slide_order,
      parseSlidesOverride(item.slides_override),
      { leadingBlank: true },
    );
    return {
      item_idx: idx,
      song_id: item.song.id,
      title: item.song.title,
      slides: slides.map((s) => s.words),
      // Phase 6 transition prior input: a parallel array of
      // `"<section>:<number>"` keys, one per slide.
      slide_sections: slides.map(
        (s) => `${s.section.type}:${s.section.number}`,
      ),
    };
  });
  return { items };
}
