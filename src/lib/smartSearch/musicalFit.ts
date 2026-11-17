/**
 * How well one song sits next to another in a set, musically: keys close on
 * the circle of fifths (a band can segue without a jarring change), tempos
 * near each other, and the same feel (both in 4/4, or both in a triple time).
 *
 * Only facts both songs' charts have count; a song with none scores 0 and is
 * left out, since "unknown" isn't "fits".
 */

import { parseChord } from "@/lib/chords/chord";
import { normalizeTime } from "@/lib/smartSearch/parseQuery";

export interface ChartMeta {
  key: string | null;
  tempo: number | null;
  time: string | null;
}

export interface Fit {
  score: number;
  /** Why, for the result row: "Same key (E) · 72 bpm · 4/4". */
  reason: string;
}

const WEIGHT = { key: 0.45, tempo: 0.35, time: 0.2 };
/** Beyond this many bpm apart, tempos don't count as close at all. */
const TEMPO_RANGE = 25;

/** Where a key sits on the circle of fifths (0-11); a minor key sits with
 *  its relative major, so A minor and C major are the same place. */
function fifthsPosition(key: string): number | null {
  const k = parseChord(key);
  if (!k) return null;
  const minor = /^(m(?!aj)|min|-)/.test(k.suffix);
  const majorPc = minor ? (k.root + 3) % 12 : k.root;
  return (majorPc * 7) % 12;
}

/** Steps apart on the circle of fifths, 0-6. */
export function fifthsDistance(a: string, b: string): number | null {
  const pa = fifthsPosition(a);
  const pb = fifthsPosition(b);
  if (pa === null || pb === null) return null;
  const d = Math.abs(pa - pb);
  return Math.min(d, 12 - d);
}

const TRIPLE = new Set(["3/4", "6/8", "9/8", "12/8", "3/8"]);
const DUPLE = new Set(["4/4", "2/4", "2/2"]);

export function hasMusicalFacts(m: ChartMeta | undefined): boolean {
  return Boolean(m && (m.key || (m.tempo !== null && Number.isFinite(m.tempo)) || normalizeTime(m.time)));
}

export function musicalFit(target: ChartMeta, other: ChartMeta | undefined): Fit {
  if (!other) return { score: 0, reason: "" };
  let score = 0;
  const why: string[] = [];

  if (target.key && other.key) {
    const d = fifthsDistance(target.key, other.key);
    if (d !== null) {
      score += WEIGHT.key * ([1, 0.7, 0.35][d] ?? 0);
      why.push(d === 0 ? `Same key (${other.key})` : d === 1 ? `Key ${other.key}, next to ${target.key}` : `Key ${other.key}`);
    }
  } else if (other.key) {
    why.push(`Key ${other.key}`);
  }

  if (target.tempo && other.tempo) {
    const gap = Math.abs(target.tempo - other.tempo);
    score += WEIGHT.tempo * Math.max(0, 1 - gap / TEMPO_RANGE);
    why.push(`${Math.round(other.tempo)} bpm`);
  } else if (other.tempo) {
    why.push(`${Math.round(other.tempo)} bpm`);
  }

  const ta = normalizeTime(target.time);
  const tb = normalizeTime(other.time);
  if (ta && tb) {
    const sameFeel = (TRIPLE.has(ta) && TRIPLE.has(tb)) || (DUPLE.has(ta) && DUPLE.has(tb));
    score += WEIGHT.time * (ta === tb ? 1 : sameFeel ? 0.5 : 0);
    why.push(tb);
  } else if (tb) {
    why.push(tb);
  }

  return { score, reason: why.join(" · ") };
}

/** "E · 72 bpm · 4/4", for saying what a song was matched against. */
export function describeMeta(m: ChartMeta): string {
  return [m.key, m.tempo ? `${Math.round(m.tempo)} bpm` : null, normalizeTime(m.time)].filter(Boolean).join(" · ");
}
