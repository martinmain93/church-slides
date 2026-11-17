/**
 * Passages prepared in a service (`docs/done/SERVICE_ITEMS.md`): filling in their
 * text, and finding one when someone asks for it live.
 */

import { useEffect, useRef } from "react";

import { loadDisplayLook } from "@/lib/displayLook";
import { resolvePassages } from "@/lib/scripture/client";
import { getSharedConnection } from "@/lib/sidecarConnection";
import {
  charsPerSlide,
  needsVerses,
  scriptureContent,
  scriptureFromPassage,
  scriptureSlides,
  type ScriptureContent,
} from "@/lib/serviceItems";
import { withScriptureDefaults } from "@/lib/scripture/settings";
import { addServiceItem, getSettings, setSetlistItemContent, type SetlistDetail } from "@/lib/tauri";
import type { ScripturePassage } from "@/types/generated";

/**
 * Look up the text of any passage that has only its reference — one an
 * assistant added over MCP, which has no Bible of its own. Returns whether
 * anything was filled in (the caller then reloads the setlist). A reference
 * the installed Bible can't find stays as it is and shows as such.
 */
export async function fillMissingVerses(detail: SetlistDetail): Promise<boolean> {
  const pending = detail.items.filter(needsVerses);
  if (pending.length === 0) return false;
  // Read here rather than passed in: a caller's settings hook may not have
  // loaded yet, and the passage would be filled in the wrong translation.
  const defaultTranslation = withScriptureDefaults((await getSettings()).scripture).translation;
  let filled = false;
  for (const item of pending) {
    const content = scriptureContent(item)!;
    const translation = content.translation || defaultTranslation;
    const resolved = await resolvePassages([content.reference], translation);
    const passage = resolved.byReference.get(content.reference);
    if (!passage) continue;
    await setSetlistItemContent(detail.id, item.id, scriptureFromPassage(passage));
    filled = true;
  }
  return filled;
}

interface ParsedRef {
  book: string;
  chapter: number;
  first: number;
  last: number;
}

/** "John 3:16–18" -> John, 3, 16, 18. Only the canonical form the Bible
 * hands back, which is all that ever reaches here. */
export function parseCanonical(reference: string): ParsedRef | null {
  const m = /^(.+?)\s+(\d+):(\d+)(?:\s*[–-]\s*(\d+))?\s*$/.exec(reference.trim());
  if (!m) return null;
  const first = Number(m[3]);
  return { book: m[1]!.toLowerCase(), chapter: Number(m[2]), first, last: m[4] ? Number(m[4]) : first };
}

/** The reference a slide shows on its last line ("John 3:16–17 · WEB"). */
function slideReference(lyrics: string): ParsedRef | null {
  const lines = lyrics.trim().split("\n");
  for (let i = lines.length - 1; i >= 0; i--) {
    const ref = parseCanonical(lines[i]!.split(" · ")[0]!);
    if (ref) return ref;
  }
  return null;
}

/**
 * Where a passage asked for live is already prepared in the service: the
 * item, and the slide showing its first verse. A passage put up by typing
 * or by voice then goes to the slides the team prepared (their translation,
 * their edits) instead of a second copy on top of them.
 *
 * The passage on screen (`showing`) is looked in first: "back to verse 16"
 * during a reading of John 3 means verse 16 of that reading, not a separate
 * John 3:16 earlier in the service.
 */
export function preparedSlideFor(
  items: { kind: string; content?: string | null; slides: { lyrics: string }[] }[],
  reference: string,
  showing?: number,
): { itemIdx: number; slideIdx: number } | null {
  const want = parseCanonical(reference);
  if (!want) return null;
  const order = items.map((_, i) => i);
  if (showing !== undefined && showing >= 0 && showing < items.length) {
    order.splice(showing, 1);
    order.unshift(showing);
  }
  for (const itemIdx of order) {
    const item = items[itemIdx]!;
    const content: ScriptureContent | null = scriptureContent({ kind: item.kind as "scripture", content: item.content ?? null });
    const have = content ? parseCanonical(content.reference) : null;
    if (!have || have.book !== want.book || have.chapter !== want.chapter) continue;
    if (want.first < have.first || want.first > have.last) continue;
    const slideIdx = item.slides.findIndex((s) => {
      const r = slideReference(s.lyrics);
      return r !== null && r.first <= want.first && want.first <= r.last;
    });
    // Edited past recognising: the item's first slide with words.
    const fallback = item.slides.findIndex((s) => s.lyrics.trim() !== "");
    return { itemIdx, slideIdx: slideIdx >= 0 ? slideIdx : Math.max(0, fallback) };
  }
  return null;
}

