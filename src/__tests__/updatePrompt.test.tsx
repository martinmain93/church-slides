import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const check = vi.fn();
const relaunch = vi.fn();
vi.mock("@tauri-apps/plugin-updater", () => ({ check: () => check() }));
vi.mock("@tauri-apps/plugin-process", () => ({ relaunch: () => relaunch() }));
vi.mock("@/lib/invoke", () => ({ isInTauri: () => true }));

import { CHECK_FOR_UPDATE_EVENT, UpdatePrompt } from "@/components/UpdatePrompt";

type Progress = (e: { event: string; data?: Record<string, number> }) => void;

function fakeUpdate(download: (onEvent: Progress) => Promise<void>) {
  return {
    version: "0.0.4",
    currentVersion: "0.0.3",
    body: "- Updates itself\n- Something else",
    downloadAndInstall: vi.fn(download),
  };
}

beforeEach(() => {
  vi.stubEnv("DEV", false);
  check.mockReset();
  relaunch.mockReset();
});
afterEach(() => vi.unstubAllEnvs());

describe("UpdatePrompt", () => {
  it("offers a newer version at startup with its notes", async () => {
    check.mockResolvedValue(fakeUpdate(async () => {}));
    render(<UpdatePrompt held={false} />);
    expect(await screen.findByText("Church Slides 0.0.4 is available")).toBeTruthy();
    expect(screen.getByText(/You have 0\.0\.3/)).toBeTruthy();
    expect(screen.getByText("Updates itself")).toBeTruthy();
  });

  it("says nothing at startup when up to date or offline", async () => {
    check.mockResolvedValueOnce(null);
    const { unmount } = render(<UpdatePrompt held={false} />);
    await waitFor(() => expect(check).toHaveBeenCalled());
    expect(screen.queryByTestId("update-prompt")).toBeNull();
    unmount();

    check.mockRejectedValueOnce(new Error("error sending request: dns error"));
    render(<UpdatePrompt held={false} />);
    await waitFor(() => expect(check).toHaveBeenCalledTimes(2));
    expect(screen.queryByTestId("update-prompt")).toBeNull();
  });

  it("waits until the service is over", async () => {
    check.mockResolvedValue(fakeUpdate(async () => {}));
    const { rerender } = render(<UpdatePrompt held />);
    await waitFor(() => expect(check).toHaveBeenCalled());
    expect(screen.queryByTestId("update-prompt")).toBeNull();
    rerender(<UpdatePrompt held={false} />);
    expect(await screen.findByText("Church Slides 0.0.4 is available")).toBeTruthy();
  });

  it("shows download progress, then relaunches", async () => {
    let emit!: Progress;
    let finish!: () => void;
    const update = fakeUpdate(
      (onEvent) =>
        new Promise<void>((resolve) => {
          emit = onEvent;
          finish = resolve;
        }),
    );
    check.mockResolvedValue(update);
    render(<UpdatePrompt held={false} />);
    fireEvent.click(await screen.findByTestId("update-install"));

    act(() => emit({ event: "Started", data: { contentLength: 20_000_000 } }));
    act(() => emit({ event: "Progress", data: { chunkLength: 5_000_000 } }));
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("25");
    expect(screen.getByText("5.0 MB of 20 MB")).toBeTruthy();

    act(() => emit({ event: "Finished" }));
    expect(screen.getByText(/Installing/)).toBeTruthy();
    await act(async () => finish());
    expect(relaunch).toHaveBeenCalledTimes(1);
  });

  it("shows progress about ten times a second, and the bar never runs backwards", async () => {
    let emit!: Progress;
    check.mockResolvedValue(
      fakeUpdate(
        (onEvent) =>
          new Promise<void>(() => {
            emit = onEvent;
          }),
      ),
    );
    let now = 1_000;
    const clock = vi.spyOn(performance, "now").mockImplementation(() => now);
    render(<UpdatePrompt held={false} />);
    fireEvent.click(await screen.findByTestId("update-install"));
    // before the size is known: an activity bar, not the progress bar
    expect(screen.queryByTestId("update-progress-bar")).toBeNull();

    act(() => emit({ event: "Started", data: { contentLength: 100_000_000 } }));
    act(() => emit({ event: "Progress", data: { chunkLength: 1_000_000 } }));
    const bar = () => screen.getByRole("progressbar").getAttribute("aria-valuenow");
    expect(bar()).toBe("1");
    // a burst of chunks within 100 ms: not shown one by one
    for (let i = 0; i < 20; i++) {
      now += 2;
      act(() => emit({ event: "Progress", data: { chunkLength: 1_000_000 } }));
    }
    expect(bar()).toBe("1");
    now += 100;
    act(() => emit({ event: "Progress", data: { chunkLength: 1_000_000 } }));
    expect(bar()).toBe("22");
    expect(screen.getByText("22 MB of 100 MB")).toBeTruthy();
    // the last chunk always shows, however soon it comes
    now += 1;
    act(() => emit({ event: "Progress", data: { chunkLength: 78_000_000 } }));
    expect(bar()).toBe("100");
    clock.mockRestore();
  });

  it("reports a failed download and offers to try again", async () => {
    check.mockResolvedValue(fakeUpdate(async () => Promise.reject(new Error("signature mismatch"))));
    render(<UpdatePrompt held={false} />);
    fireEvent.click(await screen.findByTestId("update-install"));
    expect(await screen.findByText("The update didn't finish")).toBeTruthy();
    expect(screen.getByText(/signature mismatch/)).toBeTruthy();
    expect(screen.getByText("Try again")).toBeTruthy();
    expect(relaunch).not.toHaveBeenCalled();
  });

  it("answers a manual check even when up to date", async () => {
    check.mockResolvedValue(null);
    render(<UpdatePrompt held={false} />);
    await waitFor(() => expect(check).toHaveBeenCalledTimes(1));
    act(() => {
      window.dispatchEvent(new CustomEvent(CHECK_FOR_UPDATE_EVENT));
    });
    expect(await screen.findByText("You have the latest version")).toBeTruthy();
  });
});
