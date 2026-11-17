import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AudioLevels } from "@/lib/audioLevels";

const mockLevels = vi.fn<() => AudioLevels>();
vi.mock("@/lib/audioLevels", () => ({
  useAudioLevels: () => mockLevels(),
}));

const mockHealth = vi.fn<() => unknown>(() => null);
vi.mock("@/lib/pipelineHealth", () => ({
  usePipelineHealth: () => mockHealth(),
}));

import { AudioWaveformIndicator } from "@/components/presentation/AudioWaveformIndicator";

afterEach(() => {
  cleanup();
  mockLevels.mockReset();
  mockHealth.mockReset();
  mockHealth.mockReturnValue(null);
});

describe("AudioWaveformIndicator", () => {
  it("crosses out the mic only when nothing is being captured", () => {
    mockLevels.mockReturnValue({ latest: null, history: [], connected: false });
    render(<AudioWaveformIndicator />);
    expect(screen.getByTestId("audio-waveform")).toHaveAttribute(
      "aria-label",
      "No audio input",
    );
    // 9 bars always render.
    expect(
      screen.getByTestId("audio-waveform").querySelectorAll("span"),
    ).toHaveLength(9);
  });

  it("shows the active state when a loud-enough signal is present", () => {
    const sample = { t_seconds: 1, rms_dbfs: -20, peak: 0.4 };
    mockLevels.mockReturnValue({
      latest: sample,
      history: [sample, sample, sample],
      connected: true,
    });
    render(<AudioWaveformIndicator />);
    expect(screen.getByTestId("audio-waveform")).toHaveAttribute(
      "aria-label",
      "Audio detected",
    );
  });

  it("keeps the mic live (not crossed out) when on but quiet", () => {
    // A live session feeding near-silent samples: mic stays live, bars idle.
    const quiet = { t_seconds: 1, rms_dbfs: -58, peak: 0.01 };
    mockLevels.mockReturnValue({ latest: quiet, history: [quiet], connected: true });
    render(<AudioWaveformIndicator />);
    expect(screen.getByTestId("audio-waveform")).toHaveAttribute(
      "aria-label",
      "Mic live, no sound",
    );
  });
});

describe("pipeline overload", () => {
  const levels: AudioLevels = {
    latest: { t_seconds: 1, rms_dbfs: -20, peak: 0.1 },
    history: [{ t_seconds: 1, rms_dbfs: -20, peak: 0.1 }],
    connected: true,
  };

  it("says nothing while the pipeline keeps up", () => {
    mockLevels.mockReturnValue(levels);
    mockHealth.mockReturnValue({ lagSeconds: 0.3, droppedChunks: 0, healthy: true });
    render(<AudioWaveformIndicator />);
    expect(screen.queryByTestId("pipeline-degraded")).toBeNull();
  });

  it("warns when the AI has fallen behind the room", () => {
    // Healthy input levels with a starved pipeline is exactly the case the
    // level meter cannot show: audio is arriving, the matcher just isn't
    // getting all of it.
    mockLevels.mockReturnValue(levels);
    mockHealth.mockReturnValue({ lagSeconds: 4.2, droppedChunks: 17, healthy: false });
    render(<AudioWaveformIndicator />);
    const badge = screen.getByTestId("pipeline-degraded");
    expect(badge).toHaveTextContent("4.2s behind");
    expect(badge).toHaveTextContent("17 lost");
    expect(badge.getAttribute("title")).toContain("17 chunk(s)");
  });

  it("distinguishes real loss from a lag reading with no audio lost", () => {
    // The capture queue holds 5s, so sustained real lag MUST discard audio.
    // Lag reported with zero drops means the pipeline is actually coping and
    // the number is misleading — worth being able to tell apart at a glance.
    mockLevels.mockReturnValue(levels);
    mockHealth.mockReturnValue({ lagSeconds: 3.0, droppedChunks: 0, healthy: false });
    render(<AudioWaveformIndicator />);
    const badge = screen.getByTestId("pipeline-degraded");
    expect(badge).toHaveTextContent("3.0s behind");
    expect(badge).not.toHaveTextContent("lost");
  });
});
