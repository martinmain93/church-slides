/**
 * An AI jump to another song waits for the band leader's OK when nobody is
 * at the laptop (`jumpHold.ts`): the rule for which jumps wait, the
 * countdown, the leader's answer, and the session applying (or not) the move.
 */

import { act, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
const updateSetlistContent = vi.fn();
vi.mock("@/lib/recordingClient", () => ({
  useRecordingSession: () => ({ notifySlideChange, updateSetlistContent }),
}));

const addSetlistItem = vi.fn(() => Promise.resolve("item_new"));
vi.mock("@/lib/tauri", () => ({
  markSetlistUsed: () => Promise.resolve(),
  getSetlist: () => Promise.resolve(null),
  addSetlistItem: (...a: unknown[]) => addSetlistItem(...(a as [])),
}));

import { JumpHoldBar } from "@/components/band/JumpHoldBar";
import { isBandCommand } from "@/lib/band/protocol";
import {
  heardLine,
  isSurprisingSongJump,
  JUMP_HOLD_MS,
  useJumpHold,
} from "@/lib/presentation/jumpHold";
import { usePresentationSession } from "@/lib/presentation/usePresentationSession";

function broadcast(kind: string, raw: unknown) {
  act(() => (handlers.get(kind) ?? []).forEach((h) => h(raw)));
}

describe("which AI moves wait", () => {
  it("a move inside the song doesn't", () => {
    expect(isSurprisingSongJump({ itemIdx: 1, slideIdx: 2 }, 1, 6)).toBe(false);
  });
  it("the next song from the last slide doesn't — that's the set going on", () => {
    expect(isSurprisingSongJump({ itemIdx: 1, slideIdx: 5 }, 2, 6)).toBe(false);
  });
  it("the next song mid-song does", () => {
    expect(isSurprisingSongJump({ itemIdx: 1, slideIdx: 3 }, 2, 6)).toBe(true);
  });
  it("any other song does, even from the last slide", () => {
    expect(isSurprisingSongJump({ itemIdx: 1, slideIdx: 5 }, 3, 6)).toBe(true);
    expect(isSurprisingSongJump({ itemIdx: 1, slideIdx: 5 }, 0, 6)).toBe(true);
  });
  it("the leader sees the last few words heard", () => {
    expect(heardLine(["a", "b", " ", "c", "d", "e", "f", "g", "h"], 4)).toBe("e f g h");
  });
});

describe("useJumpHold", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  const req = (over: Partial<Parameters<ReturnType<typeof useJumpHold>["offer"]>[0]> = {}) => ({
    key: "item:2",
    title: "Amazing Grace",
    words: "how sweet the sound",
    go: vi.fn(),
    stop: vi.fn(),
    ...over,
  });

  it("with no leader's screen, nothing waits", () => {
    const { result } = renderHook(() => useJumpHold());
    expect(result.current.offer(req())).toBe(false);
    expect(result.current.notice).toBeNull();
  });

  it("goes when the countdown ends", () => {
    const { result } = renderHook(() => useJumpHold());
    act(() => result.current.setLeaders(1));
    const r = req();
    act(() => void result.current.offer(r));
    expect(result.current.notice).toMatchObject({ title: "Amazing Grace", words: "how sweet the sound", durationMs: JUMP_HOLD_MS });
    act(() => vi.advanceTimersByTime(JUMP_HOLD_MS - 1));
    expect(r.go).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(1));
    expect(r.go).toHaveBeenCalledTimes(1);
    expect(result.current.notice).toBeNull();
  });

  it("Stop cancels it; a stale answer does nothing", () => {
    const { result } = renderHook(() => useJumpHold());
    act(() => result.current.setLeaders(1));
    const r = req();
    act(() => void result.current.offer(r));
    const id = result.current.notice!.id;
    act(() => result.current.answer(id + 99, true));
    expect(r.go).not.toHaveBeenCalled();
    act(() => result.current.answer(id, false));
    expect(r.stop).toHaveBeenCalledTimes(1);
    act(() => vi.advanceTimersByTime(JUMP_HOLD_MS * 2));
    expect(r.go).not.toHaveBeenCalled();
  });

  it("Go now goes at once, to the latest slide the AI picked in that song", () => {
    const { result } = renderHook(() => useJumpHold());
    act(() => result.current.setLeaders(1));
    const first = req();
    const later = req();
    act(() => void result.current.offer(first));
    const id = result.current.notice!.id;
    act(() => void result.current.offer(later)); // same song, a slide on
    expect(result.current.notice!.id).toBe(id); // the countdown carries on
    act(() => result.current.answer(id, true));
    expect(later.go).toHaveBeenCalledTimes(1);
    expect(first.go).not.toHaveBeenCalled();
  });

  it("the leader's screen dropping lets the jump go at once", () => {
    const { result } = renderHook(() => useJumpHold());
    act(() => result.current.setLeaders(1));
    const r = req();
    act(() => void result.current.offer(r));
    act(() => result.current.setLeaders(0));
    expect(r.go).toHaveBeenCalledTimes(1);
    expect(result.current.notice).toBeNull();
  });
});

