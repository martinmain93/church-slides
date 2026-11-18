import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SetlistBuilder } from "@/components/setlists/SetlistBuilder";
import { ConfirmProvider } from "@/lib/confirm";
import * as tauri from "@/lib/tauri";
import { ToastProvider } from "@/lib/toast";

const song = (id: string, title: string): tauri.SongRow => ({
  id,
  title,
  body_text: `[Verse 1]\n${title} first line`,
  ccli: null,
  author: null,
  copyright: null,
  default_tempo_wps: null,
  slide_order: null,
  created_at: 0,
  updated_at: 0,
});

function renderBuilder(justCreated: boolean) {
  return render(
    <ToastProvider>
      <ConfirmProvider>
        <SetlistBuilder setlistId="sl_new" onClose={vi.fn()} onStartService={vi.fn()} justCreated={justCreated} />
      </ConfirmProvider>
    </ToastProvider>,
  );
}

describe("setlist builder", () => {
  beforeEach(() => {
    vi.spyOn(tauri, "getSetlist").mockResolvedValue({
      id: "sl_new",
      name: "Sunday 22 Nov",
      created_at: 0,
      last_used_at: null,
      items: [],
    });
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("throws away an untouched new setlist however it's left, not only by the back button", async () => {
    const del = vi.spyOn(tauri, "deleteSetlist").mockResolvedValue();
    const view = renderBuilder(true);
    await screen.findByDisplayValue("Sunday 22 Nov");
    view.unmount();
    await waitFor(() => expect(del).toHaveBeenCalledWith("sl_new"));
  });

  it("keeps a setlist that existed before", async () => {
    const del = vi.spyOn(tauri, "deleteSetlist").mockResolvedValue();
    const view = renderBuilder(false);
    await screen.findByDisplayValue("Sunday 22 Nov");
    view.unmount();
    await new Promise((r) => setTimeout(r, 10));
    expect(del).not.toHaveBeenCalled();
  });

  it("keeps an added song in the suggestions, ticked, so the list doesn't jump", async () => {
    const songs = [song("a", "Alpha Song"), song("b", "Beta Song")];
    vi.spyOn(tauri, "listSongsSorted").mockResolvedValue(songs);
    vi.spyOn(tauri, "listSongs").mockResolvedValue(songs);
    vi.spyOn(tauri, "getSetlist").mockResolvedValue({
      id: "sl_new",
      name: "Sunday 22 Nov",
      created_at: 0,
      last_used_at: null,
      items: [{ id: "i1", song_id: "a", position: 0, song: songs[0] } as unknown as tauri.SetlistDetail["items"][number]],
    });
    renderBuilder(false);
    await screen.findByDisplayValue("Sunday 22 Nov");
    fireEvent.click(screen.getAllByRole("button", { name: /songs/i })[0]!);
    expect(await screen.findByLabelText("In the setlist")).toBeInTheDocument();
    expect(screen.getAllByText("Alpha Song").length).toBeGreaterThan(0);
  });
});
