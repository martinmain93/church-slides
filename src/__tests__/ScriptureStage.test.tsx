/**
 * The composition seam, and the promise it makes: with the feature off, this
 * renders exactly what the app rendered before it existed.
 *
 * That promise is the whole decoupling argument, so it is worth a test rather
 * than a comment. The rest pins the cases where a verse must *not* be on
 * screen — blanked, or no passage — because "scripture stays lit on a blanked
 * screen" is the kind of bug nobody finds until it happens in front of a room.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ScriptureStage } from "@/components/scripture/ScriptureStage";
import { DEFAULT_LOOK, DisplayLookContext } from "@/lib/displayLook";
import type { ParsedSlide } from "@/lib/parseSong";
import {
  DEFAULT_SCRIPTURE_SETTINGS,
  type ScriptureSettings,
} from "@/lib/scripture/settings";
import type { ScripturePassage } from "@/types/generated";

const SLIDE: ParsedSlide = {
  id: "s1",
  section: { type: "chorus", number: 1 },
  order_in_song: 1,
  order_in_section: 1,
  lyrics: "I will set my hope\nOn steady ground",
  words: ["i", "will", "set", "my", "hope", "on", "steady", "ground"],
};

const PASSAGE: ScripturePassage = {
  reference: "Matthew 7:24–25",
  translation: "web",
  verses: [{ ref: "Matthew 7:24", text: "who built his house on a rock." }],
  text: "who built his house on a rock.",
  attribution: "World English Bible (public domain)",
};

function on(overrides: Partial<ScriptureSettings> = {}): ScriptureSettings {
  return { ...DEFAULT_SCRIPTURE_SETTINGS, ...overrides };
}

describe("with no passage", () => {
  it("renders the lyrics alone, without the grid wrapper", () => {
    render(
      <ScriptureStage slide={SLIDE} blankMode="none" passage={null} settings={on()} />,
    );
    expect(screen.getByText("I will set my hope")).toBeInTheDocument();
    expect(screen.queryByTestId("scripture-pane")).not.toBeInTheDocument();
    expect(screen.queryByTestId("scripture-stage")).not.toBeInTheDocument();
  });
});

describe("with a passage", () => {
  it("shows the passage beside the lyrics", () => {
    render(
      <ScriptureStage
        slide={SLIDE}
        blankMode="none"
        passage={PASSAGE}
        settings={on()}
      />,
    );
    expect(screen.getByText("I will set my hope")).toBeInTheDocument();
    expect(screen.getByText(PASSAGE.text)).toBeInTheDocument();
    expect(screen.getByText(/Matthew 7:24–25/)).toBeInTheDocument();
  });

  it("renders lyrics alone on a slide with no passage", () => {
    render(
      <ScriptureStage
        slide={SLIDE}
        blankMode="none"
        passage={null}
        settings={on()}
      />,
    );
    expect(screen.getByText("I will set my hope")).toBeInTheDocument();
    expect(screen.queryByTestId("scripture-pane")).not.toBeInTheDocument();
  });

  it("hides the passage when the screen is blanked", () => {
    // Blanking is an operator saying "nothing on screen". A verse surviving
    // that would defeat the point of the key.
    for (const blankMode of ["black", "white"] as const) {
      const { unmount } = render(
        <ScriptureStage
          slide={SLIDE}
          blankMode={blankMode}
          passage={PASSAGE}
          settings={on()}
        />,
      );
      expect(screen.queryByTestId("scripture-pane")).not.toBeInTheDocument();
      unmount();
    }
  });

  it("hides the reference when asked to", () => {
    render(
      <ScriptureStage
        slide={SLIDE}
        blankMode="none"
        passage={PASSAGE}
        settings={on({ show_reference: false })}
      />,
    );
    expect(screen.getByText(PASSAGE.text)).toBeInTheDocument();
    expect(screen.queryByText(/Matthew 7:24–25/)).not.toBeInTheDocument();
  });

  it("shows the reference without the translation when asked to", () => {
    render(
      <ScriptureStage
        slide={SLIDE}
        blankMode="none"
        passage={PASSAGE}
        settings={on({ show_translation: false })}
      />,
    );
    expect(screen.getByText(/Matthew 7:24–25/)).toBeInTheDocument();
    expect(screen.queryByText("WEB")).not.toBeInTheDocument();
  });

  it("drops the lyrics entirely in fullscreen", () => {
    render(
      <ScriptureStage
        slide={SLIDE}
        blankMode="none"
        passage={PASSAGE}
        settings={on()}
        fullscreen
      />,
    );
    expect(screen.getByText(PASSAGE.text)).toBeInTheDocument();
    expect(screen.queryByText("I will set my hope")).not.toBeInTheDocument();
  });

  it("falls back to the lyrics when fullscreen is asked for with no passage", () => {
    render(
      <ScriptureStage
        slide={SLIDE}
        blankMode="none"
        passage={null}
        settings={on()}
        fullscreen
      />,
    );
    expect(screen.getByText("I will set my hope")).toBeInTheDocument();
  });

  it("puts the passage in a lower third under the lyrics", () => {
    render(<ScriptureStage slide={SLIDE} blankMode="none" passage={PASSAGE} settings={on()} />);
    expect(screen.getByTestId("scripture-stage")).toBeInTheDocument();
    expect(screen.getByText("I will set my hope")).toBeInTheDocument();
    expect(screen.getByText(PASSAGE.text)).toBeInTheDocument();
  });

  it("wears the display look, not scripture-only size and font settings", () => {
    render(
      <DisplayLookContext.Provider value={{ ...DEFAULT_LOOK, theme: "light", font: "georgia" }}>
        <ScriptureStage slide={SLIDE} blankMode="none" passage={PASSAGE} settings={on({ font: "sans" })} fullscreen />
      </DisplayLookContext.Provider>,
    );
    const pane = screen.getByTestId("scripture-pane");
    expect(pane.style.fontFamily).toContain("Georgia");
    expect(pane.style.color).toBe("rgb(13, 13, 13)");
  });
});