describe("the session holds AI jumps to another song", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    notifySlideChange.mockClear();
    updateSetlistContent.mockClear();
    addSetlistItem.mockClear();
    handlers.clear();
  });
  afterEach(() => vi.useRealTimers());

  const songs = [
    { title: "Holy, Holy, Holy", slideCount: 6 },
    { title: "Be Thou My Vision", slideCount: 6 },
    { title: "Amazing Grace", slideCount: 6 },
  ];

  async function setup(leaders: number, at = { itemIdx: 0, slideIdx: 2 }) {
    const jumpTo = vi.fn();
    const hook = renderHook(
      (props: { itemIdx: number; slideIdx: number }) => {
        const jumpHold = useJumpHold();
        usePresentationSession({
          setlistId: "s1",
          setlistContent: { items: [] },
          recordingEnabled: false,
          itemIdx: props.itemIdx,
          slideIdx: props.slideIdx,
          jumpTo,
          itemCount: songs.length,
          jumpHold,
          songFor: (i) => songs[i],
        });
        return jumpHold;
      },
      { initialProps: at },
    );
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    act(() => hook.result.current.setLeaders(leaders));
    broadcast("ai_status", {
      kind: "ai_status", mode: "active", state: "tracking",
      last_decision_t_seconds: null, last_override_t_seconds: null,
    });
    notifySlideChange.mockClear();
    return { ...hook, jumpTo };
  }

  const aiMove = (item_idx: number, slide_idx: number) =>
    broadcast("slide_change", { kind: "slide_change", item_idx, slide_idx, confidence: 0.9, source: "ai" });

  it("waits, shows the song and the words heard, then goes as the AI's move", async () => {
    const { result, jumpTo, rerender } = await setup(1);
    broadcast("transcript_partial", {
      kind: "transcript_partial", is_final: true,
      words: ["amazing", "grace", "how", "sweet", "the", "sound"].map((text) => ({ text, t_start: 0, t_end: 0, confidence: 1 })),
    });
    aiMove(2, 1);
    expect(jumpTo).not.toHaveBeenCalled();
    expect(result.current.notice).toMatchObject({ title: "Amazing Grace", words: "amazing grace how sweet the sound" });
    act(() => vi.advanceTimersByTime(JUMP_HOLD_MS));
    expect(jumpTo).toHaveBeenCalledWith(2, 1);
    rerender({ itemIdx: 2, slideIdx: 1 });
    expect(notifySlideChange).toHaveBeenCalledWith({ itemIdx: 2, slideIdx: 1, source: "ai" });
  });

  it("Stop keeps the screen and re-anchors the AI where it is", async () => {
    const { result, jumpTo } = await setup(1);
    aiMove(2, 1);
    act(() => result.current.answer(result.current.notice!.id, false));
    act(() => vi.advanceTimersByTime(JUMP_HOLD_MS * 2));
    expect(jumpTo).not.toHaveBeenCalled();
    expect(notifySlideChange).toHaveBeenCalledWith({ itemIdx: 0, slideIdx: 2, source: "manual" });
  });

  it("the next song from the last slide goes straight away", async () => {
    const { result, jumpTo } = await setup(1, { itemIdx: 0, slideIdx: 5 });
    aiMove(1, 1);
    expect(jumpTo).toHaveBeenCalledWith(1, 1);
    expect(result.current.notice).toBeNull();
  });

  it("with no leader's screen, a jump goes straight away", async () => {
    const { jumpTo } = await setup(0);
    aiMove(2, 1);
    expect(jumpTo).toHaveBeenCalledWith(2, 1);
  });

  it("a person moving the screen meanwhile drops the waiting jump", async () => {
    const { result, jumpTo, rerender } = await setup(1);
    aiMove(2, 1);
    rerender({ itemIdx: 0, slideIdx: 3 });
    expect(result.current.notice).toBeNull();
    act(() => vi.advanceTimersByTime(JUMP_HOLD_MS * 2));
    expect(jumpTo).not.toHaveBeenCalled();
  });

  it("a library song waits too; Stop doesn't add it and resyncs the AI's set", async () => {
    const { result } = await setup(1);
    broadcast("import_library_song", {
      kind: "import_library_song", song_id: "lib1", title: "Rock of Ages", item_idx: 3, slide_idx: 1,
      section_key: "v1", score: 0.9, runner_up_score: 0.1, query: "rock of ages cleft for me let me hide myself",
      reason: "spontaneous", source: "ai",
    });
    expect(result.current.notice).toMatchObject({ title: "Rock of Ages", words: "for me let me hide myself" });
    act(() => result.current.answer(result.current.notice!.id, false));
    await act(async () => {
      await Promise.resolve();
    });
    expect(addSetlistItem).not.toHaveBeenCalled();
    expect(updateSetlistContent).toHaveBeenCalledWith({ items: [] }, 0);
  });

  it("a spoken request for a song never waits", async () => {
    const { result } = await setup(1);
    broadcast("import_library_song", {
      kind: "import_library_song", song_id: "lib1", title: "Rock of Ages", item_idx: 3, slide_idx: 1,
      section_key: "v1", score: 0.9, runner_up_score: 0.1, query: "rock of ages",
      reason: "spoken", source: "voice",
    });
    expect(result.current.notice).toBeNull();
    await act(async () => {
      await Promise.resolve();
    });
    expect(addSetlistItem).toHaveBeenCalled();
  });
});

