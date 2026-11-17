import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { SlideStage } from "@/components/presentation/SlideStage";
import {
  DEFAULT_LOOK,
  DisplayLookContext,
  loadDisplayLook,
  normalizeLook,
  faceStyle,
  resolveFont,
  saveDisplayLook,
  scaledClamp,
} from "@/lib/displayLook";
import type { ParsedSlide } from "@/lib/parseSong";
import { DisplayLookDialog } from "@/components/presentation/DisplayLookDialog";

const slide: ParsedSlide = {
  id: "s",
  section: { type: "verse", number: 1 },
  order_in_song: 0,
  order_in_section: 0,
  lyrics: "Line one\nLine two",
  words: ["Line", "one", "Line", "two"],
};

describe("displayLook", () => {
  beforeEach(() => localStorage.clear());

  it("defaults to white on black in the app's sans", () => {
    expect(loadDisplayLook()).toEqual(DEFAULT_LOOK);
    expect(DEFAULT_LOOK.theme).toBe("dark");
    expect(resolveFont(DEFAULT_LOOK)).toBe("sans");
  });

  it("persists across loads and survives junk", () => {
    const look = { theme: "overhead", font: "georgia", size: "l", weight: "regular" } as const;
    saveDisplayLook(look);
    expect(loadDisplayLook()).toEqual(look);
    localStorage.setItem("auto-presenter.display-look", "{not json");
    expect(loadDisplayLook()).toEqual(DEFAULT_LOOK);
    expect(normalizeLook({ theme: "neon", size: "xl" })).toEqual({ ...DEFAULT_LOOK, size: "xl" });
    expect(normalizeLook(undefined)).toEqual(DEFAULT_LOOK);
  });

  it("gives the overhead EB Garamond unless overridden", () => {
    expect(resolveFont({ ...DEFAULT_LOOK, theme: "overhead" })).toBe("eb-garamond");
    expect(resolveFont({ ...DEFAULT_LOOK, theme: "overhead", font: "sans" })).toBe("sans");
  });

  it("keeps the old bold by default", () => {
    expect(faceStyle(DEFAULT_LOOK).fontWeight).toBe(700);
    expect(faceStyle({ ...DEFAULT_LOOK, weight: "regular" }).fontWeight).toBe(400);
  });

  it("thickens single-weight faces with an outline, never a faux bold", () => {
    const bold = faceStyle({ ...DEFAULT_LOOK, font: "radley" });
    expect(bold.fontWeight).toBe(400);
    expect(bold.WebkitTextStrokeWidth).toBe("0.03em");
    expect(faceStyle({ ...DEFAULT_LOOK, font: "radley", weight: "regular" }).WebkitTextStrokeWidth)
      .toBeUndefined();
  });

  it("standard size is exactly the old clamp", () => {
    const cqw = (n: number) => `${n}cqw`;
    expect(scaledClamp(DEFAULT_LOOK, 2, cqw, 5, 5)).toBe("clamp(2rem, 5cqw, 5rem)");
    expect(scaledClamp({ ...DEFAULT_LOOK, size: "xl" }, 2, cqw, 5, 5)).toBe(
      "clamp(2.6rem, 6.5cqw, 6.5rem)",
    );
  });
});

describe("SlideStage themes", () => {
  it("renders white on black without a provider", () => {
    render(<SlideStage slide={slide} blankMode="none" />);
    const surface = screen.getByTestId("stage-surface");
    expect(surface.dataset.theme).toBe("dark");
    expect(surface.style.backgroundColor).toBe("rgb(0, 0, 0)");
    expect(screen.getByText("Line one").parentElement!.style.color).toBe("rgb(255, 255, 255)");
  });

  it("renders black on white", () => {
    render(
      <DisplayLookContext.Provider value={{ ...DEFAULT_LOOK, theme: "light" }}>
        <SlideStage slide={slide} blankMode="none" />
      </DisplayLookContext.Provider>,
    );
    expect(screen.getByTestId("stage-surface").style.backgroundColor).toBe("rgb(255, 255, 255)");
    expect(screen.getByText("Line one").parentElement!.style.color).toBe("rgb(13, 13, 13)");
  });

  it("blanking black still blacks out a light theme", () => {
    render(
      <DisplayLookContext.Provider value={{ ...DEFAULT_LOOK, theme: "light" }}>
        <SlideStage slide={slide} blankMode="black" />
      </DisplayLookContext.Provider>,
    );
    expect(screen.getByTestId("stage-surface").style.backgroundColor).toBe("rgb(0, 0, 0)");
    expect(screen.queryByText("Line one")).toBeNull();
  });

  it("overhead renders in EB Garamond on a lit pane", () => {
    render(
      <DisplayLookContext.Provider value={{ ...DEFAULT_LOOK, theme: "overhead" }}>
        <SlideStage slide={slide} blankMode="none" />
      </DisplayLookContext.Provider>,
    );
    expect(screen.getByTestId("stage-surface").dataset.theme).toBe("overhead");
    expect(screen.getByText("Line one").parentElement!.style.fontFamily).toContain("EB Garamond");
  });
});

describe("DisplayLookDialog", () => {
  beforeEach(() => localStorage.clear());

  it("saves a choice as soon as it's made", () => {
    render(<DisplayLookDialog onClose={() => {}} />);
    act(() => screen.getByRole("radio", { name: /Overhead projector/ }).click());
    act(() => screen.getByRole("radio", { name: "Large" }).click());
    act(() => screen.getByRole("radio", { name: "Semibold" }).click());
    expect(loadDisplayLook()).toEqual({ theme: "overhead", font: "auto", size: "l", weight: "semibold" });
    expect(screen.getByRole("radio", { name: /Overhead projector/ }).getAttribute("aria-checked")).toBe("true");
  });
});
