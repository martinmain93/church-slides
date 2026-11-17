/**
 * The sidecar sending a message is not evidence the panel shows it. These
 * tests pin the part that has broken before elsewhere in this app: a broadcast
 * arriving, being folded into the rolling window, and being readable by a
 * component.
 *
 * The window logic is the substance. The sidecar sends only the audio since
 * the last tick, so the store — not the server — owns the ten-second history,
 * the merging of consecutive ticks into one chord band, and the eviction of
 * anything that has scrolled off.
 */

import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { handlers, conn, requests } = vi.hoisted(() => {
  const handlers = new Map<string, ((raw: unknown) => void)[]>();
  const requests: unknown[] = [];
  return {
    handlers,
    requests,
    conn: {
      status: "open",
      onMessage: (kind: string, cb: (raw: unknown) => void) => {
        const list = handlers.get(kind) ?? [];
        list.push(cb);
        handlers.set(kind, list);
        return () => handlers.set(kind, list.filter((h) => h !== cb));
      },
      onStatus: () => () => {},
      request: async (msg: unknown) => {
        requests.push(msg);
        return {};
      },
    },
  };
});

vi.mock("@/lib/sidecarConnection", () => ({
  getSharedConnection: () => Promise.resolve(conn),
}));

import {
  LEVEL_HZ,
  WINDOW_S,
  resetMusicAnalysis,
  setMusicAnalysis,
  useMusicAnalysis,
} from "@/lib/musicAnalysis";

async function connected() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

function broadcast(raw: unknown) {
  act(() => (handlers.get("music_analysis") ?? []).forEach((h) => h(raw)));
}

function tick(over: Record<string, unknown> = {}) {
  return {
    kind: "music_analysis",
    t_end: 1.0,
    levels: [0.1, 0.2],
    chord: "C",
    candidates: [{ name: "C", confidence: 0.6 }],
    beats: [],
    bpm: 96,
    chords_enabled: true,
    beats_enabled: true,
    realtime_factor: 0.002,
    ...over,
  };
}

describe("music analysis store", () => {
  beforeEach(async () => {
    resetMusicAnalysis();
    requests.length = 0;
    await connected();
  });

  it("shows what the sidecar broadcast", () => {
    const { result } = renderHook(() => useMusicAnalysis());
    broadcast(tick());
    expect(result.current.chord).toBe("C");
    expect(result.current.bpm).toBe(96);
    expect(result.current.levels).toEqual([0.1, 0.2]);
    expect(result.current.seen).toBe(true);
  });

  it("accumulates levels across ticks rather than replacing them", () => {
    const { result } = renderHook(() => useMusicAnalysis());
    broadcast(tick({ t_end: 1.0, levels: [0.1] }));
    broadcast(tick({ t_end: 1.1, levels: [0.2] }));
    expect(result.current.levels).toEqual([0.1, 0.2]);
  });

  it("merges consecutive ticks of one chord into a single band", () => {
    const { result } = renderHook(() => useMusicAnalysis());
    broadcast(tick({ t_end: 1.0, chord: "C" }));
    broadcast(tick({ t_end: 1.1, chord: "C" }));
    broadcast(tick({ t_end: 1.2, chord: "G" }));
    expect(result.current.segments.map((s) => s.name)).toEqual(["C", "G"]);
    expect(result.current.segments[0]?.tEnd).toBe(1.1);
  });

  it("drops history that has scrolled out of the window", () => {
    const { result } = renderHook(() => useMusicAnalysis());
    broadcast(tick({ t_end: 1.0, chord: "C", beats: [0.9] }));
    broadcast(tick({ t_end: 1.0 + WINDOW_S + 1, chord: "G", beats: [] }));
    expect(result.current.segments.map((s) => s.name)).toEqual(["G"]);
    expect(result.current.beats).toEqual([]);
  });

  it("never grows the level buffer past the visible window", () => {
    const { result } = renderHook(() => useMusicAnalysis());
    for (let i = 0; i < 40; i += 1) {
      broadcast(tick({ t_end: i * 0.1, levels: Array(50).fill(0.3) }));
    }
    expect(result.current.levels.length).toBe(WINDOW_S * LEVEL_HZ);
  });

  it("tolerates a message with the optional lists omitted", () => {
    const { result } = renderHook(() => useMusicAnalysis());
    broadcast({
      kind: "music_analysis",
      t_end: 2.0,
      levels: [0.4],
      chord: null,
    });
    expect(result.current.beats).toEqual([]);
    expect(result.current.candidates).toEqual([]);
    expect(result.current.chord).toBeNull();
  });

  it("measures how bursty delivery is, so the scope can absorb it", () => {
    const { result } = renderHook(() => useMusicAnalysis());
    let clock = 1000;
    const spy = vi
      .spyOn(performance, "now")
      .mockImplementation(() => clock);
    try {
      // Ten smooth ticks, then a stall and a burst — the shape the ASR pass
      // imposes on everything downstream of the capture thread.
      for (let i = 0; i < 10; i += 1) {
        clock += 100;
        broadcast(tick({ t_end: i * 0.1 }));
      }
      const smooth = result.current.arrivalJitter;
      clock += 900;
      broadcast(tick({ t_end: 1.1 }));
      for (let i = 0; i < 8; i += 1) {
        clock += 1;
        broadcast(tick({ t_end: 1.2 + i * 0.1 }));
      }
      expect(smooth).toBeLessThan(0.3);
      expect(result.current.arrivalJitter).toBeGreaterThan(0.5);
    } finally {
      spy.mockRestore();
    }
  });

  it("sends both toggle states together, so one cannot clear the other", async () => {
    await setMusicAnalysis(true, false);
    expect(requests).toEqual([
      { kind: "set_music_analysis", chords: true, beats: false },
    ]);
  });
});
