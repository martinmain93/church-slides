/**
 * Turn an imported chart into a library song: lyrics-only `body_text` in the
 * app's own format (docs/SONG_FORMAT.md) plus a `slide_order` when the source
 * spelled out an arrangement.
 *
 * Section labels are renumbered where needed so every `[tag]` in the body
 * resolves to a distinct section — two different "Chorus" blocks would
 * otherwise both parse as `chorus 1` and merge.
 */

import { parseSectionTag, type ParsedSection } from "@/lib/parseSong";
import { formatSlideOrder } from "@/lib/slideSequence";
import type { ChartSection } from "@/lib/chords/chart";
import type { ImportedChartSong } from "@/lib/chords/import/types";

const REPEAT_MARK = /\s*\(?\s*(?:x|×)\s?\d+\s*\)?\s*$|\s*\(\s*\d+\s*x\s*\)\s*$|\s*\(repeat\)\s*$/i;

/** Lyrics of a stanza as the projector should show them. */
function stanzaLyrics(stanza: ChartSection["stanzas"][number]): string {
  return stanza
    .map((l) => l.text.replace(REPEAT_MARK, "").trim())
    .filter((t) => t !== "")
    .join("\n");
}

/**
 * Labels the app's section parser can't classify still deserve a sensible
 * tag. Only sections with words reach here (a chords-only one is band-only),
 * so a sung "Interlude" or "Instrumental" is a tag, not an instrumental.
 */
const LABEL_TYPE_HINTS: [RegExp, string][] = [
  [/^(ending|end|coda)\b/i, "Outro"],
  [/^(interlude|turnaround|turn|break|solo|link|instrumental|vamp|spontaneous)\b/i, "Tag"],
  [/^(refrain|hook|refrão|coro|estribillo|reprise)\b/i, "Chorus"],
  [/^(strophe|verso)\b/i, "Verse"],
  [/^(puente|channel)\b/i, "Bridge"],
  [/^post[- ]?chorus\b/i, "Tag"],
];

/** "Chorus 1A" / "Chorus 1b" (SongSelect's variants of one section) → "Chorus 1". */
const VARIANT_SUFFIX = /(\d+)\s*[a-z]$/i;

function canonicalLabel(label: string): string {
  const plain = label.replace(VARIANT_SUFFIX, "$1");
  for (const [re, name] of LABEL_TYPE_HINTS) {
    if (re.test(plain)) {
      const num = /(\d+)$/.exec(plain)?.[1];
      return num ? `${name} ${num}` : name;
    }
  }
  return plain;
}

function key(s: ParsedSection): string {
  return `${s.type}:${s.number}`;
}

export interface SongFromChart {
  body_text: string;
  slide_order: string | null;
}

export function chartToSong(song: ImportedChartSong): SongFromChart {
  // One body section per distinct (label, lyrics) — a chorus written out in
  // full each time it's sung is stored once and repeated through the order.
  interface Entry { tag: ParsedSection; lyrics: string[]; label: string }
  const entries: Entry[] = [];
  const byContent = new Map<string, Entry>();
  const usedKeys = new Set<string>();
  const labelToEntry = new Map<string, Entry>();
  const docOrder: Entry[] = [];

  for (const section of song.chart.sections) {
    const lyrics = section.stanzas.map(stanzaLyrics).filter((t) => t !== "");
    if (lyrics.length === 0) continue; // instrumental: band-only
    const label = canonicalLabel(section.label);
    const contentKey = `${label.toLowerCase().replace(/\s*\d+$/, "")}\u0000${lyrics.join("\n\n").toLowerCase()}`;
    let entry = byContent.get(contentKey);
    if (!entry) {
      let tag = parseSectionTag(label);
      // Two different blocks both called "Chorus": number the newcomer.
      while (usedKeys.has(key(tag))) tag = { type: tag.type, number: tag.number + 1 };
      usedKeys.add(key(tag));
      entry = { tag, lyrics, label };
      entries.push(entry);
      byContent.set(contentKey, entry);
    }
    labelToEntry.set(section.label.toLowerCase(), entry);
    docOrder.push(entry);
  }

  const body = entries
    .map((e) => `[${tagText(e.tag, e.label)}]\n${e.lyrics.join("\n\n")}`)
    .join("\n\n");

  let sequence: Entry[] | null = null;
  if (song.order && song.order.length > 0) {
    sequence = song.order
      .map((l) => labelToEntry.get(l.toLowerCase()))
      .filter((e): e is Entry => e !== undefined);
  } else if (docOrder.length !== entries.length) {
    sequence = docOrder; // sections repeated in the document
  }
  const isDocOrder =
    sequence !== null && sequence.length === entries.length && sequence.every((e, i) => e === entries[i]);
  const slide_order =
    sequence && sequence.length > 0 && !isDocOrder ? formatSlideOrder(sequence.map((e) => e.tag)) : null;

  return { body_text: body, slide_order };
}

/** The `[tag]` text for a section: its own label when that parses to the same tag. */
function tagText(tag: ParsedSection, label: string): string {
  const parsed = parseSectionTag(label);
  if (parsed.type === tag.type && parsed.number === tag.number && tag.type !== "other") return label;
  const names: Record<string, string> = {
    verse: "Verse", chorus: "Chorus", bridge: "Bridge", tag: "Tag", intro: "Intro",
    outro: "Outro", "pre-chorus": "Pre-Chorus", instrumental: "Instrumental", other: "Other",
  };
  return `${names[tag.type] ?? "Other"} ${tag.number}`;
}
