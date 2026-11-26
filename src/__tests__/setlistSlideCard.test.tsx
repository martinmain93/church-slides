import { DndContext } from "@dnd-kit/core";
import { SortableContext } from "@dnd-kit/sortable";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SetlistSlideCard } from "@/components/setlists/SetlistSlideCard";
import { DEFAULT_LOOK } from "@/lib/displayLook";
import { insertAfter } from "@/lib/presentation/slideEditing";

afterEach(cleanup);

function renderCard(
  handlers: { onInsertAfter?: () => void; onDuplicate?: () => void; onDelete?: () => void; onSplit?: () => void } = {},
  lyrics = "Amazing grace",
  selected = false,
) {
  render(
    <DndContext>
      <SortableContext items={["s1"]}>
        <SetlistSlideCard
          sortableId="s1"
          look={DEFAULT_LOOK}
          sectionLabel="verse 1"
          sectionType="verse"
          lyrics={lyrics}
          emptyText="(blank)"
          selected={selected}
          editing={false}
          selectLabel="Select slide"
          onSelect={vi.fn()}
          onStartEdit={vi.fn()}
          onStopEdit={vi.fn()}
          onInsertAfter={handlers.onInsertAfter ?? vi.fn()}
          onDuplicate={handlers.onDuplicate ?? vi.fn()}
          onDelete={handlers.onDelete ?? vi.fn()}
          onSplit={handlers.onSplit}
          growWhenSelected
          data={{ "data-testid": "card" }}
        />
      </SortableContext>
    </DndContext>,
  );
}

describe("a setlist slide card", () => {
  it("drags by the card itself, so there's no separate drag button", () => {
    renderCard();
    expect(screen.queryByRole("button", { name: /drag/i })).toBeNull();
  });

  it("offers Insert slide, Duplicate and Delete in its menu, and no second Edit", () => {
    const onInsertAfter = vi.fn();
    renderCard({ onInsertAfter });
    fireEvent.click(screen.getByRole("button", { name: "More slide actions" }));
    const items = screen.getAllByRole("menuitem").map((item) => item.textContent);
    expect(items).toEqual(["Insert slide", "Duplicate", "Delete"]);
    fireEvent.click(screen.getByRole("menuitem", { name: "Insert slide" }));
    expect(onInsertAfter).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menu")).toBeNull();
  });
});

describe("splitting a setlist slide", () => {
  it("shows a split button that splits the slide", () => {
    const onSplit = vi.fn();
    renderCard({ onSplit }, "Amazing grace how sweet the sound\nThat saved a wretch like me\nI once was lost but now am found");
    fireEvent.click(screen.getByRole("button", { name: "Split slide in two" }));
    expect(onSplit).toHaveBeenCalledOnce();
  });

  it("hides it for a slide of one word, or an ordinary two-line one", () => {
    renderCard({ onSplit: vi.fn() }, "Hallelujah");
    expect(screen.queryByRole("button", { name: "Split slide in two" })).toBeNull();
    cleanup();
    renderCard({ onSplit: vi.fn() }, "Amazing grace\nHow sweet the sound");
    expect(screen.queryByRole("button", { name: "Split slide in two" })).toBeNull();
  });
});

describe("a setlist slide's picture", () => {
  it("stays a 16:9 picture of the screen when selected: only typing grows it", () => {
    renderCard({}, "Amazing grace", true);
    expect(document.querySelector("[data-expanded]")).toBeNull();
    expect(screen.getByTestId("mini-slide-words")).toHaveTextContent("Amazing grace");
  });
});

describe("insertAfter", () => {
  it("adds an empty slide in the same section straight after the given one", () => {
    const slides = [
      { section: { type: "verse" as const, number: 1 }, lyrics: "One" },
      { section: { type: "chorus" as const, number: 1 }, lyrics: "Two" },
    ];
    expect(insertAfter(slides, 0)).toEqual([
      slides[0],
      { section: { type: "verse", number: 1 }, lyrics: "" },
      slides[1],
    ]);
    expect(insertAfter(slides, 5)).toBe(slides);
  });
});
