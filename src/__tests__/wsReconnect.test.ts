import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { connectSidecar } from "@/lib/ws";

/** Opens on `open()`; fails with error-then-close on `fail()`, like a browser. */
class FakeSocket {
  static OPEN = 1;
  static instances: FakeSocket[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  constructor(public url: string) {
    FakeSocket.instances.push(this);
  }
  send() {}
  close() {
    this.readyState = 3;
    this.onclose?.();
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  fail() {
    this.onerror?.();
    this.onclose?.();
  }
}

const last = () => FakeSocket.instances.at(-1)!;

async function connected(resolvePort?: () => Promise<number>) {
  const p = connectSidecar(1000, resolvePort);
  last().open();
  return p;
}

describe("sidecar reconnect", () => {
  beforeEach(() => {
    FakeSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeSocket);
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("follows a relaunched engine to its new port and says so", async () => {
    let port = 1000;
    const conn = await connected(async () => port);
    const restarted = vi.fn();
    conn.onEngineRestart(restarted);

    port = 2000; // the engine crashed and came back elsewhere
    last().close();
    await vi.advanceTimersByTimeAsync(600);
    expect(last().url).toBe("ws://127.0.0.1:2000/");
    last().open();
    await vi.advanceTimersByTimeAsync(0);
    expect(restarted).toHaveBeenCalledTimes(1);
    expect(conn.port).toBe(2000);
  });

  it("reconnects after an engine crash, which a browser reports as error then close", async () => {
    // An abnormal closure of an open socket fires `error` before `close`. The
    // error handler used to detach `onclose` — meant for a socket that never
    // opened — so a crashed engine left the screen disconnected for good.
    let port = 1000;
    const conn = await connected(async () => port);
    const restarted = vi.fn();
    conn.onEngineRestart(restarted);

    port = 2000;
    last().fail();
    await vi.advanceTimersByTimeAsync(600);
    expect(last().url).toBe("ws://127.0.0.1:2000/");
    last().open();
    await vi.advanceTimersByTimeAsync(0);
    expect(restarted).toHaveBeenCalledTimes(1);
    expect(conn.status).toBe("open");
  });

  it("a drop to the same engine is not a restart", async () => {
    const conn = await connected(async () => 1000);
    const restarted = vi.fn();
    conn.onEngineRestart(restarted);
    last().close();
    await vi.advanceTimersByTimeAsync(600);
    last().open();
    await vi.advanceTimersByTimeAsync(0);
    expect(restarted).not.toHaveBeenCalled();
  });

  it("keeps trying when a reconnect attempt fails to open", async () => {
    await connected(async () => 1000);
    last().close();
    await vi.advanceTimersByTimeAsync(600);
    const failed = last();
    failed.fail();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(last()).not.toBe(failed);
  });

  it("waits out an engine that isn't back yet", async () => {
    let up = false;
    const conn = await connected(async () => {
      if (!up) throw new Error("starting");
      return 3000;
    });
    last().close();
    const before = FakeSocket.instances.length;
    await vi.advanceTimersByTimeAsync(3000);
    expect(FakeSocket.instances.length).toBe(before); // nothing to dial yet
    up = true;
    await vi.advanceTimersByTimeAsync(10_000);
    expect(last().url).toBe("ws://127.0.0.1:3000/");
    expect(conn.port).toBe(3000);
  });

  it("restarts the request timeout when a partial reply arrives", async () => {
    const conn = await connected();
    const onPartial = vi.fn();
    const pending = conn.request(
      { kind: "inspect_music", recording_id: "rec" },
      1_000,
      onPartial,
    );
    await vi.advanceTimersByTimeAsync(800);
    last().onmessage?.({
      data: JSON.stringify({
        id: 1,
        kind: "inspect_music_response",
        partial: true,
      }),
    });
    await vi.advanceTimersByTimeAsync(800);
    last().onmessage?.({
      data: JSON.stringify({
        id: 1,
        kind: "inspect_music_response",
        partial: false,
        duration_seconds: 1,
      }),
    });
    await expect(pending).resolves.toMatchObject({ partial: false });
    expect(onPartial).toHaveBeenCalledTimes(1);
  });
});
