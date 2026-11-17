import { describe, expect, it } from "vitest";

import { fifthsDistance, musicalFit } from "@/lib/smartSearch/musicalFit";

describe("musicalFit", () => {
  it("measures keys on the circle of fifths, minors beside their relative major", () => {
    expect(fifthsDistance("E", "E")).toBe(0);
    expect(fifthsDistance("E", "A")).toBe(1);
    expect(fifthsDistance("E", "B")).toBe(1);
    expect(fifthsDistance("C", "Am")).toBe(0);
    expect(fifthsDistance("Bb", "A#")).toBe(0);
    expect(fifthsDistance("C", "F#")).toBe(6);
  });

  it("scores the same key, tempo and time highest, and explains", () => {
    const target = { key: "E", tempo: 72, time: "4/4" };
    const same = musicalFit(target, { key: "E", tempo: 72, time: "4/4" });
    const near = musicalFit(target, { key: "A", tempo: 80, time: "4/4" });
    const far = musicalFit(target, { key: "F", tempo: 130, time: "6/8" });
    expect(same.score).toBeCloseTo(1);
    expect(same.reason).toBe("Same key (E) · 72 bpm · 4/4");
    expect(near.score).toBeGreaterThan(far.score);
    expect(far.score).toBe(0);
  });

  it("gives triple times partial credit for one another", () => {
    const a = musicalFit({ key: null, tempo: null, time: "6/8" }, { key: null, tempo: null, time: "3/4" });
    expect(a.score).toBeCloseTo(0.1);
  });

  it("a song with no facts doesn't fit", () => {
    expect(musicalFit({ key: "E", tempo: 72, time: "4/4" }, { key: null, tempo: null, time: null }).score).toBe(0);
    expect(musicalFit({ key: "E", tempo: 72, time: "4/4" }, undefined).score).toBe(0);
  });
});
