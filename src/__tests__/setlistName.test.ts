import { describe, expect, it } from "vitest";

import { defaultSetlistName } from "@/lib/setlistName";

describe("defaultSetlistName", () => {
  it("names a setlist made midweek for the coming Sunday", () => {
    // Thursday 1 October 2026.
    expect(defaultSetlistName(new Date(2026, 9, 1))).toBe("Sunday 4 Oct");
  });

  it("uses today when it is Sunday", () => {
    expect(defaultSetlistName(new Date(2026, 9, 4, 8, 30))).toBe("Sunday 4 Oct");
  });

  it("crosses into the next month", () => {
    expect(defaultSetlistName(new Date(2026, 9, 29))).toBe("Sunday 1 Nov");
  });
});
