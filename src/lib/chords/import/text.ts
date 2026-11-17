/**
 * One tolerant reader for every *text* chart format.
 *
 * ChordPro, OnSong, SongSelect chord sheets, Ultimate Guitar pages and a
 * chart someone typed into Notes are all the same few ingredients in
 * different proportions: metadata lines, section headers, lyric lines with
 * inline `[G]` chords, and chord lines sitting over lyric lines. Rather than a
 * parser per format that each half-handles the others' quirks, each line is
 * classified on its own and the format label is decided afterwards from what
 * was seen — it's only reported, never used to branch.
 */

import { isChordLine, isChordToken, parseChord } from "@/lib/chords/chord";
import {
  CHART_VERSION,
  type ChartLine,
  type ChartSection,
  type ChordChart,
  type PlacedChord,
} from "@/lib/chords/chart";
import type { ImportFormat, ImportedChartSong } from "@/lib/chords/import/types";
import { parseSectionTag } from "@/lib/parseSong";

// --- Section headers ---------------------------------------------------------

const SECTION_WORDS =
  "verse|chorus|pre[- ]?chorus|post[- ]?chorus|bridge|tag|intro|outro|ending|end|instrumental|interlude|" +
  "turnaround|turn|vamp|refrain|hook|channel|coda|break|solo|link|misc|outro|spontaneous|reprise|" +
  "strophe|refrão|coro|estribillo|verso|puente";
/** Short forms (V1, C2, PC, B) — only accepted with a number, colon or brackets. */
const SECTION_ABBREV = "v|c|pc|pre|b|br|t|i|o|e|ch|vs";

const HEADER_RE = new RegExp(
  // optional bracket/paren, the word, optional number, optional close, optional
  // colon, then optionally a repeat mark and/or chords on the same line.
  `^\\s*[\\[(]?\\s*((?:${SECTION_WORDS})(?:\\s*\\d+[a-z]?)?)\\s*[\\])]?\\s*:?\\s*(.*)$`,
  "i",
);
const ABBREV_RE = new RegExp(
  `^\\s*(?:\\[\\s*((?:${SECTION_ABBREV})\\s*\\d*)\\s*\\]|((?:${SECTION_ABBREV})\\s*\\d+)\\s*:?|((?:${SECTION_ABBREV}))\\s*:)\\s*(.*)$`,
  "i",
);
const REPEAT_TAIL = /^\(?\s*(x|×)\s?\d+\s*\)?$|^\(?\s*\d+\s*x\s*\)?$|^\(repeat\)$/i;

interface Header {
  label: string;
  /** Chords written on the header line itself ("Intro: G  C  D"). */
  rest: string;
}

function titleCase(s: string): string {
  return s
    .toLowerCase()
    .replace(/(^|[\s-])(\p{L})/gu, (_m, sep: string, ch: string) => sep + ch.toUpperCase());
}

const ABBREV_NAMES: Record<string, string> = {
  v: "Verse", vs: "Verse", c: "Chorus", ch: "Chorus", pc: "Pre-Chorus", pre: "Pre-Chorus",
  b: "Bridge", br: "Bridge", t: "Tag", i: "Intro", o: "Outro", e: "Ending",
};

function expandAbbrev(raw: string): string {
  const m = /^([a-z]+)\s*(\d*)$/i.exec(raw.trim());
  if (!m) return raw;
  const name = ABBREV_NAMES[m[1]!.toLowerCase()] ?? raw;
  return m[2] ? `${name} ${m[2]}` : name;
}

/**
 * Is this line a section header? `bracketChordMode` is true when the file
 * uses `[G]` inline chords, in which case a bare `[C]` is a chord, not "Chorus".
 */
export function parseHeader(line: string, bracketChordMode: boolean): Header | null {
  const trimmed = line.trim();
  if (!trimmed) return null;
  const m = HEADER_RE.exec(trimmed);
  if (m) {
    const rest = m[2]!.trim();
    // "Chorus" followed by lyrics isn't a header ("Chorus of angels sing…").
    if (rest && !REPEAT_TAIL.test(rest) && !isChordLine(rest)) return null;
    // A header written as a sentence ("Verse 1 is quieter") fails above; one
    // with trailing punctuation other than a colon is a lyric.
    return { label: titleCase(m[1]!.replace(/\s+/g, " ")), rest: REPEAT_TAIL.test(rest) ? "" : rest };
  }
  const a = ABBREV_RE.exec(trimmed);
  if (a) {
    const raw = (a[1] ?? a[2] ?? a[3])!;
    // "C2", "B7", "E" are chords as well as abbreviations. Only a colon makes
    // them a label — or brackets, in a file that doesn't bracket its chords.
    if (isChordToken(raw.replace(/\s+/g, ""))) {
      const bracketed = a[1] !== undefined;
      const colon = /^[^:]*:/.test(trimmed) && !bracketed;
      if (!colon && !(bracketed && !bracketChordMode)) return null;
    }
    const rest = a[4]!.trim();
    if (rest && !REPEAT_TAIL.test(rest) && !isChordLine(rest)) return null;
    return { label: expandAbbrev(raw), rest: REPEAT_TAIL.test(rest) ? "" : rest };
  }
  return null;
}

// --- Chord placement ---------------------------------------------------------

const INLINE_CHORD_RE = /\[([^\]\n]{1,24})\]/g;

/** Pull `[G]` chords out of a lyric line, recording where each one sat. */
export function parseInlineChords(line: string): ChartLine {
  const chords: PlacedChord[] = [];
  let text = "";
  let last = 0;
  INLINE_CHORD_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = INLINE_CHORD_RE.exec(line))) {
    const sym = m[1]!.trim();
    text += line.slice(last, m.index);
    last = m.index + m[0].length;
    // `[*note]` and anything not chord-shaped is an annotation, not a chord.
    if (sym.startsWith("*") || !(isChordToken(sym) || /^n\.?c\.?$/i.test(sym) || sym === "|")) continue;
    chords.push({ pos: text.length, chord: sym });
  }
  text += line.slice(last);
  return { text: text.replace(/\s+$/, ""), chords };
}

