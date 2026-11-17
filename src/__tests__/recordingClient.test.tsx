/**
 * `useRecordingsList`'s docstring claimed it "auto-refreshes when a
 * `recording_finalized` broadcast arrives" — the sidecar does broadcast it
 * (`server.py`, on every finalized recording), but nothing on the frontend
 * ever subscribed. The Settings recordings list only ever showed a
 * just-finished recording after a manual refresh or remount. This pins the
 * fix: a `recording_finalized` broadcast should trigger exactly the same
 * re-fetch a manual `refresh()` call does.
 */

import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const { handlers, conn, requestMock, notifyMock, restartHandlers } = vi.hoisted(() => {
  const handlers = new Map<string, ((raw: unknown) => void)[]>();
  const restartHandlers = new Set<() => void>();
  const notifyMock = vi.fn();
  const requestMock = vi.fn(async (payload?: { kind?: string }, _timeout?: number) => {
    if (payload?.kind === "recommend_model") {
      return {
        kind: "recommend_model_response",
        model_size: "small.en",
        backend: "mlx",
      };
    }
    return { recordings: [] };
  });
  return {
    handlers,
    requestMock,
    notifyMock,
    restartHandlers,
    conn: {
      status: "open",
      onMessage: (kind: string, cb: (raw: unknown) => void) => {
        const list = handlers.get(kind) ?? [];
        list.push(cb);
        handlers.set(kind, list);
        return () => handlers.set(kind, list.filter((h) => h !== cb));
      },
      onStatus: () => () => {},
      onEngineRestart: (cb: () => void) => {
        restartHandlers.add(cb);
        return () => restartHandlers.delete(cb);
      },
      request: requestMock,
      notify: notifyMock,
    },
  };
});

vi.mock("@/lib/sidecarConnection", () => ({
  getSharedConnection: () => Promise.resolve(conn),
}));

import {
  replayRecording,
  useRecordingSession,
  useRecordingsList,
  useSessionStartFailure,
} from "@/lib/recordingClient";

function broadcast(kind: string, raw: unknown) {
  act(() => (handlers.get(kind) ?? []).forEach((h) => h(raw)));
}

describe("useRecordingsList auto-refresh", () => {
  it("re-fetches when a recording_finalized broadcast arrives", async () => {
    requestMock.mockClear();
    renderHook(() => useRecordingsList());

    await waitFor(() => expect(requestMock).toHaveBeenCalledTimes(1));

    broadcast("recording_finalized", {
      kind: "recording_finalized",
      recording_id: "rec_1",
      duration_seconds: 120,
      ground_truth_count: 4,
    });

    await waitFor(() => expect(requestMock).toHaveBeenCalledTimes(2));
  });
});

/**
 * The Model Inspector replays the engine the app runs; the request carries
 * only the recording and the scheduler's margin.
 */
describe("replayRecording", () => {
  it("asks for the recording with the default margin", async () => {
    requestMock.mockClear();
    await replayRecording("rec_x");
    expect(requestMock.mock.calls[0]?.[0]).toMatchObject({
      kind: "replay_recording",
      recording_id: "rec_x",
      target_margin_ms: 0,
    });
  });

});

describe("useRecordingSession live ASR", () => {
  it("starts present mode with recommend_model when ASR is unset", async () => {
    localStorage.clear();
    requestMock.mockClear();
    const { unmount } = renderHook(() =>
      useRecordingSession({ setlistId: "s1", enabled: true }),
    );
    await waitFor(() =>
      expect(requestMock).toHaveBeenCalledWith(
        expect.objectContaining({
          kind: "start_audio_session",
          backend: "mlx",
          model_size: "small.en",
          // Recording is opt-in: unset means don't record.
          record: false,
          setlist_id: "s1",
        }),
        expect.any(Number),
      ),
    );
    expect(requestMock.mock.calls.map((c) => c[0]?.kind)).toContain(
      "recommend_model",
    );
    unmount();
  });
});

describe("useRecordingSession without recording", () => {
  it("starts the live session with record=false", async () => {
    localStorage.clear();
    requestMock.mockClear();
    const { unmount } = renderHook(() =>
      useRecordingSession({ setlistId: "s1", enabled: true, record: false }),
    );
    await waitFor(() =>
      expect(requestMock).toHaveBeenCalledWith(
        expect.objectContaining({ kind: "start_audio_session", record: false }),
        expect.any(Number),
      ),
    );
    unmount();
  });
});

