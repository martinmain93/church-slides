/**
 * React hook wrapping the pure transitions in `state.ts`.
 *
 * All actions are stable references (created once with `useMemo`), so
 * components can wire them straight into key handlers without re-binding
 * on every render.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import type { ParsedSlide } from "@/lib/parseSong";
import type { SongRow } from "@/lib/tauri";

import {
  INITIAL_STATE,
  type BlankMode,
  type PresentationContext,
  type PresentationItem,
  type PresentationState,
  currentItem,
  currentSlide,
  jumpTo as jumpToTransition,
  nextSlide as nextSlideTransition,
  nextSong as nextSongTransition,
  prevSlide as prevSlideTransition,
  prevSong as prevSongTransition,
  startSession,
  toggleBlank as toggleBlankTransition,
  upcomingItems,
  upcomingSlides,
} from "@/lib/presentation/state";

export interface PresentationActions {
  nextSlide: () => void;
  prevSlide: () => void;
  nextSong: () => void;
  prevSong: () => void;
  jumpTo: (itemIdx: number, slideIdx: number) => void;
  toggleBlank: (mode: Exclude<BlankMode, "none">) => void;
  reset: () => void;
}

export interface PresentationDerived {
  currentItem: PresentationItem | undefined;
  currentSlide: ParsedSlide | undefined;
  currentSong: SongRow | undefined;
  upcomingSlides: { slide: ParsedSlide; itemIdx: number; slideIdx: number; song: SongRow }[];
  upcomingItems: PresentationItem[];
  totalSongs: number;
  totalSlides: number;
}

export interface PresentationController {
  state: PresentationState;
  actions: PresentationActions;
  derived: PresentationDerived;
}

interface Options {
  /** Number of upcoming slides/songs to expose (used by the bottom bar). */
  upcomingCount?: number;
  /** Position to start at, e.g. resuming "Continue last service". Defaults to (0,0). */
  initialPosition?: { itemIdx: number; slideIdx: number };
}

/**
 * Caller is expected to remount via React `key` when the setlist changes —
 * the controller does not include reset-on-prop-change logic. The
 * `AppShell` does this with `key={setlist.id}`.
 */
/**
 * Structural fingerprint of a context — which songs, in which order, with how
 * many slides each. Two contexts with the same signature are interchangeable
 * for anchoring purposes however many times they've been rebuilt.
 */
function contextSignature(ctx: PresentationContext): string {
  return ctx.items.map((it) => `${it.itemId}:${it.slides.length}`).join("|");
}

export function usePresentationController(
  ctx: PresentationContext | null,
  opts: Options = {},
): PresentationController {
  const upcomingCount = opts.upcomingCount ?? 2;
  const initialPosition = opts.initialPosition;

  const [state, setState] = useState<PresentationState>(() => {
    if (!ctx) return INITIAL_STATE;
    const base = startSession(ctx);
    if (initialPosition) {
      return jumpToTransition(
        ctx,
        base,
        initialPosition.itemIdx,
        initialPosition.slideIdx,
      );
    }
    return base;
  });

  // Reconcile position when the context is rebuilt mid-service (operator
  // edits the running setlist — add/remove/reorder/edit). We anchor on the
  // current item's *id*, not its index, since indices shift when songs are
  // inserted or removed. `PresentationView` memoizes its `ctx` (stable
  // reference) so this never fires there; only `OperatorView`, which rebuilds
  // `ctx` after a mutation, exercises it.
  const committedRef = useRef<{
    signature?: string;
    ctx: PresentationContext | null;
    itemId: string | undefined;
    slideIdx: number;
  } | null>(null);

  useEffect(() => {
    if (!ctx) return;
    const prev = committedRef.current;
    // Re-anchor only when the setlist's *structure* actually changed, not
    // merely when the caller handed us a new object. Keying on identity makes
    // correctness depend on every caller remembering to memoize: one that
    // doesn't gets its position restored on every render, so slides never
    // advance — by hand or by AI — and nothing anywhere reports an error.
    if (
      prev &&
      prev.signature !== undefined &&
      prev.signature !== contextSignature(ctx) &&
      prev.itemId !== undefined
    ) {
      const newIdx = ctx.items.findIndex((it) => it.itemId === prev.itemId);
      setState((s) => {
        const targetIdx =
          newIdx >= 0
            ? newIdx
            : clampIdx(s.itemIdx, ctx.items.length);
        const item = ctx.items[targetIdx];
        if (!item) return s;
        return {
          ...s, // preserves blankMode
          itemIdx: targetIdx,
          slideIdx: clampIdx(prev.slideIdx, item.slides.length),
        };
      });
      // Hold the anchor; the post-update render records the settled position.
      committedRef.current = {
        ctx,
        signature: contextSignature(ctx),
        itemId: prev.itemId,
        slideIdx: prev.slideIdx,
      };
      return;
    }
    // Normal commit: remember where we are, keyed to this ctx.
    const item = ctx.items[state.itemIdx];
    committedRef.current = {
      ctx,
      signature: contextSignature(ctx),
      itemId: item?.itemId,
      slideIdx: state.slideIdx,
    };
  }, [ctx, state.itemIdx, state.slideIdx]);

  const actions = useMemo<PresentationActions>(() => {
    return {
      nextSlide: () => {
        if (!ctx) return;
        setState((s) => nextSlideTransition(ctx, s));
      },
      prevSlide: () => {
        if (!ctx) return;
        setState((s) => prevSlideTransition(ctx, s));
      },
      nextSong: () => {
        if (!ctx) return;
        setState((s) => nextSongTransition(ctx, s));
      },
      prevSong: () => {
        if (!ctx) return;
        setState((s) => prevSongTransition(ctx, s));
      },
      jumpTo: (itemIdx, slideIdx) => {
        if (!ctx) return;
        setState((s) => jumpToTransition(ctx, s, itemIdx, slideIdx));
      },
      toggleBlank: (mode) => {
        setState((s) => toggleBlankTransition(s, mode));
      },
      reset: () => {
        setState(ctx ? startSession(ctx) : INITIAL_STATE);
      },
    };
  }, [ctx]);

  const derived = useDerived(ctx, state, upcomingCount);

  return { state, actions, derived };
}

function useDerived(
  ctx: PresentationContext | null,
  state: PresentationState,
  upcomingCount: number,
): PresentationDerived {
  return useMemo<PresentationDerived>(() => {
    if (!ctx) {
      return {
        currentItem: undefined,
        currentSlide: undefined,
        currentSong: undefined,
        upcomingSlides: [],
        upcomingItems: [],
        totalSongs: 0,
        totalSlides: 0,
      };
    }
    const item = currentItem(ctx, state);
    const slide = currentSlide(ctx, state);
    return {
      currentItem: item,
      currentSlide: slide,
      currentSong: item?.song,
      upcomingSlides: upcomingSlides(ctx, state, upcomingCount),
      upcomingItems: upcomingItems(ctx, state, upcomingCount),
      totalSongs: ctx.items.length,
      totalSlides: ctx.items.reduce((acc, it) => acc + it.slides.length, 0),
    };
  }, [ctx, state, upcomingCount]);
}

/** Clamp `idx` into `[0, length - 1]` (0 when the list is empty). */
function clampIdx(idx: number, length: number): number {
  return Math.max(0, Math.min(idx, Math.max(0, length - 1)));
}

// Re-export so consumers don't need two import lines.
export type { PresentationContext, PresentationItem, PresentationState };
