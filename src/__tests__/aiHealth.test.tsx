import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mockStatus = vi.fn<() => unknown>(() => null);
vi.mock("@/lib/aiDecisions", () => ({ useAiStatus: () => mockStatus() }));

const mockAvailability = vi.fn<() => unknown>();
vi.mock("@/lib/aiAvailability", async (importActual) => {
  const actual = await importActual<typeof import("@/lib/aiAvailability")>();
  return { ...actual, useAiAvailability: () => ({ availability: mockAvailability() }) };
});

const mockStartFailure = vi.fn<() => unknown>(() => null);
vi.mock("@/lib/recordingClient", () => ({ useSessionStartFailure: () => mockStartFailure() }));
vi.mock("@/lib/pipelineHealth", () => ({ usePipelineHealth: () => null }));

// Levels from the microphone: a test pushes one through `level()`.
let onLevel: (() => void) | null = null;
vi.mock("@/lib/sidecarConnection", () => ({
  getSharedConnection: () =>
    Promise.resolve({
      onMessage: (_kind: string, cb: () => void) => {
        onLevel = cb;
        return () => undefined;
      },
    }),
}));

import { describeAiHealth, resetAiHealthDismissals, type AiHealthInputs } from "@/lib/aiHealth";
import {
  AiHealthCorner,
  AiHealthNotice,
  AiHealthProvider,
} from "@/components/presentation/AiHealthIndicator";

const READY = { kind: "ready", model: "small.en", hosted: false } as const;

function inputs(over: Partial<AiHealthInputs> = {}): AiHealthInputs {
  return {
    availability: READY,
    status: { mode: "active" },
    startFailure: null,
    micLive: true,
    micGone: false,
    asrRefused: false,
    aiOffIsProblem: true,
    ...over,
  };
}

describe("describeAiHealth", () => {
  it("is ok once the AI is on and hearing audio", () => {
    expect(describeAiHealth(inputs()).kind).toBe("ok");
  });

  it("is starting while the model downloads or the session hasn't reported", () => {
    expect(
      describeAiHealth(inputs({ availability: { kind: "downloading", model: "x", mb: 1, mbDone: null } }))
        .kind,
    ).toBe("starting");
    expect(describeAiHealth(inputs({ status: null })).kind).toBe("starting");
    expect(describeAiHealth(inputs({ micLive: false })).kind).toBe("starting");
  });

  it("flags the AI switched off on a single screen, but not in the operator view", () => {
    const off = inputs({ status: { mode: "off" } });
    const h = describeAiHealth(off);
    expect(h.kind === "problem" && h.problem.id).toBe("ai_off");
    expect(describeAiHealth({ ...off, aiOffIsProblem: false }).kind).toBe("idle");
  });

  it("flags no audio once the microphone has been silent long enough", () => {
    const h = describeAiHealth(inputs({ micLive: false, micGone: true }));
    expect(h.kind === "problem" && h.problem.id).toBe("no_audio");
  });

  it("flags a model that was never downloaded, with the reason", () => {
    const h = describeAiHealth(
      inputs({ status: null, availability: { kind: "needs_download", model: "small.en", mb: 480 } }),
    );
    expect(h.kind).toBe("problem");
    if (h.kind === "problem") expect(h.problem.detail).toMatch(/480 MB/);
  });

  it("offers a retry for a session that couldn't start", () => {
    const retry = vi.fn();
    const h = describeAiHealth(
      inputs({ startFailure: { message: "no microphone.", retry, aiMode: "active" } }),
    );
    expect(h.kind === "problem" && h.problem.retry).toBe(retry);
    if (h.kind === "problem") expect(h.problem.detail).not.toMatch(/\.\./);
  });
});

function Single() {
  return (
    <AiHealthProvider aiOffIsProblem>
      <AiHealthNotice />
      <AiHealthCorner />
    </AiHealthProvider>
  );
}

describe("corner indicator and notice", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mockAvailability.mockReturnValue(READY);
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    mockStatus.mockReset().mockReturnValue(null);
    mockStartFailure.mockReturnValue(null);
    act(() => resetAiHealthDismissals());
    localStorage.clear();
  });

  it("spins, then ticks and clears once audio arrives", async () => {
    mockStatus.mockReturnValue({ mode: "active", state: "idle" });
    render(<Single />);
    await act(async () => {}); // the level subscription resolves
    expect(screen.getByTestId("ai-health-corner").dataset.kind).toBe("starting");

    act(() => {
      onLevel?.();
      vi.advanceTimersByTime(1000);
    });
    expect(screen.getByTestId("ai-health-corner").dataset.kind).toBe("ok");

    act(() => {
      onLevel?.();
      vi.advanceTimersByTime(1700);
    });
    expect(screen.queryByTestId("ai-health-corner")).toBeNull();
  });

  it("shows a red X with a notice that dismissing clears", () => {
    mockStatus.mockReturnValue({ mode: "off", state: "idle" });
    render(<Single />);
    // Problems settle before they show.
    expect(screen.queryByTestId("ai-health-notice")).toBeNull();
    act(() => vi.advanceTimersByTime(2000));
    expect(screen.getByTestId("ai-health-corner").dataset.kind).toBe("problem");
    expect(screen.getByTestId("ai-health-notice").textContent).toMatch(/The AI is off/);

    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByTestId("ai-health-notice")).toBeNull();
    expect(screen.queryByTestId("ai-health-corner")).toBeNull();
  });

  it("shows nothing in the operator view while the AI is off", () => {
    mockStatus.mockReturnValue({ mode: "off", state: "idle" });
    render(
      <AiHealthProvider aiOffIsProblem={false}>
        <AiHealthNotice />
      </AiHealthProvider>,
    );
    act(() => vi.advanceTimersByTime(3000));
    expect(screen.queryByTestId("ai-health-notice")).toBeNull();
  });
});
