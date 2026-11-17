import { cleanup, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// A release build: every in-development feature off.
vi.mock("@/lib/features", () => ({
  FEATURES: { spokenInstructions: false, liveScripture: true },
}));

// Settings saved while song passages could still be switched on.
vi.mock("@/lib/tauri", async (orig) => ({
  ...(await orig<typeof import("@/lib/tauri")>()),
  getSettings: vi.fn(async () => ({ scripture: { enabled: true, position: "top" } })),
}));

import { DebugTabs } from "@/components/debug/DebugTabs";
import { useScriptureSettings } from "@/lib/scripture/useScriptureSettings";

afterEach(cleanup);

describe("in-development features in a release build", () => {
  it("hides the spoken-requests tab", () => {
    render(<DebugTabs />);
    expect(screen.getByRole("button", { name: "Transcript" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Instructions" })).toBeNull();
  });

  it("still reads scripture settings saved with the removed song-passage switch", async () => {
    const { result } = renderHook(() => useScriptureSettings());
    // "top" isn't the default, so seeing it means the saved settings landed.
    await waitFor(() => expect(result.current.position).toBe("top"));
  });
});