describe("in continuous mode (the default with the AI on)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    notifySlideChange.mockClear();
    handlers.clear();
  });
  afterEach(() => vi.useRealTimers());

  const slides = [{ words: [] }, { words: ["a", "b", "c", "d"] }, { words: ["e", "f", "g", "h"] }];

  async function setup() {
    const jumpTo = vi.fn();
    const hook = renderHook(
      (props: { itemIdx: number; slideIdx: number }) => {
        const jumpHold = useJumpHold();
        const session = usePresentationSession({
          setlistId: "s1",
          recordingEnabled: false,
          itemIdx: props.itemIdx,
          slideIdx: props.slideIdx,
          jumpTo,
          itemCount: 3,
          displayPref: "auto",
          slidesFor: () => slides,
          jumpHold,
          songFor: (i) => ({ title: ["Holy, Holy, Holy", "Be Thou My Vision", "Amazing Grace"][i]!, slideCount: 3 }),
        });
        return { jumpHold, session };
      },
      { initialProps: { itemIdx: 0, slideIdx: 1 } },
    );
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    act(() => hook.result.current.jumpHold.setLeaders(1));
    broadcast("ai_status", {
      kind: "ai_status", mode: "active", state: "tracking",
      last_decision_t_seconds: null, last_override_t_seconds: null,
    });
    return { ...hook, jumpTo };
  }

  const track = (item_idx: number, word: number) =>
    broadcast("flow_position", { kind: "flow_position", item_idx, word, rate_wps: 0, limit: 8, jump: true, confidence: 0.9 });

  it("a track into another song waits for the leader, then follows it", async () => {
    const { result, jumpTo } = await setup();
    expect(result.current.session.displayMode).toBe("continuous");
    track(2, 1);
    expect(jumpTo).not.toHaveBeenCalled();
    expect(result.current.jumpHold.notice?.title).toBe("Amazing Grace");
    track(2, 5); // the singing went on meanwhile
    act(() => vi.advanceTimersByTime(JUMP_HOLD_MS));
    expect(jumpTo).toHaveBeenLastCalledWith(2, 2);
  });

  it("the song on screen carries on while a jump waits", async () => {
    const { result, jumpTo, rerender } = await setup();
    track(2, 1);
    track(0, 5); // the AI came back to the song on screen
    expect(result.current.jumpHold.notice).toBeNull();
    expect(jumpTo).toHaveBeenLastCalledWith(0, 2);
    rerender({ itemIdx: 0, slideIdx: 2 });
    act(() => vi.advanceTimersByTime(JUMP_HOLD_MS * 2));
    expect(jumpTo).not.toHaveBeenCalledWith(2, expect.anything());
  });

  it("Stop keeps the song on screen", async () => {
    const { result, jumpTo } = await setup();
    track(2, 1);
    act(() => result.current.jumpHold.answer(result.current.jumpHold.notice!.id, false));
    act(() => vi.advanceTimersByTime(JUMP_HOLD_MS * 2));
    expect(jumpTo).not.toHaveBeenCalled();
    expect(notifySlideChange).toHaveBeenLastCalledWith({ itemIdx: 0, slideIdx: 1, source: "manual" });
  });
});

describe("the leader's bar", () => {
  it("says where it's going, and answers", () => {
    const onAnswer = vi.fn();
    render(<JumpHoldBar hold={{ id: 1, title: "Amazing Grace", words: "how sweet the sound", durationMs: 3000 }} onAnswer={onAnswer} />);
    expect(screen.getByText("Going to Amazing Grace")).toBeTruthy();
    expect(screen.getByText(/how sweet the sound/)).toBeTruthy();
    fireEvent.click(screen.getByText("Stop"));
    fireEvent.click(screen.getByText("Go now"));
    expect(onAnswer.mock.calls).toEqual([[false], [true]]);
  });

  it("its answer is a valid leader command", () => {
    expect(isBandCommand({ kind: "jump_answer", id: 3, go: true })).toBe(true);
    expect(isBandCommand({ kind: "jump_answer", id: "3", go: true })).toBe(false);
  });
});
