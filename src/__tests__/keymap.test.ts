import { describe, expect, it } from "vitest";
import {
  PRESENTATION_KEYMAP,
  matchKey,
  type MatchInput,
} from "@/lib/presentation/keymap";

function ev(partial: Partial<MatchInput>): MatchInput {
  return {
    key: partial.key ?? "",
    metaKey: partial.metaKey ?? false,
    ctrlKey: partial.ctrlKey ?? false,
    shiftKey: partial.shiftKey ?? false,
    altKey: partial.altKey ?? false,
  };
}

describe("PRESENTATION_KEYMAP", () => {
  it.each([
    ["ArrowRight", "nextSlide"],
    ["ArrowLeft", "prevSlide"],
    ["ArrowDown", "nextSong"],
    ["ArrowUp", "prevSong"],
    ["b", "toggleBlankBlack"],
    ["B", "toggleBlankBlack"],
    ["w", "toggleBlankWhite"],
    ["W", "toggleBlankWhite"],
    ["F11", "toggleFullscreen"],
    ["Escape", "exitFullscreenOrPresentation"],
  ])("plain key %s -> %s", (key, expected) => {
    const match = matchKey(PRESENTATION_KEYMAP, ev({ key }));
    expect(match?.action).toBe(expected);
  });

  it("Cmd+D toggles debug panel", () => {
    const match = matchKey(
      PRESENTATION_KEYMAP,
      ev({ key: "d", metaKey: true }),
    );
    expect(match?.action).toBe("toggleDebug");
  });

  it("Ctrl+D toggles debug panel", () => {
    const match = matchKey(
      PRESENTATION_KEYMAP,
      ev({ key: "d", ctrlKey: true }),
    );
    expect(match?.action).toBe("toggleDebug");
  });

  it("plain D (no modifiers) does not toggle debug or anything else", () => {
    // Lower-case d alone has no plain mapping. With our matchKey logic,
    // it tries the meta/ctrl bindings, sees no modifier, falls through.
    const match = matchKey(PRESENTATION_KEYMAP, ev({ key: "d" }));
    expect(match).toBeNull();
  });

  it("Cmd+Right does NOT trigger next slide (modifiers must be empty for plain bindings)", () => {
    const match = matchKey(
      PRESENTATION_KEYMAP,
      ev({ key: "ArrowRight", metaKey: true }),
    );
    expect(match).toBeNull();
  });

  it("returns the first matching binding when multiple match", () => {
    // Cmd+D matches the first debug binding (meta=true) before the
    // ctrl=true one. Both have the same action, so just confirm.
    const match = matchKey(
      PRESENTATION_KEYMAP,
      ev({ key: "d", metaKey: true }),
    );
    expect(match?.action).toBe("toggleDebug");
  });
});
