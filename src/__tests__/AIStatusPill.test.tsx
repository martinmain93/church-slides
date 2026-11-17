import { act, render, screen, cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const mockStatus = vi.fn<() => unknown>();
const mockSetMode = vi.fn<(mode: string) => Promise<void>>();
vi.mock("@/lib/aiDecisions", () => ({
  useAiStatus: () => mockStatus(),
  setAiMode: (mode: string) => mockSetMode(mode),
}));

// Whether live AI can run at all. Real by default would ask the Tauri shell,
// which jsdom doesn't have — so pin it: ready, unless a test says otherwise.
const mockAvailability = vi.fn<() => unknown>();
vi.mock("@/lib/aiAvailability", async (importActual) => {
  const actual = await importActual<typeof import("@/lib/aiAvailability")>();
  return {
    ...actual,
    useAiAvailability: () => ({ availability: mockAvailability(), prepare: vi.fn() }),
  };
});

const mockStartFailure = vi.fn<() => unknown>(() => null);
vi.mock("@/lib/recordingClient", () => ({
  useSessionStartFailure: () => mockStartFailure(),
}));

import { AIStatusPill } from "@/components/presentation/AIStatusPill";

const READY = { kind: "ready", model: "small.en", hosted: false };
mockAvailability.mockReturnValue(READY);

afterEach(() => {
  cleanup();
  mockStatus.mockReset();
  mockSetMode.mockReset();
  mockAvailability.mockReturnValue(READY);
  mockStartFailure.mockReturnValue(null);
  localStorage.clear();
});

describe("AIStatusPill when live AI can't run", () => {
  it("says AI is unavailable, with the reason, rather than 'starting' forever", () => {
    // No engine (a build without it, or one that can't start here) used to
    // leave the pill on "AI: starting" for the whole service.
    mockStatus.mockReturnValue(null);
    mockAvailability.mockReturnValue({
      kind: "engine_unavailable",
      reason: "This copy of the app was built without the AI engine",
    });
    render(<AIStatusPill />);
    const pill = screen.getByText("AI unavailable");
    expect(pill.closest("[title]")?.getAttribute("title")).toMatch(/built without the AI engine/);
    expect(pill.closest("[title]")?.getAttribute("title")).toMatch(/presenting by hand/);
    expect(screen.queryByTestId("ai-toggle")).toBeNull();
  });

  it("keeps live AI off on a computer too slow for it, saying so", () => {
    mockStatus.mockReturnValue({ mode: "off", state: "idle" });
    mockAvailability.mockReturnValue({
      kind: "too_slow",
      reason: "Too slow for live following at full accuracy.",
    });
    render(<AIStatusPill />);
    const pill = screen.getByText("AI unavailable on this computer");
    expect(pill.closest("[title]")?.getAttribute("title")).toMatch(/cloud option/);
    // nothing to switch on
    expect(screen.queryByTestId("ai-toggle")).toBeNull();
  });

  it("says when it's checking this computer", () => {
    mockStatus.mockReturnValue({ mode: "off", state: "idle" });
    mockAvailability.mockReturnValue({ kind: "checking_machine" });
    render(<AIStatusPill />);
    expect(screen.getByText("AI: checking this computer")).toBeTruthy();
    expect(screen.queryByTestId("ai-toggle")).toBeNull();
  });

  it("says a failed model download needs internet", () => {
    mockStatus.mockReturnValue({ mode: "off", state: "idle" });
    mockAvailability.mockReturnValue({
      kind: "download_failed",
      model: "small.en",
      message: "Couldn't download the small.en speech model (about 480 MB). It needs an internet connection the first time",
    });
    render(<AIStatusPill />);
    const pill = screen.getByText("AI: needs internet");
    expect(pill.closest("[title]")?.getAttribute("title")).toMatch(/480 MB/);
  });

  it("shows a download in progress", () => {
    mockStatus.mockReturnValue({ mode: "off", state: "idle" });
    mockAvailability.mockReturnValue({ kind: "downloading", model: "small.en", mb: 480, mbDone: null });
    render(<AIStatusPill />);
    expect(screen.getByText("AI: downloading model")).toBeInTheDocument();
  });

  it("says the session couldn't start, with the reason and a retry", async () => {
    // A microphone that wouldn't open used to leave "AI: starting" up for the
    // whole service, with the reason only in the console.
    const retry = vi.fn();
    mockStatus.mockReturnValue(null);
    mockStartFailure.mockReturnValue({ message: "Error opening InputStream", retry, aiMode: "active" });
    render(<AIStatusPill />);
    const pill = screen.getByText(/couldn.t start/i);
    expect(pill.closest("[title]")?.getAttribute("title")).toMatch(/Error opening InputStream/);
    await act(async () => (pill.closest("button") as HTMLButtonElement).click());
    expect(retry).toHaveBeenCalled();
  });

  it("shows a start failure over a stale AI status", () => {
    // The director reports in as soon as it is built, before the microphone
    // opens, so a mic that then fails left a status behind — and the failure
    // was only shown when there was none.
    mockStatus.mockReturnValue({ mode: "active", state: "idle" });
    mockStartFailure.mockReturnValue({
      message: "Error opening InputStream",
      retry: vi.fn(),
      aiMode: "active",
    });
    render(<AIStatusPill />);
    expect(screen.getByText(/couldn.t start/i)).toBeInTheDocument();
    expect(screen.queryByText(/AI: ready/)).toBeNull();
  });

  it("keeps a start failure quiet while the AI is off, and says it when turned on", async () => {
    // No microphone at all, AI off: nothing needs the mic yet, so a permanent
    // amber "couldn't start" would read like a crash.
    mockStatus.mockReturnValue({ mode: "off", state: "idle" });
    mockStartFailure.mockReturnValue({
      message: "Error opening InputStream",
      retry: vi.fn(),
      aiMode: "off",
    });
    render(<AIStatusPill />);
    expect(screen.queryByText(/couldn.t start/i)).toBeNull();
    await act(async () => screen.getByTestId("ai-toggle").click());
    // Turning on can't work without the session: say why, not "couldn't switch".
    expect(mockSetMode).not.toHaveBeenCalled();
    expect(screen.getByText(/couldn.t start/i)).toBeInTheDocument();
  });

  it("invites turning the AI on when an AI-off start failed before any status arrived", async () => {
    mockStatus.mockReturnValue(null);
    mockStartFailure.mockReturnValue({
      message: "whisper backend failed to load",
      retry: vi.fn(),
      aiMode: "off",
    });
    render(<AIStatusPill />);
    expect(screen.queryByText("AI: starting")).toBeNull();
    await act(async () => screen.getByTestId("ai-toggle").click());
    expect(screen.getByText(/couldn.t start/i)).toBeInTheDocument();
  });

  it("still invites turning AI on when the model just needs downloading", () => {
    mockStatus.mockReturnValue({ mode: "off", state: "idle" });
    mockAvailability.mockReturnValue({ kind: "needs_download", model: "small.en", mb: 480 });
    render(<AIStatusPill />);
    const toggle = screen.getByTestId("ai-toggle");
    expect(toggle.getAttribute("title")).toMatch(/internet connection once/);
  });
});

describe("AIStatusPill lost mode", () => {
  it("says the AI is searching and deliberately holding the slide", () => {
    // Lost mode holds the current slide rather than guessing. Silence here
    // reads as a crash from the front of the room, and the operator's
    // reasonable response — taking over — comes exactly when the tracker was
    // seconds from re-anchoring itself.
    mockStatus.mockReturnValue({ mode: "active", state: "uncertain" });
    render(<AIStatusPill />);
    expect(screen.getByText("AI: searching")).toBeInTheDocument();
    expect(screen.getByText("holding slide")).toBeInTheDocument();
  });

  it("shadow mode still wins over lost mode", () => {
    // In shadow the AI never drives slides at all, so "searching" would imply
    // an autonomy it doesn't have.
    mockStatus.mockReturnValue({ mode: "shadow", state: "uncertain" });
    render(<AIStatusPill />);
    expect(screen.getByText("AI: shadow")).toBeInTheDocument();
    expect(screen.queryByText("AI: searching")).toBeNull();
  });

  it("returns to tracking once it re-anchors", () => {
    mockStatus.mockReturnValue({ mode: "active", state: "tracking" });
    render(<AIStatusPill />);
    expect(screen.getByText("AI: tracking")).toBeInTheDocument();
    expect(screen.queryByText("AI: searching")).toBeNull();
  });
});

describe("AIStatusPill toggle", () => {
  it("invites the operator to turn the AI on when it's off", async () => {
    mockStatus.mockReturnValue({ mode: "off", state: "idle" });
    mockSetMode.mockResolvedValue();
    render(<AIStatusPill />);
    const button = screen.getByRole("button", { name: /turn on ai tracking/i });
    await act(async () => button.click());
    // Nothing chosen in the debug panel → on means Active.
    expect(mockSetMode).toHaveBeenCalledWith("active");
  });

  it("turns on in Shadow only when a developer chose it, ignoring the old Settings default", async () => {
    // The retired Settings picker's Shadow must not leak into "turn on".
    localStorage.setItem("auto-presenter.ai-prefs", JSON.stringify({ mode: "shadow" }));
    mockStatus.mockReturnValue({ mode: "off", state: "idle" });
    mockSetMode.mockResolvedValue();
    render(<AIStatusPill />);
    await act(async () => screen.getByTestId("ai-toggle").click());
    expect(mockSetMode).toHaveBeenCalledWith("active");
    cleanup();
    localStorage.setItem("auto-presenter.ai-on-mode", "shadow");
    render(<AIStatusPill />);
    await act(async () => screen.getByTestId("ai-toggle").click());
    expect(mockSetMode).toHaveBeenLastCalledWith("shadow");
  });

  it("turns the AI off from any on state", async () => {
    for (const status of [
      { mode: "active", state: "tracking" },
      { mode: "active", state: "uncertain" },
      { mode: "active", state: "following_human", lastOverrideTSeconds: Date.now() / 1000 },
      { mode: "shadow", state: "tracking" },
    ]) {
      mockStatus.mockReturnValue(status);
      mockSetMode.mockResolvedValue();
      render(<AIStatusPill />);
      await act(async () => screen.getByTestId("ai-toggle").click());
      expect(mockSetMode).toHaveBeenLastCalledWith("off");
      cleanup();
    }
  });

  it("gives focus back so the space bar keeps driving slides", async () => {
    mockStatus.mockReturnValue({ mode: "active", state: "tracking" });
    mockSetMode.mockResolvedValue();
    render(<AIStatusPill />);
    const button = screen.getByTestId("ai-toggle");
    button.focus();
    await act(async () => button.click());
    expect(document.activeElement).not.toBe(button);
  });

  it("says so when the switch fails", async () => {
    mockStatus.mockReturnValue({ mode: "off", state: "idle" });
    mockSetMode.mockRejectedValue(new Error("no active session"));
    render(<AIStatusPill />);
    await act(async () => screen.getByTestId("ai-toggle").click());
    expect(screen.getByText(/couldn.t switch/i)).toBeInTheDocument();
  });

  it("isn't clickable before the AI has reported in", () => {
    mockStatus.mockReturnValue(null);
    render(<AIStatusPill />);
    expect(screen.queryByTestId("ai-toggle")).toBeNull();
    expect(screen.getByText("AI: starting")).toBeInTheDocument();
  });
});

describe("AIStatusPill in the operator view", () => {
  it("opens Assist / Auto / Off instead of switching straight on", async () => {
    mockStatus.mockReturnValue({ mode: "off", state: "idle" });
    mockSetMode.mockResolvedValue();
    render(<AIStatusPill operator />);
    await act(async () => screen.getByTestId("ai-toggle").click());
    expect(mockSetMode).not.toHaveBeenCalled();
    expect(screen.getByText("Assist me")).toBeTruthy();
    expect(screen.getByText("Change slides for me")).toBeTruthy();
    expect(screen.getByText("AI off")).toBeTruthy();
    await act(async () => screen.getByText("Assist me").click());
    expect(mockSetMode).toHaveBeenCalledWith("assist");
  });

  it("says when it's assisting rather than driving", () => {
    mockStatus.mockReturnValue({ mode: "assist", state: "tracking" });
    render(<AIStatusPill operator />);
    expect(screen.getByText("AI: assisting")).toBeTruthy();
    expect(screen.queryByText("AI: tracking")).toBeNull();
  });

  it("remembers Auto as what 'on' means next time", async () => {
    const { operatorOnMode } = await import("@/lib/aiPrefs");
    expect(operatorOnMode()).toBe("assist");
    mockStatus.mockReturnValue({ mode: "assist", state: "tracking" });
    mockSetMode.mockResolvedValue();
    render(<AIStatusPill operator />);
    await act(async () => screen.getByTestId("ai-toggle").click());
    await act(async () => screen.getByText("Change slides for me").click());
    expect(mockSetMode).toHaveBeenCalledWith("active");
    expect(operatorOnMode()).toBe("active");
  });

  it("on a computer fast enough only for Assist, offers Assist and never Auto", async () => {
    mockAvailability.mockReturnValue({ ...READY, checked: true, assistOnly: true });
    mockStatus.mockReturnValue({ mode: "off", state: "idle" });
    mockSetMode.mockResolvedValue();
    localStorage.setItem("auto-presenter.operator-ai-on-mode", "active"); // last chose Auto
    render(<AIStatusPill operator />);
    await act(async () => screen.getByTestId("ai-toggle").click());
    const auto = screen.getByText("Change slides for me").closest("button");
    expect(auto?.disabled).toBe(true);
    expect(screen.getByText(/too slow for the AI to change slides on time/)).toBeTruthy();
    await act(async () => screen.getByText("Assist me").click());
    expect(mockSetMode).toHaveBeenCalledWith("assist");
    expect(mockSetMode).not.toHaveBeenCalledWith("active");
  });

  it("outside the operator view, an Assist-only computer has no AI to switch on", () => {
    mockAvailability.mockReturnValue({ ...READY, checked: true, assistOnly: true });
    mockStatus.mockReturnValue({ mode: "off", state: "idle" });
    render(<AIStatusPill />);
    const pill = screen.getByText("AI unavailable on this computer");
    expect(pill.closest("[title]")?.getAttribute("title")).toMatch(/operator view/);
    expect(screen.queryByTestId("ai-toggle")).toBeNull();
  });

  it("outside the operator view, a click still just switches on", async () => {
    mockStatus.mockReturnValue({ mode: "off", state: "idle" });
    mockSetMode.mockResolvedValue();
    render(<AIStatusPill />);
    await act(async () => screen.getByTestId("ai-toggle").click());
    expect(mockSetMode).toHaveBeenCalledWith("active");
  });
});
