import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "@/App";
import * as tauri from "@/lib/tauri";

const seedSong: tauri.SongRow = {
  id: "song_amazing_grace",
  title: "Amazing Grace",
  body_text: "[verse 1]\nAmazing grace how sweet the sound\nThat saved a wretch like me",
  ccli: null,
  author: "John Newton",
  copyright: null,
  default_tempo_wps: null,
  slide_order: null,
  created_at: 0,
  updated_at: 0,
};

class FakeWebSocket {
  static OPEN = 1;
  static CLOSED = 3;
  readyState = FakeWebSocket.OPEN;
  onopen: ((ev: Event) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  constructor(public url: string) {
    setTimeout(() => this.onopen?.(new Event("open")), 0);
  }
  send() {}
  close() {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.(new CloseEvent("close"));
  }
}

/** Click the Library nav item to leave the default Home view. */
function gotoLibrary() {
  fireEvent.click(screen.getByRole("button", { name: /^library$/i }));
}

describe("App", () => {
  beforeEach(() => {
    // Mark the audio setup wizard as already-completed so the App tests
    // don't have to dismiss its overlay before interacting with the
    // main UI.
    localStorage.setItem(
      "auto-presenter.audio-input-prefs",
      JSON.stringify({
        device_index: null,
        source: "vocal_aux",
        setup_completed: true,
      }),
    );
    vi.spyOn(tauri, "getSidecarPort").mockResolvedValue(54321);
    vi.spyOn(tauri, "listSongs").mockResolvedValue([seedSong]);
    vi.spyOn(tauri, "listSongsSorted").mockResolvedValue([seedSong]);
    vi.spyOn(tauri, "searchSongs").mockResolvedValue([seedSong]);
    vi.spyOn(tauri, "getSong").mockResolvedValue(seedSong);
    vi.spyOn(tauri, "createSong").mockResolvedValue("song_new");
    vi.spyOn(tauri, "updateSong").mockResolvedValue();
    vi.spyOn(tauri, "listSetlists").mockResolvedValue([]);
    vi.stubGlobal("WebSocket", FakeWebSocket as unknown as typeof WebSocket);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it("lands on Home and exposes Library + Settings nav", async () => {
    render(<App />);
    // Home heading
    expect(await screen.findByRole("heading", { name: "Home" })).toBeInTheDocument();
    // Sidebar nav buttons
    expect(screen.getByRole("button", { name: /^library$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^settings$/i })).toBeInTheDocument();
  });

  it("Settings opens its sub-pages beneath it, one page at a time", async () => {
    vi.stubEnv("VITE_CHURCH_SLIDES_STATS_URL", ""); // a build from source
    vi.spyOn(tauri, "getSettings").mockResolvedValue({
      send_error_reports: false,
      error_reports_asked: true,
      share_usage: false,
      record_services: false,
    } as unknown as tauri.AppSettings);
    render(<App />);
    await screen.findByRole("heading", { name: "Home" });
    expect(screen.queryByRole("list", { name: "Settings pages" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /^settings$/i }));
    const pages = await screen.findByRole("list", { name: "Settings pages" });
    expect(pages).toBeInTheDocument();
    // Opens on the first page.
    expect(screen.getByRole("heading", { name: "Audio" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Audio" })).toHaveAttribute("aria-current", "page");

    fireEvent.click(screen.getByRole("button", { name: "Other" }));
    expect(await screen.findByRole("heading", { name: "Other" })).toBeInTheDocument();
    // Privacy and updates together; developer tools folded at the bottom.
    // A build from source has nowhere to send reports, so it offers none.
    expect(await screen.findByText("Check for updates")).toBeInTheDocument();
    expect(screen.queryByText("Send error reports")).toBeNull();
    const developer = screen.getByText("Developer tools").closest("details");
    expect(developer).not.toHaveAttribute("open");
    // Only the open page's settings show.
    expect(screen.queryByText("Where speech is recognised")).toBeNull();

    // Assistants live on the AI page; the display look is inline on Display.
    fireEvent.click(screen.getByRole("button", { name: "AI" }));
    expect(await screen.findByRole("heading", { name: "AI assistants" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Display look" }));
    expect(await screen.findByRole("radiogroup", { name: "Colours" })).toBeInTheDocument();

    // Leaving settings folds the list away again.
    fireEvent.click(screen.getByRole("button", { name: /^home$/i }));
    await waitFor(() =>
      expect(screen.queryByRole("list", { name: "Settings pages" })).toBeNull(),
    );
  });

  it("an official build offers error reports and usage statistics in Settings", async () => {
    vi.stubEnv("VITE_CHURCH_SLIDES_STATS_URL", "https://stats.example.test");
    render(<App />);
    await screen.findByRole("heading", { name: "Home" });
    fireEvent.click(screen.getByRole("button", { name: /^settings$/i }));
    await screen.findByRole("list", { name: "Settings pages" });
    fireEvent.click(screen.getByRole("button", { name: "Other" }));
    expect(await screen.findByRole("switch", { name: "Send error reports" })).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Share usage statistics" })).toBeInTheDocument();
  });

  it("Home shows Library count", async () => {
    render(<App />);
    expect(await screen.findByText(/1 song in your library/i)).toBeInTheDocument();
  });

  it("Library tab renders the seed song", async () => {
    render(<App />);
    // Wait for app to mount
    await screen.findByRole("heading", { name: "Home" });
    gotoLibrary();
    expect(await screen.findByText("Amazing Grace")).toBeInTheDocument();
  });

  it("clicking a song row opens the editor and loads its content", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "Home" });
    gotoLibrary();
    const row = await screen.findByText("Amazing Grace");
    fireEvent.click(row);
    await waitFor(() => {
      expect(screen.getByDisplayValue("Amazing Grace")).toBeInTheDocument();
    });
    expect(tauri.getSong).toHaveBeenCalledWith("song_amazing_grace");
  });

  it("clicking 'New song' from Library opens a blank editor", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "Home" });
    gotoLibrary();
    const newBtn = await screen.findByRole("button", { name: /new song/i });
    fireEvent.click(newBtn);
    expect(screen.getByPlaceholderText(/untitled song/i)).toHaveValue("");
  });

  it("renders the live preview as the user types", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "Home" });
    gotoLibrary();
    fireEvent.click(await screen.findByRole("button", { name: /new song/i }));
    const textarea = screen.getByPlaceholderText(/\[verse 1\]/);
    fireEvent.change(textarea, {
      target: { value: "[chorus]\nGlory glory" },
    });
    const slides = await screen.findAllByTestId("preview-slide");
    expect(slides).toHaveLength(1);
    expect(slides[0]).toHaveTextContent("Glory glory");
  });

  it("shows an error toast on save failure", async () => {
    vi.spyOn(tauri, "createSong").mockRejectedValueOnce(new Error("disk full"));
    render(<App />);
    await screen.findByRole("heading", { name: "Home" });
    gotoLibrary();
    fireEvent.click(await screen.findByRole("button", { name: /new song/i }));
    const titleInput = screen.getByPlaceholderText(/untitled song/i);
    fireEvent.change(titleInput, { target: { value: "Test" } });
    const textarea = screen.getByPlaceholderText(/\[verse 1\]/);
    fireEvent.change(textarea, { target: { value: "[v]\nLine" } });
    await waitFor(
      () => expect(screen.getByText(/disk full/)).toBeInTheDocument(),
      { timeout: 3000 },
    );
  });

  it("Home -> New setlist opens a setlist builder", async () => {
    vi.spyOn(tauri, "createSetlist").mockResolvedValue("setlist_new");
    vi.spyOn(tauri, "getSetlist").mockResolvedValue({
      id: "setlist_new",
      name: "New setlist",
      created_at: 0,
      last_used_at: null,
      items: [],
    });
    render(<App />);
    await screen.findByRole("heading", { name: "Home" });
    // Two "New setlist" buttons render when the list is empty: the section
    // action and the empty-state CTA. Either should work; click the first.
    const buttons = await screen.findAllByRole("button", { name: /new setlist/i });
    fireEvent.click(buttons[0]!);
    await waitFor(() => {
      expect(screen.getByDisplayValue("New setlist")).toBeInTheDocument();
    });
    expect(screen.getByTestId("setlist-workspace")).toBeInTheDocument();
    // Global sidebar is collapsed inside the unified setlist workspace
    expect(screen.queryByRole("button", { name: /^library$/i })).toBeNull();
    // Named for the coming Sunday, so the home list can tell setlists apart.
    expect(tauri.createSetlist).toHaveBeenCalledWith(expect.stringMatching(/^Sunday \d+ \w+$/), expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/));
  });
});
