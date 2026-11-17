import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// What release builds ship (2026-10-17): Bibles and passages put up live,
// but not passages attached to songs.
vi.mock("@/lib/features", () => ({
  FEATURES: { spokenInstructions: true, liveScripture: true },
}));

vi.mock("@/lib/tauri", () => ({
  // Song passages off, as a release install saves them.
  getSettings: () => Promise.resolve({ scripture: { enabled: false, translation: "web" } }),
  updateSettings: vi.fn(() => Promise.resolve()),
}));

vi.mock("@/lib/scripture/client", () => ({
  listTranslations: () =>
    Promise.resolve({
      defaultTranslation: "web",
      translations: [
        { id: "web", name: "World English Bible", abbreviation: "WEB", installed: true },
      ],
    }),
  removeTranslation: vi.fn(),
}));

vi.mock("@/lib/toast", () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn() }) }));

import { ScriptureSettingsPanel } from "@/components/scripture/ScriptureSettingsPanel";

afterEach(cleanup);

describe("Settings → Scripture without song passages", () => {
  it("offers the Bibles and how passages look, but not the song-passages switch", async () => {
    render(<ScriptureSettingsPanel />);
    expect(await screen.findByText("Find a translation…")).toBeTruthy();
    expect(screen.getByText("Import a Bible file…")).toBeTruthy();
    expect(screen.getByText("Default translation")).toBeTruthy();
    // One style system: no scripture-only position, size or font — an
    // override of the default style instead.
    expect(screen.getByRole("button", { name: "Override default style" })).toBeTruthy();
    expect(screen.queryByText("Position")).toBeNull();
    expect(screen.queryByText("Text size")).toBeNull();
    expect(screen.queryByText("Show scripture")).toBeNull();
  });
});
