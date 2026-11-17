/**
 * The debug panel renders its tabs conditionally, so switching tabs unmounts
 * one. With per-mount `useState` the returning tab showed nothing until the
 * next broadcast — three to five seconds mid-song — which looked exactly like
 * the tracker losing its place. These tests pin the fix: the value lives in
 * module scope, and a remount reads it back immediately.
 */

import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Hoisted, because the stores subscribe at *module* scope: the mock is
// consulted while `@/lib/belief` is being imported, which is before an ordinary
// top-level `const` in this file has been initialised.
const { handlers, conn } = vi.hoisted(() => {
  // A list per kind, not one callback: the real connection fans out to every
  // subscriber, and more than one store can feed off the same message.
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

import { createLiveStore, useLiveStore } from "@/lib/liveStore";
import { useBeliefState } from "@/lib/belief";

/** Let `pipeFromSidecar`'s awaited connection settle. */
async function connected() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

function broadcast(kind: string, raw: unknown) {
  act(() => (handlers.get(kind) ?? []).forEach((h) => h(raw)));
}

const BELIEF = {
  top_3: [{ song_id: "2", slide_idx: 3, marginal: 0.9 }],
  ess: 4.2,
  is_lost: false,
};

describe("useLiveStore", () => {
  it("seeds a fresh mount with the value the store already holds", () => {
    const store = createLiveStore(7);
    store.set(9);
    const { result } = renderHook(() => useLiveStore(store));
    expect(result.current).toBe(9);
  });

  it("re-renders on later writes", () => {
    const store = createLiveStore(0);
    const { result } = renderHook(() => useLiveStore(store));
    act(() => store.set(5));
    expect(result.current).toBe(5);
  });
});

describe("useBeliefState across a tab switch", () => {
  beforeEach(connected);

  it("still has the last belief after unmount and remount", async () => {
    broadcast("belief_state", BELIEF);

    const first = renderHook(() => useBeliefState());
    expect(first.result.current.latest?.top3.at(0)?.slideIdx).toBe(3);

    first.unmount(); // the operator switches to the Transcript tab
    const second = renderHook(() => useBeliefState()); // ...and switches back

    expect(second.result.current.latest?.top3.at(0)?.slideIdx).toBe(3);
    expect(second.result.current.essHistory.at(0)).toBe(4.2);
  });

  it("keeps receiving broadcasts that arrive while no tab is mounted", async () => {
    renderHook(() => useBeliefState()).unmount();
    broadcast("belief_state", { ...BELIEF, ess: 1.5 });

    const { result } = renderHook(() => useBeliefState());
    await connected();
    expect(result.current.latest?.ess).toBe(1.5);
  });
});

describe("transcript words", () => {
  beforeEach(connected);

  it("clears the partial once its words arrive as final", async () => {
    const { useTranscriptStream } = await import("@/lib/transcriptStream");
    await connected(); // the module's pipes subscribe on import
    const { result } = renderHook(() => useTranscriptStream());

    broadcast("transcript_partial", {
      is_final: false,
      words: [{ text: "amazing", t_start: 0, t_end: 1 }],
    });
    expect(result.current.partialWords).toHaveLength(1);

    broadcast("transcript_partial", {
      is_final: true,
      words: [{ text: "amazing", t_start: 0, t_end: 1 }],
    });
    expect(result.current.partialWords).toHaveLength(0);
    expect(result.current.finalWords).toHaveLength(1);
  });

  it("keeps finals across a tab switch", async () => {
    const { useTranscriptStream } = await import("@/lib/transcriptStream");
    await connected();
    renderHook(() => useTranscriptStream()).unmount();
    broadcast("transcript_partial", {
      is_final: true,
      words: [{ text: "grace", t_start: 1, t_end: 2 }],
    });

    const { result } = renderHook(() => useTranscriptStream());
    await connected();
    expect(result.current.finalWords.at(-1)?.text).toBe("grace");
  });
});

describe("transcript layout", () => {
  beforeEach(connected);

  it("puts a break opportunity between words so a long transcript wraps", async () => {
    const { render, screen, act: rtlAct } = await import("@testing-library/react");
    const { DebugPanel } = await import("@/components/debug/DebugPanel");
    await connected();

    render(<DebugPanel />);
    // The panel opens on Cmd/Ctrl-D and defaults to the Transcript tab.
    rtlAct(() => {
      window.dispatchEvent(
        new KeyboardEvent("keydown", { key: "d", metaKey: true }),
      );
    });

    broadcast("transcript_partial", {
      is_final: true,
      words: "amazing grace how sweet the sound".split(" ").map((t, i) => ({
        text: t,
        t_start: i,
        t_end: i + 1,
      })),
    });

    // Adjacent inline boxes with no whitespace between them give the browser
    // nowhere to break the line, which is what pushed the live transcript off
    // the right edge and left only the first few words visible.
    const text = screen.getByTestId("final-words").textContent ?? "";
    expect(text).toContain("amazing grace");
    expect(text).not.toContain("amazinggrace");
  });
});

describe("transcript length", () => {
  beforeEach(connected);

  it("shows only the most recent words, not the whole session", async () => {
    const { render, screen, act: rtlAct } = await import("@testing-library/react");
    const { DebugPanel } = await import("@/components/debug/DebugPanel");
    await connected();

    render(<DebugPanel />);
    rtlAct(() => {
      window.dispatchEvent(
        new KeyboardEvent("keydown", { key: "d", metaKey: true }),
      );
    });

    // A song's worth of words, arriving the way they do live.
    for (let i = 0; i < 40; i++) {
      broadcast("transcript_partial", {
        is_final: true,
        words: [{ text: `w${i}`, t_start: i, t_end: i + 1 }],
      });
    }

    // The panel is for judging whether the words are any good, which is a
    // glance at the last line — a wall of scrollback buries what just arrived.
    const shown = screen.getByTestId("final-words").textContent ?? "";
    expect(shown).toContain("w39");
    expect(shown).not.toContain("w0 ");
    expect(shown.trim().split(/\s+/).length).toBeLessThanOrEqual(15);
  });
});

describe("singing state", () => {
  beforeEach(connected);

  it("assumes the screen is right until told otherwise", async () => {
    const { useSingingState, resetSingingState } =
      await import("@/lib/singingState");
    await connected();
    resetSingingState();
    const { result } = renderHook(() => useSingingState());
    // Blanking a service on a guess is worse than a stale slide, so the
    // default before any evidence is "carry on".
    expect(result.current.singing).toBe(true);
    expect(result.current.blanking).toBe(false);
  });

  it("distinguishes silence from hearing the wrong words", async () => {
    const { useSingingState, resetSingingState } =
      await import("@/lib/singingState");
    await connected();
    resetSingingState();
    const { result } = renderHook(() => useSingingState());

    broadcast("singing_state", {
      singing: false,
      reason: "silence",
      blanking: true,
    });
    expect(result.current.reason).toBe("silence");
    expect(result.current.blanking).toBe(true);

    broadcast("singing_state", {
      singing: false,
      reason: "not_these_words",
      blanking: false,
    });
    // Detected but not acted on — the toggle is off.
    expect(result.current.reason).toBe("not_these_words");
    expect(result.current.blanking).toBe(false);
  });

  it("keeps the toggle checked while enabled, even when not currently blanking", async () => {
    // Regression: the checkbox used to bind to `blanking` (is it blanking
    // *right now*) instead of `enabled` (is the feature on) — so checking
    // it while the tracker was correctly following the singing looked like
    // it "wouldn't check": `blanking` stays false until something actually
    // goes off-book, unchecking the box the instant singing resumed.
    const { useSingingState, resetSingingState, setBlankWhenIdle } =
      await import("@/lib/singingState");
    await connected();
    resetSingingState();
    const { result } = renderHook(() => useSingingState());

    await act(async () => {
      await setBlankWhenIdle(true);
    });
    expect(result.current.enabled).toBe(true);

    // A broadcast arrives while singing is on-book — blanking is correctly
    // false, but the toggle itself must stay on.
    broadcast("singing_state", {
      singing: true,
      reason: "singing",
      blanking: false,
    });
    expect(result.current.blanking).toBe(false);
    expect(result.current.enabled).toBe(true);
  });
});

describe("spoken instructions", () => {
  beforeEach(connected);

  it("shows nothing before any utterance has been reported", async () => {
    const { useSpokenInstructions, resetSpokenInstructions } =
      await import("@/lib/spokenInstructions");
    await connected();
    resetSpokenInstructions();
    const { result } = renderHook(() => useSpokenInstructions());
    expect(result.current.seen).toBe(false);
    expect(result.current.recent).toHaveLength(0);
  });

  it("keeps the newest utterance first and bounds the history", async () => {
    const { useSpokenInstructions, resetSpokenInstructions } =
      await import("@/lib/spokenInstructions");
    await connected();
    resetSpokenInstructions();
    const { result } = renderHook(() => useSpokenInstructions());
    for (let i = 0; i < 20; i++) {
      broadcast("spoken_instruction", {
        t_seconds: i,
        utterance: `utterance ${i}`,
        item_idx: null,
        slide_idx: null,
        applied: false,
      });
    }
    expect(result.current.recent.at(0)?.utterance).toBe("utterance 19");
    expect(result.current.recent.length).toBeLessThanOrEqual(15);
  });

  it("records what was applied, distinct from what was merely heard", async () => {
    const { useSpokenInstructions, resetSpokenInstructions } =
      await import("@/lib/spokenInstructions");
    await connected();
    resetSpokenInstructions();
    const { result } = renderHook(() => useSpokenInstructions());

    broadcast("spoken_instruction", {
      t_seconds: 12.5,
      utterance: "amen, come on somebody",
      item_idx: null,
      slide_idx: null,
      applied: false,
    });
    expect(result.current.recent[0]).toMatchObject({
      applied: false,
      itemIdx: null,
      slideIdx: null,
    });

    broadcast("spoken_instruction", {
      t_seconds: 20.0,
      utterance: "let's go back to verse one",
      item_idx: 0,
      slide_idx: 1,
      applied: true,
    });
    expect(result.current.recent[0]).toMatchObject({
      applied: true,
      itemIdx: 0,
      slideIdx: 1,
    });
  });
});
