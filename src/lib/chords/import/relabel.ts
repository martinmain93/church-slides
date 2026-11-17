/**
 * Name an existing song's sections from a chart being attached to it.
 *
 * Songs brought in from a slide export (ProPresenter without groups, say)
 * arrive as "verse 1" … "verse 9", one per slide: the chorus is three of
 * those verses and nobody can tell which. A chord chart for the same song
 * knows. This keeps the library song's own lyrics — every word and every
 * slide break — and only renames its sections after the chart's, merging the
 * runs of slides that are one chart section and writing a repeated chorus
 * once, with the play order carrying the repeats.
 *
 * Only for songs whose labels say nothing (all verses): a song someone has
 * already labelled is left alone.
 */

import { parseSong, tokenize, type ParsedSection } from "@/lib/parseSong";
import { formatSlideOrder, parseSlideOrder } from "@/lib/slideSequence";
import { chartToSong, type SongFromChart } from "@/lib/chords/import/toSong";
import type { ImportedChartSong } from "@/lib/chords/import/types";

/** How much of a library section's words must appear in a chart section to call it that. */
const MATCH = 0.7;

const NAMES: Record<string, string> = {
  verse: "Verse", chorus: "Chorus", bridge: "Bridge", tag: "Tag", intro: "Intro",
  outro: "Outro", "pre-chorus": "Pre-Chorus", instrumental: "Instrumental", other: "Other",
};

const keyOf = (t: ParsedSection) => `${t.type}:${t.number}`;

interface Section {
  tag: ParsedSection;
  slides: string[];
  words: Set<string>;
}

function sectionsOf(body: string): Section[] {
  const out = new Map<string, Section>();
  for (const slide of parseSong(body).slides) {
    const k = keyOf(slide.section);
    const s = out.get(k) ?? { tag: slide.section, slides: [], words: new Set<string>() };
    s.slides.push(slide.lyrics);
    for (const w of slide.words) s.words.add(w);
    out.set(k, s);
  }
  return [...out.values()];
}

function overlap(words: Set<string>, c: Section): number {
  if (words.size === 0) return 0;
  let shared = 0;
  for (const w of words) if (c.words.has(w)) shared += 1;
  return shared / words.size;
}

/**
 * The chart section these words belong to, or -1. A tie goes to `prefer`,
 * the section the previous slide was in: a line that ends one verse and
 * opens the next ("We live for You") belongs with the slide before it.
 */
function bestMatch(words: Set<string>, chart: Section[], prefer: number): number {
  let best = -1;
  let bestScore = 0;
  chart.forEach((c, i) => {
    const score = overlap(words, c);
    if (score > bestScore) {
      best = i;
      bestScore = score;
    }
  });
  if (bestScore < MATCH) return -1;
  if (prefer >= 0 && overlap(words, chart[prefer]!) >= bestScore - 0.05) return prefer;
  return best;
}

/**
 * The song's body and play order with sections named from `chart`, or null
 * when there's nothing to gain (already labelled, or the chart doesn't fit).
 */
export function relabelFromChart(
  existing: { body_text: string; slide_order: string | null },
  chartSong: ImportedChartSong,
): SongFromChart | null {
  const library = sectionsOf(existing.body_text);
  if (library.length < 2 || library.some((s) => s.tag.type !== "verse" && s.tag.type !== "other")) return null;
  const chart = sectionsOf(chartToSong(chartSong).body_text);
  if (!chart.some((c) => c.tag.type !== "verse")) return null;

  const target: number[] = [];
  for (const s of library) target.push(bestMatch(s.words, chart, target[target.length - 1] ?? -1));
  // Most of the song has to line up, or the chart is a different arrangement
  // (or a different song) and the old names are no worse than a guess.
  if (target.filter((t) => t >= 0).length < Math.ceil(library.length * 0.8)) return null;

  // Runs of consecutive library sections that are one chart section become
  // one section; a later run with the same words is that section repeated.
  interface Group { target: number; slides: string[]; tag?: ParsedSection }
  const groups: Group[] = [];
  const groupOf = new Map<string, { group: Group; at: number }>();
  let run: Group | null = null;
  library.forEach((s, i) => {
    const t = target[i]!;
    if (run && t >= 0 && run.target === t) {
      groupOf.set(keyOf(s.tag), { group: run, at: run.slides.length });
      run.slides.push(...s.slides);
      return;
    }
    run = { target: t, slides: [...s.slides] };
    groups.push(run);
    groupOf.set(keyOf(s.tag), { group: run, at: 0 });
  });
  const norm = (g: Group) => tokenize(g.slides.join(" ")).join(" ");
  const unique: Group[] = [];
  const canonical = new Map<Group, Group>();
  for (const g of groups) {
    const same = unique.find((u) => u.target === g.target && g.target >= 0 && norm(u) === norm(g));
    canonical.set(g, same ?? g);
    if (!same) unique.push(g);
  }

  // Tags: the chart's, numbered apart where two different sections share one.
  const used = new Set<string>();
  for (const g of unique) {
    let tag: ParsedSection = g.target >= 0 ? { ...chart[g.target]!.tag } : { type: "verse", number: 1 };
    while (used.has(keyOf(tag))) tag = { type: tag.type, number: tag.number + 1 };
    used.add(keyOf(tag));
    g.tag = tag;
  }
  const perType = new Map<string, number>();
  for (const g of unique) perType.set(g.tag!.type, (perType.get(g.tag!.type) ?? 0) + 1);
  const label = (t: ParsedSection) => {
    const name = NAMES[t.type] ?? "Other";
    return perType.get(t.type)! > 1 || t.number > 1 ? `${name} ${t.number}` : name;
  };
  const body_text = unique.map((g) => `[${label(g.tag!)}]\n${g.slides.join("\n\n")}`).join("\n\n");

  // The old play order, in the new sections: a run is played once, from its start.
  const oldOrder = parseSlideOrder(existing.slide_order);
  const played = oldOrder.length > 0 ? oldOrder : library.map((s) => s.tag);
  const sequence: ParsedSection[] = [];
  let last: { group: Group; at: number } | null = null;
  for (const t of played) {
    const hit = groupOf.get(keyOf(t));
    if (!hit) continue;
    const continuing = last && last.group === hit.group && hit.at > last.at;
    if (!continuing) sequence.push(canonical.get(hit.group)!.tag!);
    last = hit;
  }
  const docOrder = unique.map((g) => keyOf(g.tag!)).join(",");
  const slide_order = sequence.map(keyOf).join(",") === docOrder ? null : formatSlideOrder(sequence);
  return { body_text, slide_order };
}