/** Line has inline bracket chords (and they're not the whole line). */
function hasInlineChords(line: string): boolean {
  INLINE_CHORD_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = INLINE_CHORD_RE.exec(line))) {
    if (isChordToken(m[1]!.trim())) return true;
  }
  return false;
}

/** Chord tokens of a chord line with their columns. */
function chordColumns(line: string): PlacedChord[] {
  const out: PlacedChord[] = [];
  const re = /\S+/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(line))) {
    if (isChordToken(m[0]) || /^n\.?c\.?$/i.test(m[0])) out.push({ pos: m.index, chord: m[0] });
  }
  return out;
}

/** Merge a chord line sitting over a lyric line into one chart line. */
function mergeOver(chordLine: string, lyric: string): ChartLine {
  return { text: lyric.replace(/\s+$/, ""), chords: chordColumns(chordLine) };
}

/** A chord-only line, keeping bar lines and repeat marks readable. */
function chordOnlyLine(line: string): ChartLine {
  const chords = chordColumns(line);
  // Keep the whole line's text (bars, "x2") as the chord row by storing
  // non-chord tokens as chords too — they render in the chord row, which is
  // where a musician expects to read them.
  const all: PlacedChord[] = [];
  const re = /\S+/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(line))) all.push({ pos: m.index, chord: m[0] });
  return { text: "", chords: chords.length > 0 ? all : [] };
}

// --- Metadata ----------------------------------------------------------------

interface Meta {
  title: string | null;
  artist: string | null;
  key: string | null;
  tempo: number | null;
  time: string | null;
  ccli: string | null;
  copyright: string | null;
  capo: number | null;
  /** The church's licence number from a SongSelect footer. */
  license: string | null;
}

function emptyMeta(): Meta {
  return {
    title: null, artist: null, key: null, tempo: null, time: null, ccli: null, copyright: null, capo: null,
    license: null,
  };
}

