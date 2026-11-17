import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  cloudRelaySetupToken: vi.fn(),
  cloudRelaySetupOauth: vi.fn(),
  cloudRelayHealth: vi.fn(async () => true),
  cloudRelayForget: vi.fn(async () => undefined),
  cloudRelayRedeploy: vi.fn(),
  cloudRelayOpenPage: vi.fn(async () => undefined),
}));
vi.mock("@/lib/tauri", () => api);

import { OnlineRoomPanel } from "@/components/band/OnlineRoomPanel";
import type { OnlineRoomApi } from "@/lib/band/useOnlineRoom";
import type { CloudStatus } from "@/lib/tauri";

const notSetUp: CloudStatus = {
  configured: false, worker_url: null, account_name: null, needs_update: false, oauth_available: false, relay_built: true,
};
const setUp: CloudStatus = {
  ...notSetUp, configured: true, worker_url: "https://church-slides-relay.grace.workers.dev", account_name: "Grace Church",
};

function online(over: Partial<OnlineRoomApi> = {}): OnlineRoomApi {
  return {
    status: notSetUp, refreshStatus: vi.fn(async () => undefined), room: null, connection: "off", clients: 0, error: null,
    start: vi.fn(async () => undefined), stop: vi.fn(), newLinks: vi.fn(async () => undefined), ...over,
  };
}

describe("online room panel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.cloudRelayHealth.mockResolvedValue(true);
  });

  it("sets up with a pasted token, asking which account when there are several", async () => {
    const o = online();
    api.cloudRelaySetupToken
      .mockResolvedValueOnce({ outcome: "choose_account", accounts: [{ id: "a1", name: "Grace" }, { id: "a2", name: "Personal" }] })
      .mockResolvedValueOnce({ outcome: "done", status: setUp });
    render(<OnlineRoomPanel online={o} />);
    fireEvent.change(screen.getByTestId("online-token"), { target: { value: "x".repeat(40) } });
    await act(async () => screen.getByTestId("online-setup").click());
    expect(api.cloudRelaySetupToken).toHaveBeenLastCalledWith("x".repeat(40));
    await act(async () => {
      fireEvent.change(screen.getByRole("combobox"), { target: { value: "a1" } });
    });
    expect(api.cloudRelaySetupToken).toHaveBeenLastCalledWith("x".repeat(40), "a1");
    expect(o.refreshStatus).toHaveBeenCalled();
  });

  it("offers Cloudflare sign-in only when the build can do it", () => {
    render(<OnlineRoomPanel online={online({ status: { ...notSetUp, oauth_available: true } })} />);
    expect(screen.getByText("Sign in with Cloudflare")).toBeInTheDocument();
  });

  it("once set up, starts a room; started, shows the links and can stop", async () => {
    const o = online({ status: setUp });
    const { rerender } = render(<OnlineRoomPanel online={o} />);
    await act(async () => undefined); // health check
    expect(screen.getByText(/Grace Church/)).toBeInTheDocument();
    await act(async () => screen.getByTestId("online-start").click());
    expect(o.start).toHaveBeenCalled();

    const started = online({
      status: setUp, connection: "live", clients: 3,
      room: { bandUrl: "https://r.example/r/abc/band?k=1", leaderUrl: "https://r.example/r/abc/leader?k=1&pin=2468" },
    });
    rerender(<OnlineRoomPanel online={started} />);
    expect(screen.getByLabelText("QR code for Band (online)")).toBeInTheDocument();
    expect(screen.getByText("3 devices online")).toBeInTheDocument();
    act(() => screen.getByTestId("online-stop").click());
    expect(started.stop).toHaveBeenCalled();
  });

  it("holds off starting while a new relay address is still coming online", async () => {
    api.cloudRelayHealth.mockResolvedValue(false);
    render(<OnlineRoomPanel online={online({ status: setUp })} />);
    await act(async () => undefined);
    expect(screen.getByText(/still coming online/)).toBeInTheDocument();
    expect(screen.getByTestId("online-start")).toBeDisabled();
  });

  it("opens the room by itself when set up — the shortcut", async () => {
    const o = online({ status: setUp });
    render(<OnlineRoomPanel online={o} autoStart />);
    await act(async () => undefined); // health check answers
    expect(o.start).toHaveBeenCalledTimes(1);
  });

  it("doesn't open it by itself while the new address isn't answering yet", async () => {
    api.cloudRelayHealth.mockResolvedValue(false);
    const o = online({ status: setUp });
    render(<OnlineRoomPanel online={o} autoStart />);
    await act(async () => undefined);
    expect(o.start).not.toHaveBeenCalled();
    api.cloudRelayHealth.mockResolvedValue(true);
  });

  it("closing goes back to Open / Disconnect and doesn't reopen by itself", async () => {
    const room = { bandUrl: "https://r/r/a/band?k=1", leaderUrl: "https://r/r/a/leader?k=1&pin=1" };
    const onClosed = vi.fn();
    const open = online({ status: setUp, room, connection: "live" });
    const { rerender } = render(<OnlineRoomPanel online={open} autoStart onClosed={onClosed} />);
    act(() => screen.getByTestId("online-stop").click());
    expect(onClosed).toHaveBeenCalled();
    expect(open.stop).toHaveBeenCalled();
    // The parent turns the shortcut off after a close.
    const closed = online({ status: setUp });
    rerender(<OnlineRoomPanel online={closed} autoStart={false} onClosed={onClosed} />);
    await act(async () => undefined);
    expect(screen.getByTestId("online-start")).toBeInTheDocument();
    expect(screen.getByText("Disconnect Cloudflare")).toBeInTheDocument();
    expect(closed.start).not.toHaveBeenCalled();
  });

  it("asks twice before making new links", async () => {
    const o = online({ status: setUp, room: { bandUrl: "https://r/b", leaderUrl: "https://r/l" }, connection: "live" });
    render(<OnlineRoomPanel online={o} />);
    act(() => screen.getByTestId("online-new-links").click());
    expect(o.newLinks).not.toHaveBeenCalled();
    expect(screen.getByTestId("online-new-links").textContent).toContain("old QR codes will stop working");
    await act(async () => screen.getByTestId("online-new-links").click());
    expect(o.newLinks).toHaveBeenCalledTimes(1);
  });
});
