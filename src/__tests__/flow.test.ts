import { describe, expect, it } from "vitest";

import { yOfWord } from "@/components/presentation/FlowStage";
import { parseSong } from "@/lib/parseSong";
import {
  anchorForItem,
  anchorWord,
  layoutFlow,
  resolveDisplayMode,
  slideAtWord,
  slideOffsets,
  trackFromMessage,
  trackWordAt,
  type FlowTrack,
} from "@/lib/presentation/flow";
import { resolveSlideSequence } from "@/lib/slideSequence";

function track(over: Partial<FlowTrack> = {}): FlowTrack {
  return {
    itemIdx: 0, word: 10, rateWps: 2, correction: 0, settleSeconds: 2,
    limit: 100, jump: false, receivedAtMs: 1_000, ...over,
  };
}

describe("resolveDisplayMode", () => {
  it("auto scrolls only while the AI drives the screen", () => {
    expect(resolveDisplayMode("auto", "active")).toBe("continuous");
    expect(resolveDisplayMode("auto", "shadow")).toBe("slide");
    expect(resolveDisplayMode("auto", "off")).toBe("slide");
  });

  it("an explicit choice wins over the AI mode", () => {
    expect(resolveDisplayMode("slide", "active")).toBe("slide");
    expect(resolveDisplayMode("continuous", "off")).toBe("continuous");
  });
});

describe("trackWordAt — must match decision/flow.py FlowTrack.at", () => {
  it("advances at the singing rate", () => {
    expect(trackWordAt(track(), 1_000)).toBe(10);
    expect(trackWordAt(track(), 2_500)).toBe(13);
  });

  it("pays a correction back over the settle time, then stops adding it", () => {
    const t = track({ correction: 4 });
    expect(trackWordAt(t, 2_000)).toBe(10 + 2 + 2);
    expect(trackWordAt(t, 3_000)).toBe(10 + 4 + 4);
    expect(trackWordAt(t, 4_000)).toBe(10 + 6 + 4);
  });

  it("never passes its limit, and never runs backwards in time", () => {
    expect(trackWordAt(track({ limit: 12 }), 60_000)).toBe(12);
    expect(trackWordAt(track(), 0)).toBe(10);
  });

  it("fills wire defaults", () => {
    const t = trackFromMessage(
      { kind: "flow_position", item_idx: 1, word: 3, rate_wps: 1, limit: 9 },
      5,
    );
    expect(t).toMatchObject({ itemIdx: 1, correction: 0, settleSeconds: 2, jump: false, receivedAtMs: 5 });
  });
});

describe("slide offsets and lookup", () => {
  // Blank lead slide, then 4, 0 (instrumental), 3 words.
  const offsets = slideOffsets([{ words: [] }, { words: ["a", "b", "c", "d"] }, { words: [] }, { words: ["e", "f", "g"] }]);

  it("counts words slide by slide", () => {
    expect(offsets).toEqual([0, 0, 4, 4, 7]);
  });

  it("skips blank slides, and a word at a slide's end belongs to the next", () => {
    expect(slideAtWord(offsets, 0)).toBe(1);
    expect(slideAtWord(offsets, 3.9)).toBe(1);
    expect(slideAtWord(offsets, 4)).toBe(3);
    expect(slideAtWord(offsets, 99)).toBe(3);
  });

  it("a slide anchor sits on that slide's first word", () => {
    expect(anchorWord({ kind: "slide", itemIdx: 0, slideIdx: 3 }, offsets, 0)).toBe(4);
  });

  it("a track for another song does not land in this column", () => {
    const a = anchorForItem({ kind: "track", track: track({ itemIdx: 2 }) }, 1, 3);
    expect(a).toEqual({ kind: "slide", itemIdx: 1, slideIdx: 3 });
  });
});

describe("layoutFlow", () => {
  const body = "[Verse 1]\nAmazing grace, how sweet\nthe sound!\n\n[Chorus]\n— My chains are gone —\nI've been set free";
  const slides = resolveSlideSequence(body, null, null, { leadingBlank: true });

  it("line ranges tile each slide's tokens, in the matcher's numbering", () => {
    const layout = layoutFlow(slides);
    const offsets = slideOffsets(slides);
    for (const s of layout) {
      expect(s.lines[0]!.start).toBe(offsets[s.slideIdx]);
      expect(s.lines[s.lines.length - 1]!.end).toBe(offsets[s.slideIdx + 1]);
    }
    const flat = layout.flatMap((s) => s.lines);
    for (let i = 1; i < flat.length; i++) expect(flat[i]!.start).toBe(flat[i - 1]!.end);
  });

  it("keeps punctuation in the displayed text", () => {
    const text = layoutFlow(slides).flatMap((s) => s.lines.map((l) => l.text));
    expect(text).toContain("the sound!");
  });

  it("omits blank slides from the column", () => {
    expect(layoutFlow(slides).map((s) => s.slideIdx)).not.toContain(0);
    expect(parseSong(body).slides).toHaveLength(2);
  });
});

describe("yOfWord", () => {
  const lines = [
    { start: 0, end: 4, top: 0, height: 50 },
    { start: 4, end: 8, top: 50, height: 50 },
    { start: 8, end: 10, top: 160, height: 50 }, // after a slide gap
  ];

  it("interpolates within a line", () => {
    expect(yOfWord(lines, 2)).toBe(25);
    expect(yOfWord(lines, 9)).toBe(185);
  });

  it("parks at the next line's top, not in the gap, once a line is sung", () => {
    expect(yOfWord(lines, 8)).toBe(160);
  });

  it("past the end sits under the last line", () => {
    expect(yOfWord(lines, 50)).toBe(210);
  });
});
