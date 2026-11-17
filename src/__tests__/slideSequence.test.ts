import { describe, expect, it } from "vitest";

import { parseSong } from "@/lib/parseSong";
import {
  formatSlideOrder,
  isBlankSlide,
  parseSlideOrder,
  parseSlidesOverride,
  resolveSlideSequence,
  serializeSlidesOverride,
  slidesToBody,
  type OverrideSlide,
} from "@/lib/slideSequence";

const SONG = [
  "[verse 1]",
  "Verse one slide one",
  "",
  "[verse 1]",
  "Verse one slide two",
  "",
  "[chorus]",
  "Chorus line",
  "",
  "[verse 2]",
  "Verse two line",
  "",
  "[bridge]",
  "Bridge line",
].join("\n");

describe("parseSlideOrder", () => {
  it("maps abbreviations and numbers through the body parser's tags", () => {
    const refs = parseSlideOrder("V1, C, V2, B");
    expect(refs).toEqual([
      { type: "verse", number: 1 },
      { type: "chorus", number: 1 },
      { type: "verse", number: 2 },
      { type: "bridge", number: 1 },
    ]);
  });

  it("splits on commas and/or whitespace and ignores empties", () => {
    expect(parseSlideOrder("V1  C ,, V2")).toEqual([
      { type: "verse", number: 1 },
      { type: "chorus", number: 1 },
      { type: "verse", number: 2 },
    ]);
  });

  it("returns [] for null/empty", () => {
    expect(parseSlideOrder(null)).toEqual([]);
    expect(parseSlideOrder("")).toEqual([]);
    expect(parseSlideOrder("   ")).toEqual([]);
  });
});

describe("formatSlideOrder", () => {
  it("renders the document section order compactly", () => {
    const { section_order } = parseSong(SONG);
    // verse 1, chorus, verse 2, bridge → multiple verses keep numbers;
    // lone chorus/bridge drop theirs.
    expect(formatSlideOrder(section_order)).toBe("V1, C, V2, B");
  });

  it("round-trips a set of distinct sections (its real input: section_order)", () => {
    // formatSlideOrder is only ever handed the distinct section_order, where
    // each section appears once. A lone chorus stays "C"; multiple verses keep
    // their numbers.
    const refs = parseSlideOrder("V1, V2, V3, C, B");
    expect(formatSlideOrder(refs)).toBe("V1, V2, V3, C, B");
  });

  it("numbers a section type once more than one of it exists", () => {
    // Two distinct choruses → both numbered, since the bare-letter shorthand
    // would be ambiguous.
    const refs = parseSlideOrder("C1, C2");
    expect(formatSlideOrder(refs)).toBe("C1, C2");
  });
});

describe("resolveSlideSequence", () => {
  it("defaults to document order when no slide_order is given", () => {
    const seq = resolveSlideSequence(SONG);
    const fromParser = parseSong(SONG).slides;
    expect(seq.map((s) => s.lyrics)).toEqual(fromParser.map((s) => s.lyrics));
    // words stay parity-equal to the canonical parser tokens.
    expect(seq.map((s) => s.words)).toEqual(fromParser.map((s) => s.words));
  });

  it("shows a section written twice once per block, not twice per block", () => {
    const body = "[Chorus]\nC one\n\nC two\n\n[Verse 1]\nV one\n\n[Chorus]\nC one\n\nC two";
    expect(resolveSlideSequence(body).map((s) => s.lyrics)).toEqual(
      parseSong(body).slides.map((s) => s.lyrics),
    );
    expect(resolveSlideSequence(body)).toHaveLength(5);
  });

  it("expands a multi-slide section fully", () => {
    // verse 1 has two slides; ordering it once yields both.
    const seq = resolveSlideSequence(SONG, "V1");
    expect(seq.map((s) => s.lyrics)).toEqual([
      "Verse one slide one",
      "Verse one slide two",
    ]);
  });

  it("repeats a section when the order references it more than once", () => {
    const seq = resolveSlideSequence(SONG, "C, V2, C");
    expect(seq.map((s) => s.lyrics)).toEqual([
      "Chorus line",
      "Verse two line",
      "Chorus line",
    ]);
  });

  it("gives repeated slides distinct ids but shares sourceId", () => {
    const seq = resolveSlideSequence(SONG, "C, C");
    expect(seq[0]!.id).not.toBe(seq[1]!.id);
    expect(seq[0]!.sourceId).toBe(seq[1]!.sourceId);
    expect(seq.map((s) => s.order_in_song)).toEqual([0, 1]);
  });

  it("skips unknown / typo'd section references gracefully", () => {
    const seq = resolveSlideSequence(SONG, "C, V9, B");
    expect(seq.map((s) => s.lyrics)).toEqual(["Chorus line", "Bridge line"]);
  });

  it("uses a per-setlist override verbatim, ignoring body + order", () => {
    const override: OverrideSlide[] = [
      { section: { type: "chorus", number: 1 }, lyrics: "Edited chorus" },
      { section: { type: "chorus", number: 1 }, lyrics: "Edited chorus" },
      { section: { type: "verse", number: 1 }, lyrics: "Custom verse" },
    ];
    const seq = resolveSlideSequence(SONG, "V1, V2", override);
    expect(seq.map((s) => s.lyrics)).toEqual([
      "Edited chorus",
      "Edited chorus",
      "Custom verse",
    ]);
    // words are tokenized the same way the parser would.
    expect(seq[2]!.words).toEqual(["custom", "verse"]);
    // order_in_section increments for repeated sections.
    expect(seq.map((s) => s.order_in_section)).toEqual([0, 1, 0]);
  });

  it("falls back to body when the override is empty", () => {
    const seq = resolveSlideSequence(SONG, undefined, []);
    expect(seq.map((s) => s.lyrics)).toEqual(parseSong(SONG).slides.map((s) => s.lyrics));
  });
});

