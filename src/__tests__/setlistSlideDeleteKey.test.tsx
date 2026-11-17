import type { ComponentProps } from "react";
import { DndContext } from "@dnd-kit/core";
import { SortableContext } from "@dnd-kit/sortable";
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SetlistSlideCard } from "@/components/setlists/SetlistSlideCard";
import { DEFAULT_LOOK } from "@/lib/displayLook";

afterEach(cleanup);

function renderCard(onDelete: () => void, opts: { selected: boolean; deleteOnKey?: boolean }) {
  const props = {
    sortableId: "s1",
    look: DEFAULT_LOOK,
    sectionLabel: "verse 1",
    sectionType: "verse",
    lyrics: "Amazing grace",
    emptyText: "(blank)",
    selected: opts.selected,
    editing: false,
    selectLabel: "Select slide",
    onSelect: vi.fn(),
    onStartEdit: vi.fn(),
    onStopEdit: vi.fn(),
    onInsertAfter: vi.fn(),
    onDuplicate: vi.fn(),
    onDelete,
    data: { "data-testid": "card" },
    deleteOnKey: opts.deleteOnKey,
  } as ComponentProps<typeof SetlistSlideCard>;
  render(
    <DndContext>
      <SortableContext items={["s1"]}>
        <SetlistSlideCard {...props} />
      </SortableContext>
    </DndContext>,
  );
}

describe("Delete on a selected setlist slide", () => {
  it("deletes it on Delete or Backspace where deleteOnKey is set", () => {
    const onDelete = vi.fn();
    renderCard(onDelete, { selected: true, deleteOnKey: true });
    fireEvent.keyDown(window, { key: "Delete" });
    fireEvent.keyDown(window, { key: "Backspace" });
    expect(onDelete).toHaveBeenCalledTimes(2);
  });

  it("does nothing when not selected, or without deleteOnKey (the live view)", () => {
    const onDelete = vi.fn();
    renderCard(onDelete, { selected: false, deleteOnKey: true });
    fireEvent.keyDown(window, { key: "Delete" });
    cleanup();
    renderCard(onDelete, { selected: true });
    fireEvent.keyDown(window, { key: "Delete" });
    expect(onDelete).not.toHaveBeenCalled();
  });
});
