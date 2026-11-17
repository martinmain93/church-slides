/**
 * Ask the fast chord model for the next chord, or for a whole phrase.
 *
 * The sidecar verifies every placement before it comes back. A missing
 * sidecar, a declined guess, or a spent budget is an empty list — the editor
 * keeps showing only the exact later copies it can see locally.
 *
 * The budget is in tokens because that's Groq's binding limit: the free tier
 * allows 8,000 tokens a minute on gpt-oss-20b, output included, and one call
 * costs ~1,500 in plus up to ~1,000 out (measured 2026-10-21). Requests
 * (1,000 a day) are not the constraint. After a 429 the editor stops asking
 * for a minute rather than keep failing.
 */

import type { ChordChart } from "@/lib/chords/chart";
import { buildChordLines } from "@/lib/chords/lineWords";
import { modelPlacementsToSuggestions, type SongSuggestion } from "@/lib/chords/suggestions";
import { requestSidecar } from "@/lib/sidecarClient";

/** Lines sent per call: a window around the edit, not the whole song. */
const MAX_LINES = 24;
/** Under Groq's 8,000 a minute, with room for a stray long reply. */
export const SUGGEST_BUDGET = { tokens: 6_000, windowMs: 60_000 };
/** System prompt and tool definition, then per line, then the capped reply. */
export function estimateCallTokens(lineCount: number): number {
  return 700 + 35 * lineCount + 1_024;
}

/** Spends at most `limit` per rolling `windowMs`. `take(cost)` is false when that would overspend. */
export function createRateBudget(limit: number, windowMs: number, now: () => number = Date.now) {
  let spent: { at: number; cost: number }[] = [];
  let blockedUntil = 0;
  return {
    take(cost = 1): boolean {
      const t = now();
      if (t < blockedUntil) return false;
      spent = spent.filter((item) => t - item.at < windowMs);
      if (spent.reduce((sum, item) => sum + item.cost, 0) + cost > limit) return false;
      spent.push({ at: t, cost });
      return true;
    },
    /** The provider said slow down: ask nothing until the window has passed. */
    block(): void {
      blockedUntil = now() + windowMs;
    },
  };
}

const budget = createRateBudget(SUGGEST_BUDGET.tokens, SUGGEST_BUDGET.windowMs);

export async function fetchChordSuggestions(
  body: string,
  chart: ChordChart | null,
  songKey: string | null | undefined,
  focusSlide?: number,
): Promise<SongSuggestion[]> {
  const lines = buildChordLines(body, chart, { maxLines: MAX_LINES, focusSlide });
  if (!lines.some((line) => (line.chords ?? []).length > 0)) return [];
  if (!lines.some((line) => line.role === "target")) return [];
  if (!budget.take(estimateCallTokens(lines.length))) return [];
  const reply = await requestSidecar(
    { kind: "suggest_chords", song_key: songKey ?? "", lines },
    { timeoutMs: 8_000 },
  );
  if (reply.error) {
    budget.block();
    return [];
  }
  const placements = reply.placements ?? [];
  if (placements.length === 0) return [];
  return modelPlacementsToSuggestions(body, placements, Boolean(reply.phrase));
}
