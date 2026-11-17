/**
 * The home screen's speech-model card: shown until the model is on this
 * computer, so a fresh install says what live AI is waiting for.
 */

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const mockAvailability = vi.fn<() => unknown>();
const mockPrepare = vi.fn<() => Promise<void>>(() => Promise.resolve());
const mockRecheck = vi.fn<() => Promise<void>>(() => Promise.resolve());
vi.mock("@/lib/aiAvailability", async (importActual) => ({
  ...(await importActual<typeof import("@/lib/aiAvailability")>()),
  useAiAvailability: () => ({
    availability: mockAvailability(),
    prepare: mockPrepare,
    recheck: mockRecheck,
  }),
}));

import { SpeechModelStatus } from "@/components/home/SpeechModelStatus";

describe("SpeechModelStatus", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("offers the download when the model isn't here", async () => {
    mockAvailability.mockReturnValue({ kind: "needs_download", model: "small.en", mb: 460 });
    render(<SpeechModelStatus />);
    expect(screen.getByText(/isn.t on this computer yet/)).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Download \(460 MB\)/ }));
    });
    expect(mockPrepare).toHaveBeenCalledTimes(1);
  });

  it("shows how far a download has got", () => {
    mockAvailability.mockReturnValue({ kind: "downloading", model: "small.en", mb: 460, mbDone: 115 });
    render(<SpeechModelStatus />);
    expect(screen.getByTestId("speech-model-status").dataset.state).toBe("downloading");
    expect(screen.getByText("25%")).toBeInTheDocument();
  });

  it("says why a download failed, with a retry", () => {
    mockAvailability.mockReturnValue({
      kind: "download_failed",
      model: "small.en",
      message: "No internet connection.",
    });
    render(<SpeechModelStatus />);
    expect(screen.getByText(/didn't download/)).toBeInTheDocument();
    expect(screen.getByText("No internet connection.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Try again/ })).toBeInTheDocument();
  });

  it("says nothing once the model is here, or with no engine to need it", () => {
    for (const a of [
      { kind: "ready", model: "small.en", hosted: false },
      { kind: "engine_unavailable", reason: "no engine" },
      { kind: "checking" },
    ]) {
      mockAvailability.mockReturnValue(a);
      const { container } = render(<SpeechModelStatus />);
      expect(container).toBeEmptyDOMElement();
      cleanup();
    }
  });

  it("says before a service that live AI isn't available on a computer too slow for it", () => {
    mockAvailability.mockReturnValue({ kind: "too_slow", reason: "Too slow for live following." });
    render(<SpeechModelStatus />);
    expect(screen.getByText("Live AI isn't available on this computer")).toBeInTheDocument();
    expect(screen.getByText(/Presenting by hand works as always/)).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("offers another check when the last one couldn't judge", () => {
    mockAvailability.mockReturnValue({
      kind: "check_unavailable",
      reason: "This computer is saving power (Low Power Mode). Plug it in and check again.",
    });
    render(<SpeechModelStatus />);
    fireEvent.click(screen.getByRole("button", { name: "Check again" }));
    expect(mockRecheck).toHaveBeenCalledTimes(1);
  });

  it("shows the check running", () => {
    mockAvailability.mockReturnValue({ kind: "checking_machine" });
    render(<SpeechModelStatus />);
    expect(screen.getByTestId("speech-model-status").dataset.state).toBe("checking");
  });
});
