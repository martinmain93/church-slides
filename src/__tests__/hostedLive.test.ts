import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { liveOrganization, type AccountStatus } from "@/lib/account";
import { CloudHost } from "@/lib/band/cloudHost";
import { pickOrganization } from "@/lib/band/memberSignIn";
import { bandInviteUrl, bandPageHasLink, bandSocketUrl, hostedLink, useBandConnection } from "@/lib/band/useBandConnection";

class FakeSocket {
  static OPEN = 1;
  static CLOSED = 3;
  static all: FakeSocket[] = [];
  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
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
  close(code = 1006) {
    this.readyState = 3;
    this.onclose?.({ code });
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  deliver(m: unknown) {
    this.onmessage?.({ data: JSON.stringify(m) });
  }
}

const LINK = "https://churchslides.app/live/#s=https%3A%2F%2Fapi.churchslides.app&t=csb_secret";

describe("hosted band links", () => {
  it("reads the server and token from the fragment, never the query", () => {
    const loc = new URL(LINK);
    expect(hostedLink(loc)).toEqual({ server: "https://api.churchslides.app", token: "csb_secret" });
    expect(bandSocketUrl(loc as unknown as Location)).toBe("wss://api.churchslides.app/band/v1/live");
    expect(bandInviteUrl(loc as unknown as Location)).toBe(LINK);
    expect(hostedLink(new URL("https://churchslides.app/live/?s=https://x&t=y"))).toBeNull();
    expect(hostedLink(new URL("https://churchslides.app/live/#s=javascript:alert(1)&t=y"))).toBeNull();
    // Only servers the page trusts: a link can't point it at someone else's.
    expect(hostedLink(new URL("https://churchslides.app/live/#s=https%3A%2F%2Fevil.example&t=y"))).toBeNull();
    expect(hostedLink(new URL("https://churchslides.app/live/#s=https%3A%2F%2Fapi.churchslides.app.evil.example&t=y"))).toBeNull();
    expect(hostedLink(new URL("https://churchslides.app/live/#s=http%3A%2F%2Flocalhost%3A8787&t=y"))).toEqual({ server: "http://localhost:8787", token: "y" });
  });
});

describe("a band page with no link", () => {
  it("is only the website's bare /live/", () => {
    expect(bandPageHasLink({ hash: "", pathname: "/live/" })).toBe(false);
    expect(bandPageHasLink({ hash: "#s=https://api.churchslides.app&t=csb_x", pathname: "/live/" })).toBe(true);
    expect(bandPageHasLink({ hash: "", pathname: "/r/abc123/band" })).toBe(true);
  });
});

describe("the band page in a hosted room", () => {
  beforeEach(() => {
    FakeSocket.all = [];
    vi.stubGlobal("WebSocket", FakeSocket);
    window.history.replaceState(null, "", "/live/#s=https%3A%2F%2Fapi.churchslides.app&t=csb_secret");
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    window.history.replaceState(null, "", "/");
  });

  it("sends the token as a subprotocol, asks for leader controls, and learns the local address", () => {
    const { result } = renderHook(() => useBandConnection("leader", null));
    const sock = FakeSocket.all[0]!;
    expect(sock.url).toBe("wss://api.churchslides.app/band/v1/live");
    expect(sock.protocols).toEqual(["csb", "csb_secret"]);
    act(() => sock.open());
    expect(JSON.parse(sock.sent[0]!)).toEqual({ type: "hello", role: "leader" });

    act(() => sock.deliver({ type: "welcome", role: "band" }));
    expect(result.current.isLeader).toBe(false);
    expect(result.current.pinRejected).toBe(false);

    act(() => sock.deliver({ type: "lan", url: "http://192.168.1.5:7777/band" }));
    expect(result.current.lanUrl).toBe("http://192.168.1.5:7777/band");
    // A room can't make the page run a script through "Connect directly".
    act(() => sock.deliver({ type: "lan", url: "javascript:alert(document.domain)//" }));
    expect(result.current.lanUrl).toBeNull();
  });

  it("stops and says so when its link was removed", async () => {
    const fetchMock = vi.fn(async () => new Response("{}", { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useBandConnection("leader", null));
    const sock = FakeSocket.all[0]!;
    act(() => sock.open());
    await act(async () => sock.close(4001));
    expect(fetchMock).toHaveBeenCalledWith("https://api.churchslides.app/band/v1/me", { headers: { Authorization: "Bearer csb_secret" } });
    expect(result.current.linkRemoved).toBe(true);
    await act(async () => new Promise((r) => setTimeout(r, 500)));
    expect(FakeSocket.all).toHaveLength(1);
  });

  it("keeps retrying when the server can't be asked (offline isn't removed)", async () => {
    vi.useFakeTimers();
    try {
      vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("offline"))));
      const { result } = renderHook(() => useBandConnection("leader", null));
      await act(async () => FakeSocket.all[0]!.close());
      await act(async () => vi.advanceTimersByTimeAsync(1000));
      expect(result.current.linkRemoved).toBe(false);
      expect(FakeSocket.all.length).toBeGreaterThan(1);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("a signed-in member on the band page", () => {
  beforeEach(() => {
    FakeSocket.all = [];
    vi.stubGlobal("WebSocket", FakeSocket);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("connects with a fresh address each time and asks for leader controls", async () => {
    let n = 0;
    const source = { socketUrl: vi.fn(async () => `wss://api.churchslides.app/live/member?ticket=${++n}`) };
    renderHook(() => useBandConnection("leader", null, source));
    await act(async () => undefined);
    const first = FakeSocket.all[0]!;
    expect(first.url).toBe("wss://api.churchslides.app/live/member?ticket=1");
    act(() => first.open());
    expect(JSON.parse(first.sent[0]!)).toEqual({ type: "hello", role: "leader" });
    vi.useFakeTimers();
    try {
      await act(async () => first.close(4001));
      await act(async () => vi.advanceTimersByTimeAsync(1000));
    } finally {
      vi.useRealTimers();
    }
    expect(FakeSocket.all[1]?.url).toBe("wss://api.churchslides.app/live/member?ticket=2");
  });

  it("stops when the person can't join any more", async () => {
    const source = { socketUrl: vi.fn(async () => Promise.reject(Object.assign(new Error("gone"), { removed: true }))) };
    const { result } = renderHook(() => useBandConnection("leader", null, source));
    await act(async () => undefined);
    expect(result.current.linkRemoved).toBe(true);
    expect(FakeSocket.all).toHaveLength(0);
  });

  it("goes straight to the organization with a service running", () => {
    const a = { orgId: "a", orgName: "Grace", role: "member" };
    const b = { orgId: "b", orgName: "Hope", role: "operator" };
    expect(pickOrganization([a, b], { b: true }, null)).toBe(b);
    expect(pickOrganization([a], {}, null)).toBe(a);
    expect(pickOrganization([a, b], {}, "a")).toBe(a);
    expect(pickOrganization([a, b], {}, null)).toBeNull();
    // Two running at once: the person picks.
    expect(pickOrganization([a, b], { a: true, b: true }, "a")).toBeNull();
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

  it("the open library's organization, where the role runs the live set", () => {
    expect(liveOrganization(null)).toBeNull();
    expect(liveOrganization(status([{ orgId: "m", orgName: "M", role: "member" }]))).toBeNull();
    const s = status([
      { orgId: "m", orgName: "M", role: "member" },
      { orgId: "a", orgName: "A", role: "operator" },
      { orgId: "b", orgName: "B", role: "admin" },
    ]);
    // This computer's own library open: the first that can run it.
    expect(liveOrganization(s, "this-computer")?.orgId).toBe("a");
    expect(liveOrganization(s)?.orgId).toBe("a");
    // An organization's library open: its services go live there.
    expect(liveOrganization(s, "b")?.orgId).toBe("b");
    // Its library is open but the role can't run the live set: nowhere.
    expect(liveOrganization(s, "m")).toBeNull();
  });
});
