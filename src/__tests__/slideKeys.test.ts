import { describe, expect, it } from "vitest";

import { isTypingTarget, slideAfterArrow } from "@/lib/slideKeys";

const songs = [
  { id: "a", slideCount: 3 },
  { id: "b", slideCount: 2 },
];

describe("arrow keys over the setlist's slides", () => {
  it("steps through a song and crosses into the next and previous", () => {
    expect(slideAfterArrow("ArrowRight", songs, { itemId: "a", slideIdx: 1 })).toEqual({ itemId: "a", slideIdx: 2 });
    expect(slideAfterArrow("ArrowRight", songs, { itemId: "a", slideIdx: 2 })).toEqual({ itemId: "b", slideIdx: 0 });
    expect(slideAfterArrow("ArrowLeft", songs, { itemId: "b", slideIdx: 0 })).toEqual({ itemId: "a", slideIdx: 2 });
    expect(slideAfterArrow("ArrowRight", songs, { itemId: "b", slideIdx: 1 })).toBeNull();
  });

  it("jumps by song with up and down", () => {
    expect(slideAfterArrow("ArrowDown", songs, { itemId: "a", slideIdx: 2 })).toEqual({ itemId: "b", slideIdx: 0 });
    expect(slideAfterArrow("ArrowUp", songs, { itemId: "b", slideIdx: 1 })).toEqual({ itemId: "b", slideIdx: 0 });
    expect(slideAfterArrow("ArrowUp", songs, { itemId: "b", slideIdx: 0 })).toEqual({ itemId: "a", slideIdx: 0 });
    expect(slideAfterArrow("Enter", songs, { itemId: "a", slideIdx: 0 })).toBeNull();
  });

  it("leaves keys alone while typing in a field", () => {
    expect(isTypingTarget(document.createElement("input"))).toBe(true);
    expect(isTypingTarget(document.createElement("textarea"))).toBe(true);
    expect(isTypingTarget(document.createElement("div"))).toBe(false);
    expect(isTypingTarget(null)).toBe(false);
  });
});
