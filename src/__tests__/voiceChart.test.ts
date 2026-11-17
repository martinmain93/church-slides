import { describe, expect, it } from "vitest";

import { buildChordLines } from "@/lib/chords/lineWords";
import { applySlideEdits, applyVoiceChart, changedSlideIndexes, describeVoiceChart, utteranceBoundary } from "@/lib/chords/voiceChart";
import { parseSong } from "@/lib/parseSong";
import type { ChordChart } from "@/lib/chords/chart";

describe("applyVoiceChart", () => {
  it("writes a Nashville progression onto the verses and holds the last chorus chord", () => {
    const body = [
      "[verse 1]",
      "Blessed be Your name",
      "In the land where the barley grows",
      "Where the quiet waters flow",
      "Blessed be Your name",
      "",
      "[chorus]",
      "Every blessing You pour out",
      "Turn back to praise",
    ].join("\n");

    const next = applyVoiceChart(
      body,
      null,
      [
        { line_id: "s0-l0", word: "Blessed", chord: "1" },
        { line_id: "s0-l1", word: "In", chord: "4" },
        { line_id: "s0-l2", word: "Where", chord: "5" },
        { line_id: "s0-l3", word: "Blessed", chord: "1" },
        { line_id: "s1-l0", word: "Every", chord: "6m" },
        { line_id: "s1-l1", word: "Turn", chord: "4" },
      ],
      [{ line_id: "s1-l1", chord: "5", bars: 3 }],
      "G",
    );

    expect(next.chordText).toContain("G\nBlessed be Your name");
    expect(next.chordText).toContain("C\nIn the land where the barley grows");
    expect(next.chordText).toContain("D\nWhere the quiet waters flow");
    expect(next.chordText).toContain("Em\nEvery blessing You pour out");
    expect(next.chordText).toContain("C");
    expect(next.chordText).toContain("D D D");
    expect(next.chart?.key).toBe("G");

    const cleared = applyVoiceChart(body, next.chart, [], [], "G", [
      { line_id: "s0-l0" },
      { line_id: "s0-l1", word: "In" },
    ]);
    expect(cleared.chordText.startsWith("G\n")).toBe(false);
    expect(cleared.chordText).not.toContain("C\nIn the land");
    expect(cleared.chordText).toContain("Em\nEvery blessing You pour out");
    expect(
      describeVoiceChart(
        [{ line_id: "s0-l0", word: "Blessed", chord: "1" }],
        [{ line_id: "s1-l1", chord: "5", bars: 3 }],
      ),
    ).toBe("s0-l0 Blessed=1; hold s1-l1 5 x3");
  });

  it("gives each repeated word its own chord", () => {
    const body = "[verse 1]\nHoly, holy, holy";
    const next = applyVoiceChart(
      body,
      null,
      [
        { line_id: "s0-l0", word: "Holy", word_index: 0, chord: "1" },
        { line_id: "s0-l0", word: "holy", word_index: 1, chord: "4" },
        { line_id: "s0-l0", word: "holy", word_index: 2, chord: "5" },
      ],
      [],
      "D",
    );
    expect(next.chordText.split("\n")[1]).toBe("D     G     A");
  });

  it("sends the chords already on verse 1 so a later verse can be copied from them", () => {
    const body = ["[verse 1]", "Blessed be Your name", "", "[verse 2]", "When the road"].join("\n");
    const chart: ChordChart = {
      version: 1,
      key: null,
      tempo: null,
      time: null,
      sections: [
        {
          label: "Verse 1",
          stanzas: [
            [
              {
                text: "Blessed be Your name",
                chords: [
                  { pos: 0, chord: "G" },
                  { pos: 8, chord: "D" },
                ],
              },
            ],
          ],
        },
      ],
    };
    const lines = buildChordLines(body, chart);
    expect(lines[0]).toMatchObject({
      line_id: "s0-l0",
      chords: ["G", "D"],
      chorded_words: ["Blessed", "be"],
      chorded_word_indexes: [0, 1],
      role: "target",
    });
    expect(lines[1]).toMatchObject({ line_id: "s1-l0", chords: [], role: "target" });
  });
});

describe("voice slide edits", () => {
  const body = ["[verse 1]", "One", "", "[verse 2]", "Two", "", "[verse 3]", "Though I walk", "through the wilderness"].join("\n");

  it("renames a verse to a chorus", () => {
    const next = applySlideEdits(body, [
      { op: "rename", section: "verse 3", section_type: "chorus", section_number: 1 },
    ]);
    const slides = parseSong(next.body).slides;
    expect(slides[2]?.section).toEqual({ type: "chorus", number: 1 });
    expect(slides[2]?.lyrics).toContain("Though I walk");
    expect(next.notes[0]).toContain("chorus");
  });

  it("splits, deletes, and moves slides", () => {
    const split = applySlideEdits(body, [{ op: "split", section: "verse 3", after_line: 0 }]);
    const slides = parseSong(split.body).slides;
    expect(slides).toHaveLength(4);
    expect(slides[2]?.lyrics).toBe("Though I walk");
    expect(slides[3]?.lyrics).toBe("through the wilderness");

    const removed = applySlideEdits(body, [{ op: "delete", section: "verse 2" }]);
    expect(parseSong(removed.body).slides.map((slide) => slide.section.number)).toEqual([1, 3]);

    const moved = applySlideEdits(body, [
      { op: "move", section: "verse 3", anchor: "verse 1", place: "before" },
    ]);
    expect(parseSong(moved.body).slides.map((slide) => slide.lyrics.split("\n")[0])).toEqual([
      "Though I walk",
      "One",
      "Two",
    ]);
  });
});

describe("changedSlideIndexes", () => {
  it("marks a renamed section and leaves the other slides alone", () => {
    const before = ["[verse 1]", "Blessed be Your name", "", "[verse 3]", "Though I walk"].join("\n");
    const after = applySlideEdits(before, [
      { op: "rename", section: "verse 3", section_type: "chorus", section_number: 1 },
    ]).body;
    expect(changedSlideIndexes(before, after, null, null)).toEqual([1]);
  });
});

describe("utteranceBoundary", () => {
  it("flushes after two seconds of silence following speech, and waits through a fragment", () => {
    expect(utteranceBoundary({ speechMs: 500, silenceMs: 2000, holding: false, busy: false })).toBe("flush");
    expect(utteranceBoundary({ speechMs: 500, silenceMs: 1000, holding: false, busy: false })).toBe("wait");
    expect(utteranceBoundary({ speechMs: 500, silenceMs: 2000, holding: true, busy: false })).toBe("wait");
  });
});