import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { LibraryProvider } from "@/library/context";
import { webLibrary } from "@/library/web";
import { memoryPersist } from "@/library/web/persist";
import { RecordStore } from "@/library/web/store";
import { SongEditor } from "@/web/pages/SongEditor";

afterEach(cleanup);

// Public-domain words only (AGENTS.md).
const LONG = "[Verse 1]\nAmazing grace how sweet the sound\nThat saved a wretch like me\nI once was lost but now am found";
const TWO = "[Verse 1]\nAmazing grace how sweet the sound\n\nThat saved a wretch like me\n";

async function editorFor(body: string) {
  const store = await RecordStore.open(memoryPersist());
  const lib = webLibrary(store, { changedHere: () => undefined, onChange: () => () => undefined });
  const id = await lib.createSong({ title: "Amazing Grace", body_text: body });
  render(
    <LibraryProvider source={lib}>
      <SongEditor id={id} />
    </LibraryProvider>,
  );
  // Let the song, its chart and the look finish loading.
  await act(async () => undefined);
  await screen.findAllByTestId("preview-slide");
}

const cards = () => screen.getAllByTestId("preview-slide");
const words = () => {
  fireEvent.click(screen.getByTestId("words-as-text"));
  const value = (screen.getByTestId("song-body") as HTMLTextAreaElement).value;
  fireEvent.click(screen.getByTestId("words-as-text"));
  return value;
};

describe("the web song editor", () => {
  it("shows a song as the desktop's slide cards, and splits a long one with Undo", async () => {
    await editorFor(LONG);
    expect(cards()).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Split slide in two" }));
    expect(cards()).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Undo split" }));
    expect(cards()).toHaveLength(1);
    expect(screen.queryByTestId("undo-change")).toBeNull();
    expect(words()).toBe(LONG);
  });

  it("puts a deleted slide back with Undo", async () => {
    await editorFor(TWO);
    const second = cards()[1]!;
    fireEvent.click(within(second).getByRole("button", { name: "Select slide" }));
    fireEvent.click(within(second).getByRole("button", { name: "More slide actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Delete slide" }));
    expect(cards()).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Undo delete" }));
    expect(cards()).toHaveLength(2);
    expect(words()).toBe(TWO);
  });

  it("selects a slide on the first tap and opens its words on the second", async () => {
    await editorFor(TWO);
    const first = cards()[0]!;
    // Until it's picked, its buttons are hidden and take no taps.
    expect(within(first).getByRole("button", { name: "Edit slide text", hidden: true }).parentElement!.className).toMatch(/(^|\s)invisible(\s|$)/);
    fireEvent.click(within(first).getByRole("button", { name: "Select slide" }));
    expect(first).toHaveAttribute("data-selected", "true");
    expect(within(first).queryByRole("textbox")).toBeNull();
    fireEvent.click(within(first).getAllByRole("button", { name: "Edit slide text" }).at(-1)!);
    expect(within(first).getByRole("textbox")).toHaveValue("Amazing grace how sweet the sound");
  });

  it("still edits the words as text", async () => {
    await editorFor(LONG);
    fireEvent.click(screen.getByTestId("words-as-text"));
    const text = screen.getByTestId("song-body") as HTMLTextAreaElement;
    expect(text.value).toBe(LONG);
    fireEvent.change(text, { target: { value: "[Verse 1]\nAmazing grace\n\nHow sweet the sound" } });
    fireEvent.click(screen.getByTestId("words-as-text"));
    expect(cards()).toHaveLength(2);
  });
});