describe("useRecordingSession after an engine crash", () => {
  it("starts the session again on the new engine, in the live mode, at the current slide", async () => {
    localStorage.clear();
    requestMock.mockClear();
    notifyMock.mockClear();
    const content = { title: "Sunday", items: [] };
    const { rerender, unmount } = renderHook(
      (p: { mode: "shadow" | "active"; slide: number }) =>
        useRecordingSession({
          setlistId: "s1",
          enabled: true,
          setlistContent: content,
          aiMode: p.mode,
          position: { itemIdx: 2, slideIdx: p.slide },
        }),
      { initialProps: { mode: "shadow", slide: 0 } },
    );
    const starts = () => requestMock.mock.calls.filter((c) => c[0]?.kind === "start_audio_session");
    await waitFor(() => expect(starts()).toHaveLength(1));
    expect(starts()[0]![0]).toMatchObject({ ai_mode: "shadow" });

    // Mid-service: the AI was switched on and the service moved on.
    rerender({ mode: "active", slide: 5 });
    act(() => restartHandlers.forEach((cb) => cb()));

    await waitFor(() => expect(starts()).toHaveLength(2));
    expect(starts()[1]![0]).toMatchObject({ ai_mode: "active", setlist_content: content });
    await waitFor(() =>
      expect(notifyMock).toHaveBeenCalledWith({
        kind: "record_slide_change",
        item_idx: 2,
        slide_idx: 5,
        source: "manual",
      }),
    );
    unmount();
    expect(restartHandlers.size).toBe(0);
  });
});

describe("useRecordingSession identity", () => {
  it("returns the same object across renders", () => {
    // OperatorView's slide-edit callbacks depend on this object. A fresh one
    // per render changed all of them on every slide change, so no song block
    // ever skipped a render: 611 of 611 slide cards redrawn per keypress.
    const { result, rerender } = renderHook(() =>
      useRecordingSession({ setlistId: "s1", enabled: false }),
    );
    const first = result.current;
    rerender();
    expect(result.current).toBe(first);
  });
});

describe("useRecordingSession when the microphone fails after the start reply", () => {
  it("reports an audio_session_state error as a start failure, and clears it once running", async () => {
    // The mic opens on the engine's capture thread; a failure there only
    // arrives as a broadcast, which used to reach the debug panel alone.
    localStorage.clear();
    requestMock.mockClear();
    const { result, unmount } = renderHook(() => {
      useRecordingSession({ setlistId: "s1", enabled: true });
      return useSessionStartFailure();
    });
    await waitFor(() =>
      expect(requestMock.mock.calls.map((c) => c[0]?.kind)).toContain("start_audio_session"),
    );
    await waitFor(() => expect(handlers.get("audio_session_state")?.length ?? 0).toBeGreaterThan(0));

    // The debug panel's transcript-only session (no setlist) failing is not
    // the service's AI failing.
    broadcast("audio_session_state", {
      kind: "audio_session_state",
      state: "error",
      error: "capture start failed: debug panel mic",
      setlist_id: null,
    });
    expect(result.current).toBeNull();

    // A mic that fails in this service's own session — at start, or
    // unplugged mid-service — is.
    broadcast("audio_session_state", {
      kind: "audio_session_state",
      state: "error",
      error: "capture start failed: Error opening InputStream",
      setlist_id: "s1",
    });
    expect(result.current?.message).toMatch(/Error opening InputStream/);
    // Carries the mode it started in, so the pill can stay quiet with the AI off.
    expect(result.current?.aiMode).toBe("shadow");

    // Another session coming up doesn't clear it; only this one's does.
    broadcast("audio_session_state", { kind: "audio_session_state", state: "running", setlist_id: null });
    expect(result.current).not.toBeNull();

    // Retry asks for the session again.
    const before = requestMock.mock.calls.filter((c) => c[0]?.kind === "start_audio_session").length;
    act(() => result.current?.retry());
    await waitFor(() =>
      expect(requestMock.mock.calls.filter((c) => c[0]?.kind === "start_audio_session").length).toBe(
        before + 1,
      ),
    );

    broadcast("audio_session_state", { kind: "audio_session_state", state: "running", setlist_id: "s1" });
    expect(result.current).toBeNull();
    unmount();
    expect(handlers.get("audio_session_state") ?? []).toHaveLength(0);
  });
});
