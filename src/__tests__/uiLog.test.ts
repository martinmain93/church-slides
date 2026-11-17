import { afterEach, describe, expect, it, vi } from "vitest";

const invokeMock = vi.fn<(cmd: string, args?: Record<string, unknown>) => Promise<unknown>>(
  () => Promise.resolve(),
);
vi.mock("@/lib/invoke", () => ({
  invoke: (cmd: string, args?: Record<string, unknown>) => invokeMock(cmd, args),
  isInTauri: () => true,
}));

import { formatLogArgs, installUiLogForwarding } from "@/lib/uiLog";

afterEach(() => invokeMock.mockClear());

describe("formatLogArgs", () => {
  it("keeps an error's stack and serialises objects", () => {
    const err = new Error("boom");
    expect(formatLogArgs(["failed:", err])).toContain("boom");
    expect(formatLogArgs([{ a: 1 }])).toBe('{"a":1}');
  });

  it("caps very long messages", () => {
    expect(formatLogArgs(["x".repeat(10_000)])).toHaveLength(4000);
  });
});

describe("installUiLogForwarding", () => {
  it("copies console errors into the log file, rate-limited", () => {
    installUiLogForwarding();
    const quiet = vi.spyOn(console, "error");
    for (let i = 0; i < 100; i++) console.error("start_audio_session failed", i);
    const forwarded = invokeMock.mock.calls.filter(([cmd]) => cmd === "log_from_ui");
    expect(forwarded.length).toBe(30);
    expect(forwarded[0]?.[1]).toMatchObject({ level: "error" });
    expect(String(forwarded[0]?.[1]?.message)).toContain("start_audio_session failed 0");
    quiet.mockRestore();
  });
});
