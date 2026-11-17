/**
 * `usePresentationSession` consolidates recording bookkeeping that used to
 * be copy-pasted, nearly verbatim, into both `PresentationView` and
 * `OperatorView` — including the "was this position applied by the AI or
 * typed/clicked by a human" tagging that the recording sink and the
 * director both rely on to avoid looping a change back on itself. These
 * pin that logic now that it lives in one place.
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

const notifySlideChange = vi.fn();
const recordingOpts = vi.fn();
vi.mock("@/lib/recordingClient", () => ({
  useRecordingSession: (opts: unknown) => {
    recordingOpts(opts);
    return { notifySlideChange, updateSetlistContent: vi.fn() };
  },
}));

vi.mock("@/lib/tauri", () => ({
  markSetlistUsed: () => Promise.resolve(),
  getSetlist: () => Promise.resolve(null),
  addSetlistItem: () => Promise.resolve("item_new"),
}));

import { useLiftBlankOnMove, usePresentationSession } from "@/lib/presentation/usePresentationSession";

async function connected() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

function broadcast(kind: string, raw: unknown) {
  act(() => (handlers.get(kind) ?? []).forEach((h) => h(raw)));
}

function renderSession(itemIdx: number, slideIdx: number, jumpTo = vi.fn()) {
  return renderHook(
    (props: { itemIdx: number; slideIdx: number }) =>
      usePresentationSession({
        setlistId: "s1",
        recordingEnabled: true,
        itemIdx: props.itemIdx,
        slideIdx: props.slideIdx,
        jumpTo,
      }),
    { initialProps: { itemIdx, slideIdx } },
  );
}

describe("usePresentationSession with recording off", () => {
  // Recording used to be the AI's on switch: with "keep a recording" off, no
  // session started, so the AI never ran and its button vanished.
  it("still runs the AI session — it just doesn't keep a recording", async () => {
    recordingOpts.mockClear();
    notifySlideChange.mockClear();
    const { rerender } = renderHook(
      (props: { slideIdx: number }) =>
        usePresentationSession({
          setlistId: "s1",
          recordingEnabled: false,
          itemIdx: 0,
          slideIdx: props.slideIdx,
          jumpTo: vi.fn(),
        }),
      { initialProps: { slideIdx: 0 } },
    );
    await connected();
    expect(recordingOpts).toHaveBeenLastCalledWith(
      expect.objectContaining({ enabled: true, record: false }),
    );
    // The AI still hears about manual moves, or it would fight the operator.
    rerender({ slideIdx: 2 });
    expect(notifySlideChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ itemIdx: 0, slideIdx: 2, source: "manual" }),
    );
  });
});

describe("usePresentationSession recording notifications", () => {
  it("notifies once for the initial position", async () => {
    notifySlideChange.mockClear();
    renderSession(0, 0);
    await connected();
    expect(notifySlideChange).toHaveBeenCalledTimes(1);
    expect(notifySlideChange).toHaveBeenCalledWith({
      itemIdx: 0, slideIdx: 0, source: "manual",
    });
  });

  it("does not re-notify when the position is unchanged", async () => {
    notifySlideChange.mockClear();
    const { rerender } = renderSession(0, 0);
    await connected();
    notifySlideChange.mockClear();
    rerender({ itemIdx: 0, slideIdx: 0 });
    expect(notifySlideChange).not.toHaveBeenCalled();
  });

  it("tags a manual/typed move as source=manual", async () => {
    notifySlideChange.mockClear();
    const { rerender } = renderSession(0, 0);
    await connected();
    notifySlideChange.mockClear();
    rerender({ itemIdx: 0, slideIdx: 2 });
    expect(notifySlideChange).toHaveBeenCalledWith({
      itemIdx: 0, slideIdx: 2, source: "manual",
    });
  });

  it("tags a move that came from an ai decision as source=ai — the bug this pins", async () => {
    notifySlideChange.mockClear();
    const jumpTo = vi.fn();
    const { rerender } = renderSession(0, 0, jumpTo);
    await connected();
    notifySlideChange.mockClear();

    // "ai"-sourced decisions only apply in active mode (see
    // `shouldApplyDecision`) — the sidecar's live status, not just the
    // mount-time preference, per `useLiveAiMode`.
    broadcast("ai_status", {
      kind: "ai_status", mode: "active", state: "tracking",
      last_decision_t_seconds: null, last_override_t_seconds: null,
    });

    // The sidecar broadcasts an "ai" decision; the view applies it by
    // calling jumpTo (simulated here by re-rendering with that position,
    // since `usePresentationController` — not this hook — owns state).
    broadcast("slide_change", {
      kind: "slide_change", item_idx: 0, slide_idx: 3, confidence: 0.9, source: "ai",
    });
    expect(jumpTo).toHaveBeenCalledWith(0, 3);

    rerender({ itemIdx: 0, slideIdx: 3 });
    expect(notifySlideChange).toHaveBeenCalledWith({
      itemIdx: 0, slideIdx: 3, source: "ai",
    });
  });

  it("tags a voice decision as voice — neither the ai nor a hand on the keyboard", async () => {
    notifySlideChange.mockClear();
    const jumpTo = vi.fn();
    const { rerender } = renderSession(0, 0, jumpTo);
    await connected();
    notifySlideChange.mockClear();

    broadcast("slide_change", {
      kind: "slide_change", item_idx: 0, slide_idx: 5, confidence: 1.0, source: "voice",
    });
    expect(jumpTo).toHaveBeenCalledWith(0, 5);

    rerender({ itemIdx: 0, slideIdx: 5 });
    expect(notifySlideChange).toHaveBeenCalledWith({
      itemIdx: 0, slideIdx: 5, source: "voice",
    });
  });
});

describe("usePresentationSession continuous mode", () => {
  // Blank lead slide, then three 10-word slides: global words 0..30.
  const ten = Array.from({ length: 10 }, (_, i) => `w${i}`);
  const slides = [{ words: [] }, { words: ten }, { words: ten }, { words: ten }];

  function renderContinuous(jumpTo = vi.fn()) {
    return renderHook(
      (props: { itemIdx: number; slideIdx: number }) =>
        usePresentationSession({
          setlistId: "s1",
          recordingEnabled: true,
          itemIdx: props.itemIdx,
          slideIdx: props.slideIdx,
          jumpTo,
          displayPref: "auto",
          slidesFor: (i) => (i === 0 ? slides : undefined),
          // As the operator view starts: off until the operator turns it on.
          initialAiMode: "off",
        }),
      { initialProps: { itemIdx: 0, slideIdx: 1 } },
    );
  }

  const active = {
    kind: "ai_status", mode: "active", state: "tracking",
    last_decision_t_seconds: null, last_override_t_seconds: null,
  };
  const flow = (word: number, item_idx = 0) => ({
    kind: "flow_position", item_idx, word, rate_wps: 0, limit: 30,
  });

  it("stays on slides, and ignores tracks, until the AI is active", async () => {
    const jumpTo = vi.fn();
    const { result } = renderContinuous(jumpTo);
    await connected();
    expect(result.current.displayMode).toBe("slide");
    broadcast("flow_position", flow(15));
    expect(result.current.flowAnchor.kind).toBe("slide");
    expect(jumpTo).not.toHaveBeenCalled();
  });

  it("follows the track and keeps the slide position in step, tagged as the AI's", async () => {
    notifySlideChange.mockClear();
    const jumpTo = vi.fn();
    const { result, rerender } = renderContinuous(jumpTo);
    await connected();
    broadcast("ai_status", active);
    expect(result.current.displayMode).toBe("continuous");

    broadcast("flow_position", flow(15));
    expect(result.current.flowAnchor).toMatchObject({ kind: "track", track: { word: 15 } });
    expect(jumpTo).toHaveBeenCalledWith(0, 2);

    notifySlideChange.mockClear();
    rerender({ itemIdx: 0, slideIdx: 2 });
    // The track moved the slide: it keeps following, and it was the AI.
    expect(result.current.flowAnchor.kind).toBe("track");
    expect(notifySlideChange).toHaveBeenCalledWith({ itemIdx: 0, slideIdx: 2, source: "ai" });
  });

  it("drops the slide engine's switches, which would fight the track", async () => {
    const jumpTo = vi.fn();
    renderContinuous(jumpTo);
    await connected();
    broadcast("ai_status", active);
    broadcast("slide_change", {
      kind: "slide_change", item_idx: 0, slide_idx: 3, confidence: 0.9, source: "ai",
    });
    expect(jumpTo).not.toHaveBeenCalled();
  });

  it("a move the track did not make re-anchors the column on that slide", async () => {
    const { result, rerender } = renderContinuous();
    await connected();
    broadcast("ai_status", active);
    broadcast("flow_position", flow(15));
    rerender({ itemIdx: 0, slideIdx: 2 });
    rerender({ itemIdx: 0, slideIdx: 3 }); // arrow key
    expect(result.current.flowAnchor).toEqual({ kind: "slide", itemIdx: 0, slideIdx: 3 });
  });

  it("ignores a track for a song this view does not have", async () => {
    const jumpTo = vi.fn();
    const { result } = renderContinuous(jumpTo);
    await connected();
    broadcast("ai_status", active);
    broadcast("flow_position", flow(5, 7));
    expect(result.current.flowAnchor.kind).toBe("slide");
    expect(jumpTo).not.toHaveBeenCalled();
  });
});

describe("useLiftBlankOnMove", () => {
  const fakeActions = () => ({
    nextSlide: vi.fn(),
    prevSlide: vi.fn(),
    nextSong: vi.fn(),
    prevSong: vi.fn(),
    jumpTo: vi.fn(),
    toggleBlank: vi.fn(),
    reset: vi.fn(),
  });

  it("lifts the blank screen it's under before moving", () => {
    const actions = fakeActions();
    const { result } = renderHook(() => useLiftBlankOnMove(actions, "black"));
    result.current.nextSlide();
    expect(actions.toggleBlank).toHaveBeenCalledWith("black");
    expect(actions.nextSlide).toHaveBeenCalledTimes(1);
  });

  it("lifts a white blank the same way, on every kind of move", () => {
    const actions = fakeActions();
    const { result } = renderHook(() => useLiftBlankOnMove(actions, "white"));
    result.current.prevSlide();
    result.current.nextSong();
    result.current.prevSong();
    result.current.jumpTo(1, 2);
    expect(actions.toggleBlank).toHaveBeenCalledTimes(4);
    expect(actions.toggleBlank).toHaveBeenCalledWith("white");
    expect(actions.jumpTo).toHaveBeenCalledWith(1, 2);
  });

  it("doesn't touch the blank when nothing is blanked", () => {
    const actions = fakeActions();
    const { result } = renderHook(() => useLiftBlankOnMove(actions, "none"));
    result.current.nextSlide();
    expect(actions.toggleBlank).not.toHaveBeenCalled();
    expect(actions.nextSlide).toHaveBeenCalledTimes(1);
  });

  it("reads the latest blank state, not the one it was built with", () => {
    const actions = fakeActions();
    const { result, rerender } = renderHook(
      ({ blank }: { blank: "none" | "black" }) => useLiftBlankOnMove(actions, blank),
      { initialProps: { blank: "none" } },
    );
    const before = result.current;
    rerender({ blank: "black" });
    // Same object (memoised for the grid's memo'd blocks), current state.
    expect(result.current).toBe(before);
    result.current.nextSlide();
    expect(actions.toggleBlank).toHaveBeenCalledWith("black");
  });
});
