/**
 * A passage too long for one screen, asked for live, becomes a reading in the
 * service: a screen at a time, starting at its first verses. Live, "all of
 * John 3" went up as one pane and ran off the screen (2026-10-28).
 */

import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { addServiceItem, notify } = vi.hoisted(() => ({
  addServiceItem: vi.fn(),
  notify: vi.fn(),
}));

vi.mock("@/lib/tauri", () => ({
  addServiceItem,
  getSettings: () => Promise.resolve({}),
  setSetlistItemContent: vi.fn(),
}));
vi.mock("@/lib/sidecarConnection", () => ({
  getSharedConnection: () => Promise.resolve({ notify }),
}));

import {
  longerThanOneScreen,
  readingReferenceOnSlide,
  useLongPassageAsReading,
  useReadingOnScreen,
} from "@/lib/scripture/prepared";
import type { ScripturePassage } from "@/types/generated";

function passage(reference: string, verses: number, chars = 120): ScripturePassage {
  const [book, chapter] = ["John", 3];
  return {
    reference,
    translation: "web",
    abbreviation: "WEB",
    text: "",
    verses: Array.from({ length: verses }, (_, i) => ({
      ref: `${book} ${chapter}:${i + 1}`,
      text: "word ".repeat(chars / 5).trim() + ".",
    })) as ScripturePassage["verses"],
  };
}

const SONG = { itemId: "item_song", kind: "song", content: null, slides: [{ lyrics: "" }, { lyrics: "Amazing grace" }] };
const READING = {
  itemId: "item_reading",
  kind: "scripture",
  content: JSON.stringify({ reference: "John 3:1–28" }),
  slides: [
    { lyrics: "¹Now there was a man…\nJohn 3:1 · WEB" },
    { lyrics: "²He came to Jesus by night…\nJohn 3:2–3 · WEB" },
  ],
};

beforeEach(() => {
  addServiceItem.mockReset().mockResolvedValue("item_reading");
  notify.mockReset();
});

describe("a long passage asked for live", () => {
  it("is long when it needs more than one screen", () => {
    expect(longerThanOneScreen(passage("John 3:16", 1))).toBe(false);
    expect(longerThanOneScreen(passage("John 3:1–28", 28))).toBe(true);
  });

  it("joins the service after the current item and goes to its first verses", async () => {
    const jumpTo = vi.fn();
    let items: (typeof SONG | typeof READING)[] = [SONG];
    const reload = vi.fn(async () => {
      items = [SONG, READING];
    });
    const { result, rerender } = renderHook(() =>
      useLongPassageAsReading({ setlistId: "s1", items, itemIdx: 0, jumpTo, reload }),
    );

    let took = false;
    act(() => {
      took = result.current(passage("John 3:1–28", 28));
    });
    expect(took).toBe(true);
    await waitFor(() => expect(reload).toHaveBeenCalled());
    expect(addServiceItem).toHaveBeenCalledWith(
      "s1",
      "scripture",
      expect.objectContaining({ reference: "John 3:1–28" }),
      { afterItemId: "item_song" },
    );
    rerender();
    expect(jumpTo).toHaveBeenCalledWith(1, 0);
  });

  it("leaves a passage that fits to the live pane", () => {
    const { result } = renderHook(() =>
      useLongPassageAsReading({ setlistId: "s1", items: [SONG], itemIdx: 0, jumpTo: vi.fn(), reload: vi.fn() }),
    );
    expect(result.current(passage("John 3:16", 1))).toBe(false);
    expect(addServiceItem).not.toHaveBeenCalled();
  });
});

describe("a reading on screen", () => {
  it("names the verses its slide shows", () => {
    expect(readingReferenceOnSlide(READING, 1)).toBe("John 3:2–3");
    expect(readingReferenceOnSlide(SONG, 1)).toBeNull();
  });

  it("tells the engine, so 'next verse' goes on through the passage", async () => {
    const { rerender } = renderHook(
      ({ itemIdx, slideIdx }) => useReadingOnScreen([SONG, READING], itemIdx, slideIdx, false),
      { initialProps: { itemIdx: 0, slideIdx: 1 } },
    );
    expect(notify).not.toHaveBeenCalled();
    rerender({ itemIdx: 1, slideIdx: 1 });
    await waitFor(() =>
      expect(notify).toHaveBeenLastCalledWith({ kind: "live_scripture", reference: "John 3:2–3", display: "full" }),
    );
    rerender({ itemIdx: 0, slideIdx: 1 });
    await waitFor(() =>
      expect(notify).toHaveBeenLastCalledWith({ kind: "live_scripture", reference: "", display: "off" }),
    );
  });
});
