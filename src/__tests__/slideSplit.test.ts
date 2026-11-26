import { describe, expect, it } from "vitest";

import { canSplit, smartSplit, splitAt, splitLineIndex } from "@/lib/slideSplit";

// Public-domain hymn lines and invented lines only.

describe("smartSplit", () => {
  it("halves four even lines 2 + 2", () => {
    expect(
      smartSplit("Amazing grace how sweet the sound\nThat saved a wretch like me\nI once was lost but now am found\nWas blind but now I see"),
    ).toEqual([
      "Amazing grace how sweet the sound\nThat saved a wretch like me",
      "I once was lost but now am found\nWas blind but now I see",
    ]);
  });

  it("weighs by words, not line count", () => {
    // Two long lines then two short ones: the middle is after the first line.
    const lines = [
      "We walk along the winding road that climbs the morning hill",
      "And every step we take is held within a steady hand",
      "Sing out",
      "Sing on",
    ];
    expect(splitLineIndex(lines)).toBe(0);
  });

  it("prefers a breath (punctuation) when it is nearly as central", () => {
    const lines = ["Holy, holy, holy,", "Lord God Almighty", "Early in the morning our song", "shall rise to thee"];
    // Characters say after line 2; the comma after line 1 isn't close enough.
    expect(splitLineIndex(lines)).toBe(1);
    const nearTie = ["The light is on the water now,", "the boats are coming in", "and all the town is waiting"];
    expect(splitLineIndex(nearTie)).toBe(0);
  });

  it("gives the first slide the extra line on a tie", () => {
    expect(smartSplit("one two\nthree four\nfive six")).toEqual(["one two\nthree four", "five six"]);
  });

  it("splits a single long line at a clause mark near the middle", () => {
    expect(smartSplit("Praise God from whom all blessings flow; praise him all creatures here below")).toEqual([
      "Praise God from whom all blessings flow;",
      "praise him all creatures here below",
    ]);
  });

  it("falls back to the space nearest the middle", () => {
    expect(smartSplit("one two three four five six")).toEqual(["one two three", "four five six"]);
  });

  it("ignores blank lines at the edges", () => {
    expect(smartSplit("\nfirst line\nsecond line\n\n")).toEqual(["first line", "second line"]);
  });

  it("can keep to line breaks (a song with chords)", () => {
    expect(smartSplit("one two three four five six", { midLine: false })).toBeNull();
    expect(smartSplit("one two\nthree four", { midLine: false })).toEqual(["one two", "three four"]);
  });

  it("won't split one word or nothing", () => {
    expect(smartSplit("Hallelujah")).toBeNull();
    expect(smartSplit("   ")).toBeNull();
    expect(canSplit("")).toBe(false);
    expect(canSplit("two words")).toBe(true);
  });
});

describe("splitAt", () => {
  const v1 = { type: "verse" as const, number: 1 };
  const c1 = { type: "chorus" as const, number: 1 };

  it("replaces the slide with its halves, both in its section", () => {
    const slides = [
      { section: v1, lyrics: "a b\nc d" },
      { section: c1, lyrics: "chorus" },
    ];
    const next = splitAt(slides, 0);
    expect(next).toEqual([
      { section: v1, lyrics: "a b" },
      { section: v1, lyrics: "c d" },
      { section: c1, lyrics: "chorus" },
    ]);
    expect(next[0]!.section).not.toBe(next[1]!.section);
  });

  it("leaves the list alone when the slide can't split", () => {
    const slides = [{ section: v1, lyrics: "Amen" }];
    expect(splitAt(slides, 0)).toBe(slides);
    expect(splitAt(slides, 3)).toBe(slides);
  });
});
