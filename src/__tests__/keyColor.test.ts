import { describe, expect, it } from "vitest";

import { KEY_COLORS, UNKNOWN_KEY_COLOR, keyColor, keyFamily } from "@/lib/chords/keyColor";

describe("keyColor", () => {
  it("gives each major key its own colour, chromatically", () => {
    const majors = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"];
    expect(majors.map(keyColor)).toEqual([...KEY_COLORS]);
    expect(new Set(KEY_COLORS).size).toBe(12);
  });

  it("treats enharmonic spellings as the same key", () => {
    expect(keyColor("C#")).toBe(keyColor("Db"));
    expect(keyColor("Gb")).toBe(keyColor("F#"));
    expect(keyColor("A♭")).toBe(keyColor("Ab"));
  });

  it("colours a minor key like its relative major", () => {
    expect(keyColor("Am")).toBe(keyColor("C"));
    expect(keyColor("Em")).toBe(keyColor("G"));
    expect(keyColor("F#m")).toBe(keyColor("A"));
    expect(keyColor("Bbm")).toBe(keyColor("Db"));
    expect(keyColor("Cmin")).toBe(keyColor("Eb"));
    // "maj" is major, not minor.
    expect(keyFamily("Cmaj")).toBe(0);
  });

  it("falls back to a neutral colour for a key it can't read", () => {
    expect(keyColor(null)).toBe(UNKNOWN_KEY_COLOR);
    expect(keyColor("Hello")).toBe(UNKNOWN_KEY_COLOR);
  });
});
