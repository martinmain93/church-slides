import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/components/home/SpeechModelStatus", () => ({ SpeechModelStatus: () => null }));

const peers = vi.hoisted(() => ({ list: [] as { id: string; name: string; manual: boolean }[] }));
vi.mock("@/lib/peerSync/usePeers", () => ({
  usePeers: () => ({ peers: peers.list, me: { name: "Back desk", addresses: ["192.168.1.20"], discovery_error: null, network_code: "D612" }, refreshMe: () => {} }),
}));

const screensApi = vi.hoisted(() => ({
  listScreens: vi.fn(),
  moveWindowToScreen: vi.fn(),
  setScreenAvoid: vi.fn(),
  identifyScreens: vi.fn(),
}));
vi.mock("@/lib/presentation/projection", async (orig) => ({
  ...(await orig<typeof import("@/lib/presentation/projection")>()),
  ...screensApi,
}));

import { HomeScreen } from "@/components/home/HomeScreen";
import { PeerSyncButton } from "@/components/peerSync/PeerSyncButton";
import { ScreensDialog, windowName } from "@/components/presentation/ScreensDialog";
import { ConfirmProvider } from "@/lib/confirm";
import type { ScreenInfo } from "@/lib/presentation/projection";
import * as tauri from "@/lib/tauri";
import { ToastProvider } from "@/lib/toast";

function renderHome() {
  return render(
    <ToastProvider>
      <ConfirmProvider>
        <HomeScreen
          onCreateSetlist={vi.fn()}
          onCopySetlist={vi.fn()}
          onOpenSetlist={vi.fn()}
          onContinueService={vi.fn()}
          onOpenLibrary={vi.fn()}
          onStartService={vi.fn()}
        />
      </ConfirmProvider>
    </ToastProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  peers.list = [];
});

describe("getting a setlist from another computer", () => {
  beforeEach(() => {
    vi.spyOn(tauri, "listSongs").mockResolvedValue([]);
  });

  it("is on Home of a fresh install, with no other computer found yet", async () => {
    vi.spyOn(tauri, "listSetlists").mockResolvedValue([]);
    renderHome();
    fireEvent.click(await screen.findByTestId("peer-sync-button"));
    // The address the other computer types in when discovery is blocked.
    expect(screen.getByText("192.168.1.20")).toBeInTheDocument();
  });

  it("hides on Home once there are setlists, until another computer appears", async () => {
    vi.spyOn(tauri, "listSetlists").mockResolvedValue([
      { id: "a", name: "Sunday", created_at: 1, last_used_at: null, song_titles: ["Way Maker"] },
    ]);
    renderHome();
    expect(await screen.findByText("Sunday")).toBeInTheDocument();
    expect(screen.queryByTestId("peer-sync-button")).toBeNull();
  });

  it("offers only Get without a setlist, and both with one", () => {
    peers.list = [{ id: "p1", name: "Martin's Mac", manual: false }];
    const { unmount } = render(<PeerSyncButton onMessage={vi.fn()} />);
    // A newcomer opens the panel by itself.
    expect(screen.getByText("Get a service")).toBeInTheDocument();
    expect(screen.queryByText("Send this service")).toBeNull();
    unmount();
    render(<PeerSyncButton setlistId="s1" setlistName="Sunday" onMessage={vi.fn()} openSignal={1} />);
    expect(screen.getByText("Send this service")).toBeInTheDocument();
    expect(screen.getByText("Get a service")).toBeInTheDocument();
  });

  it("shows this network's code, and sends the whole library", async () => {
    peers.list = [{ id: "p1", name: "Martin's Mac", manual: false }];
    const send = vi.spyOn(tauri, "sendLibraryToPeer").mockResolvedValue("offer_1");
    render(<PeerSyncButton onMessage={vi.fn()} openSignal={1} />);
    expect(screen.getByTestId("peer-network")).toHaveTextContent("Network D612");
    fireEvent.click(screen.getByTestId("peer-send-library"));
    await waitFor(() => expect(send).toHaveBeenCalledWith("p1"));
    expect(await screen.findByTestId("peer-flow-status")).toHaveTextContent("Sent your library");
  });
});

describe("Screens", () => {
  const displays: ScreenInfo[] = [
    { key: "Built-in|2880x1800", name: "Built-in", x: 0, y: 0, width: 1440, height: 900, pixel_width: 2880, pixel_height: 1800, operator: true, avoid: false, windows: [] },
    { key: "Epson|1920x1080", name: "Epson", x: 1440, y: 0, width: 1920, height: 1080, pixel_width: 1920, pixel_height: 1080, operator: false, avoid: false, windows: ["band-1"] },
    { key: "Blackmagic|1920x1080", name: "Blackmagic", x: 3360, y: 0, width: 1920, height: 1080, pixel_width: 1920, pixel_height: 1080, operator: false, avoid: false, windows: ["audience"] },
  ];

  it("names windows the way the operator knows them", () => {
    expect(windowName("audience")).toBe("Audience");
    expect(windowName("band-2")).toBe("Band screen 2");
  });

  it("moves a window to another display and marks one Don't use", async () => {
    screensApi.listScreens.mockResolvedValue(displays);
    screensApi.moveWindowToScreen.mockResolvedValue(undefined);
    screensApi.setScreenAvoid.mockResolvedValue(undefined);
    render(<ScreensDialog onClose={vi.fn()} onError={vi.fn()} />);

    const audience = (await screen.findByTestId("screen-for-audience")) as HTMLSelectElement;
    expect(audience.value).toBe("Blackmagic|1920x1080");
    fireEvent.change(audience, { target: { value: "Epson|1920x1080" } });
    await waitFor(() => expect(screensApi.moveWindowToScreen).toHaveBeenCalledWith("audience", "Epson|1920x1080"));

    // The operator's own display has no Don't use: it's never picked anyway.
    expect(screen.queryByTestId("avoid-1")).toBeNull();
    fireEvent.click(screen.getByTestId("avoid-3"));
    await waitFor(() => expect(screensApi.setScreenAvoid).toHaveBeenCalledWith("Blackmagic|1920x1080", true));
  });
});
