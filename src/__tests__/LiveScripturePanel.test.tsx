/**
 * The operator's Scripture tab: the translation picker is for this service,
 * starts at the Settings default, and lookups follow it.
 */

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { requestSidecar, conn } = vi.hoisted(() => ({
  requestSidecar: vi.fn(),
  conn: { status: "open", onMessage: () => () => {}, onStatus: () => () => {} },
}));
vi.mock("@/lib/sidecarClient", () => ({ requestSidecar }));
vi.mock("@/lib/sidecarConnection", () => ({ getSharedConnection: () => Promise.resolve(conn) }));
const { updateSettings } = vi.hoisted(() => ({ updateSettings: vi.fn(async (s: unknown) => s) }));
vi.mock("@/lib/tauri", () => ({ getSettings: () => Promise.resolve({ scripture: {} }), updateSettings }));

import { LiveScripturePanel } from "@/components/scripture/LiveScripturePanel";
import { setSessionTranslation } from "@/lib/scripture/liveScripture";

const TRANSLATIONS = [
  { id: "web", name: "World English Bible", abbreviation: "WEB", attribution: "", installed: true },
  { id: "kjv", name: "King James Version", abbreviation: "KJV", attribution: "", installed: true },
  { id: "asv", name: "American Standard Version", abbreviation: "ASV", attribution: "", installed: false },
];

describe("LiveScripturePanel", () => {
  beforeEach(() => {
    act(() => setSessionTranslation(null));
    requestSidecar.mockReset().mockImplementation(async (msg: { kind: string }) => {
      if (msg.kind === "list_scripture_translations") {
        return { translations: TRANSLATIONS, default_translation: "web" };
      }
      if (msg.kind === "resolve_passages") return { passages: [], errors: {}, resolved: {} };
      if (msg.kind === "search_scripture") return { passages: [] };
      throw new Error(msg.kind);
    });
  });

  it("offers the installed translations, starting at the default", async () => {
    render(<LiveScripturePanel translation="web" />);
    const picker = (await screen.findByLabelText("Translation")) as HTMLSelectElement;
    expect(picker.value).toBe("web");
    expect([...picker.options].map((o) => o.textContent)).toEqual(["WEB (default)", "KJV"]);
  });

  it("looks passages up in the translation picked for this service", async () => {
    render(<LiveScripturePanel translation="web" />);
    fireEvent.change(await screen.findByLabelText("Translation"), { target: { value: "kjv" } });
    fireEvent.change(screen.getByTestId("live-scripture-input"), { target: { value: "john 3:16" } });
    await waitFor(() =>
      expect(requestSidecar).toHaveBeenCalledWith(
        expect.objectContaining({ kind: "resolve_passages", translation: "kjv" }),
      ),
    );
  });

  it("with nothing installed, offers the KJV by name and makes it the default", async () => {
    const none = TRANSLATIONS.map((t) => ({ ...t, installed: false }));
    let kjvInstalled = false;
    requestSidecar.mockImplementation(async (msg: { kind: string; translation?: string }) => {
      if (msg.kind === "list_scripture_translations") {
        const kjv = { ...none.find((t) => t.id === "kjv")!, installed: kjvInstalled };
        return { translations: [...none.filter((t) => t.id !== "kjv"), kjv], default_translation: "web" };
      }
      if (msg.kind === "install_scripture_translation") {
        kjvInstalled = true;
        return { installed: true, translation: msg.translation };
      }
      throw new Error(msg.kind);
    });
    render(<LiveScripturePanel translation="web" />);
    fireEvent.click(await screen.findByRole("button", { name: "Download KJV" }));
    await waitFor(() =>
      expect(requestSidecar).toHaveBeenCalledWith(
        expect.objectContaining({ kind: "install_scripture_translation", translation: "kjv" }),
        expect.anything(),
      ),
    );
    await waitFor(() =>
      expect(updateSettings).toHaveBeenCalledWith(
        expect.objectContaining({ scripture: expect.objectContaining({ translation: "kjv" }) }),
      ),
    );
    await waitFor(() => expect(screen.queryByRole("button", { name: "Download KJV" })).toBeNull());
    // Live, there's no way off to Settings: it would close the operator view.
    expect(screen.queryByRole("button", { name: "See all translations" })).toBeNull();
  });

  it("before the service, See all opens Settings → Scripture", async () => {
    requestSidecar.mockImplementation(async (msg: { kind: string }) => {
      if (msg.kind === "list_scripture_translations") {
        return { translations: TRANSLATIONS.map((t) => ({ ...t, installed: false })), default_translation: "web" };
      }
      throw new Error(msg.kind);
    });
    const opened = vi.fn();
    window.addEventListener("auto-presenter:open-settings", opened);
    render(<LiveScripturePanel translation="web" onAdd={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "See all translations" }));
    expect((opened.mock.calls[0]?.[0] as CustomEvent).detail).toEqual({ page: "scripture" });
    window.removeEventListener("auto-presenter:open-settings", opened);
  });

  it("says the engine isn't answering instead of claiming nothing is installed", async () => {
    requestSidecar.mockRejectedValue(new Error("timed out"));
    render(<LiveScripturePanel translation="web" />);
    expect(await screen.findByText(/engine isn't answering/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Download KJV" })).toBeNull();
  });
});
