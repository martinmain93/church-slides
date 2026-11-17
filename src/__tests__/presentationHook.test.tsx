/**
 * Tests for the position-reconcile path in `usePresentationController`.
 *
 * When the operator edits a running setlist, the operator view rebuilds the
 * presentation context (a new object reference). The controller must keep the
 * operator on the *same song* — anchored by item id — even though indices
 * shift as songs are inserted/removed.
 */

import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { usePresentationController } from "@/lib/presentation/hook";
import type { PresentationContext, PresentationItem } from "@/lib/presentation/hook";
import type { ParsedSlide } from "@/lib/parseSong";
import type { SongRow } from "@/lib/tauri";

function fakeSlides(n: number): ParsedSlide[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `s${i}`,
    section: { type: "verse", number: 1 },
    order_in_song: i,
    order_in_section: i,
    lyrics: `line ${i}`,
    words: [`line`, `${i}`],
  }));
}

function fakeSong(id: string): SongRow {
  return {
    id,
    title: id,
    body_text: "",
    ccli: null,
    author: null,
    copyright: null,
    default_tempo_wps: null,
    slide_order: null,
    created_at: 0,
    updated_at: 0,
  };
}

function item(itemId: string, songId: string, slideCount: number): PresentationItem {
  return { itemId, kind: "song", song: fakeSong(songId), slides: fakeSlides(slideCount) };
}

function ctxOf(items: PresentationItem[]): PresentationContext {
  return { setlistId: "sl1", setlistName: "S", items };
}

describe("usePresentationController reconcile", () => {
  it("keeps the operator on the same song when a song is inserted before it", () => {
    const A = item("itA", "a", 3);
    const B = item("itB", "b", 4);
    const initial = ctxOf([A, B]);

    const { result, rerender } = renderHook(
      ({ ctx }) => usePresentationController(ctx),
      { initialProps: { ctx: initial } },
    );

    // Move onto song B, slide 2.
    act(() => result.current.actions.jumpTo(1, 2));
    expect(result.current.state.itemIdx).toBe(1);
    expect(result.current.state.slideIdx).toBe(2);

    // Insert a new song at the front → B is now index 2.
    const NEW = item("itNew", "new", 2);
    rerender({ ctx: ctxOf([NEW, A, B]) });

    expect(result.current.state.itemIdx).toBe(2); // still song B (itB)
    expect(result.current.state.slideIdx).toBe(2); // slide preserved
    expect(result.current.derived.currentItem?.itemId).toBe("itB");
  });

  it("clamps the slide index when the current song now has fewer slides", () => {
    const A = item("itA", "a", 5);
    const { result, rerender } = renderHook(
      ({ ctx }) => usePresentationController(ctx),
      { initialProps: { ctx: ctxOf([A]) } },
    );
    act(() => result.current.actions.jumpTo(0, 4));
    expect(result.current.state.slideIdx).toBe(4);

    // Same song id, but slides shrank to 2.
    rerender({ ctx: ctxOf([item("itA", "a", 2)]) });
    expect(result.current.state.itemIdx).toBe(0);
    expect(result.current.state.slideIdx).toBe(1); // clamped to last
  });

  it("falls back to a valid index when the current song is removed", () => {
    const A = item("itA", "a", 3);
    const B = item("itB", "b", 3);
    const { result, rerender } = renderHook(
      ({ ctx }) => usePresentationController(ctx),
      { initialProps: { ctx: ctxOf([A, B]) } },
    );
    act(() => result.current.actions.jumpTo(1, 1));
    // Remove song B (the current one).
    rerender({ ctx: ctxOf([A]) });
    expect(result.current.state.itemIdx).toBe(0);
    expect(result.current.derived.currentItem?.itemId).toBe("itA");
  });

  it("advancing sticks when the caller rebuilds its context every render", () => {
    // The failure this guards: `PresentationView` built its context inline, so
    // every render — including the one caused by the state update itself —
    // produced a new object. The reconcile path read that as "the setlist
    // changed" and re-anchored to the last committed slide, silently reverting
    // every advance, by hand or by AI. Nothing anywhere reported an error.
    //
    // Building the context INSIDE the hook callback is what reproduces it;
    // rerendering with a fresh object does not, because the state-driven
    // render in between still sees the old one and commits normally.
    const { result } = renderHook(() =>
      usePresentationController(ctxOf([item("itA", "a", 5)])),
    );

    act(() => result.current.actions.nextSlide());
    expect(result.current.state.slideIdx).toBe(1);

    act(() => result.current.actions.nextSlide());
    expect(result.current.state.slideIdx).toBe(2);
  });

  it("still re-anchors when the setlist really does change", () => {
    // The counterweight: content-based comparison must not make the hook blind
    // to genuine edits.
    const A = item("itA", "a", 3);
    const B = item("itB", "b", 4);
    const { result, rerender } = renderHook(
      ({ ctx }) => usePresentationController(ctx),
      { initialProps: { ctx: ctxOf([A, B]) } },
    );

    act(() => result.current.actions.jumpTo(1, 2));
    rerender({ ctx: ctxOf([item("itNew", "new", 2), A, B]) });

    expect(result.current.state.itemIdx).toBe(2);
    expect(result.current.derived.currentItem?.itemId).toBe("itB");
  });
});