/** `Key: G`-style lines, as OnSong and many plain charts write them. */
const META_LINE_RE =
  /^\s*(title|artist|author|authors|writer|writers|words and music by|written by|key|original key|tempo|bpm|time|time signature|meter|ccli|ccli song ?#?|ccli number|copyright|capo)\s*[:#=-]\s*(.+?)\s*$/i;

function applyMeta(meta: Meta, name: string, rawValue: string): boolean {
  const value = rawValue.trim();
  if (!value) return true;
  switch (name.toLowerCase().replace(/\s+/g, " ")) {
    case "title": case "t":
      meta.title ??= value; return true;
    case "subtitle": case "st": case "artist": case "author": case "authors": case "writer":
    case "writers": case "words and music by": case "written by": case "composer": case "lyricist":
      meta.artist ??= value; return true;
    case "key": case "original key": {
      const k = value.replace(/\s*(major|maj)$/i, "").replace(/\s*minor$/i, "m").split(/[\s,/]/)[0]!;
      if (parseChord(k)) meta.key ??= k;
      return true;
    }
    case "tempo": case "bpm": {
      const n = parseFloat(value);
      if (Number.isFinite(n)) meta.tempo ??= n;
      return true;
    }
    case "time": case "time signature": case "meter":
      meta.time ??= value; return true;
    case "ccli": case "ccli song #": case "ccli song#": case "ccli song": case "ccli number": {
      const d = /\d{3,}/.exec(value);
      if (d) meta.ccli ??= d[0];
      return true;
    }
    case "copyright": case "(c)":
      meta.copyright ??= value; return true;
    case "capo": {
      const n = parseInt(value, 10);
      if (Number.isFinite(n)) meta.capo ??= n;
      return true;
    }
  }
  return false;
}

/** SongSelect / publisher footer lines — metadata, not lyrics. */
function footerLine(line: string, meta: Meta): boolean {
  const t = line.trim();
  // PDF text often arrives with stray spaces inside words ("w ith",
  // "w w w .ccli.com"), so match the boilerplate with spaces squeezed out.
  const squeezed = t.replace(/\s+/g, "").toLowerCase();
  let m = /^ccli\s*song\s*(?:#|no\.?|number)?\s*:?\s*(\d+)/i.exec(t);
  if (m) { meta.ccli ??= m[1]!; return true; }
  m = /^ccli®?\s*licen[cs]e\s*(?:#|no\.?|number)?\s*:?\s*(\d+)?/i.exec(t);
  if (m) { if (m[1]) meta.license ??= m[1]; return true; }
  if (squeezed.startsWith("foruse") && squeezed.includes("songselect")) return true;
  if (squeezed.includes("songselect") && squeezed.includes("termsofuse")) return true;
  if (/^Note:\s*Reproduction of this sheet music/i.test(t)) return true;
  if (/^All rights reserved/i.test(t)) return true;
  if (/^(used by permission|www\.ccli\.com)/.test(squeezed)) return true;
  if (squeezed.includes("ccli.com") && t.length < 80) return true;
  m = /^(?:©|\(c\)|copyright\b)\s*(.+)$/i.exec(t);
  if (m) { meta.copyright ??= cleanCopyright(m[1]!) || null; return true; }
  return false;
}

/**
 * CCLI numbers wherever a sheet prints them — its own line ("CCLI Song #
 * 7117726"), inside the copyright ("…(ASCAP) CCLI# 6494911"), or wrapped
 * across lines ("CCLI Song No.\n7001228"). "CCLI Licence #" and a bare "CCLI
 * No." after "property of <church>" are the church's licence, not the song.
 */
export function scanCcli(text: string): { song: string | null; license: string | null } {
  const flat = text.replace(/\s+/g, " ");
  const song =
    /CCLI\s*Song\s*(?:#|No\.?|Number)?\s*:?\s*(\d{3,8})/i.exec(flat)?.[1] ??
    /CCLI\s*#\s*(\d{3,8})/i.exec(flat)?.[1] ??
    null;
  const license =
    /CCLI®?\s*Licen[cs]e\s*(?:#|No\.?|Number)?\s*:?\s*(\d{3,8})/i.exec(flat)?.[1] ??
    /property of [^.]+\.\s*CCLI\s*(?:#|No\.?)\s*(\d{3,8})/i.exec(flat)?.[1] ??
    null;
  return { song, license };
}

/** A copyright notice without the licensing boilerplate that follows it. */
function cleanCopyright(text: string): string {
  return text
    .replace(/\s*CCLI\s*(?:Song\s*)?(?:#|No\.?|Number)?\s*:?\s*\d*\.?/gi, " ")
    .replace(/\s*This chart is the property of.*$/i, "")
    .replace(/\s*(Unauthorized|For use solely|All rights reserved|Used by permission).*$/i, "")
    .replace(/\s{2,}/g, " ")
    .replace(/[\s.,;]+$/, "")
    .trim();
}

// --- Header block: title, writers, key/tempo/time lines -----------------------

const KEY_RE = /\bkey(?:\s+of|\s*[:=–-]|\s)\s*([A-G](?:#|b|♯|♭)?(?:m(?!aj)|min(?:or)?|maj(?:or)?)?)(?![a-z#♯♭])/i;
const TEMPO_RE = /(?:\btempo\s*[:=–-]?\s*(\d{2,3})\b|\b(\d{2,3})\s*bpm\b|\bbpm\s*[:=–-]?\s*(\d{2,3})\b|@\s*(\d{2,3})\b)/i;
/** A bare key among other facts: "C • 81 BPM • 4/4", "[G, 48 bpm, 6/8]". */
const BARE_KEY_RE = /(?:^|[\s,•·|[(])([A-G](?:#|b|♯|♭)?m?)(?=$|[\s,•·|\])])/;
const TIME_RE = /\b(\d{1,2}\/(?:2|4|8|16))\b/;

/**
 * A line of key/tempo/time-signature facts in any of the ways sheets write
 * them — "Key - G | Tempo - 63 | Time - 4/4", "Key: A, Tempo: 75, 4/4",
 * "Key of A   68 BPM   4/4 Time". Returns true (and records them) only if
 * the line is *nothing but* such facts, so a lyric that mentions a key never
 * gets eaten.
 */
export function parseFactsLine(line: string, meta: Meta): boolean {
  let key: RegExpExecArray | null = KEY_RE.exec(line);
  const tempo = TEMPO_RE.exec(line);
  const time = TIME_RE.exec(line);
  if (!key && !tempo && !time) return false;
  let rest = line;
  for (const m of [key, tempo, time]) if (m) rest = rest.replace(m[0], " ");
  // With a tempo or time signature present, a lone chord name is the key.
  if (!key && (tempo || time)) {
    const bare = BARE_KEY_RE.exec(rest);
    if (bare) {
      key = bare;
      rest = rest.replace(bare[1]!, " ");
    }
  }
  rest = rest
    .replace(/\b(key|tempo|time|signature|bpm|capo\s*\d*(\s*\([A-G][#b]?m?\))?|original|in)\b/gi, " ")
    .replace(/[|,:;=–\-/()[\]·•@\s]+/g, "");
  if (rest !== "") return false;
  if (key) {
    const k = key[1]!.replace(/♯/g, "#").replace(/♭/g, "b").replace(/min(or)?$/i, "m").replace(/maj(or)?$/i, "");
    if (parseChord(k)) meta.key ??= k[0]!.toUpperCase() + k.slice(1);
  }
  if (tempo) meta.tempo ??= Number(tempo[1] ?? tempo[2] ?? tempo[3] ?? tempo[4]);
  if (time) meta.time ??= time[1]!;
  return true;
}

const AUTHOR_PREFIX_RE = /^(?:words\s*(?:and|&)\s*music\s*by|music\s*(?:and|&)\s*words\s*by|words\s*by|music\s*by|lyrics\s*by|written\s*by|by)\s*:?\s+(.+)$/i;

/**
 * A roadmap line — "Intro, V1, V2, PC, Cx2, B", "Verse 1 > Verse 2 > Chorus
 * (2x)" — the order to play the sections in, not lyrics. Returns the labels
 * (repeats expanded) or null.
 */
export function roadmapLine(line: string): string[] | null {
  const t = line.trim().replace(/^(order|arrangement|roadmap|form|flow|structure)\s*:\s*/i, "");
  const parts = t.split(/\s*(?:,|>|→|–|\|)\s*|\s+-\s+/).map((p) => p.trim()).filter(Boolean);
  if (parts.length < 3) return null;
  const labels: string[] = [];
  let hits = 0;
  for (const part of parts) {
    const m = /^(.*?)\s*\(?\s*[x×]\s*(\d)\s*\)?$|^(.*?)\s*\(?(\d)\s*x\)?$/i.exec(part);
    const name = (m ? (m[1] ?? m[3]) : part)!.trim();
    const times = m ? Number(m[2] ?? m[4]) : 1;
    // In a roadmap "C" and "B" are sections, not chords: read them as "C:".
    const h =
      parseHeader(name, false) ??
      (/^[a-z]{1,3}\d*$/i.test(name) ? parseHeader(`${name}:`, false) : null) ??
      (/^(inst|inter|turn|tag|vamp|end|ending)\w*\s*\d*$/i.test(name) ? { label: name, rest: "" } : null);
    if (!h) continue;
    hits += 1;
    for (let i = 0; i < Math.min(times, 4); i++) labels.push(h.label);
  }
  return hits >= Math.max(3, Math.ceil(parts.length * 0.8)) ? labels : null;
}

/** "Words and Music by X", "By X", or a " | " separated list of names. */
function authorLine(line: string): string | null {
  const m = AUTHOR_PREFIX_RE.exec(line.trim());
  if (m) return tidyNames(m[1]!);
  if (/\s\|\s/.test(line) && !/\d/.test(line)) return tidyNames(line);
  return null;
}

function tidyNames(s: string): string {
  return s
    .trim()
    .replace(/\s*\|\s*/g, ", ")
    .replace(/\s*\/\s*/g, ", ")
    .replace(/\s{2,}/g, " ")
    // PDF kerning can split a word after its capital ("Ed C ash").
    .replace(/\b([A-Z]) ([a-z]{2,})\b/g, "$1$2")
    .replace(/[.,;]\s*$/, "");
}

/**
 * Page furniture that is never part of a song, wherever it appears: web
 * addresses, "Page 1 of 2", a browser's print footer ("… 1/2"), and the
 * Ultimate Guitar logo.
 */
function furniture(line: string): boolean {
  const t = line.trim();
  // Chord-diagram rows (string markers "o o x", finger dots), with or
  // without the chord names above them.
  const withoutChords = t.replace(INLINE_CHORD_RE, " ").trim();
  if (withoutChords !== "" && /^([oxX•·●○]\s*)+$/.test(withoutChords)) return true;
  return (
    /^(https?:\/\/|www\.)\S*(\s+\d+\/\d+)?$/i.test(t) ||
    /^page\s+\d+\s+of\s+\d+$/i.test(t) ||
    /^(ultimate|guitar|com)$/i.test(t)
  );
}

/**
 * Titles as web pages print them: a browser's header ("1/12/2019 X Chords
 * (ver 2) by Bethel Live tabs @ Ultimate Guitar Archive") or UG's own
 * heading ("X chords by Bethel Live"). Returns the title and artist.
 */
function webTitle(line: string): { title: string; artist: string | null } | null {
  let t = line.trim().replace(/^\d{1,2}\/\d{1,2}\/\d{2,4},?\s+(\d{1,2}:\d{2}\s*(am|pm)?\s+)?/i, "");
  const ug = /^(.+?)\s+(?:chords|tabs?|chord chart|lyrics)(?:\s*\(ver\s*\d+\))?\s+by\s+(.+?)(?:\s*tabs?)?(?:\s*@\s*ultimate guitar.*|\s+guitar)?$/i.exec(t);
  if (ug) return { title: ug[1]!.trim(), artist: ug[2]!.trim() };
  if (t !== line.trim()) {
    // A browser header without the UG shape: "<date> Title - Site".
    t = t.replace(/\s+[-|–]\s+[^-|–]+$/, "");
    return { title: t, artist: null };
  }
  return null;
}

/** Lines in a sheet's heading that are neither title nor lyrics. */
function headingNoise(line: string): boolean {
  return /^\((?:based on|as recorded|from the album|arr\.?|arranged)/i.test(line.trim()) ||
    /^(?:arr\.?|arranged by|translated by)\s/i.test(line.trim());
}

/** Words that mark a phrase as part of a title rather than someone's name. */
const TITLE_WORDS = new Set(
  "a an the my your our his her its their of to in on at for and is are be all no one god lord jesus christ king love name grace holy praise glory".split(" "),
);

/** "David Brymer", "Jeremy Riddle": two or three capitalised words, none a title word. */
function personName(s: string): boolean {
  const words = s.trim().split(/\s+/);
  return words.length >= 2 && words.length <= 3 &&
    words.every((w) => /^\p{Lu}[\p{Ll}'’.-]+$/u.test(w) && !TITLE_WORDS.has(w.toLowerCase()));
}

/**
 * A title line that carries other facts after a wide gap — "Holy Spirit
 * Key - G", "Majesty   By Stuart Garrard". Splits them off and records them;
 * returns what's left as the title.
 */
function splitTitleLine(line: string, meta: Meta): string {
  const segments = line.split(/\s{3,}|\t/).map((x) => x.trim()).filter(Boolean);
  let title = segments.shift() ?? "";
  for (const seg of segments) {
    if (parseFactsLine(seg, meta)) continue;
    const author = authorLine(seg);
    if (author) meta.artist ??= author;
  }
  // Facts glued to the end with a single space: "The Stand Key - A".
  const tail = /\s+(key(?:\s+of|\s*[:=–-])\s*\S*.*)$/i.exec(title);
  if (tail && parseFactsLine(tail[1]!, meta)) title = title.slice(0, tail.index);
  title = title.replace(/\s+key\s*[:=–-]?\s*$/i, ""); // "No Other Name  Key -" (key missing)
  // "Great Are You Lord [G, 48 bpm, 6/8]"
  const bracketFacts = /\s*[[(]([^\])]*)[\])]\s*$/.exec(title);
  if (bracketFacts && parseFactsLine(bracketFacts[1]!, meta)) title = title.slice(0, bracketFacts.index);
  // "…(Bryan and Katie Torwalt)72bpm"
  const bpm = /\s*(\d{2,3})\s*bpm\s*$/i.exec(title);
  if (bpm) {
    meta.tempo ??= Number(bpm[1]);
    title = title.slice(0, bpm.index);
  }
  // "Majesty (Here I Am)  By Stuart Garrard & Martin Smith"
  const by = /\s+by\s+([A-Z][^()]*?(?:&|\band\b|,)[^()]*)$/.exec(title);
  if (by) {
    meta.artist ??= tidyNames(by[1]!);
    title = title.slice(0, by.index);
  }
  // "Title - Artist (Band)" on one line.
  const dash = /^(.+?)\s+[-–]\s+([^-–]+)$/.exec(title.replace(/\s{2,}/g, " "));
  // Only when the right side is plainly a performer — "Amazing Grace - My
  // Chains Are Gone" is a title, not "Amazing Grace" by "My Chains Are Gone".
  const performer = /(&|\band\b|\bft\.?|\bfeat\.?|worship|music|band|live|church|united|collective|\()/i;
  if (dash && !/\d/.test(dash[2]!) && /^[A-Z]/.test(dash[2]!.trim()) && (performer.test(dash[2]!) || personName(dash[2]!))) {
    meta.artist ??= tidyNames(dash[2]!.replace(/\bft\.?\s+/i, "& "));
    title = dash[1]!;
  }
  // Curly quotes some publishers wrap titles in.
  return title.replace(/^[“"']+|[”"']+$/g, "").replace(/\s{2,}/g, " ").trim();
}

// --- Lyric tidying -------------------------------------------------------------

/** Interjections a real " - " separates ("Whoa - oh"), which must not be joined. */
const INTERJECTION = /^(oh+|o+|whoa|woah|yeah|hey|ah+|la|na|ha)\b/i;

/**
 * Clean a lyric line for display, keeping every chord on its syllable:
 * rejoin syllables split for chord placement ("sur - rendered" →
 * "surrendered") and close the wide gaps sheets leave between phrases.
 */
export function tidyLyricLine(line: ChartLine): ChartLine {
  let text = line.text;
  let chords = line.chords.map((c) => ({ ...c }));
  // Replace text[start, end) with `keep`, moving chords that sat inside it to
  // just after `keep` (the next letter) and shifting later ones back.
  const splice = (start: number, end: number, keep: string) => {
    const removed = end - start - keep.length;
    text = text.slice(0, start) + keep + text.slice(end);
    chords = chords.map((c) =>
      c.pos >= end ? { ...c, pos: c.pos - removed } : c.pos > start + keep.length ? { ...c, pos: start + keep.length } : c,
    );
  };
  const syllable = /(\p{L})( *- +| +- *)(\p{Ll}+)/gu;
  let m: RegExpExecArray | null;
  while ((m = syllable.exec(text))) {
    if (INTERJECTION.test(m[3]!)) continue;
    const start = m.index + m[1]!.length;
    splice(start, start + m[2]!.length, "");
    syllable.lastIndex = start;
  }
  const gaps = /(\S)( {2,})(?=\S)/g;
  while ((m = gaps.exec(text))) {
    const start = m.index + 1;
    splice(start, start + m[2]!.length, " ");
    gaps.lastIndex = start + 1;
  }
  const lead = text.length - text.trimStart().length;
  if (lead > 0) splice(0, lead, "");
  return { text: text.replace(/\s+$/, ""), chords };
}

// --- ChordPro directives -----------------------------------------------------

const DIRECTIVE_RE = /^\s*\{\s*([a-z_-]+)\s*(?:[:\s]\s*(.*?))?\s*\}\s*$/i;

const START_SECTION: Record<string, string> = {
  start_of_verse: "Verse", sov: "Verse",
  start_of_chorus: "Chorus", soc: "Chorus",
  start_of_bridge: "Bridge", sob: "Bridge",
  start_of_part: "", sop: "",
};
const END_SECTION = new Set(["end_of_verse", "eov", "end_of_chorus", "eoc", "end_of_bridge", "eob", "end_of_part", "eop"]);
const TAB_START = new Set(["start_of_tab", "sot", "start_of_abc", "start_of_ly", "start_of_svg", "start_of_textblock"]);
const TAB_END = new Set(["end_of_tab", "eot", "end_of_abc", "end_of_ly", "end_of_svg", "end_of_textblock"]);
const GRID_START = new Set(["start_of_grid", "sog"]);
const GRID_END = new Set(["end_of_grid", "eog"]);
const COMMENT_DIRECTIVES = new Set(["comment", "c", "comment_italic", "ci", "comment_box", "cb", "highlight"]);

// --- The reader --------------------------------------------------------------

interface SongBuilder {
  meta: Meta;
  sections: ChartSection[];
  current: ChartSection | null;
  stanza: ChartLine[];
  pendingChordLine: string | null;
  /** Labels in play order when the source repeats sections by reference ({chorus}). */
  order: string[];
  warnings: string[];
  sawDirective: boolean;
  sawInline: boolean;
  sawOverLyric: boolean;
  sawMetaLines: boolean;
  sawUgTags: boolean;
  sawSongSelectFooter: boolean;
  sawBody: boolean;
  /** A roadmap line's section order, if the sheet had one. */
  roadmap: string[] | null;
}

function newBuilder(): SongBuilder {
  return {
    meta: emptyMeta(), sections: [], current: null, stanza: [], pendingChordLine: null,
    order: [], warnings: [], sawDirective: false, sawInline: false,
    sawOverLyric: false, sawMetaLines: false, sawUgTags: false, sawSongSelectFooter: false,
    sawBody: false, roadmap: null,
  };
}

function flushPendingChordLine(b: SongBuilder) {
  if (b.pendingChordLine !== null) {
    const line = chordOnlyLine(b.pendingChordLine);
    if (line.chords.length > 0) pushLine(b, line);
    b.pendingChordLine = null;
  }
}

function ensureSection(b: SongBuilder): ChartSection {
  if (!b.current) {
    b.current = { label: "", stanzas: [] };
    b.sections.push(b.current);
    b.order.push("");
  }
  return b.current;
}

function pushLine(b: SongBuilder, line: ChartLine) {
  ensureSection(b);
  b.stanza.push(line.text.trim() ? tidyLyricLine(line) : line);
  b.sawBody = true;
}

function endStanza(b: SongBuilder) {
  flushPendingChordLine(b);
  if (b.stanza.length > 0 && b.current) {
    b.current.stanzas.push(b.stanza);
  }
  b.stanza = [];
}

function startSection(b: SongBuilder, label: string) {
  endStanza(b);
  // An empty unlabelled section left from a stray line isn't worth keeping.
  if (b.current && b.current.stanzas.length === 0 && b.current.label === "") {
    b.sections.pop();
    b.order.pop();
  }
  b.current = { label, stanzas: [] };
  b.sections.push(b.current);
  b.order.push(label);
}

/** `{chorus}` / "Repeat Chorus" — play an earlier section again. */
function referenceSection(b: SongBuilder, label: string) {
  endStanza(b);
  b.current = null;
  const target = [...b.sections].reverse().find((s) => s.label.toLowerCase() === label.toLowerCase())
    ?? [...b.sections].reverse().find((s) => s.label.toLowerCase().startsWith(label.toLowerCase()));
  if (target) {
    b.order.push(target.label);
  } else {
    b.warnings.push(`"${label}" is repeated but never written out`);
  }
}

function stanzaWords(stanza: ChartLine[]): string {
  return stanza.map((l) => l.text).join(" ").toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
}

/**
 * A sheet with no headings at all is one long "Verse". Name its stanzas when
 * the shape says what they are: a stanza sung word for word more than once is
 * the chorus, and stanzas all the same length (a hymn's verses) are verses
 * 1, 2, 3. Anything else stays one verse — guessing would be worse.
 */
function labelStanzas(sections: ChartSection[]): ChartSection[] {
  if (sections.length !== 1) return sections;
  const stanzas = sections[0]!.stanzas;
  if (stanzas.length < 2) return sections;
  const words = stanzas.map(stanzaWords);
  const repeated = new Set(words.filter((w, i) => w && words.indexOf(w) !== i));
  const hymn = stanzas.length >= 3 && stanzas.every((s) => s.length === stanzas[0]!.length && s.length >= 3);
  if (repeated.size === 0 && !hymn) return sections;
  const out: ChartSection[] = [];
  let verse = 0;
  stanzas.forEach((stanza, i) => {
    if (repeated.has(words[i]!)) {
      out.push({ label: "Chorus", stanzas: [stanza] });
      return;
    }
    // Without a chorus, consecutive non-chorus stanzas of a song that isn't a
    // hymn are one verse's slides, not separate verses.
    const prev = out[out.length - 1];
    if (!hymn && prev && prev.label !== "Chorus") prev.stanzas.push(stanza);
    else out.push({ label: `Verse ${++verse}`, stanzas: [stanza] });
  });
  return out;
}

const REPEAT_REF_RE =/^\s*\(?\s*(?:repeat|rpt\.?)\s+(.+?)\s*\)?\s*(?:x\s*\d+)?\s*$/i;

function finish(b: SongBuilder, fallbackTitle: string): ImportedChartSong | null {
  endStanza(b);
  const sections = b.sections.filter((s) => s.stanzas.length > 0);
  if (sections.length === 0) return null;

  // Unlabelled leading material: call it a verse if nothing else is labelled,
  // otherwise leave it for the song builder to number.
  const labelled = sections.some((s) => s.label);
  for (const s of sections) if (!s.label) s.label = labelled ? "Other" : "Verse";

  const chart: ChordChart = {
    version: CHART_VERSION,
    key: b.meta.key,
    tempo: b.meta.tempo,
    time: b.meta.time,
    sections: labelled ? sections : labelStanzas(sections),
  };
  if (b.meta.capo) b.warnings.push(`Chart is written for capo ${b.meta.capo}; chords are as written`);

  let format: ImportFormat;
  if (b.sawUgTags) format = "ultimate-guitar";
  else if (b.sawDirective) format = "chordpro";
  else if (b.sawSongSelectFooter && b.sawOverLyric) format = "songselect-text";
  else if (b.sawInline && b.sawMetaLines) format = "onsong";
  else if (b.sawInline) format = "chordpro";
  else if (b.sawOverLyric) format = "chords-over-lyrics";
  else format = "plain-lyrics";

  // The play order, when it isn't simply the sections in document order: a
  // `{chorus}` directive, "Repeat Chorus", or a bare "Chorus" header with
  // nothing under it all mean "play that one again".
  const written = new Set(sections.map((s) => s.label.toLowerCase()));
  const played = b.order.filter((l) => written.has(l.toLowerCase()));
  const docOrder = sections.map((s) => s.label);
  let order: string[] | null =
    played.length === docOrder.length && played.every((l, i) => l === docOrder[i]) ? null : played;
  // No repeats in the sheet itself, but a roadmap line ("V1, C, V2, C, B"):
  // play it that way, if its labels match the sections written out.
  if (!order && b.roadmap) {
    const sameTag = (a: string, c: string) => {
      const x = parseSectionTag(a);
      const y = parseSectionTag(c);
      return x.type === y.type && x.number === y.number && x.type !== "other";
    };
    const mapped = b.roadmap
      .map((l) => sections.find((s) => s.label.toLowerCase() === l.toLowerCase() || sameTag(s.label, l))?.label)
      .filter((l): l is string => l !== undefined);
    if (mapped.length >= b.roadmap.length * 0.6) order = mapped;
  }

  return {
    title: b.meta.title ?? fallbackTitle,
    artist: b.meta.artist,
    ccli: b.meta.ccli,
    copyright: b.meta.copyright,
    ccliLicense: b.meta.license,
    chart,
    order,
    format,
    warnings: b.warnings,
  };
}

/** Normalise tabs, UG markup and stray whitespace before line classification. */
function preprocess(text: string): { lines: string[]; ug: boolean } {
  let t = text.replace(/^\uFEFF/, "").replace(/\r\n?/g, "\n");
  const ug = /\[ch\]|\[tab\]/i.test(t);
  if (ug) t = t.replace(/\[\/?tab\]/gi, "").replace(/\[ch\]([^[]*?)\[\/ch\]/gi, "$1");
  // Tabs → spaces at 8-column stops, so chord columns line up with lyrics.
  const lines = t.split("\n").map((line) => {
    if (!line.includes("\t")) return line;
    let out = "";
    for (const ch of line) {
      if (ch === "\t") out += " ".repeat(8 - (out.length % 8));
      else out += ch;
    }
    return out;
  });
  return { lines, ug };
}

/**
 * Read a text chart. May return several songs — ChordPro allows
 * `{new_song}` separators, and people paste whole setlists.
 */
export function parseTextChart(
  text: string,
  fallbackTitle: string,
  opts: {
    /**
     * The text is a song body, not a file: no title/writer/metadata heading
     * to find, so the first short lines are lyrics. (The chord editor.)
     */
    bodyOnly?: boolean;
  } = {},
): ImportedChartSong[] {
  const { lines, ug } = preprocess(text);
  const bracketChordMode = lines.some((l) => hasInlineChords(l) && l.replace(INLINE_CHORD_RE, "").trim() !== "")
    || lines.some((l) => /^\s*\{.*\}\s*$/.test(l));

  const songs: ImportedChartSong[] = [];
  let b = newBuilder();
  b.sawUgTags = ug;
  let inTab = false;
  let inGrid = false;
  let headerZone = !opts.bodyOnly; // before any lyric: title/artist lines are metadata
  let untitledLines: string[] = [];
  // The short lines at the top were followed by a blank line — a title block,
  // not the first stanza.
  let titleBlockClosed = false;
  // After a copyright/CCLI line, everything up to the next section is the
  // sheet's footer (licensing text, "Words and music by", chord diagrams).
  let inFooter = false;

  const newSong = () => {
    const s = finish(b, fallbackTitle);
    if (s) songs.push(s);
    b = newBuilder();
    b.sawUgTags = ug;
    headerZone = !opts.bodyOnly;
    untitledLines = [];
    titleBlockClosed = false;
    inFooter = false;
  };

  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i]!;
    const line = raw.replace(/\s+$/, "");
    const trimmed = line.trim();

    // ChordPro directives.
    const d = DIRECTIVE_RE.exec(trimmed);
    if (d) {
      b.sawDirective = true;
      const name = d[1]!.toLowerCase();
      const value = d[2] ?? "";
      if (inTab && !TAB_END.has(name)) continue;
      if (name === "new_song" || name === "ns") { newSong(); continue; }
      if (TAB_START.has(name)) { inTab = true; continue; }
      if (TAB_END.has(name)) { inTab = false; continue; }
      if (GRID_START.has(name)) { inGrid = true; startSection(b, value || "Instrumental"); continue; }
      if (GRID_END.has(name)) { inGrid = false; endStanza(b); continue; }
      if (name in START_SECTION) {
        const base = START_SECTION[name]!;
        startSection(b, value ? titleCase(value.replace(/^label=/i, "").replace(/^"|"$/g, "")) : base || "Other");
        headerZone = false;
        continue;
      }
      if (END_SECTION.has(name)) { endStanza(b); continue; }
      if (name === "chorus") { referenceSection(b, value || "Chorus"); continue; }
      if (COMMENT_DIRECTIVES.has(name)) {
        const h = parseHeader(value, true);
        if (h) {
          startSection(b, h.label);
          if (h.rest && isChordLine(h.rest)) pushLine(b, chordOnlyLine(h.rest));
          headerZone = false;
        } else {
          const rep = REPEAT_REF_RE.exec(value);
          if (rep && parseHeader(rep[1]!, true)) referenceSection(b, parseHeader(rep[1]!, true)!.label);
        }
        continue;
      }
      if (name === "artist" || name === "composer" || name === "lyricist") { applyMeta(b.meta, "artist", value); continue; }
      applyMeta(b.meta, name, value);
      continue;
    }
    if (inTab) continue;
    if (trimmed.startsWith("#") && !/^#\d/.test(trimmed)) continue; // ChordPro comment

    if (trimmed === "") {
      if (!headerZone) endStanza(b);
      else if (untitledLines.length > 0) titleBlockClosed = true;
      continue;
    }

    if (inGrid) {
      pushLine(b, chordOnlyLine(trimmed));
      continue;
    }

    if (furniture(trimmed)) continue;
    // "www.praisecharts.com/28816   Key: D · Time: 4/4", "Music resources
    // from www.site.co.uk": a web address never belongs to the song.
    if (/(https?:\/\/|www\.)\S+/i.test(trimmed) && trimmed.split(/\s+/).length <= 12) {
      const rest = trimmed.replace(/(https?:\/\/|www\.)\S+/gi, " ").trim();
      if (rest) parseFactsLine(rest, b.meta);
      continue;
    }
    if (!headerZone && /(^|\s)(©|\(c\)\s*\d{4}|copyright\s+(©\s*)?\d{4}|CCLI\b)/i.test(trimmed) && !hasInlineChords(trimmed)) {
      inFooter = true;
      if (/CCLI|SongSelect/i.test(trimmed)) b.sawSongSelectFooter = true;
      const m = /(?:©|\(c\)|copyright\b)\s*(.+)$/i.exec(trimmed);
      if (m) b.meta.copyright ??= cleanCopyright(m[1]!) || null;
      endStanza(b);
      continue;
    }
    if (inFooter) {
      if (parseHeader(trimmed, bracketChordMode)) inFooter = false;
      else continue;
    }
    if (headerZone && !b.meta.title && untitledLines.length === 0) {
      const web = webTitle(trimmed);
      if (web) {
        b.meta.title = web.title;
        if (web.artist) b.meta.artist ??= web.artist;
        continue;
      }
    }
    // UG repeats the title under its logo: "X chords by Y".
    if (headerZone && b.meta.title) {
      const again = webTitle(trimmed);
      if (again && again.title.toLowerCase() === b.meta.title.toLowerCase()) continue;
    }

    // A roadmap of the arrangement. Kept aside: `finish` uses it as the play
    // order when the sheet doesn't repeat sections itself.
    const roadmap = roadmapLine(trimmed);
    if (roadmap) {
      b.roadmap ??= roadmap;
      continue;
    }
    if (/^(order|arrangement|roadmap)\s*:?\s*$/i.test(trimmed)) continue;

    // Key/tempo/time, however they're written — only when that's all the
    // line holds.
    if (parseFactsLine(trimmed, b.meta)) {
      b.sawMetaLines = true;
      continue;
    }

    // In the heading: writers, and notes like "(based on the recording by …)".
    if (headerZone) {
      if (headingNoise(trimmed)) continue;
      const author = untitledLines.length > 0 || b.meta.title ? authorLine(trimmed) : null;
      if (author) {
        b.meta.artist ??= author;
        continue;
      }
    }

    // Metadata lines (only before the song body starts, or unmistakable ones
    // like "Key: G" anywhere).
    const meta = META_LINE_RE.exec(trimmed);
    if (meta && (headerZone || /^(key|capo|tempo|bpm|ccli)/i.test(meta[1]!))) {
      if (applyMeta(b.meta, meta[1]!, meta[2]!)) {
        b.sawMetaLines = true;
        continue;
      }
    }
    if (footerLine(trimmed, b.meta)) {
      b.sawSongSelectFooter = true;
      continue;
    }

    // "Repeat Chorus", "(Chorus x2)" on its own — a reference, not new lyrics.
    const rep = REPEAT_REF_RE.exec(trimmed);
    if (rep) {
      const h = parseHeader(rep[1]!, bracketChordMode);
      if (h) { referenceSection(b, h.label); headerZone = false; continue; }
    }

    const header = parseHeader(trimmed, bracketChordMode);
    if (header) {
      // A header that ends up with nothing under it ("Chorus" again at the
      // end) is a repeat; `finish` reads it that way from the play order.
      startSection(b, header.label);
      if (header.rest && isChordLine(header.rest)) pushLine(b, chordOnlyLine(header.rest));
      headerZone = false;
      // Title lines seen before the first header were the title/artist.
      if (untitledLines.length > 0) {
        commitUntitled(b, untitledLines);
        untitledLines = [];
      }
      continue;
    }

    // Chord line over a lyric line.
    if (isChordLine(trimmed) && !(bracketChordMode && hasInlineChords(trimmed))) {
      flushPendingChordLine(b);
      const next = lines[i + 1];
      const nextTrim = next?.trim() ?? "";
      if (
        next !== undefined && nextTrim !== "" && !isChordLine(nextTrim) &&
        !parseHeader(nextTrim, bracketChordMode) && !DIRECTIVE_RE.test(nextTrim)
      ) {
        b.pendingChordLine = line;
      } else {
        pushLine(b, chordOnlyLine(line));
      }
      headerZone = false;
      commitUntitled(b, untitledLines);
      untitledLines = [];
      continue;
    }

    // A bracket-chord line that is only chords: `[G] [C] [D]` (an intro).
    if (hasInlineChords(trimmed) && trimmed.replace(INLINE_CHORD_RE, "").replace(/[\s|/.-]/g, "") === "") {
      flushPendingChordLine(b);
      const parsed = parseInlineChords(trimmed);
      pushLine(b, { text: "", chords: parsed.chords });
      b.sawInline = true;
      headerZone = false;
      commitUntitled(b, untitledLines);
      untitledLines = [];
      continue;
    }

    // Before any section or chord: a short line might be the title/artist.
    if (
      headerZone && !titleBlockClosed && !b.meta.title && !hasInlineChords(trimmed) &&
      trimmed.length <= 120 &&
      // Title and writers; a third credit line ("Traditional Scottish Folk
      // Tune") only when the heading carries on into a key/tempo line.
      (untitledLines.length < 2 ||
        (untitledLines.length < 3 && !b.sawMetaLines && nextIsFactsLine(lines, i)))
    ) {
      untitledLines.push(untitledLines.length === 0 ? splitTitleLine(line, b.meta) || trimmed : trimmed);
      continue;
    }
    if (untitledLines.length > 0) {
      if (titleBlockClosed) commitUntitled(b, untitledLines);
      // Otherwise those lines ran straight into more text: lyrics after all.
      else for (const l of untitledLines) pushLine(b, { text: l, chords: [] });
      untitledLines = [];
    }
    headerZone = false;

    // A lyric line — maybe under a pending chord line, maybe with inline chords.
    if (b.pendingChordLine !== null) {
      pushLine(b, mergeOver(b.pendingChordLine, line));
      b.pendingChordLine = null;
      b.sawOverLyric = true;
      continue;
    }
    if (hasInlineChords(trimmed)) b.sawInline = true;
    const parsed = parseInlineChords(line.trim());
    if (parsed.text.trim() === "" && parsed.chords.length === 0) continue;
    pushLine(b, parsed);
  }

  // A file that was nothing but one or two short lines: lyrics, not a title.
  for (const l of untitledLines) pushLine(b, { text: l, chords: [] });
  const last = finish(b, fallbackTitle);
  if (last) songs.push(last);
  // One song per file (nearly always): catch CCLI numbers the line rules
  // missed — mid-sentence or wrapped.
  if (songs.length === 1) {
    const found = scanCcli(lines.join("\n"));
    songs[0]!.ccli ??= found.song;
    songs[0]!.ccliLicense ??= found.license;
  }
  return songs;
}

/** The next non-blank line after `i` is a key/tempo/time line. */
function nextIsFactsLine(lines: string[], i: number): boolean {
  for (let j = i + 1; j < lines.length; j++) {
    const t = lines[j]!.trim();
    if (t === "") continue;
    return parseFactsLine(t, newBuilder().meta);
  }
  return false;
}

/** Title/artist lines seen at the top, now that the body has started. */
function commitUntitled(b: SongBuilder, lines: string[]) {
  if (lines.length === 0) return;
  b.meta.title ??= lines[0]!;
  if (lines[1] && !b.meta.artist) b.meta.artist = tidyNames(lines[1]);
}
