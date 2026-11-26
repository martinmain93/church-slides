import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/components/home/SpeechModelStatus", () => ({ SpeechModelStatus: () => null }));

import { HomeScreen } from "@/components/home/HomeScreen";
import { shouldDiscardOnBack } from "@/components/setlists/SetlistBuilder";
import { ConfirmProvider } from "@/lib/confirm";
import { LibraryProvider } from "@/library/context";
import { desktopLibrary } from "@/library/desktop";
import * as tauri from "@/lib/tauri";
import { ToastProvider } from "@/lib/toast";

function setlist(i: number, songs: string[] = []): tauri.SetlistRow {
  // Past services, one a day in October 2026: Sunday 7 is the latest.
  return { id: `sl${i}`, name: `Sunday ${i}`, created_at: i, last_used_at: null, service_date: `2026-10-${10 + i}`, song_titles: songs };
}

function renderHome() {
  return render(
    <LibraryProvider source={desktopLibrary}>
    <ToastProvider>
      <ConfirmProvider>
        <HomeScreen
          onCreateSetlist={vi.fn()}
          onCopySetlist={vi.fn()}
          onOpenSetlist={vi.fn()}
          onContinueService={vi.fn()}
          onOpenLibrary={vi.fn()}
          onStartService={vi.fn()}
        />
      </ConfirmProvider>
    </ToastProvider>
    </LibraryProvider>,
  );
}

describe("leaving a setlist", () => {
  it("discards only a brand-new setlist that was never named and never had a song", () => {
    expect(shouldDiscardOnBack({ justCreated: true, renamed: false, hadSongs: false })).toBe(true);
    // Named it ("Christmas Eve — songs TBD"): kept, even after the name autosaved.
    expect(shouldDiscardOnBack({ justCreated: true, renamed: true, hadSongs: false })).toBe(false);
    // Added a song and took it out again: kept.
    expect(shouldDiscardOnBack({ justCreated: true, renamed: false, hadSongs: true })).toBe(false);
    // An empty setlist made last week and opened again: never deleted.
    expect(shouldDiscardOnBack({ justCreated: false, renamed: false, hadSongs: false })).toBe(false);
  });
});

describe("Home setlists", () => {
  beforeEach(() => {
    vi.spyOn(tauri, "listSongs").mockResolvedValue([]);
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows six past services, then all, and searches song titles inside them", async () => {
    const rows = Array.from({ length: 8 }, (_, i) => setlist(i, i === 7 ? ["Way Maker"] : []));
    vi.spyOn(tauri, "listSetlists").mockResolvedValue(rows);
    renderHome();
    // Latest first: Sunday 7 is the most recent, Sunday 0 the oldest.
    expect(await screen.findByText("Sunday 7")).toBeInTheDocument();
    expect(screen.queryByText("Sunday 0")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Show all 8 past services" }));
    expect(screen.getByText("Sunday 0")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Show fewer" }));
    fireEvent.change(screen.getByLabelText("Search services"), { target: { value: "way maker" } });
    expect(screen.getByText("Sunday 7")).toBeInTheDocument();
    expect(screen.queryByText("Sunday 6")).toBeNull();
  });

  it("asks before deleting a setlist with songs in it", async () => {
    vi.spyOn(tauri, "listSetlists").mockResolvedValue([setlist(1, ["Amazing Grace"])]);
    const del = vi.spyOn(tauri, "deleteSetlist").mockResolvedValue();
    renderHome();
    fireEvent.click(await screen.findByRole("button", { name: "Delete Sunday 1" }));
    const dialog = await screen.findByRole("dialog");
    expect(del).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(del).toHaveBeenCalledWith("sl1"));
    await waitFor(() => expect(screen.queryByText("Sunday 1")).toBeNull());
  });
});
