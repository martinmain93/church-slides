/**
 * The debug panel's sidecar toggles were written as `void setSomething(x)`
 * with no `.catch()`. When the request rejected — sidecar down, no session —
 * the checkbox snapped back with no explanation, which reads as the app
 * ignoring the click. Three tabs shared the pattern (spoken instructions,
 * the since-removed fast channel, blank-when-idle), and none of them surfaced the error
 * the way the rest of the app does.
 *
 * Same class as the bare `catch {}` in `recordingClient.ts`: the reason
 * existed only in an unread rejected promise.
 */

import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const errorSpy = vi.fn();
vi.mock("@/lib/toast", () => ({
  useToast: () => ({ error: errorSpy, success: vi.fn(), info: vi.fn() }),
}));

import { useToggleRequest } from "@/components/debug/useToggleRequest";

describe("useToggleRequest", () => {
  it("reports a rejected toggle instead of swallowing it", async () => {
    errorSpy.mockClear();
    const send = vi.fn(async () => {
      throw new Error("no active session");
    });
    const { result } = renderHook(() => useToggleRequest("spoken instructions", send));

    await act(async () => {
      result.current(true);
    });

    expect(send).toHaveBeenCalledWith(true);
    expect(errorSpy).toHaveBeenCalledTimes(1);
    const message = errorSpy.mock.calls[0]?.[0] as string;
    // Names the setting and the direction, so the toast is actionable
    // rather than a bare stack-trace fragment.
    expect(message).toContain("spoken instructions");
    expect(message).toContain("on");
    expect(message).toContain("no active session");
  });

  it("says so when turning something off fails too", async () => {
    errorSpy.mockClear();
    const send = vi.fn(async () => {
      throw new Error("sidecar unavailable");
    });
    const { result } = renderHook(() => useToggleRequest("blank when idle", send));

    await act(async () => {
      result.current(false);
    });

    expect(errorSpy.mock.calls[0]?.[0]).toContain("off");
  });

  it("stays quiet when the toggle succeeds", async () => {
    errorSpy.mockClear();
    const send = vi.fn(async () => undefined);
    const { result } = renderHook(() => useToggleRequest("blanking", send));

    await act(async () => {
      result.current(true);
    });

    expect(errorSpy).not.toHaveBeenCalled();
  });
});
