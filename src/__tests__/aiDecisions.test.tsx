/**
 * `useAiDecisions` used to hard-filter `slide_change` broadcasts to
 * `source === "ai"` only. The sidecar has broadcast `source: "voice"` for
 * spoken instructions since they shipped (`protocol.SlideChangeMessage`),
 * but nothing on the frontend ever accepted it — a spoken command could be
 * heard, resolved to a slide, and broadcast, and the UI would silently
 * drop it on the floor. These pin the fix: both "ai" and "voice" pass
 * through with their source intact so callers can gate them differently
 * (see `PresentationView`/`OperatorView`, which apply "ai" only in active
 * mode but "voice" unconditionally).
 */

import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const { handlers, conn } = vi.hoisted(() => {
  const handlers = new Map<string, ((raw: unknown) => void)[]>();
  return {
    handlers,
    conn: {
      status: "open",
      onMessage: (kind: string, cb: (raw: unknown) => void) => {
        const list = handlers.get(kind) ?? [];
        list.push(cb);
        handlers.set(kind, list);
        return () => handlers.set(kind, list.filter((h) => h !== cb));
      },
      onStatus: () => () => {},
      request: async () => ({}),
    },
  };
});

vi.mock("@/lib/sidecarConnection", () => ({
  getSharedConnection: () => Promise.resolve(conn),
}));

import { shouldApplyDecision, useAiDecisions, useLiveAiMode, type AiDecision } from "@/lib/aiDecisions";

async function connected() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

function broadcast(kind: string, raw: unknown) {
  act(() => (handlers.get(kind) ?? []).forEach((h) => h(raw)));
}

describe("useAiDecisions", () => {
  it("passes an ai-sourced slide_change through with source intact", async () => {
    const seen: AiDecision[] = [];
    renderHook(() => useAiDecisions({ onDecision: (d) => seen.push(d) }));
    await connected();

    broadcast("slide_change", {
      kind: "slide_change",
      item_idx: 1,
      slide_idx: 2,
      confidence: 0.9,
      source: "ai",
    });

    expect(seen).toEqual([{ itemIdx: 1, slideIdx: 2, confidence: 0.9, source: "ai" }]);
  });

  it("passes a voice-sourced slide_change through — the bug this pins", async () => {
    const seen: AiDecision[] = [];
    renderHook(() => useAiDecisions({ onDecision: (d) => seen.push(d) }));
    await connected();

    broadcast("slide_change", {
      kind: "slide_change",
      item_idx: 0,
      slide_idx: 4,
      confidence: 1.0,
      source: "voice",
    });

    expect(seen).toEqual([{ itemIdx: 0, slideIdx: 4, confidence: 1.0, source: "voice" }]);
  });

  it("ignores manual and override broadcasts — those originate in this UI, not the sidecar", async () => {
    const seen: AiDecision[] = [];
    renderHook(() => useAiDecisions({ onDecision: (d) => seen.push(d) }));
    await connected();

    broadcast("slide_change", {
      kind: "slide_change", item_idx: 0, slide_idx: 1, confidence: 1.0, source: "manual",
    });
    broadcast("slide_change", {
      kind: "slide_change", item_idx: 0, slide_idx: 1, confidence: 1.0, source: "override",
    });

    expect(seen).toEqual([]);
  });
});

describe("shouldApplyDecision", () => {
  // The full source x mode matrix — this is the exact rule whose two
  // independently-drifted copies (in PresentationView and OperatorView)
  // caused a spoken command to silently do nothing in shadow mode.
  it.each([
    ["ai", "off", false],
    ["ai", "shadow", false],
    ["ai", "active", true],
    ["voice", "off", true],
    ["voice", "shadow", true],
    ["voice", "active", true],
  ] as const)("source=%s, mode=%s -> applies=%s", (source, mode, expected) => {
    expect(shouldApplyDecision({ source }, mode)).toBe(expected);
  });
});

describe("useLiveAiMode", () => {
  it("uses the initial snapshot until the sidecar's live status arrives", async () => {
    const { result } = renderHook(() => useLiveAiMode("shadow"));
    expect(result.current).toBe("shadow");
    await connected();
    expect(result.current).toBe("shadow");
  });

  it("picks up a live mode switch — the bug this pins", async () => {
    // AISettings lets the operator flip AI mode while PresentationView is
    // already mounted and fires `set_ai_mode` immediately; the sidecar
    // starts broadcasting "ai" decisions under the new mode right away.
    // A view gating on a mount-time snapshot of `loadAiPrefs().mode`
    // never saw this and kept silently refusing every one of them.
    const { result } = renderHook(() => useLiveAiMode("shadow"));
    await connected();
    expect(result.current).toBe("shadow");

    broadcast("ai_status", {
      kind: "ai_status",
      mode: "active",
      state: "tracking",
      last_decision_t_seconds: null,
      last_override_t_seconds: null,
    });

    expect(result.current).toBe("active");
  });

  it("does not re-render when a status repeats the same mode", async () => {
    // The director broadcasts `ai_status` on every manual move, each with a
    // fresh override timestamp. OperatorView calls this, so each one used to
    // re-render the whole slide grid a second time per keypress.
    let renders = 0;
    renderHook(() => {
      renders += 1;
      return useLiveAiMode("off");
    });
    await connected();
    const status = (t: number) => ({
      kind: "ai_status",
      mode: "active",
      state: "following_human",
      last_decision_t_seconds: null,
      last_override_t_seconds: t,
    });
    broadcast("ai_status", status(1));
    const afterFirst = renders;
    for (let t = 2; t <= 11; t++) broadcast("ai_status", status(t));
    // React may call the component once more right after an update before it
    // bails out (without rendering children); what must not happen is one
    // render per repeated status.
    expect(renders - afterFirst).toBeLessThanOrEqual(1);
  });
});
