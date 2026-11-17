/**
 * The key a chart is in, worked out from its chords — for sheets that never
 * say ("I will rejoice.pdf": no key in the name, none on the page). Without a
 * written key a per-service key has nothing to transpose from.
 *
 * Scores every major key by how many of the chart's chords are its diatonic
 * triads, with a bonus for the song opening or closing on the tonic. Answers
 * only when the chords clearly sit in one key; a chart that wanders (or has
 * too few chords to tell) gets no key rather than a wrong one.
 */

import type { ChordChart } from "@/lib/chords/chart";
import { parseChord } from "@/lib/chords/chord";

const MAJOR_KEY_NAMES = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"];
const MINOR_KEY_NAMES = ["Cm", "C#m", "Dm", "Ebm", "Em", "Fm", "F#m", "Gm", "G#m", "Am", "Bbm", "Bm"];

/** Degrees of the major scale with a major / minor triad on them. */
const MAJOR_DEGREES = [0, 5, 7];
const MINOR_DEGREES = [2, 4, 9];

type Quality = "major" | "minor" | "either" | "other";

function quality(suffix: string): Quality {
  if (/^(dim|°|ø|m7b5|aug|\+)/.test(suffix)) return "other";
  if (/^(m(?!aj)|min|-)/.test(suffix)) return "minor";
  // Sus and power chords have no third: they fit either.
  if (/^(sus|5$)/.test(suffix)) return "either";
  return "major";
}

function fits(tonic: number, root: number, q: Quality): boolean {
  const degree = (root - tonic + 12) % 12;
  if (q === "major") return MAJOR_DEGREES.includes(degree);
  if (q === "minor") return MINOR_DEGREES.includes(degree);
  if (q === "either") return MAJOR_DEGREES.includes(degree) || MINOR_DEGREES.includes(degree);
  return false;
}

/** At least this many chords before we'll name a key. */
const MIN_CHORDS = 4;
/** Share of the chords the winning key must explain. */
const MIN_FIT = 0.75;

export function inferKey(chart: ChordChart): string | null {
  const chords: { root: number; q: Quality }[] = [];
  for (const section of chart.sections) {
    for (const stanza of section.stanzas) {
      for (const line of stanza) {
        for (const placed of line.chords) {
          const c = parseChord(placed.chord.replace(/^\((.*)\)$/, "$1"));
          if (c) chords.push({ root: c.root, q: quality(c.suffix) });
        }
      }
    }
  }
  if (chords.length < MIN_CHORDS) return null;
  const first = chords[0]!;
  const last = chords[chords.length - 1]!;

  let best = { tonic: -1, score: -Infinity, fit: 0 };
  for (let tonic = 0; tonic < 12; tonic++) {
    let fit = 0;
    for (const c of chords) if (fits(tonic, c.root, c.q)) fit += 1;
    let score = fit;
    // Songs mostly open and land on the tonic (or its relative minor).
    for (const end of [first, last]) {
      if (end.root === tonic && end.q !== "minor") score += 2;
    }
    if (score > best.score) best = { tonic, score, fit };
  }
  if (best.fit / chords.length < MIN_FIT) return null;

  // A minor song: opens and closes on the relative minor (vi).
  const vi = (best.tonic + 9) % 12;
  if (first.root === vi && last.root === vi && first.q === "minor") return MINOR_KEY_NAMES[vi]!;
  return MAJOR_KEY_NAMES[best.tonic]!;
}
