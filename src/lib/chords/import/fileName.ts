/**
 * What a song file's *name* says, for when its contents don't: people and
 * sites name chord sheets "goodness-of-god-G.pdf", "Build My Life (37195) -
 * Chord Chart - C - 2 Column.pdf", "Holy Forever-chords-Bb.pdf".
 */

import { parseChord } from "@/lib/chords/chord";

const KEY = "([A-G](?:#|b|♯|♭)?m?)";

export function fromFileName(fileName: string): { title: string; key: string | null } {
  let t = fileName.replace(/\.[^.]+$/, "");
  let key: string | null = null;
  const take = (re: RegExp) => {
    const m = re.exec(t);
    if (m) {
      if (!key && m[1] && parseChord(m[1])) key = m[1];
      t = t.slice(0, m.index) + t.slice(m.index + m[0].length);
    }
  };
  t = t.replace(/(_\d+|-\d+|\s\(\d\))$/, ""); // "_2", "-2" copies
  take(new RegExp(`\\s*-\\s*chord chart\\s*-\\s*${KEY}\\s*-\\s*\\d\\s*column.*$`, "i")); // PraiseCharts
  take(new RegExp(`\\s*chord chart in\\s+${KEY}\\s*$`, "i"));
  take(/\s*chords?\s+by\s+.+$/i); // "X Chords by Y tabs @ Ultimate Guitar Archive"
  take(new RegExp(`[-_\\s]+key[-_\\s]+(?:of[-_\\s]+)?${KEY}(?=$|[-_\\s])`, "i"));
  take(new RegExp(`[-_\\s]+chords?[-_\\s]+${KEY}\\s*$`, "i"));
  take(new RegExp(`\\s*\\(${KEY}\\)\\s*$`)); // "Majesty (Here I Am) (A)"
  take(new RegExp(`\\s*[-_]\\s*${KEY}\\s*$`)); // "goodness-of-god-G", "Tremble -C"
  t = t
    .replace(/\s*\(\d{3,}\)/g, "") // catalogue numbers "(37195)"
    .replace(/[-_\s]+chords?(?:[-_\s]+charts?)?$/i, "")
    .replace(/[-_\s]*\d{2,3}[-_\s]*bpm[-_\s]*$/i, "")
    .replace(/\s*\((recording|lead sheet|chords?)\)\s*$/i, "")
    .replace(/[_+]+/g, " ")
    // "dry-bones" -> "dry bones". A capture rather than a lookbehind, which
    // Safari only parses from 16.4 — older Mac web views threw on it.
    .replace(/(\w)-(?=\w)/g, "$1 ")
    .replace(/^\d{3,}\s*[-_ ]\s*/, "") // "14418-dry-bones"
    .replace(/\s{2,}/g, " ")
    .trim();
  // Slug-case names ("dry bones") read better title-cased.
  if (t === t.toLowerCase()) t = t.replace(/\b\p{L}/gu, (c) => c.toUpperCase());
  return { title: t || "Untitled", key };
}
