import { act, cleanup, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DisplayLookDialog } from "@/components/presentation/DisplayLookDialog";
import {
  DEFAULT_LOOK,
  lookForItem,
  parseLook,
  saveDisplayLook,
  saveScriptureLook,
  useScreenLook,
  useScriptureLook,
  type DisplayLook,
} from "@/lib/displayLook";

afterEach(() => {
  cleanup();
  act(() => {
    saveScriptureLook(null);
    saveDisplayLook(DEFAULT_LOOK);
  });
});

const LIGHT: DisplayLook = { ...DEFAULT_LOOK, theme: "light" };
const SERIF: DisplayLook = { ...DEFAULT_LOOK, font: "georgia" };

describe("which style an item is shown in", () => {
  it("is the item's own, else scripture's for a passage, else the default", () => {
    const own = JSON.stringify(LIGHT);
    expect(lookForItem(DEFAULT_LOOK, SERIF, { kind: "song", look: own })).toEqual(LIGHT);
    expect(lookForItem(DEFAULT_LOOK, SERIF, { kind: "scripture", look: own })).toEqual(LIGHT);
    expect(lookForItem(DEFAULT_LOOK, SERIF, { kind: "scripture", look: null })).toEqual(SERIF);
    expect(lookForItem(DEFAULT_LOOK, SERIF, { kind: "song", look: null })).toEqual(DEFAULT_LOOK);
    expect(lookForItem(DEFAULT_LOOK, null, { kind: "scripture" })).toEqual(DEFAULT_LOOK);
  });

  it("ignores a stored look it can't read", () => {
    expect(parseLook("not json")).toBeNull();
    expect(parseLook(null)).toBeNull();
    expect(parseLook('{"theme":"nope"}')).toEqual(DEFAULT_LOOK);
  });

  it("puts a full-screen passage in scripture's style, whatever song is on", () => {
    act(() => saveScriptureLook(SERIF));
    const song = { kind: "song", look: JSON.stringify(LIGHT) };
    const { result, rerender } = renderHook(({ full }) => useScreenLook(song, full), {
      initialProps: { full: true },
    });
    expect(result.current).toEqual(SERIF);
    rerender({ full: false });
    expect(result.current).toEqual(LIGHT);
  });

  it("drops scripture's style with Use default style", () => {
    const { result } = renderHook(() => useScriptureLook());
    act(() => saveScriptureLook(SERIF));
    expect(result.current).toEqual(SERIF);
    act(() => saveScriptureLook(null));
    expect(result.current).toBeNull();
  });
});

describe("the style modal as an override", () => {
  it("edits the override, not the default, and has no slides-or-scrolling choice", () => {
    const onChange = vi.fn();
    const onUseDefault = vi.fn();
    render(
      <DisplayLookDialog
        onClose={vi.fn()}
        override={{ title: "Scripture style", description: "d", value: DEFAULT_LOOK, onChange, onUseDefault }}
      />,
    );
    expect(screen.getByRole("heading", { name: "Scripture style" })).toBeInTheDocument();
    expect(screen.queryByRole("radiogroup", { name: "Lyrics" })).toBeNull();
    fireEvent.click(screen.getByRole("radio", { name: /Black on white/ }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ theme: "light" }));
    fireEvent.click(screen.getByRole("button", { name: "Use default style" }));
    expect(onUseDefault).toHaveBeenCalledOnce();
  });

  it("can't drop an override that isn't there", () => {
    render(
      <DisplayLookDialog
        onClose={vi.fn()}
        override={{ title: "t", description: "d", value: DEFAULT_LOOK, onChange: vi.fn() }}
      />,
    );
    expect(screen.getByRole("button", { name: "Use default style" })).toBeDisabled();
  });
});
