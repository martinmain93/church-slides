import { render, screen, cleanup, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const openDebugWindow = vi.fn(async () => undefined);
const closeDebugWindow = vi.fn(async () => undefined);
const isDebugWindowOpen = vi.fn(async () => false);
vi.mock("@/lib/presentation/projection", () => ({
  openDebugWindow: () => openDebugWindow(),
  closeDebugWindow: () => closeDebugWindow(),
  isDebugWindowOpen: () => isDebugWindowOpen(),
}));

import { DebugWindow } from "@/components/debug/DebugWindow";
import { TABS } from "@/components/debug/DebugTabs";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  isDebugWindowOpen.mockResolvedValue(false);
});

describe("detached debug window", () => {
  it("renders every tab the docked panel has", () => {
    // The dock and the window share `DebugTabs` precisely so a tab cannot
    // exist in one and not the other — this file already carries a scar from
    // the Audio tab appearing twice in one list and not at all in the other.
    render(<DebugWindow />);
    for (const name of TABS) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
  });

  it("needs nothing from the shell it was detached from", () => {
    // No props, no context: every tab reads module-scoped live stores fed by
    // the shared sidecar socket, and each window opens its own connection.
    expect(() => render(<DebugWindow />)).not.toThrow();
    expect(screen.getByTestId("debug-window")).toBeInTheDocument();
  });
});

describe("the dock, once detached", () => {
  it("hands off rather than rendering a second copy", async () => {
    const { DebugPanel } = await import("@/components/debug/DebugPanel");
    isDebugWindowOpen.mockResolvedValue(true);
    render(<DebugPanel />);
    window.dispatchEvent(
      new KeyboardEvent("keydown", { key: "d", metaKey: true, bubbles: true }),
    );
    await waitFor(() =>
      expect(screen.getByText(/Open in its own window/i)).toBeInTheDocument(),
    );
    // Two live copies is twice the render cost for one reader, and a render
    // loop has starved this pipeline before.
    expect(screen.queryByRole("button", { name: "Transcript" })).toBeNull();
  });
});

describe("a tab that throws", () => {
  it("gives the window the providers its tabs expect", () => {
    // The live failure: `useToast` throws when its provider is missing, the
    // detached window does not mount `AppShell` which supplies it, and with
    // no boundary React unmounted the whole tree — a blank near-black window
    // with no way back, because nothing was left alive to render one.
    // Tabs reach `useToast` through `useToggleRequest`.
    expect(() => render(<DebugWindow />)).not.toThrow();
    expect(screen.getByTestId("debug-window")).toBeInTheDocument();
  });

  it("still shows the other tabs when one fails", async () => {
    // The boundary is the belt, the providers are the braces: a debug surface
    // that can black itself out fails exactly when something is already wrong
    // and being investigated.
    vi.doMock("@/components/debug/TranscriptTab", () => ({
      TranscriptTab: () => {
        throw new Error("tab exploded");
      },
    }));
    vi.resetModules();
    const { DebugWindow: Fresh } = await import("@/components/debug/DebugWindow");
    render(<Fresh />);
    expect(screen.getByText(/This tab failed to render/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Try again/i })).toBeInTheDocument();
    vi.doUnmock("@/components/debug/TranscriptTab");
  });
});
