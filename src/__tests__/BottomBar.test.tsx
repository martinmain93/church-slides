import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BottomBar } from "@/components/presentation/BottomBar";
import type { ParsedSlide } from "@/lib/parseSong";
import type { PresentationItem } from "@/lib/presentation/state";
import type { SongRow } from "@/lib/tauri";

function item(id: string, title: string, lines: string[]): PresentationItem {
  const song = { id, title, body_text: lines.join("\n\n") } as SongRow;
  const slides = lines.map(
    (lyrics, i): ParsedSlide => ({
      id: `${id}-${i}`,
      section: { type: "verse", number: i + 1 },
      order_in_song: i,
      order_in_section: 0,
      lyrics,
      words: lyrics.split(" "),
    }),
  );
  return { itemId: id, kind: "song", song, slides };
}

function renderBar() {
  render(
    <BottomBar
      items={[]}
      currentItemIdx={0}
      currentSlideIdx={0}
      onJump={() => undefined}
      toolbar={<span>AI ready</span>}
    />,
  );
  return screen.getByTestId("bottom-bar");
}

describe("BottomBar", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("starts hidden and ignores key presses", () => {
    const bar = renderBar();
    expect(bar.dataset.shown).toBe("false");
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(bar.dataset.shown).toBe("false");
  });

  it("opens at the bottom edge and closes after the pointer leaves", () => {
    const bar = renderBar();
    fireEvent.mouseMove(window, { clientY: window.innerHeight - 10 });
    expect(bar.dataset.shown).toBe("true");
    fireEvent.mouseMove(window, { clientY: 10 });
    expect(bar.dataset.shown).toBe("true"); // grace period
    act(() => vi.advanceTimersByTime(2100));
    expect(bar.dataset.shown).toBe("false");
  });

  it("stays open while the pointer is over it", () => {
    const bar = renderBar();
    fireEvent.mouseMove(window, { clientY: window.innerHeight - 10 });
    fireEvent.mouseMove(screen.getByText("AI ready"), { clientY: 10 });
    act(() => vi.advanceTimersByTime(5000));
    expect(bar.dataset.shown).toBe("true");
  });

  it("lists every slide and song, and jumps to the one clicked", () => {
    const onJump = vi.fn();
    render(
      <BottomBar
        items={[
          item("a", "First Song", ["One a", "Two a", "Three a"]),
          item("b", "Second Song", ["One b", "Two b"]),
          item("c", "Third Song", ["One c"]),
        ]}
        currentItemIdx={1}
        currentSlideIdx={0}
        onJump={onJump}
      />,
    );
    // Slides before the current one are there too, to go back to.
    fireEvent.click(screen.getByRole("button", { name: /Two a/ }));
    expect(onJump).toHaveBeenLastCalledWith(0, 1);
    fireEvent.click(screen.getByRole("button", { name: "Third Song" }));
    expect(onJump).toHaveBeenLastCalledWith(2, 0);
    // The current slide and song are labels, not buttons.
    expect(screen.queryByRole("button", { name: /One b/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Second Song" })).toBeNull();
  });
});
