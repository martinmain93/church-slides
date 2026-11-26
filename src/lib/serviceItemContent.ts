/**
 * What a scripture or text item in a service holds, and the slides a text
 * item starts with (`docs/done/SERVICE_ITEMS.md`). Shared by the desktop
 * (`serviceItems.ts`, which also presents items) and the web app, so an item
 * made on either arrives the same on the other. Nothing here touches the
 * platform.
 */

import { resolveSlideSequence, serializeSlidesOverride, type OverrideSlide } from "@/lib/slideSequence";
import type { SetlistItemRow, SongRow } from "@/core/library";
import type { ScriptureVerse } from "@/types/generated";

export type ItemKind = "song" | "scripture" | "text";

export interface ScriptureContent {
  /** Canonical, as the Bible returned it ("John 3:16–17"). */
  reference: string;
  /** Translation id ("web"); missing = the service's default when filled. */
  translation?: string;
  /** Short name shown with the reference ("WEB"). */
  abbreviation?: string;
  /** The verses, copied when the item was added. Missing until filled — an
   * item added by an assistant over MCP has only its reference. */
  verses?: ScriptureVerse[];
  /** Text pasted by the church in place of `verses` (a licensed translation). */
  own_text?: string;
  /** The copyright line that goes with `own_text`. */
  credit?: string;
}

export interface TextContent {
  title: string;
}

const TEXT_SECTION = { type: "other" as const, number: 1 };

export function itemKind(item: Pick<SetlistItemRow, "kind">): ItemKind {
  return item.kind === "scripture" || item.kind === "text" ? item.kind : "song";
}

export function isSongItem(item: Pick<SetlistItemRow, "kind">): boolean {
  return itemKind(item) === "song";
}

function parseJson<T>(raw: string | null | undefined): T | null {
  if (!raw) return null;
  try {
    const v = JSON.parse(raw) as unknown;
    return v && typeof v === "object" ? (v as T) : null;
  } catch {
    return null;
  }
}

export function scriptureContent(item: Pick<SetlistItemRow, "kind" | "content">): ScriptureContent | null {
  if (itemKind(item) !== "scripture") return null;
  const c = parseJson<ScriptureContent>(item.content);
  return c && typeof c.reference === "string" ? c : null;
}

export function textContent(item: Pick<SetlistItemRow, "kind" | "content">): TextContent | null {
  if (itemKind(item) !== "text") return null;
  const c = parseJson<TextContent>(item.content);
  return { title: typeof c?.title === "string" && c.title.trim() ? c.title : "Text" };
}

/** What the running order calls an item. `song` is the item's library
 * song, when it has one. */
export function itemTitle(item: Pick<SetlistItemRow, "kind" | "content">, song: Pick<SongRow, "title"> | null | undefined): string {
  switch (itemKind(item)) {
    case "scripture":
      return scriptureContent(item)?.reference ?? "Scripture";
    case "text":
      return textContent(item)!.title;
    default:
      return song?.title ?? "Missing song";
  }
}

/** A new text item's slides: what was typed, as its first slide. */
export function textSlides(lines: string[]): string {
  const slides: OverrideSlide[] = lines.map((lyrics) => ({ section: TEXT_SECTION, lyrics }));
  return serializeSlidesOverride(slides.length > 0 ? slides : [{ section: TEXT_SECTION, lyrics: "" }]);
}

/** A saved reading's body as a text item's slides. */
export function readingSlides(body: string): string {
  return textSlides(resolveSlideSequence(body, null).map((s) => s.lyrics));
}

/** A text item's slides as a reading's body: the song format without
 * section tags, one blank line between slides. */
export function readingBody(slides: string[]): string {
  return bodyFromSlides(slides.map((s) => s.trim()).filter(Boolean));
}

/** Body text in the song format: one slide per block. A line that would read
 * as a section tag ("[...]") gets a zero-width space in front — the parser
 * trims whitespace but not that — so it stays text. */
export function bodyFromSlides(slides: string[]): string {
  return slides
    .map((s) =>
      s
        .split("\n")
        .map((line) => (/^\s*\[[^\]]+\]\s*$/.test(line) ? `\u200b${line}` : line))
        .join("\n"),
    )
    .join("\n\n");
}
