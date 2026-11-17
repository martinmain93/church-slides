import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CloudHost, libraryParts } from "@/lib/band/cloudHost";
import { bandSocketUrl } from "@/lib/band/useBandConnection";
import type { LibrarySong } from "@/lib/band/librarySearch";

class FakeSocket {
  static OPEN = 1;
  static CLOSED = 3;
  static all: FakeSocket[] = [];
  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) {
    FakeSocket.all.push(this);
  }
  send(s: string) {
    this.sent.push(s);
  }
  close() {
    this.readyState = 3;
    this.onclose?.();
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  deliver(m: unknown) {
    this.onmessage?.({ data: typeof m === "string" ? m : JSON.stringify(m) });
  }
}

function song(id: string, body = "x"): LibrarySong {
  return { id, title: id, author: null, body, lastUsed: null, uses: 0, key: null, hasChart: false };
}

describe("online relay host", () => {
  beforeEach(() => {
    FakeSocket.all = [];
    vi.stubGlobal("WebSocket", FakeSocket);
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  function make() {
    const commands: unknown[] = [];
    const states: string[] = [];
    let clients = 0;
    const host = new CloudHost("wss://relay/r/room1/host?hk=x", {
      onCommand: (c) => commands.push(c),
      onClients: (n) => (clients = n),
      onConnection: (s) => states.push(s),
      libraryIndex: async () => [song("a"), song("b")],
    });
    return { host, commands, states, clients: () => clients };
  }

  it("re-sends the latest snapshots on every (re)connect", () => {
    const { host, states } = make();
    host.publish("setlist", { name: "Sunday" }); // before connecting: remembered
    host.connect();
    const first = FakeSocket.all[0]!;
    first.open();
    expect(first.sent.map((m) => JSON.parse(m).type)).toEqual(["setlist"]);
    host.publish("position", { slideIdx: 2 });
    host.publish("position", { slideIdx: 3 });
    first.close(); // Wi-Fi blip
    expect(states.at(-1)).toBe("reconnecting");
    vi.advanceTimersByTime(600);
    const second = FakeSocket.all[1]!;
    second.open();
    const resent = second.sent.map((m) => JSON.parse(m));
    expect(resent).toEqual([
      { type: "setlist", data: { name: "Sunday" } },
      { type: "position", data: { slideIdx: 3 } },
    ]);
    expect(states.at(-1)).toBe("live");
  });

  it("passes leader commands and device counts through, and answers library requests", async () => {
    const { host, commands, clients } = make();
    host.connect();
    const s = FakeSocket.all[0]!;
    s.open();
    s.deliver({ type: "clients", count: 4 });
    s.deliver({ type: "command", command: { kind: "go_to_song", itemId: "i2" } });
    s.deliver({ type: "command", command: { kind: "launch_missiles" } }); // not a band command
    expect(clients()).toBe(4);
    expect(commands).toEqual([{ kind: "go_to_song", itemId: "i2" }]);
    s.deliver({ type: "library_request", from: "dev-7" });
    for (let i = 0; i < 5; i++) await Promise.resolve(); // the library lookup resolves
    const lib = s.sent.filter((m) => m.startsWith("{")).map((m) => JSON.parse(m)).find((m) => m.type === "library");
    expect(lib).toMatchObject({ to: "dev-7", part: 0, parts: 1 });
    expect(lib.data.map((x: LibrarySong) => x.id)).toEqual(["a", "b"]);
  });

  it("pings to stay alive and treats silence as a dead socket", () => {
    const { host } = make();
    host.connect();
    const s = FakeSocket.all[0]!;
    s.open();
    vi.advanceTimersByTime(20_000);
    expect(s.sent).toContain("ping");
    s.deliver("pong");
    vi.advanceTimersByTime(44_000);
    expect(s.readyState).toBe(1);
    vi.advanceTimersByTime(2_000); // 45 s since the last pong
    expect(s.readyState).toBe(3);
  });

  it("ending the service tells the room to forget, and stops reconnecting", () => {
    const { host, states } = make();
    host.connect();
    const s = FakeSocket.all[0]!;
    s.open();
    host.close(true);
    expect(JSON.parse(s.sent.at(-1)!)).toEqual({ type: "close" });
    vi.advanceTimersByTime(60_000);
    expect(FakeSocket.all).toHaveLength(1);
    expect(states.at(-1)).toBe("closed");
  });

  it("survives a socket that errors again while closing (no recursion)", () => {
    const { host, states } = make();
    host.connect();
    const s = FakeSocket.all[0]!;
    // Like Node: close() on a socket that never opened fires error again.
    s.close = () => {
      s.onerror?.();
      s.readyState = 3;
      s.onclose?.();
    };
    s.onerror?.();
    expect(states.at(-1)).toBe("reconnecting");
  });

  it("reconnects after a failed attempt that reports an error but never closes", () => {
    const { host } = make();
    host.connect();
    const s = FakeSocket.all[0]!;
    s.close = () => {
      s.readyState = 3; // no close event, like Node's failed connect
    };
    s.onerror?.();
    vi.advanceTimersByTime(600);
    expect(FakeSocket.all).toHaveLength(2);
  });

  it("splits a big library into parts that fit a relay message", () => {
    const big = Array.from({ length: 900 }, (_, i) => song(`s${i}`, "lyrics ".repeat(200)));
    const parts = libraryParts(big);
    expect(parts.length).toBeGreaterThan(1);
    expect(parts.flat()).toHaveLength(900);
    for (const p of parts) expect(JSON.stringify(p).length).toBeLessThan(450_000);
  });
});

describe("band page socket address on the relay", () => {
  it("connects to the room's socket with the key from the link", () => {
    const loc = { protocol: "https:", host: "church-slides-relay.grace.workers.dev", hostname: "x", pathname: "/r/01abc23/leader", search: "?k=deadbeef&pin=1234" } as Location;
    expect(bandSocketUrl(loc)).toBe("wss://church-slides-relay.grace.workers.dev/r/01abc23/ws?k=deadbeef");
  });
});