/** More text than one screen holds at the display's text size. */
export function longerThanOneScreen(passage: ScripturePassage): boolean {
  const budget = charsPerSlide(loadDisplayLook().size);
  return scriptureSlides(scriptureFromPassage(passage), budget).length > 1;
}

type ItemsWithIds = { itemId: string; kind: string; content?: string | null; slides: { lyrics: string }[] }[];

/**
 * A passage too long for one screen ("all of John 3"), asked for live: added
 * to the service as a reading right after the current item, and gone to. It
 * then shows a screen at a time, its first verses first, and moves on as it
 * is read (or with the arrow keys) — like a passage prepared in advance. Put
 * up as one live pane, a whole chapter ran off the screen (2026-10-28).
 *
 * Returns the handler for `usePreparedScripture`'s fallthrough: true when it
 * took the passage, false to show it live as before.
 */
export function useLongPassageAsReading(opts: {
  setlistId: string;
  items: ItemsWithIds;
  itemIdx: number;
  jumpTo: (itemIdx: number, slideIdx: number) => void;
  /** Reload the setlist and re-sync the AI, as adding a song mid-service does. */
  reload: () => Promise<void>;
}): (passage: ScripturePassage) => boolean {
  const latest = useRef(opts);
  latest.current = opts;
  const pending = useRef<string | null>(null);

  // Go to it once the reloaded setlist has it.
  useEffect(() => {
    if (!pending.current) return;
    const at = preparedSlideFor(opts.items, pending.current);
    if (!at) return;
    pending.current = null;
    latest.current.jumpTo(at.itemIdx, at.slideIdx);
  }, [opts.items]);

  return (passage) => {
    if (!longerThanOneScreen(passage)) return false;
    const { setlistId, items, itemIdx, reload } = latest.current;
    pending.current = passage.reference;
    void (async () => {
      try {
        await addServiceItem(setlistId, "scripture", scriptureFromPassage(passage), {
          afterItemId: items[itemIdx]?.itemId ?? null,
        });
        await reload();
      } catch (err) {
        pending.current = null;
        console.error("adding the passage to the service failed:", err);
      }
    })();
    return true;
  };
}

/** The verses a reading's slide shows ("John 3:4–6"), from its last line. */
export function readingReferenceOnSlide(
  item: { kind: string; slides: { lyrics: string }[] } | undefined,
  slideIdx: number,
): string | null {
  if (item?.kind !== "scripture") return null;
  const lines = (item.slides[slideIdx]?.lyrics ?? "").trim().split("\n");
  for (let i = lines.length - 1; i >= 0; i--) {
    const ref = lines[i]!.split(" · ")[0]!.trim();
    if (parseCanonical(ref)) return ref;
  }
  return null;
}

/**
 * Tell the engine which verses a reading in the service has on screen, the
 * way a live passage does (`liveScripture.ts`), so "next verse" goes on
 * through the passage. Unaware of it, the model moved the *song* underneath
 * to its next verse (2026-10-28).
 */
export function useReadingOnScreen(
  items: { kind: string; slides: { lyrics: string }[] }[],
  itemIdx: number,
  slideIdx: number,
  livePassageUp: boolean,
): void {
  const reference = readingReferenceOnSlide(items[itemIdx], slideIdx);
  const told = useRef(false);
  useEffect(() => {
    // A live passage over the reading speaks for itself.
    if (livePassageUp) return;
    if (!reference && !told.current) return;
    told.current = reference !== null;
    void getSharedConnection()
      .then((conn) =>
        conn.notify({ kind: "live_scripture", reference: reference ?? "", display: reference ? "full" : "off" }),
      )
      .catch(() => {
        // No engine: no spoken requests to ground.
      });
  }, [reference, livePassageUp]);
}
