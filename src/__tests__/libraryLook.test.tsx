import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/invoke", () => ({ isInTauri: () => true }));

import { SongHistoryDialog } from "@/components/library/SongHistoryDialog";
import { DEFAULT_LOOK, loadDisplayLook, loadLibraryLook, saveDisplayLook } from "@/lib/displayLook";
import * as tauri from "@/lib/tauri";

const library = (kind: "computer" | "organization"): tauri.LibraryInfo[] => [{ id: "x", name: "X", kind, open: true }];
const mine = { ...DEFAULT_LOOK, theme: "light" as const };

describe("the library's look", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.restoreAllMocks());

  it("this computer's library takes the look the window already had", async () => {
    localStorage.setItem("auto-presenter.display-look", JSON.stringify(mine));
    vi.spyOn(tauri, "getLibraryLook").mockResolvedValue(null);
    vi.spyOn(tauri, "listLibraries").mockResolvedValue(library("computer"));
    const set = vi.spyOn(tauri, "setLibraryLook").mockResolvedValue();
    await loadLibraryLook();
    expect(set).toHaveBeenCalledWith(JSON.stringify(mine), null);
  });

  it("an organization's library without one shows the defaults and sends nothing", async () => {
    localStorage.setItem("auto-presenter.display-look", JSON.stringify(mine));
    vi.spyOn(tauri, "getLibraryLook").mockResolvedValue(null);
    vi.spyOn(tauri, "listLibraries").mockResolvedValue(library("organization"));
    const set = vi.spyOn(tauri, "setLibraryLook").mockResolvedValue();
    await loadLibraryLook();
    expect(set).not.toHaveBeenCalled();
    expect(loadDisplayLook()).toEqual(DEFAULT_LOOK);
  });

  it("the library's look wins, and a change is saved into the library", async () => {
    vi.spyOn(tauri, "getLibraryLook").mockResolvedValue({ display: JSON.stringify(mine), scripture: null });
    vi.spyOn(tauri, "listLibraries").mockResolvedValue(library("organization"));
    const set = vi.spyOn(tauri, "setLibraryLook").mockResolvedValue();
    await loadLibraryLook();
    expect(loadDisplayLook()).toEqual(mine);
    saveDisplayLook(DEFAULT_LOOK);
    await waitFor(() => expect(set).toHaveBeenCalledWith(JSON.stringify(DEFAULT_LOOK), null));
  });
});

describe("a song's earlier words", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows one and brings it back into the editor", async () => {
    vi.spyOn(tauri, "songRevisions").mockResolvedValue([{ id: 3, field: "body_text", replacedAt: 0, replacedBy: "Sam" }]);
    vi.spyOn(tauri, "songRevision").mockResolvedValue({ id: 3, field: "body_text", replacedAt: 0, replacedBy: "Sam", value: "old words" });
    const restore = vi.fn();
    render(<SongHistoryDialog songId="s1" onRestore={restore} onClose={vi.fn()} />);
    fireEvent.click(await screen.findByText(/replaced by Sam/));
    expect(await screen.findByText("old words")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Bring back" }));
    expect(restore).toHaveBeenCalledWith("old words");
  });
});
