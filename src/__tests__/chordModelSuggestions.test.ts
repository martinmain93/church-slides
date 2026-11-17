import { describe, expect, it } from "vitest";

import { createRateBudget, estimateCallTokens } from "@/lib/chords/chordModel";
import { setWordChordInBody } from "@/lib/chords/editorText";
import { buildChordLines, findLineWord, lineWords } from "@/lib/chords/lineWords";
import { modelPlacementsToSuggestions } from "@/lib/chords/suggestions";

describe("chord model suggestions", () => {
  it("marks a finished line as charted and a later empty line as a target", () => {
    const slide = "Blessed be Your name\nIn the land where the barley grows";
    const body = `[Verse 1]\n${slide}`;
    const charted = setWordChordInBody(body, null, slide, 0, 0, 7, 0, "A");
    const lines = buildChordLines(body, charted.chart);
    expect(lines.map((line) => [line.line_id, line.role, line.chorded_words, line.chorded_word_indexes])).toEqual([
      ["s0-l0", "target", ["Blessed"], [0]],
      ["s0-l1", "target", [], []],
    ]);
  });

  it("turns a verified phrase into one Tab group and a single chord into one ghost", () => {
    const body = "[Verse 2]\nBlessed be Your name\nWhen the road is long and dry";
    const phrase = modelPlacementsToSuggestions(
      body,
      [
        { line_id: "s0-l0", word: "Blessed", chord: "A" },
        { line_id: "s0-l1", word: "road", chord: "D" },
      ],
      true,
    );
    expect(phrase.map((s) => s.chord)).toEqual(["A", "D"]);
    expect(phrase.every((s) => s.phraseGroup === "model-phrase")).toBe(true);
    expect(phrase[0]).toMatchObject({ slideIdx: 0, lineIdx: 0, wordStart: 0 });

    const single = modelPlacementsToSuggestions(
      body,
      [{ line_id: "s0-l0", word: "Blessed", chord: "A" }],
      false,
    );
    expect(single).toHaveLength(1);
    expect(single[0]?.phraseGroup).toBeUndefined();
  });

  it("puts a chord on the third holy, not the first", () => {
    const body = "[Verse 1]\nHoly, holy, holy";
    const [ghost] = modelPlacementsToSuggestions(body, [{ line_id: "s0-l0", word: "holy", word_index: 2, chord: "A" }], false);
    expect(ghost).toMatchObject({ wordStart: 12, wordEnd: 16 });
  });

  it("windows a long song around the slide being charted", () => {
    const body = Array.from({ length: 30 }, (_, i) => `[Verse ${i + 1}]\nLine ${i} a\nLine ${i} b`).join("\n\n");
    const top = buildChordLines(body, null, { maxLines: 12 });
    expect(top[0]?.line_id).toBe("s0-l0");
    const late = buildChordLines(body, null, { maxLines: 12, focusSlide: 20 });
    expect(late).toHaveLength(12);
    expect(late.map((line) => line.line_id)).toContain("s20-l0");
    expect(late[0]?.line_id).not.toBe("s0-l0");
  });

  it("spends a token budget per minute and stops for a minute after a 429", () => {
    let t = 0;
    const budget = createRateBudget(6_000, 60_000, () => t);
    const call = estimateCallTokens(24);
    expect(budget.take(call)).toBe(true);
    expect(budget.take(call)).toBe(true);
    expect(budget.take(call)).toBe(false);
    t = 60_000;
    expect(budget.take(call)).toBe(true);
    budget.block();
    t = 100_000;
    expect(budget.take(1)).toBe(false);
    t = 120_000;
    expect(budget.take(1)).toBe(true);
  });
});

describe("line words", () => {
  it("counts words the way the sidecar does: punctuation stays, lone dashes don't count", () => {
    expect(lineWords("Holy, holy — holy!").map((w) => [w.index, w.text, w.start, w.end])).toEqual([
      [0, "Holy,", 0, 4],
      [1, "holy", 6, 10],
      [2, "holy!", 13, 17],
    ]);
  });

  it("trusts a matching index, and otherwise takes the first free copy", () => {
    expect(findLineWord("Holy, holy, holy", "holy", 2)?.index).toBe(2);
    expect(findLineWord("Holy, holy, holy", "grace", 2)).toBeNull();
    expect(findLineWord("Holy, holy, holy", "holy", undefined, new Set([0]))?.index).toBe(1);
    expect(findLineWord("Holy, holy, holy", "holy", 9)?.index).toBe(0);
  });
});
