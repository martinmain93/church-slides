/**
 * A verse put on screen now — typed by the operator or asked for out loud —
 * and the rules for when it comes down.
 */

import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Hoisted: the live store subscribes to the sidecar at module scope.
const { handlers, conn, requestSidecar } = vi.hoisted(() => {
  const handlers = new Map<string, ((raw: unknown) => void)[]>();
  return {
    handlers,
    conn: {
      status: "open",
      onMessage: (kind: string, cb: (raw: unknown) => void) => {
        handlers.set(kind, [...(handlers.get(kind) ?? []), cb]);
        return () => {};
      },
      onStatus: () => () => {},
      notify: vi.fn(),
    },
    requestSidecar: vi.fn(),
  };
});

vi.mock("@/lib/sidecarConnection", () => ({
  getSharedConnection: () => Promise.resolve(conn),
}));
vi.mock("@/lib/sidecarClient", () => ({ requestSidecar }));
vi.mock("@/lib/tauri", () => ({
  getSettings: () => Promise.resolve({ scripture: { translation: "kjv" } }),
}));

import type { PresentationActions } from "@/lib/presentation/hook";
import { resolvePassages } from "@/lib/scripture/client";
import {
  clearLiveScripture,
  setSessionTranslation,
  showLiveScripture,
  useActiveTranslation,
  useHumanActions,
  useLiveScripture,
  useScriptureOnScreen,
} from "@/lib/scripture/liveScripture";
import { DEFAULT_SCRIPTURE_SETTINGS } from "@/lib/scripture/settings";
import type { ScripturePassage } from "@/types/generated";

const JER: ScripturePassage = {
  reference: "Jeremiah 29:11",
  translation: "kjv",
  verses: [{ ref: "Jeremiah 29:11", text: "For I know the thoughts…" }],
  text: "For I know the thoughts…",
  attribution: "",
};

async function settle() {
  await act(async () => {
    for (let i = 0; i < 6; i++) await Promise.resolve();
  });
}

function broadcast(kind: string, raw: unknown) {
  act(() => (handlers.get(kind) ?? []).forEach((h) => h(raw)));
}

function fakeActions(): PresentationActions {
  return {
    nextSlide: vi.fn(),
    prevSlide: vi.fn(),
    nextSong: vi.fn(),
    prevSong: vi.fn(),
    jumpTo: vi.fn(),
    toggleBlank: vi.fn(),
    reset: vi.fn(),
  };
}

beforeEach(async () => {
  await settle();
  act(() => clearLiveScripture());
  requestSidecar.mockReset();
});

describe("resolvePassages", () => {
  it("finds a passage by the reference as typed, not only its canonical form", async () => {
    requestSidecar.mockResolvedValue({
      passages: [{ ...JER, reference: "Psalm 103:1–2" }],
      errors: {},
      resolved: { "ps 103:1-2": "Psalm 103:1–2" },
    });
    const out = await resolvePassages(["ps 103:1-2"]);
    expect(out.byReference.get("ps 103:1-2")?.reference).toBe("Psalm 103:1–2");
    expect(out.byReference.get("Psalm 103:1–2")).toBeDefined();
  });
});

