/**
 * Pure presentation state machine.
 *
 * Transitions are pure functions: `(ctx, state) -> state`. The React hook
 * in `hook.ts` wraps them with `useState`, but the logic itself is fully
 * testable without a renderer.
 *
 * Phase 5's AI engine will dispatch these same actions through a WS event,
 * so the contract here is what the eventual matcher relies on.
 */

import { type ParsedSlide } from "@/lib/parseSong";
import { itemKind, type ItemKind } from "@/lib/serviceItemContent";
import { parseSlidesOverride, resolveSlideSequence } from "@/lib/slideSequence";
import type { SetlistDetail, SongRow } from "@/lib/tauri";

export type BlankMode = "none" | "black" | "white";

export interface PresentationState {
  setlistId: string | null;
  itemIdx: number;
  slideIdx: number;
  blankMode: BlankMode;
}

export interface PresentationItem {
  itemId: string;
  /** A song, a Bible passage, or one-off text. The AI follows songs only;
   * see `serviceItems.ts` for how the others come to have a `song`. */
  kind: ItemKind;
  /** A passage's or text item's stored content (`serviceItems.ts`). */
  content?: string | null;
  /** This item's own style in the setlist (JSON, `displayLook.ts`), or null. */
  look?: string | null;
  /** The key this service plays the song in, or null for its chart's own key. */
  songKey?: string | null;
  song: SongRow;
  slides: ParsedSlide[];
}

export interface PresentationContext {
  setlistId: string;
  setlistName: string;
  items: PresentationItem[];
}

export const INITIAL_STATE: PresentationState = {
  setlistId: null,
  itemIdx: 0,
  slideIdx: 0,
  blankMode: "none",
};

/** Build a context from the data the Tauri layer returns. */
export function buildContext(detail: SetlistDetail): PresentationContext {
  const items: PresentationItem[] = [];
  for (const it of detail.items) {
    if (!it.song) continue; // skip orphan items
    items.push({
      itemId: it.id,
      kind: itemKind(it),
      content: it.content ?? null,
      look: it.look ?? null,
      songKey: it.song_key ?? null,
      song: it.song,
      slides: resolveSlideSequence(
        it.song.body_text,
        it.song.slide_order,
        parseSlidesOverride(it.slides_override),
        { leadingBlank: true },
      ),
    });
  }
  return {
    setlistId: detail.id,
    setlistName: detail.name,
    items,
  };
}

export function startSession(ctx: PresentationContext): PresentationState {
  return {
    setlistId: ctx.setlistId,
    itemIdx: 0,
    slideIdx: 0,
    blankMode: "none",
  };
}

export function nextSlide(
  ctx: PresentationContext,
  state: PresentationState,
): PresentationState {
  if (ctx.items.length === 0) return state;
  const item = ctx.items[state.itemIdx];
  if (!item) return clampToCtx(ctx, state);
  if (state.slideIdx + 1 < item.slides.length) {
    return { ...state, slideIdx: state.slideIdx + 1 };
  }
  if (state.itemIdx + 1 < ctx.items.length) {
    return { ...state, itemIdx: state.itemIdx + 1, slideIdx: 0 };
  }
  return state;
}

export function prevSlide(
  ctx: PresentationContext,
  state: PresentationState,
): PresentationState {
  if (ctx.items.length === 0) return state;
  if (state.slideIdx > 0) {
    return { ...state, slideIdx: state.slideIdx - 1 };
  }
  if (state.itemIdx > 0) {
    const prev = ctx.items[state.itemIdx - 1]!;
    return {
      ...state,
      itemIdx: state.itemIdx - 1,
      slideIdx: Math.max(0, prev.slides.length - 1),
    };
  }
  return state;
}

export function nextSong(
  ctx: PresentationContext,
  state: PresentationState,
): PresentationState {
  if (state.itemIdx + 1 < ctx.items.length) {
    return { ...state, itemIdx: state.itemIdx + 1, slideIdx: 0 };
  }
  return state;
}

export function prevSong(
  _ctx: PresentationContext,
  state: PresentationState,
): PresentationState {
  if (state.itemIdx > 0) {
    return { ...state, itemIdx: state.itemIdx - 1, slideIdx: 0 };
  }
  // If we're past slide 0 of the current song, prevSong rewinds to its start.
  if (state.slideIdx > 0) {
    return { ...state, slideIdx: 0 };
  }
  return state;
}

export function jumpTo(
  ctx: PresentationContext,
  state: PresentationState,
  itemIdx: number,
  slideIdx: number,
): PresentationState {
  const clampedItem = clamp(itemIdx, 0, Math.max(0, ctx.items.length - 1));
  const item = ctx.items[clampedItem];
  if (!item) return state;
  const clampedSlide = clamp(slideIdx, 0, Math.max(0, item.slides.length - 1));
  return { ...state, itemIdx: clampedItem, slideIdx: clampedSlide };
}

export function toggleBlank(
  state: PresentationState,
  mode: Exclude<BlankMode, "none">,
): PresentationState {
  if (state.blankMode === mode) {
    return { ...state, blankMode: "none" };
  }
  return { ...state, blankMode: mode };
}

// --- Derived selectors -----------------------------------------------------

export function currentItem(
  ctx: PresentationContext,
  state: PresentationState,
): PresentationItem | undefined {
  return ctx.items[state.itemIdx];
}

export function currentSlide(
  ctx: PresentationContext,
  state: PresentationState,
): ParsedSlide | undefined {
  return currentItem(ctx, state)?.slides[state.slideIdx];
}

/** N slides immediately after the current one, walking across song boundaries. */
export function upcomingSlides(
  ctx: PresentationContext,
  state: PresentationState,
  n: number,
): { slide: ParsedSlide; itemIdx: number; slideIdx: number; song: SongRow }[] {
  const out: {
    slide: ParsedSlide;
    itemIdx: number;
    slideIdx: number;
    song: SongRow;
  }[] = [];
  let itemIdx = state.itemIdx;
  let slideIdx = state.slideIdx + 1;
  while (out.length < n && itemIdx < ctx.items.length) {
    const item = ctx.items[itemIdx]!;
    while (out.length < n && slideIdx < item.slides.length) {
      out.push({
        slide: item.slides[slideIdx]!,
        itemIdx,
        slideIdx,
        song: item.song,
      });
      slideIdx += 1;
    }
    itemIdx += 1;
    slideIdx = 0;
  }
  return out;
}

/** N items (songs) starting AFTER the current one. */
export function upcomingItems(
  ctx: PresentationContext,
  state: PresentationState,
  n: number,
): PresentationItem[] {
  return ctx.items.slice(state.itemIdx + 1, state.itemIdx + 1 + n);
}

// --- Internals -------------------------------------------------------------

function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}

function clampToCtx(
  ctx: PresentationContext,
  state: PresentationState,
): PresentationState {
  const itemIdx = clamp(state.itemIdx, 0, Math.max(0, ctx.items.length - 1));
  const item = ctx.items[itemIdx];
  const slideIdx = item
    ? clamp(state.slideIdx, 0, Math.max(0, item.slides.length - 1))
    : 0;
  return { ...state, itemIdx, slideIdx };
}
