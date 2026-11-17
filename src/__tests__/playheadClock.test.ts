import { describe, expect, it } from "vitest";

import { followOutputClock, type PlayAnchor } from "@/lib/recordings/bandSolo";

describe("followOutputClock", () => {
  it("uses the element clock while the two clocks agree", () => {
    const first = followOutputClock(141.9, 10, null);
    expect(first.time).toBe(141.9);

    const next = followOutputClock(142.05, 10.15, first.anchor);
    expect(next.time).toBe(142.05);
    expect(next.anchor).toEqual({ media: 142.05, output: 10.15 });
  });

  it("rides the output clock after the element clock stalls", () => {
    const anchor: PlayAnchor = { media: 142, output: 20 };
    const stalled = followOutputClock(142, 21.2, anchor);
    expect(stalled.time).toBeCloseTo(143.2);
    expect(stalled.anchor).toBe(anchor);

    const later = followOutputClock(142, 22, stalled.anchor);
    expect(later.time).toBeCloseTo(144);
    expect(later.anchor).toBe(anchor);
  });

  it("re-anchors once the element clock catches up", () => {
    const anchor: PlayAnchor = { media: 142, output: 20 };
    const caught = followOutputClock(144, 22, anchor);
    expect(caught.time).toBe(144);
    expect(caught.anchor).toEqual({ media: 144, output: 22 });
  });
});
