import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const sockets = vi.hoisted(() => ({ all: [] as { url: string; sent: string[]; open(): void; deliver(m: unknown): void }[] }));
vi.mock("@/lib/tauri", async (orig) => ({
  ...(await orig<typeof import("@/lib/tauri")>()),
  cloudRelayNewRoom: vi.fn(async (pin: string) => ({
    base_url: "https://relay.example", room: "room1", key: "k1", host_socket: `wss://relay.example/r/room1/host?hk=h&pin=${pin}`,
  })),
  cloudRelayStatus: vi.fn(async () => ({
    configured: true, worker_url: "https://relay.example", account_name: "Grace", needs_update: false, oauth_available: false, relay_built: true,
  })),
  cloudRelayHealth: vi.fn(async () => true),
  cloudRelayRedeploy: vi.fn(async () => ({
    configured: true, worker_url: "https://relay.example", account_name: "Grace", needs_update: false, oauth_available: false, relay_built: true,
  })),
  bandLibraryIndex: vi.fn(async () => []),
}));

import { BandScreensButton } from "@/components/band/BandScreensButton";
import { onlineRoom, resetOnlineRoomForTests } from "@/lib/band/onlineRoom";
import { useOnlineRoom } from "@/lib/band/useOnlineRoom";
import type { BandHostApi } from "@/lib/band/useBandHost";

class FakeSocket {
  static OPEN = 1;
  static CLOSED = 3;
  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) {
    sockets.all.push(this);
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

beforeEach(() => {
  sockets.all = [];
  vi.stubGlobal("WebSocket", FakeSocket);
  resetOnlineRoomForTests();
});
afterEach(() => vi.unstubAllGlobals());

describe("the app-wide online room", () => {
  it("opens one room however many screens ask, and keeps it between services", async () => {
    const pin = vi.fn(async () => "2468");
    await Promise.all([onlineRoom.start(pin), onlineRoom.start(pin)]);
    expect(sockets.all).toHaveLength(1);
    expect(onlineRoom.getState().room?.leaderUrl).toBe("https://relay.example/r/room1/leader?k=k1&pin=2468");
    const sock = sockets.all[0]!;
    sock.open();

    // A service runs: its leader commands reach it; then it ends.
    const handler = vi.fn();
    onlineRoom.setCommandHandler(handler);
    onlineRoom.publish("position", { slideIdx: 2 });
    sock.deliver({ type: "command", command: { kind: "go_to_song", itemId: "i1" } });
    expect(handler).toHaveBeenCalledWith({ kind: "go_to_song", itemId: "i1" });
    onlineRoom.setCommandHandler(null);
    onlineRoom.idle();
    expect(JSON.parse(sock.sent.at(-1)!)).toEqual({ type: "idle" });
    // Still open, same links: the band keeps waiting for the next service.
    expect(onlineRoom.getState().room).not.toBeNull();
    sock.deliver({ type: "command", command: { kind: "go_to_song", itemId: "i1" } }); // between services: ignored
    expect(handler).toHaveBeenCalledTimes(1);

    onlineRoom.stop();
    expect(JSON.parse(sock.sent.at(-1)!)).toEqual({ type: "close" });
    expect(onlineRoom.getState().room).toBeNull();
  });
});

function Panel() {
  const online = useOnlineRoom({ getPin: async () => "2468" });
  const band: BandHostApi = { info: null, clients: 0, start: async () => undefined, error: null, online };
  return <BandScreensButton band={band} />;
}

describe("band screens panel: the online room page", () => {
  // The Cloudflare status is only fetched inside the desktop app.
  beforeEach(() => {
    (window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__ = { invoke: async () => 0 };
  });
  afterEach(() => {
    delete (window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__;
  });

  it("opens straight onto the online room once it's set up, never running off the screen", async () => {
    render(<Panel />);
    await act(async () => undefined); // setup status loads (set up, in this test)
    act(() => screen.getByTestId("band-screens-button").click());
    expect(screen.getByTestId("band-panel").className).toContain("max-h-[calc(100vh-6rem)]");
    expect(screen.getByTestId("band-panel").className).toContain("overflow-y-auto");
    expect(screen.queryByTestId("band-start")).toBeNull(); // the local page isn't shown
    expect(screen.getByTestId("online-panel")).toBeInTheDocument();
    expect(screen.getByTestId("online-back").textContent).toContain("Local network");

    // Set up: opening the page opened the room — no extra click.
    await act(async () => undefined);
    expect(screen.getByLabelText("QR code for Band (online)")).toBeInTheDocument();

    act(() => screen.getByTestId("online-back").click());
    expect(screen.getByTestId("band-start")).toBeInTheDocument();
    // Back on the main panel the open room shows as a status line.
    expect(screen.getByTestId("online-entry").textContent).toContain("Online room is open");

    // Closed and reopened: the online room again.
    act(() => screen.getByTestId("band-screens-button").click());
    act(() => screen.getByTestId("band-screens-button").click());
    expect(screen.getByTestId("online-panel")).toBeInTheDocument();
  });

  it("opens on the local network page while the online room isn't set up", async () => {
    const tauri = await import("@/lib/tauri");
    vi.mocked(tauri.cloudRelayStatus).mockResolvedValueOnce({
      configured: false, worker_url: null, account_name: null, needs_update: false, oauth_available: false, relay_built: true,
    });
    render(<Panel />);
    await act(async () => undefined);
    act(() => screen.getByTestId("band-screens-button").click());
    expect(screen.getByTestId("band-start")).toBeInTheDocument();
    expect(screen.getByTestId("online-entry").textContent).toContain("Devices can");
  });

  describe("an app update changed the relay", () => {
    const stale = {
      configured: true, worker_url: "https://relay.example", account_name: "Grace", needs_update: true, oauth_available: false, relay_built: true,
    };

    it("updates the relay before opening the room, so devices get the new band page", async () => {
      const tauri = await import("@/lib/tauri");
      vi.mocked(tauri.cloudRelayRedeploy).mockClear();
      vi.mocked(tauri.cloudRelayNewRoom).mockClear();
      vi.mocked(tauri.cloudRelayStatus).mockResolvedValueOnce(stale);
      await onlineRoom.start(async () => "2468");
      expect(tauri.cloudRelayRedeploy).toHaveBeenCalledTimes(1);
      expect(vi.mocked(tauri.cloudRelayRedeploy).mock.invocationCallOrder[0]!).toBeLessThan(
        vi.mocked(tauri.cloudRelayNewRoom).mock.invocationCallOrder[0]!,
      );
      expect(onlineRoom.getState().room).not.toBeNull();
    });

    it("leaves an up-to-date relay alone", async () => {
      const tauri = await import("@/lib/tauri");
      vi.mocked(tauri.cloudRelayRedeploy).mockClear();
      await onlineRoom.start(async () => "2468");
      expect(tauri.cloudRelayRedeploy).not.toHaveBeenCalled();
    });

    it("still opens the room on the old relay when the update fails", async () => {
      const tauri = await import("@/lib/tauri");
      const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
      vi.mocked(tauri.cloudRelayStatus).mockResolvedValueOnce(stale);
      vi.mocked(tauri.cloudRelayRedeploy).mockRejectedValueOnce(new Error("keychain refused"));
      await onlineRoom.start(async () => "2468");
      expect(onlineRoom.getState().room).not.toBeNull();
      expect(onlineRoom.getState().error).toBeNull();
      warn.mockRestore();
    });
  });
});
