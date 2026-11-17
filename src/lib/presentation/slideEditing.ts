/**
 * Pure helpers for editing a setlist item's slides as a `slides_override`.
 *
 * The operator view and the setlist builder both let an operator reshape a
 * song's slides *for one setlist only*. The storage format is the same
 * `slides_override` JSON the builder already uses (see `slideSequence.ts`);
 * these helpers centralise deriving the editable list and applying the
 * duplicate / delete / add / reorder / edit operations so both surfaces stay
 * in lockstep.
 *
 * Note: the editable list contains the **real, storable** slides only — it
 * never includes the synthetic leading blank that presentation contexts
 * inject (that's a render-time concern, not something we persist).
 */

import type { ParsedSection } from "@/lib/parseSong";
import {
  parseSlidesOverride,
  resolveSlideSequence,
  serializeSlidesOverride,
  type OverrideSlide,
} from "@/lib/slideSequence";
import { setSetlistItemSlides, type SetlistItemWithSong } from "@/lib/tauri";

/** The current editable slide list for an item: its override, or the library
 *  song's resolved slides (without the leading blank). */
export function editableSlides(item: SetlistItemWithSong): OverrideSlide[] {
  const override = parseSlidesOverride(item.slides_override);
  if (override) return override;
  if (!item.song) return [];
  return resolveSlideSequence(item.song.body_text, item.song.slide_order).map(
    (s) => ({ section: s.section, lyrics: s.lyrics }),
  );
}

export function editLyrics(
  slides: OverrideSlide[],
  idx: number,
  lyrics: string,
): OverrideSlide[] {
  return slides.map((s, i) => (i === idx ? { ...s, lyrics } : s));
}

export function duplicateAt(slides: OverrideSlide[], idx: number): OverrideSlide[] {
  const target = slides[idx];
  if (!target) return slides;
  return [...slides.slice(0, idx + 1), { ...target }, ...slides.slice(idx + 1)];
}

/** A blank slide in the same section straight after `idx`. */
export function insertAfter(slides: OverrideSlide[], idx: number): OverrideSlide[] {
  const target = slides[idx];
  if (!target) return slides;
  return [...slides.slice(0, idx + 1), { section: { ...target.section }, lyrics: "" }, ...slides.slice(idx + 1)];
}

export function deleteAt(slides: OverrideSlide[], idx: number): OverrideSlide[] {
  return slides.filter((_, i) => i !== idx);
}

/** Append a blank slide that continues the last slide's section. */
export function addSlide(slides: OverrideSlide[]): OverrideSlide[] {
  const last = slides[slides.length - 1];
  const section: ParsedSection = last ? last.section : { type: "other", number: 1 };
  return [...slides, { section, lyrics: "" }];
}

export function moveSlide(
  slides: OverrideSlide[],
  fromIdx: number,
  toIdx: number,
): OverrideSlide[] {
  if (fromIdx === toIdx) return slides;
  const next = [...slides];
  const [moved] = next.splice(fromIdx, 1);
  if (!moved) return slides;
  next.splice(toIdx, 0, moved);
  return next;
}

/** Persist the edited list as the item's `slides_override`. */
export async function persistOverride(
  item: SetlistItemWithSong,
  slides: OverrideSlide[],
): Promise<void> {
  await setSetlistItemSlides(
    item.setlist_id,
    item.id,
    serializeSlidesOverride(slides),
  );
}
