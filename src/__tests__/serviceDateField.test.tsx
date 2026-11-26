import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { plausibleServiceDate, useServiceDateField } from "@/lib/useServiceDateField";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

function DateBox({ saved, commit }: { saved: string | null; commit: (n: string | null, p: string | null) => Promise<void> }) {
  const field = useServiceDateField(saved, commit);
  return <input type="date" aria-label="date" {...field} />;
}

describe("a service's date box", () => {
  it("saves once the date settles, not each half-typed year", async () => {
    vi.useFakeTimers();
    const commit = vi.fn(async () => undefined);
    render(<DateBox saved="2026-11-29" commit={commit} />);
    const box = screen.getByLabelText("date");
    for (const v of ["0002-11-29", "0020-11-29", "0202-11-29", "2027-11-29"]) fireEvent.change(box, { target: { value: v } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(commit).toHaveBeenCalledTimes(1);
    expect(commit).toHaveBeenCalledWith("2027-11-29", "2026-11-29");
  });

  it("saves on leaving the box, tells each save the date before it, and skips the same date", async () => {
    const commit = vi.fn(async () => undefined);
    render(<DateBox saved="2026-11-29" commit={commit} />);
    const box = screen.getByLabelText("date");
    fireEvent.change(box, { target: { value: "2026-12-06" } });
    fireEvent.blur(box);
    fireEvent.change(box, { target: { value: "2026-12-13" } });
    fireEvent.blur(box);
    fireEvent.blur(box);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(commit.mock.calls).toEqual([
      ["2026-12-06", "2026-11-29"],
      ["2026-12-13", "2026-12-06"],
    ]);
  });

  it("knows a date no service is for", () => {
    expect(plausibleServiceDate("2026-11-29")).toBe(true);
    expect(plausibleServiceDate("0202-11-29")).toBe(false);
    expect(plausibleServiceDate("2026-02-30")).toBe(false);
  });
});