describe("resolveSlideSequence with leadingBlank", () => {
  it("prepends a blank slide as slide 0 and shifts the rest", () => {
    const plain = resolveSlideSequence(SONG, "V1");
    const withBlank = resolveSlideSequence(SONG, "V1", undefined, {
      leadingBlank: true,
    });
    // One extra slide at the front.
    expect(withBlank.length).toBe(plain.length + 1);
    expect(isBlankSlide(withBlank[0]!)).toBe(true);
    expect(withBlank[0]!.lyrics).toBe("");
    expect(withBlank[0]!.words).toEqual([]);
    // The real slides follow, unchanged in content, re-sequenced from 1.
    expect(withBlank.slice(1).map((s) => s.lyrics)).toEqual(
      plain.map((s) => s.lyrics),
    );
    expect(withBlank.map((s) => s.order_in_song)).toEqual([0, 1, 2]);
  });

  it("defaults to no blank slide when the option is omitted", () => {
    expect(resolveSlideSequence(SONG, "V1").some(isBlankSlide)).toBe(false);
  });

  it("prepends a blank even when an override is used", () => {
    const override: OverrideSlide[] = [
      { section: { type: "chorus", number: 1 }, lyrics: "Edited" },
    ];
    const seq = resolveSlideSequence(SONG, undefined, override, {
      leadingBlank: true,
    });
    expect(seq.map((s) => s.lyrics)).toEqual(["", "Edited"]);
    expect(isBlankSlide(seq[0]!)).toBe(true);
  });
});

describe("buildSetlistContent / presentation index alignment", () => {
  it("ships the blank slide so matcher indices match the live sequence", () => {
    // The matcher payload and the live presentation must resolve the same
    // number of slides per song so a MatcherDecision's slide_idx maps onto
    // the slide the operator/audience actually see.
    const ctxSlides = resolveSlideSequence(SONG, "V1, C", undefined, {
      leadingBlank: true,
    });
    const matcherSlides = resolveSlideSequence(SONG, "V1, C", undefined, {
      leadingBlank: true,
    }).map((s) => s.words);
    expect(matcherSlides.length).toBe(ctxSlides.length);
    // Slide 0 in both is the blank (empty word list).
    expect(matcherSlides[0]).toEqual([]);
  });
});

describe("slides override (de)serialization", () => {
  const slides: OverrideSlide[] = [
    { section: { type: "verse", number: 1 }, lyrics: "Line A" },
    { section: { type: "chorus", number: 1 }, lyrics: "Chorus" },
  ];

  it("round-trips through serialize/parse", () => {
    const json = serializeSlidesOverride(slides);
    expect(parseSlidesOverride(json)).toEqual(slides);
  });

  it("returns null for absent / empty / malformed JSON", () => {
    expect(parseSlidesOverride(null)).toBeNull();
    expect(parseSlidesOverride("")).toBeNull();
    expect(parseSlidesOverride("[]")).toBeNull();
    expect(parseSlidesOverride("not json")).toBeNull();
  });

  it("drops malformed entries", () => {
    const json = JSON.stringify([
      { section: { type: "verse", number: 1 }, lyrics: "ok" },
      { lyrics: "missing section" },
      { section: { type: "x" }, lyrics: "missing number" },
    ]);
    expect(parseSlidesOverride(json)).toEqual([
      { section: { type: "verse", number: 1 }, lyrics: "ok" },
    ]);
  });
});

describe("slidesToBody", () => {
  it("groups consecutive same-section slides and re-parses to the same list", () => {
    const slides: OverrideSlide[] = [
      { section: { type: "verse", number: 1 }, lyrics: "V1 slide one" },
      { section: { type: "verse", number: 1 }, lyrics: "V1 slide two" },
      { section: { type: "chorus", number: 1 }, lyrics: "Chorus" },
      { section: { type: "verse", number: 1 }, lyrics: "V1 again" },
    ];
    const body = slidesToBody(slides);
    const reparsed = parseSong(body).slides;
    expect(reparsed.map((s) => s.lyrics)).toEqual(slides.map((s) => s.lyrics));
    expect(reparsed.map((s) => `${s.section.type}:${s.section.number}`)).toEqual([
      "verse:1",
      "verse:1",
      "chorus:1",
      "verse:1",
    ]);
  });
});
