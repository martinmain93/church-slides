import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { assessMachine } from "@/lib/machinePower";
import type { RecommendModelResponse } from "@/types/generated";

function rec(over: Partial<RecommendModelResponse>): RecommendModelResponse {
  return {
    kind: "recommend_model_response",
    model_size: "base.en",
    device: "cpu",
    compute_type: "int8",
    reason: "",
    has_cuda: false,
    has_mps: false,
    cpu_count: 8,
    total_ram_gb: 16,
    backend: "whisper",
    model_downloaded: true,
    download_mb: 0,
    ...over,
  } as RecommendModelResponse;
}

describe("assessMachine", () => {
  it("is happy with speech on Apple silicon's graphics chip", () => {
    expect(assessMachine(rec({ backend: "mlx", has_mps: true })).level).toBe("good");
  });

  it("on an older macOS, says updating to macOS 14 turns live AI on", () => {
    const mac = assessMachine(rec({ has_mps: true }));
    expect(mac.level).toBe("weak");
    expect(mac.summary).toMatch(/macOS 14/);
    expect(mac.summary).not.toMatch(/Groq/);
  });

  it("off Apple silicon, says live AI needs it, however fast the computer", () => {
    for (const r of [rec({}), rec({ cpu_count: 16, total_ram_gb: 64 })]) {
      const pc = assessMachine(r);
      expect(pc.level).toBe("weak");
      expect(pc.summary).toMatch(/Apple silicon/);
      expect(pc.summary).not.toMatch(/Groq|macOS 14/);
    }
  });
});

// --- The pill's warnings -------------------------------------------------------

const mockStatus = vi.fn<() => unknown>();
const mockSetMode = vi.fn<(mode: string) => Promise<void>>();
vi.mock("@/lib/aiDecisions", () => ({
  useAiStatus: () => mockStatus(),
  setAiMode: (mode: string) => mockSetMode(mode),
}));
vi.mock("@/lib/aiAvailability", async (importActual) => ({
  ...(await importActual<typeof import("@/lib/aiAvailability")>()),
  useAiAvailability: () => ({
    availability: { kind: "ready", model: "base.en", hosted: false },
    prepare: vi.fn(),
  }),
}));
const mockPower = vi.fn<() => unknown>();
vi.mock("@/lib/machinePower", async (importActual) => ({
  ...(await importActual<typeof import("@/lib/machinePower")>()),
  useMachinePower: () => mockPower(),
}));
const mockHealth = vi.fn<() => unknown>();
vi.mock("@/lib/pipelineHealth", () => ({ usePipelineHealth: () => mockHealth() }));

import {
  AIStatusPill,
  behindMessage,
  resetSlowComputerWarnings,
} from "@/components/presentation/AIStatusPill";
import { ToastProvider } from "@/lib/toast";

const health = (droppedChunks: number, lagSeconds = 0) => ({
  lagSeconds,
  droppedChunks,
  healthy: true,
  cpuPercent: 0,
  rssMb: 0,
  asrMs: 0,
  matcherMs: 0,
  scorerMs: 0,
  scorerSpans: 0,
  asrRefused: false,
  lowPowerMode: false,
  batteryPercent: null as number | null,
  onBattery: false,
});

describe("AIStatusPill on a slow computer", () => {
  beforeEach(() => {
    resetSlowComputerWarnings();
    mockSetMode.mockResolvedValue(undefined);
    mockHealth.mockReturnValue(null);
  });
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("says once that live AI needs Apple silicon, when the AI is switched on", async () => {
    mockPower.mockReturnValue(assessMachine(rec({ cpu_count: 2 })));
    mockStatus.mockReturnValue({ mode: "off", state: "idle" });
    render(<ToastProvider><AIStatusPill /></ToastProvider>);
    await act(async () => {
      fireEvent.click(screen.getByTestId("ai-toggle"));
    });
    expect(screen.getByText(/needs a Mac with Apple silicon/)).toBeTruthy();

    // Off and on again: no second warning.
    cleanup();
    render(<ToastProvider><AIStatusPill /></ToastProvider>);
    await act(async () => {
      fireEvent.click(screen.getByTestId("ai-toggle"));
    });
    expect(screen.queryByText(/needs a Mac with Apple silicon/)).toBeNull();
  });

  it("says nothing on a fast computer, or before the engine has answered", async () => {
    mockStatus.mockReturnValue({ mode: "off", state: "idle" });
    for (const power of [assessMachine(rec({ backend: "mlx", has_mps: true })), null]) {
      mockPower.mockReturnValue(power);
      render(<ToastProvider><AIStatusPill /></ToastProvider>);
      await act(async () => {
        fireEvent.click(screen.getByTestId("ai-toggle"));
      });
      expect(screen.queryByText(/Apple silicon|Groq/)).toBeNull();
      cleanup();
    }
  });

  it("says so when the engine actually falls behind during the service", () => {
    mockPower.mockReturnValue(assessMachine(rec({})));
    mockStatus.mockReturnValue({ mode: "active", state: "tracking" });
    mockHealth.mockReturnValue(health(5));
    const { rerender } = render(<ToastProvider><AIStatusPill /></ToastProvider>);
    // Dropped before the AI came on doesn't count; a few more is fine.
    mockHealth.mockReturnValue(health(15));
    rerender(<ToastProvider><AIStatusPill /></ToastProvider>);
    expect(screen.queryByText(/falling behind/)).toBeNull();
    mockHealth.mockReturnValue(health(30));
    rerender(<ToastProvider><AIStatusPill /></ToastProvider>);
    expect(screen.getByText(/falling behind the band/)).toBeTruthy();
  });

  it("blames the computer's power saving, not its speed, when that is on", () => {
    // A fast Mac run down to a critical battery stayed ~4x slower after it was
    // plugged in, and the warning pointed at cloud speech (2026-10-14).
    const base = { ...health(0), healthy: false };
    expect(behindMessage({ ...base, lowPowerMode: true, batteryPercent: 80, onBattery: true }))
      .toMatch(/Low Power Mode/);
    const low = behindMessage({ ...base, batteryPercent: 4, onBattery: false });
    expect(low).toMatch(/battery is very low \(4%\).*even on the charger/);
    expect(low).not.toMatch(/Groq/);
    expect(behindMessage({ ...base, batteryPercent: 4, onBattery: true })).toMatch(/Plug it in/);
    expect(behindMessage({ ...base, batteryPercent: 60, onBattery: true })).toMatch(/busy apps/);
    expect(behindMessage({ ...base, batteryPercent: null })).toMatch(/busy apps/);
  });

  it("says once when Groq turns requests away, on any computer", () => {
    // Its rate limit left a live service with almost no transcript, and
    // nothing on screen said why the AI had stopped moving (2026-10-13).
    mockPower.mockReturnValue(null);
    mockStatus.mockReturnValue({ mode: "active", state: "tracking" });
    mockHealth.mockReturnValue(health(0));
    const { rerender } = render(<ToastProvider><AIStatusPill /></ToastProvider>);
    expect(screen.queryByText(/turning requests away/)).toBeNull();
    mockHealth.mockReturnValue({ ...health(0), asrRefused: true });
    rerender(<ToastProvider><AIStatusPill /></ToastProvider>);
    expect(screen.getByText(/turning requests away/)).toBeTruthy();
  });
});
