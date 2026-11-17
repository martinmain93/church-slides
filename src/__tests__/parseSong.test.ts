/**
 * TS parity test: assert each fixture body produces the same sections, lyrics,
 * and words as the Python parser. Both languages assert against the same
 * golden file at `data/fixtures/parser_cases.expected.json`.
 *
 * Slide IDs are preview-only on the TS side and not compared.
 */

import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";
import { parseSong } from "@/lib/parseSong";

const REPO_ROOT = path.resolve(__dirname, "..", "..");

interface Case {
  name: string;
  body: string;
}

interface ExpectedSlide {
  section: { type: string; number: number };
  order_in_song: number;
  order_in_section: number;
  lyrics: string;
  words: string[];
}

interface ExpectedCase {
  name: string;
  slides: ExpectedSlide[];
  section_order: { type: string; number: number }[];
}

const cases = JSON.parse(
  readFileSync(path.join(REPO_ROOT, "data", "fixtures", "parser_cases.json"), "utf-8"),
) as Case[];
const expected = JSON.parse(
  readFileSync(
    path.join(REPO_ROOT, "data", "fixtures", "parser_cases.expected.json"),
    "utf-8",
  ),
) as ExpectedCase[];

const expectedByName = new Map(expected.map((e) => [e.name, e]));

describe.each(cases)("$name", ({ name, body }) => {
  it("matches golden output", () => {
    const exp = expectedByName.get(name);
    expect(exp).toBeDefined();
    const ts = parseSong(body);
    const actual = {
      slides: ts.slides.map((s) => ({
        section: s.section,
        order_in_song: s.order_in_song,
        order_in_section: s.order_in_section,
        lyrics: s.lyrics,
        words: s.words,
      })),
      section_order: ts.section_order,
    };
    expect(actual).toEqual({
      slides: exp!.slides,
      section_order: exp!.section_order,
    });
  });
});
