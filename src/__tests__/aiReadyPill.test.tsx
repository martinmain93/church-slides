import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const mockAvailability = vi.fn<() => unknown>();
vi.mock("@/lib/aiAvailability", async (importActual) => {
  const actual = await importActual<typeof import("@/lib/aiAvailability")>();
  return { ...actual, useAiAvailability: () => ({ availability: mockAvailability() }) };
});

import {
  AiReadyPill,
  OPEN_SETTINGS_EVENT,
  describeAiReadiness,
} from "@/components/setlists/AiReadyPill";
import { isAiEnabled, serviceStartAiMode, setAiEnabled } from "@/lib/aiPrefs";
import { saveAudioInputPrefs, loadAudioInputPrefs } from "@/lib/audioInputPrefs";

const READY = { kind: "ready", model: "small.en", hosted: false } as const;

function audioSetUp() {
  saveAudioInputPrefs({ ...loadAudioInputPrefs(), setup_completed: true });
}

afterEach(() => {
  cleanup();
  setAiEnabled(true);
  localStorage.clear();
});

describe("describeAiReadiness", () => {
  it("is ready with the model here and audio chosen, even while still checking", () => {
    expect(describeAiReadiness(READY, true).kind).toBe("ready");
    expect(describeAiReadiness({ kind: "checking" }, true).kind).toBe("ready");
  });

  it("sends a missing audio input to the audio setup, a missing model to Settings", () => {
    expect(describeAiReadiness(READY, false)).toMatchObject({ kind: "not_ready", fix: "audio_setup" });
    expect(
      describeAiReadiness({ kind: "needs_download", model: "small.en", mb: 480 }, true),
    ).toMatchObject({ kind: "not_ready", fix: "settings" });
  });

  it("is unavailable on a computer too slow for it", () => {
    expect(describeAiReadiness({ kind: "too_slow", reason: "Too slow." }, true).kind).toBe(
      "unavailable",
    );
  });
});

describe("AiReadyPill", () => {
  it("is on by default, and services start with the AI on", () => {
    expect(isAiEnabled()).toBe(true);
    expect(serviceStartAiMode()).toBe("active");
  });

  it("reads AI Ready, and switches the AI off and on", () => {
    mockAvailability.mockReturnValue(READY);
    audioSetUp();
    render(<AiReadyPill />);
    const pill = screen.getByTestId("ai-ready-pill");
    expect(pill.textContent).toBe("AI Ready");
    expect(pill.getAttribute("title")).toMatch(/^Click to disable AI Tracking/);

    fireEvent.click(pill);
    expect(screen.getByTestId("ai-ready-pill").textContent).toBe("Turn on AI");
    expect(serviceStartAiMode()).toBe("off");

    fireEvent.click(screen.getByTestId("ai-ready-pill"));
    expect(screen.getByTestId("ai-ready-pill").textContent).toBe("AI Ready");
  });

  it("reads AI Not Ready without a model, and opens Settings", () => {
    mockAvailability.mockReturnValue({ kind: "needs_download", model: "small.en", mb: 480 });
    audioSetUp();
    const opened = vi.fn();
    window.addEventListener(OPEN_SETTINGS_EVENT, opened);
    render(<AiReadyPill />);
    const pill = screen.getByTestId("ai-ready-pill");
    expect(pill.textContent).toBe("AI Not Ready");
    expect(pill.getAttribute("title")).toMatch(/480 MB/);
    fireEvent.click(pill);
    expect(opened).toHaveBeenCalled();
    window.removeEventListener(OPEN_SETTINGS_EVENT, opened);
  });

  it("opens the audio setup when no input is chosen, and turns ready once it is", () => {
    mockAvailability.mockReturnValue(READY);
    const opened = vi.fn();
    window.addEventListener("auto-presenter:open-audio-wizard", opened);
    render(<AiReadyPill />);
    fireEvent.click(screen.getByTestId("ai-ready-pill"));
    expect(opened).toHaveBeenCalled();
    window.removeEventListener("auto-presenter:open-audio-wizard", opened);

    act(() => audioSetUp());
    expect(screen.getByTestId("ai-ready-pill").textContent).toBe("AI Ready");
  });

  it("reads AI Not available on a computer that can't run it, with why", () => {
    mockAvailability.mockReturnValue({ kind: "too_slow", reason: "Too slow for live following." });
    render(<AiReadyPill />);
    const pill = screen.getByTestId("ai-ready-pill");
    expect(pill.textContent).toBe("AI Not available");
    expect(pill.getAttribute("title")).toMatch(/Too slow for live following/);
  });
});
