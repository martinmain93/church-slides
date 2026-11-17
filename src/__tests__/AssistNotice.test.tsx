import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { createRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AssistNotice } from "@/components/presentation/AssistNotice";
import type { AssistSuggestion } from "@/lib/assist";

const PLACES: Record<string, { title: string; section: string; firstLine: string }> = {
  "0:2": { title: "Build My Life", section: "verse 2", firstLine: "Worthy of every breath" },
  "0:5": { title: "Build My Life", section: "verse 5", firstLine: "Holy" },
  "1:1": { title: "Mighty God", section: "other", firstLine: "Our God is a mighty God" },
};
const describePlace = (i: number, s: number) => PLACES[`${i}:${s}`] ?? null;

function suggestion(over: Partial<AssistSuggestion> = {}): AssistSuggestion {
  return {
    id: 1,
    tier: "place",
    itemIdx: 0,
    slideIdx: 2,
    alternatives: [{ itemIdx: 0, slideIdx: 5 }, { itemIdx: 1, slideIdx: 1 }],
    ...over,
  };
}

function renderNotice(s: AssistSuggestion | null, onGo = vi.fn(), onDismiss = vi.fn()) {
  // jsdom has no IntersectionObserver, so no tile ever counts as in view.
  const gridRef = createRef<HTMLDivElement>();
  const view = render(
    <AssistNotice
      suggestion={s}
      gridRef={gridRef}
      describe={describePlace}
      onGo={onGo}
      onDismiss={onDismiss}
    />,
  );
  const rerender = (next: AssistSuggestion | null) =>
    view.rerender(
      <AssistNotice
        suggestion={next}
        gridRef={gridRef}
        describe={describePlace}
        onGo={onGo}
        onDismiss={onDismiss}
      />,
    );
  return { onGo, onDismiss, rerender };
}

afterEach(cleanup);

describe("AssistNotice", () => {
  it("says where the band is, and Go takes the screen there", () => {
    const { onGo } = renderNotice(suggestion());
    expect(screen.getByTestId("assist-notice").textContent).toMatch(
      /singing Build My Life, verse 2.*Worthy of every breath/,
    );
    fireEvent.click(screen.getByText("Go"));
    expect(onGo).toHaveBeenCalledWith(0, 2);
  });

  it("× dismisses that suggestion", () => {
    const { onDismiss } = renderNotice(suggestion({ id: 7 }));
    fireEvent.click(screen.getByLabelText("Dismiss suggestion"));
    expect(onDismiss).toHaveBeenCalledWith(7);
  });

  it("'Something else' lists the alternatives, each with its own Go", () => {
    const { onGo } = renderNotice(suggestion());
    expect(screen.queryByText("Mighty God")).toBeNull();
    fireEvent.click(screen.getByText("Something else"));
    expect(screen.getByText("Mighty God")).toBeTruthy();
    const goes = screen.getAllByText("Go");
    expect(goes).toHaveLength(3);
    fireEvent.click(goes[2]!);
    expect(onGo).toHaveBeenCalledWith(1, 1);
  });

  it("has no 'Something else' when there are no alternatives", () => {
    renderNotice(suggestion({ alternatives: [] }));
    expect(screen.queryByText("Something else")).toBeNull();
  });

  it("never shows for the next-slide tier — that's the tile's pulse alone", () => {
    renderNotice(suggestion({ tier: "next" }));
    expect(screen.queryByTestId("assist-notice")).toBeNull();
  });

  it("freezes under the pointer: a cleared suggestion stays until the pointer leaves", () => {
    const { rerender } = renderNotice(suggestion());
    fireEvent.mouseEnter(screen.getByRole("status"));
    act(() => rerender(null));
    expect(screen.getByTestId("assist-notice")).toBeTruthy();
    fireEvent.mouseLeave(screen.getByRole("status"));
    expect(screen.queryByTestId("assist-notice")).toBeNull();
  });

  it("freezes under the pointer: a moved suggestion doesn't change under a reaching hand", () => {
    const { rerender } = renderNotice(suggestion());
    fireEvent.mouseEnter(screen.getByRole("status"));
    act(() => rerender(suggestion({ id: 2, slideIdx: 5 })));
    expect(screen.getByTestId("assist-notice").textContent).toMatch(/verse 2/);
    fireEvent.mouseLeave(screen.getByRole("status"));
    expect(screen.getByTestId("assist-notice").textContent).toMatch(/verse 5/);
  });
});
