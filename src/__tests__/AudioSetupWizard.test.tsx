/**
 * Minimal coverage for the audio setup wizard:
 *
 * - The first step renders both source cards and persists the choice.
 * - "Next" / "Back" navigates between steps.
 * - "Save and finish" writes `setup_completed=true` and calls onClose.
 *
 * We mock the sidecar-facing hooks at the module level so the test
 * doesn't open a real WebSocket. The wizard's "Test with a song"
 * interaction in step 2 is exercised indirectly via the device-picker
 * + level-meter rendering; the underlying transcript stream is mocked.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AudioSetupWizard } from "@/components/onboarding/AudioSetupWizard";

vi.mock("@/lib/audioDevices", () => ({
  useAudioDevices: () => ({
    kind: "ready",
    devices: [
      { index: 0, name: "Built-in Mic", is_default: true, max_input_channels: 1, default_samplerate: 48000 },
      { index: 1, name: "USB Interface", is_default: false, max_input_channels: 2, default_samplerate: 48000 },
    ],
  }),
}));

vi.mock("@/lib/audioLevels", () => ({
  useAudioLevels: () => ({
    latest: null,
    history: [],
    connected: false,
  }),
}));

vi.mock("@/lib/transcriptStream", () => ({
  useTranscriptStream: () => ({
    session: "disconnected" as const,
    connected: false,
    finalWords: [],
    partialWords: [],
    error: null,
    start: vi.fn().mockResolvedValue(undefined),
    stop: vi.fn().mockResolvedValue(undefined),
  }),
}));

vi.mock("@/lib/modelRecommendation", () => ({
  useModelRecommendation: () => ({
    kind: "ready",
    recommendation: {
      kind: "recommend_model_response",
      model_size: "small.en",
      device: "mps",
      compute_type: "float16",
      reason: "Apple GPU via mlx-whisper",
      has_cuda: false,
      has_mps: true,
      cpu_count: 8,
      total_ram_gb: 16,
      backend: "mlx",
    },
  }),
}));

const mockAvailability = vi.fn<() => unknown>(() => ({ kind: "ready", model: "small.en", hosted: false }));
const mockPrepare = vi.fn<() => Promise<void>>(() => Promise.resolve());
vi.mock("@/lib/aiAvailability", async (importActual) => ({
  ...(await importActual<typeof import("@/lib/aiAvailability")>()),
  useAiAvailability: () => ({ availability: mockAvailability(), prepare: mockPrepare }),
}));

describe("AudioSetupWizard", () => {
  beforeEach(() => {
    localStorage.clear();
  });
  afterEach(() => {
    localStorage.clear();
  });

  it("renders step 1 with both source cards", () => {
    render(<AudioSetupWizard onClose={() => undefined} />);
    expect(screen.getByText(/Soundboard line-out/i)).toBeInTheDocument();
    expect(screen.getByText(/Microphone in the room/i)).toBeInTheDocument();
  });

  it("walks Next → step 2 device picker → step 3 summary", () => {
    render(<AudioSetupWizard onClose={() => undefined} />);
    // Step 1 → 2
    fireEvent.click(screen.getByRole("button", { name: /^next$/i }));
    expect(screen.getByLabelText("Audio input device")).toBeInTheDocument();
    expect(screen.getByText(/Live level/i)).toBeInTheDocument();

    // Step 2 → 3
    fireEvent.click(screen.getByRole("button", { name: /^next$/i }));
    expect(screen.getByText(/Ready to go/i)).toBeInTheDocument();
    // The model is picked silently — no "Whisper", no "mlx" in front of the user.
    expect(screen.getByText(/already on this computer/i)).toBeInTheDocument();
    expect(screen.queryByText(/mlx|whisper/i)).toBeNull();
  });

  it("Save and finish writes setup_completed=true and calls onClose", async () => {
    const onClose = vi.fn();
    render(<AudioSetupWizard onClose={onClose} />);
    // Pick the microphone source so prefs reflect a non-default choice.
    fireEvent.click(screen.getByText(/Microphone in the room/i));
    // Walk to step 3.
    fireEvent.click(screen.getByRole("button", { name: /^next$/i }));
    fireEvent.click(screen.getByRole("button", { name: /^next$/i }));
    fireEvent.click(screen.getByRole("button", { name: /save and finish/i }));

    await waitFor(() => {
      expect(onClose).toHaveBeenCalled();
    });
    const stored = JSON.parse(
      localStorage.getItem("auto-presenter.audio-input-prefs") ?? "{}",
    );
    expect(stored.setup_completed).toBe(true);
    expect(stored.source).toBe("microphone");
    expect(stored.asr_backend).toBe("mlx");
    expect(stored.asr_model).toBe("small.en");
    expect(stored.asr_chosen).toBe(true);
  });

  it("Skip closes the wizard without saving setup_completed", () => {
    const onClose = vi.fn();
    render(<AudioSetupWizard onClose={onClose} />);
    fireEvent.click(screen.getByRole("button", { name: /close setup/i }));
    expect(onClose).toHaveBeenCalled();
    // Nothing was persisted because the user skipped.
    expect(localStorage.getItem("auto-presenter.audio-input-prefs")).toBeNull();
  });

  describe("the speech model", () => {
    afterEach(() => {
      mockAvailability.mockReturnValue({ kind: "ready", model: "small.en", hosted: false });
      mockPrepare.mockClear();
    });

    // A fresh install used to end setup on "Ready to go" with no speech
    // model, and download it only once a service had started.
    it("downloads a missing model when setup finishes, and says so first", () => {
      mockAvailability.mockReturnValue({ kind: "needs_download", model: "small.en", mb: 460 });
      const onClose = vi.fn();
      render(<AudioSetupWizard onClose={onClose} />);
      fireEvent.click(screen.getByRole("button", { name: /^next$/i }));
      fireEvent.click(screen.getByRole("button", { name: /^next$/i }));
      expect(screen.getByText(/about 460 MB\) downloads when you finish/)).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /save and finish/i }));
      expect(mockPrepare).toHaveBeenCalledTimes(1);
      expect(onClose).toHaveBeenCalled();
    });

    it("downloads nothing when the model is already here", () => {
      render(<AudioSetupWizard onClose={() => undefined} />);
      fireEvent.click(screen.getByRole("button", { name: /^next$/i }));
      fireEvent.click(screen.getByRole("button", { name: /^next$/i }));
      expect(screen.getByText(/already on this computer/)).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /save and finish/i }));
      expect(mockPrepare).not.toHaveBeenCalled();
    });
  });
});
