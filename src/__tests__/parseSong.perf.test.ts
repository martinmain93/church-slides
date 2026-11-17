/**
 * Long-body parser perf guard.
 *
 * The Phase 1 success criterion says the editor must handle 1000+-line
 * bodies without lag. The TS parser runs on every keystroke (via the
 * preview pane's `useMemo`), so it has to stay well below frame budget
 * (16 ms) on realistic content.
 *
 * Threshold is generous (200 ms on a 1500-line synthetic body; typically
 * ~15 ms) so a busy machine doesn't fail the build: it's a tripwire for an
 * order-of-magnitude regression, not a benchmark.
 */

import { describe, expect, it } from "vitest";
import { parseSong } from "@/lib/parseSong";

function buildLongBody(slides: number): string {
  const sections = ["verse 1", "chorus", "verse 2", "bridge"];
  const out: string[] = [];
  for (let i = 0; i < slides; i++) {
    out.push(`[${sections[i % sections.length]}]`);
    for (let line = 0; line < 4; line++) {
      out.push(`Slide ${i} line ${line} with some lyrics that vary a little`);
    }
    out.push(""); // blank line splits slides
  }
  return out.join("\n");
}

describe("parseSong perf", () => {
  // Timings mean something on a known machine, not on a shared cloud runner.
  it.skipIf(process.env.CI)("parses a 1500-line body in well under a frame budget", () => {
    // ~300 slides × ~5 lines each ≈ 1500 lines.
    const body = buildLongBody(300);
    expect(body.split("\n").length).toBeGreaterThan(1500);

    const start = performance.now();
    const result = parseSong(body);
    const elapsed = performance.now() - start;

    expect(result.slides.length).toBeGreaterThanOrEqual(300);
    expect(elapsed).toBeLessThan(200);
  });
});