describe("on screen", () => {
  it("shows the live passage when one is up", () => {
    const { result } = renderHook(() => useScriptureOnScreen(DEFAULT_SCRIPTURE_SETTINGS));
    expect(result.current.passage).toBeNull();
    act(() => showLiveScripture(JER, "full"));
    expect(result.current.passage).toBe(JER);
    expect(result.current.fullscreen).toBe(true);
  });

  it("a passage left up does not open the next service", () => {
    const first = renderHook(() => useScriptureOnScreen(DEFAULT_SCRIPTURE_SETTINGS));
    act(() => showLiveScripture(JER, "beside"));
    first.unmount();
    const next = renderHook(() => useScriptureOnScreen(DEFAULT_SCRIPTURE_SETTINGS));
    expect(next.result.current.passage).toBeNull();
  });

  it("a person moving the slide takes a full-screen passage down", () => {
    const actions = fakeActions();
    const { result } = renderHook(() => ({
      live: useLiveScripture(),
      human: useHumanActions(actions),
    }));
    act(() => showLiveScripture(JER, "full"));
    act(() => result.current.human.jumpTo(1, 2));
    expect(actions.jumpTo).toHaveBeenCalledWith(1, 2);
    expect(result.current.live).toBeNull();
  });

  it("but a passage beside the lyrics stays through slide changes", () => {
    const actions = fakeActions();
    const { result } = renderHook(() => ({
      live: useLiveScripture(),
      human: useHumanActions(actions),
    }));
    act(() => showLiveScripture(JER, "beside"));
    act(() => result.current.human.nextSlide());
    expect(result.current.live?.passage).toBe(JER);
  });
});

describe("the engine is told what is up", () => {
  it("so a spoken request can be relative to it", async () => {
    conn.notify.mockClear();
    act(() => showLiveScripture(JER, "full"));
    await settle();
    expect(conn.notify).toHaveBeenLastCalledWith({
      kind: "live_scripture", reference: "Jeremiah 29:11", display: "full",
    });
    act(() => clearLiveScripture());
    await settle();
    expect(conn.notify).toHaveBeenLastCalledWith({
      kind: "live_scripture", reference: "", display: "off",
    });
  });
});

describe("spoken requests", () => {
  it("resolve in the operator's translation and go up full screen", async () => {
    requestSidecar.mockResolvedValue({
      passages: [JER],
      errors: {},
      resolved: { "Jeremiah 29:11": "Jeremiah 29:11" },
    });
    const { result } = renderHook(() => useLiveScripture());
    broadcast("show_scripture", { reference: "Jeremiah 29:11", display: "full" });
    await settle();
    expect(requestSidecar).toHaveBeenCalledWith(
      expect.objectContaining({ kind: "resolve_passages", translation: "kjv" }),
    );
    expect(result.current).toMatchObject({ display: "full", source: "voice" });
    expect(result.current?.passage.reference).toBe("Jeremiah 29:11");
  });

  it("a named translation is used, and stays the service's translation", async () => {
    requestSidecar.mockResolvedValue({
      passages: [JER],
      errors: {},
      resolved: { "Jeremiah 29:11": "Jeremiah 29:11" },
    });
    const { result } = renderHook(() => useActiveTranslation("kjv"));
    broadcast("show_scripture", {
      reference: "Jeremiah 29:11", display: "full", translation: "web",
    });
    await settle();
    expect(requestSidecar).toHaveBeenLastCalledWith(
      expect.objectContaining({ kind: "resolve_passages", translation: "web" }),
    );
    expect(result.current).toBe("web");
    // The next request, with no translation named, keeps reading from it.
    broadcast("show_scripture", { reference: "Jeremiah 29:11", display: "full" });
    await settle();
    expect(requestSidecar).toHaveBeenLastCalledWith(
      expect.objectContaining({ kind: "resolve_passages", translation: "web" }),
    );
    act(() => setSessionTranslation(null));
    expect(result.current).toBe("kjv");
  });

  it("'off' takes it down", () => {
    const { result } = renderHook(() => useLiveScripture());
    act(() => showLiveScripture(JER, "beside"));
    broadcast("show_scripture", { reference: "", display: "off" });
    expect(result.current).toBeNull();
  });

  it("a spoken slide move takes a full-screen passage down", () => {
    const { result } = renderHook(() => useLiveScripture());
    act(() => showLiveScripture(JER, "full"));
    broadcast("spoken_instruction", {
      t_seconds: 1, utterance: "go to the chorus", applied: true, item_idx: 0, slide_idx: 2,
    });
    expect(result.current).toBeNull();
  });
});
