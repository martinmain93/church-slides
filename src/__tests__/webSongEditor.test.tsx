import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { LibraryProvider } from "@/library/context";
import { webLibrary } from "@/library/web";
import { memoryPersist } from "@/library/web/persist";
import { RecordStore } from "@/library/web/store";
import { SongEditor } from "@/web/pages/SongEditor";

afterEach(cleanup);

// Public-domain words only (AGENTS.md).
const LONG = "[Verse 1]\nAmazing grace how sweet the sound\nThat saved a wretch like me\nI once was lost but now am found";

async function editorFor(body: string) {
  const store = await RecordStore.open(memoryPersist());
  const lib = webLibrary(store, { changedHere: () => undefined, onChange: () => () => undefined });
  const id = await lib.createSong({ title: "Amazing Grace", body_text: body });
  render(
    <LibraryProvider source={lib}>
      <SongEditor id={id} />
    </LibraryProvider>,
  );
  return id;
}

describe("the web song editor", () => {
  it("shows a song as the desktop's slide cards, and splits a long one with Undo", async () => {
    await editorFor(LONG);
    expect(await screen.findAllByTestId("preview-slide")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Split slide in two" }));
    expect(screen.getAllByTestId("preview-slide")).toHaveLength(2);
    fireEvent.click(screen.getByTestId("undo-split"));
    expect(screen.getAllByTestId("preview-slide")).toHaveLength(1);
    expect(screen.queryByTestId("undo-split")).toBeNull();
  });

  it("still edits the words as text", async () => {
    await editorFor(LONG);
    await screen.findAllByTestId("preview-slide");
    fireEvent.click(screen.getByTestId("words-as-text"));
    const text = screen.getByTestId("song-body") as HTMLTextAreaElement;
    expect(text.value).toBe(LONG);
    fireEvent.change(text, { target: { value: "[Verse 1]\nAmazing grace\n\nHow sweet the sound" } });
    fireEvent.click(screen.getByTestId("words-as-text"));
    expect(screen.getAllByTestId("preview-slide")).toHaveLength(2);
  });
});
