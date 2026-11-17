import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { liveOrganization, setLiveOrganization, type AccountStatus } from "@/lib/account";
import { CloudHost } from "@/lib/band/cloudHost";
import { bandInviteUrl, bandSocketUrl, hostedLink, useBandConnection } from "@/lib/band/useBandConnection";

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
  constructor(
    public url: string,
    public protocols?: string[],
  ) {
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
    this.onmessage?.({ data: JSON.stringify(m) });
  }
}

const LINK = "https://churchslides.app/live/#s=https%3A%2F%2Fapi.example.org&t=csb_secret";

describe("hosted band links", () => {
  it("reads the server and token from the fragment, never the query", () => {
    const loc = new URL(LINK);
    expect(hostedLink(loc)).toEqual({ server: "https://api.example.org", token: "csb_secret" });
    expect(bandSocketUrl(loc as unknown as Location)).toBe("wss://api.example.org/band/v1/live");
    expect(bandInviteUrl(loc as unknown as Location)).toBe(LINK);
    expect(hostedLink(new URL("https://churchslides.app/live/?s=https://x&t=y"))).toBeNull();
    expect(hostedLink(new URL("https://churchslides.app/live/#s=javascript:alert(1)&t=y"))).toBeNull();
  });
});

describe("the band page in a hosted room", () => {
  beforeEach(() => {
    FakeSocket.all = [];
    vi.stubGlobal("WebSocket", FakeSocket);
    window.history.replaceState(null, "", "/live/#s=https%3A%2F%2Fapi.example.org&t=csb_secret");
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    window.history.replaceState(null, "", "/");
  });

  it("sends the token as a subprotocol, asks for leader controls, and learns the local address", () => {
    const { result } = renderHook(() => useBandConnection("leader", null));
    const sock = FakeSocket.all[0]!;
    expect(sock.url).toBe("wss://api.example.org/band/v1/live");
    expect(sock.protocols).toEqual(["csb", "csb_secret"]);
    act(() => sock.open());
    expect(JSON.parse(sock.sent[0]!)).toEqual({ type: "hello", role: "leader" });

    act(() => sock.deliver({ type: "welcome", role: "band" }));
    expect(result.current.isLeader).toBe(false);
    expect(result.current.pinRejected).toBe(false);

    act(() => sock.deliver({ type: "lan", url: "http://192.168.1.5:7777/band" }));
    expect(result.current.lanUrl).toBe("http://192.168.1.5:7777/band");
  });
});

describe("the operator's connection to a hosted room", () => {
  beforeEach(() => {
    FakeSocket.all = [];
    vi.stubGlobal("WebSocket", FakeSocket);
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("gets a fresh ticket for every connection and re-sends what devices need", async () => {
    let n = 0;
    const host = new CloudHost(async () => `wss://api/live/host?ticket=t${++n}`, {
      onCommand: () => undefined,
      onClients: () => undefined,
      onConnection: () => undefined,
      libraryIndex: async () => [],
    });
    host.publish("setlist", { name: "Sunday" });
    host.publish("lan", { band: "http://192.168.1.5:7777/band" });
    host.connect();
    await vi.runOnlyPendingTimersAsync();
    expect(FakeSocket.all[0]!.url).toBe("wss://api/live/host?ticket=t1");
    FakeSocket.all[0]!.open();
    expect(FakeSocket.all[0]!.sent.map((s) => JSON.parse(s).type)).toEqual(["setlist", "lan"]);

    FakeSocket.all[0]!.close();
    await vi.advanceTimersByTimeAsync(1000);
    expect(FakeSocket.all[1]!.url).toBe("wss://api/live/host?ticket=t2");

    // The service ends: the set goes, where to find this computer stays.
    host.idle();
    FakeSocket.all[1]!.open();
    expect(FakeSocket.all[1]!.sent.map((s) => JSON.parse(s).type)).toEqual(["lan"]);
    host.close(false);
  });

  it("keeps retrying when a ticket can't be had (offline)", async () => {
    let calls = 0;
    const host = new CloudHost(
      async () => {
        calls += 1;
        throw new Error("offline");
      },
      { onCommand: () => undefined, onClients: () => undefined, onConnection: () => undefined, libraryIndex: async () => [] },
    );
    host.connect();
    await vi.advanceTimersByTimeAsync(5000);
    expect(calls).toBeGreaterThan(2);
    expect(FakeSocket.all).toHaveLength(0);
    host.close(false);
  });
});

describe("which organization a service goes to", () => {
  const status = (memberships: AccountStatus["memberships"]): AccountStatus => ({
    signedIn: true,
    userId: "u",
    email: "a@example.org",
    displayName: "A",
    memberships,
    server: "https://api.churchslides.app",
    serverIsDefault: true,
  });

  it("only one whose role runs the live set, the picked one first", () => {
    localStorage.clear();
    expect(liveOrganization(null)).toBeNull();
    expect(liveOrganization(status([{ orgId: "m", orgName: "M", role: "member" }]))).toBeNull();
    const s = status([
      { orgId: "m", orgName: "M", role: "member" },
      { orgId: "a", orgName: "A", role: "operator" },
      { orgId: "b", orgName: "B", role: "admin" },
    ]);
    expect(liveOrganization(s)?.orgId).toBe("a");
    setLiveOrganization("b");
    expect(liveOrganization(s)?.orgId).toBe("b");
    setLiveOrganization("m"); // can't run it there
    expect(liveOrganization(s)?.orgId).toBe("a");
  });
});
