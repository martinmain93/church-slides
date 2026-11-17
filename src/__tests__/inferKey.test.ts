import { describe, expect, it } from "vitest";

import { emptyChart, type ChordChart } from "@/lib/chords/chart";
import { inferKey } from "@/lib/chords/import/inferKey";

function chartOf(...lines: string[]): ChordChart {
  return {
    ...emptyChart(),
    sections: [{
      label: "Verse",
      stanzas: [lines.map((l) => ({ text: "la", chords: l.split(" ").map((chord, pos) => ({ pos, chord })) }))],
    }],
  };
}

describe("inferKey", () => {
  it("reads the keys of real sheets that never wrote one", () => {
    // I Will Rejoice: opens on IV, all diatonic to C.
    expect(inferKey(chartOf("F C", "Em Am", "F C", "G Am", "F C G Am", "F C G"))).toBe("C");
    // I Could Sing Of Your Love Forever: sus chords fit either quality.
    expect(inferKey(chartOf("D Em G Asus", "D Em G Asus D"))).toBe("D");
    // The Heart Of Worship.
    expect(inferKey(chartOf("D A Em A", "D A Em", "Em D/F# A", "A A/C# D", "Em G A D"))).toBe("D");
  });

  it("names a minor key when the song lives on vi", () => {
    expect(inferKey(chartOf("Am F C G", "Am F C G", "Am F G Am"))).toBe("Am");
  });

  it("gives no key rather than a wrong one", () => {
    expect(inferKey(chartOf("G C"))).toBeNull();
    // Chromatic wandering no single key explains.
    expect(inferKey(chartOf("C Eb F# A", "Db E G Bb"))).toBeNull();
  });
});
