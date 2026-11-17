/**
 * Read a library search into filters and what's left to search for.
 *
 *   "Hymns in the key of E about the blood of Jesus"
 *     -> hymns, key E, topic "the blood of Jesus"
 *   "Good songs for easter that we haven't played in the last 3 services"
 *     -> occasion Easter, not in the last 3 services
 *   "fast songs by Chris Tomlin in 6/8"
 *     -> tempo fast, by "Chris Tomlin", time 6/8
 *   "songs that would musically fit with How Great Thou Art"
 *     -> fit with "How Great Thou Art" (key, tempo, time signature)
 *
 * Plain rules, no language model: on the spike's held-out phrasings a local
 * 1.5B model read 15/25 correctly at ~2 s a query and broke remembered lyric
 * lines; these rules read 16/25 in ~0 ms before the fixes below
 * (research/spikes/semantic_search). Each recognised part keeps its span in
 * the query, so the UI can show it as a chip and remove it from the text.
 */

import { parseChord } from "@/lib/chords/chord";
import { OCCASIONS, type Occasion } from "@/lib/smartSearch/occasions";

export interface SongKey {
  /** As written for the chip: "E", "Bb", "F#m". */
  name: string;
  /** Pitch class of the tonic, 0 = C. */
  pc: number;
  minor: boolean;
}

export interface PlayedFilter {
  /** "not_in": leave out songs played in the window; "in": only those. */
  mode: "not_in" | "in";
  /** The window as a count of services, or as days, or (neither) all history. */
  services?: number;
  days?: number;
  label: string;
}

export interface TempoFilter {
  /** Beats per minute, inclusive; either end open. */
  min?: number;
  max?: number;
  label: string;
}

export interface AddedFilter {
  /** Songs added to the library in the last this-many days. */
  days: number;
  label: string;
}

export interface FitTarget {
  /** The song's title as typed; search resolves it against the library. */
  title: string;
  /** "fit": rank by key, tempo and time signature close to it. "sameKey":
   *  only songs in its key. */
  how: "fit" | "sameKey";
}

export type PartKind = "hymn" | "key" | "played" | "occasion" | "author" | "added" | "tempo" | "time" | "fit";

export interface QueryPart {
  kind: PartKind;
  label: string;
  /** Where in the query it was read from: removing the chip removes this. */
  start: number;
  end: number;
}

export interface ParsedQuery {
  raw: string;
  hymn: boolean;
  key: SongKey | null;
  played: PlayedFilter | null;
  occasion: Occasion | null;
  /** "by Chris Tomlin": matched against each song's author and copyright. */
  author: string | null;
  added: AddedFilter | null;
  tempo: TempoFilter | null;
  /** A time signature as written in charts: "6/8", "3/4". */
  time: string | null;
  fit: FitTarget | null;
  /** What's left once filters and request words are gone ("the blood of
   *  Jesus"), for searching by meaning; null when nothing is. */
  topic: string | null;
  /** The topic's content words only, for the keyword search ("blood Jesus"). */
  keywords: string | null;
  parts: QueryPart[];
}

const NUMBERS: Record<string, number> = {
  one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10,
  twelve: 12, "a couple": 2, "a couple of": 2, "a few": 3, several: 4,
};

/** "Haven't played in a while": four services, about a month of Sundays. */
const A_WHILE = 4;

/** How a request starts or ends rather than what it's about: "good songs
 *  about …", "a song for …", "… hymns". Only at the edges, and the bare
 *  noun only in the plural, so a lyric keeps its words ("I will sing a new
 *  song", "How Great Thou Art"). */
const REQUEST_LEAD =
  /^(?:(?:show\s+me|find(?:\s+me)?|list|give\s+me|suggest|any|some|good|great|nice|other|worship|praise|more)\s+)*(?:songs|hymns|music|something|anything|ones|(?:a|one|another)\s+(?:song|hymn)|song\s+ideas|suggestions|ideas)\b/i;
const REQUEST_TRAIL = /\s+(?:songs|hymns|music|ones)$/i;
/** Joining words left at the edges once filters are cut out. */
const EDGE_WORDS =
  /^(?:(?:about|for|on|of|with|to|that|which|we|i|in|the|a|an|and|or|are|is|what|sunday|service)\b\s*)+|(?:\s*\b(?:about|for|on|of|with|to|that|which|we|i|in|the|a|an|and|or|sunday|service))+$/gi;
/** Not worth a keyword match: dropped from `keywords`, kept in `topic`. */
const STOP_WORDS = new Set(
  ("a an the of for on in to with about and or is are be by at from it its this that these those " +
    "we i you our my me us what which who songs song hymns hymn music sunday service").split(" "),
);

export interface ParseOptions {
  /** Read these as plain words instead: search's second try when a "by …"
   *  or "fits with …" names nothing in the library ("saved by grace"). */
  ignore?: PartKind[];
  now?: Date;
}

export function parseQuery(raw: string, options: ParseOptions = {}): ParsedQuery {
  const ignore = new Set(options.ignore ?? []);
  const now = options.now ?? new Date();
  const parts: QueryPart[] = [];
  // Matched spans are blanked here (same length, so indices stay true) so a
  // later rule can't read the same words again.
  let work = raw;
  const take = (kind: PartKind, label: string, start: number, end: number) => {
    parts.push({ kind, label, start, end });
    work = work.slice(0, start) + " ".repeat(end - start) + work.slice(end);
  };

  // Clauses that carry a title or a name go first: their words ("in",
  // "by", "fast") belong to the name, not to another rule.
  const fit = ignore.has("fit") ? null : readFit(work);
  if (fit) take("fit", fit.target.how === "fit" ? `Fits with ${fit.target.title}` : `Same key as ${fit.target.title}`, fit.start, fit.end);

  const author = ignore.has("author") ? null : readAuthor(work);
  if (author) take("author", `By ${author.name}`, author.start, author.end);

  const key = readKey(work);
  if (key) take("key", `Key ${key.key.name}`, key.start, key.end);

  const added = readAdded(work, now);
  if (added) take("added", added.filter.label, added.start, added.end);

  const played = readPlayed(work, now);
  if (played) take("played", played.filter.label, played.start, played.end);

  const tempo = readTempo(work);
  if (tempo) take("tempo", tempo.filter.label, tempo.start, tempo.end);

  const time = readTime(work);
  if (time) take("time", time.time, time.start, time.end);

  let occasion: Occasion | null = null;
  for (const o of OCCASIONS) {
    const m = o.pattern.exec(work);
    if (!m) continue;
    occasion = o;
    take("occasion", o.label, m.index, m.index + m[0].length);
    // "christmas carols": both words name it; only the first is the chip.
    for (const more of work.matchAll(new RegExp(o.pattern.source, "gi"))) {
      work = work.slice(0, more.index) + " ".repeat(more[0].length) + work.slice(more.index + more[0].length);
    }
    break;
  }

  const hymnMatch = /\bhymns?\b/i.exec(work);
  if (hymnMatch) take("hymn", "Hymns", hymnMatch.index, hymnMatch.index + hymnMatch[0].length);

  const topic = topicOf(work, parts.length > 0);
  const keywords = topic
    ? topic
        .toLowerCase()
        .split(/[^\p{L}\p{N}']+/u)
        .filter((w) => w && !STOP_WORDS.has(w))
        .join(" ") || null
    : null;

  parts.sort((a, b) => a.start - b.start);
  return {
    raw,
    hymn: hymnMatch !== null,
    key: key?.key ?? null,
    played: played?.filter ?? null,
    occasion,
    author: author?.name ?? null,
    added: added?.filter ?? null,
    tempo: tempo?.filter ?? null,
    time: time?.time ?? null,
    fit: fit?.target ?? null,
    topic,
    keywords,
    parts,
  };
}

/** The query with one recognised part cut out — what removing its chip does. */
export function withoutPart(raw: string, part: QueryPart): string {
  return (raw.slice(0, part.start) + " " + raw.slice(part.end)).replace(/\s+/g, " ").trim();
}

/** Did the query ask for anything beyond plain words? */
export function hasFilters(q: ParsedQuery): boolean {
  return (
    q.hymn ||
    q.key !== null ||
    q.played !== null ||
    q.occasion !== null ||
    q.author !== null ||
    q.added !== null ||
    q.tempo !== null ||
    q.time !== null ||
    q.fit !== null
  );
}

// --- key ------------------------------------------------------------------

const ACCIDENTAL = String.raw`(?:\s*(?:#|♯|♭|sharp|flat)|b(?![a-z]))`;
const MODE = String.raw`(?:\s*(?:minor|min|major|maj|m)(?![a-z]))`;
// After "key of", any case ("key of e"); after a bare "in", only a capital
// letter, so "in a while" and "in Christ" are left alone.
const KEY_OF = new RegExp(String.raw`\b(?:in\s+)?(?:the\s+)?key\s+of\s+([A-Ga-g])(${ACCIDENTAL})?(${MODE})?(?=$|[\s,.?!;:])`, "i");
const IN_KEY = new RegExp(String.raw`\bin\s+([A-G])(${ACCIDENTAL})?(${MODE})?(?=$|[\s,.?!;:])`);

function readKey(text: string): { key: SongKey; start: number; end: number } | null {
  const m = KEY_OF.exec(text) ?? IN_KEY.exec(text);
  if (!m) return null;
  const letter = m[1]!.toUpperCase();
  const accRaw = (m[2] ?? "").trim().toLowerCase();
  const acc = accRaw === "#" || accRaw === "♯" || accRaw === "sharp" ? "#" : accRaw ? "b" : "";
  const modeRaw = (m[3] ?? "").trim().toLowerCase();
  const minor = modeRaw === "m" || modeRaw === "min" || modeRaw === "minor";
  const name = `${letter}${acc}${minor ? "m" : ""}`;
  const chord = parseChord(name);
  if (!chord) return null;
  return { key: { name, pc: chord.root, minor }, start: m.index, end: m.index + m[0].length };
}

/** Is a song's written key the one asked for? Enharmonics match (A# = Bb). */
export function keyMatches(want: SongKey, songKey: string | null | undefined): boolean {
  if (!songKey) return false;
  const k = parseChord(songKey);
  if (!k) return false;
  const minor = /^(m(?!aj)|min|-)/.test(k.suffix);
  return k.root === want.pc && minor === want.minor;
}

// --- played ---------------------------------------------------------------

const NEG = String.raw`(?:haven'?t|havent|have\s+not|hasn'?t|not|didn'?t|did\s+not|never|weren'?t|wasn'?t)`;
const VERB = String.raw`(?:play(?:ed|ing)?|sung|sang|sing|done|do|did|use[ds]?|used)`;
const COUNT = String.raw`(\d+|one|two|three|four|five|six|seven|eight|nine|ten|twelve|a\s+couple(?:\s+of)?|a\s+few|several)`;
const UNIT = String.raw`(services?|sundays?|weeks?|months?)`;

const PERIODS: { re: RegExp; read: (m: RegExpExecArray, now: Date) => Omit<PlayedFilter, "mode" | "label"> & { what: string } }[] = [
  {
    re: new RegExp(String.raw`(?:in|for|over)?\s*(?:the\s+)?(?:last|past)?\s*${COUNT}\s+${UNIT}`, "i"),
    read: (m) => {
      const n = /^\d+$/.test(m[1]!) ? Number(m[1]) : NUMBERS[m[1]!.toLowerCase().replace(/\s+/g, " ")] ?? A_WHILE;
      const unit = m[2]!.toLowerCase();
      if (unit.startsWith("week")) return { days: n * 7, what: `${n} ${n === 1 ? "week" : "weeks"}` };
      if (unit.startsWith("month")) return { days: n * 30, what: `${n} ${n === 1 ? "month" : "months"}` };
      return { services: n, what: `${n} ${n === 1 ? "service" : "services"}` };
    },
  },
  {
    re: /\b(?:last|this\s+past)\s+(sunday|service|week|time|month)\b/i,
    read: (m) => {
      const u = m[1]!.toLowerCase();
      if (u === "week") return { days: 7, what: "last week" };
      if (u === "month") return { days: 30, what: "last month" };
      return { services: 1, what: "last service" };
    },
  },
  {
    re: /\b(?:in|for)\s+(?:over\s+|more\s+than\s+)?a\s+month\b/i,
    read: () => ({ days: 30, what: "a month" }),
  },
  {
    re: /\bthis\s+year\b/i,
    read: (_m, now) => ({
      days: Math.max(1, Math.ceil((now.getTime() - new Date(now.getFullYear(), 0, 1).getTime()) / 86_400_000)),
      what: "this year",
    }),
  },
  {
    re: /\b(?:in\s+a\s+(?:while|bit)|for\s+a\s+while|in\s+ages|for\s+ages|recently|lately)\b/i,
    read: () => ({ services: A_WHILE, what: "lately" }),
  },
];

/** Leading words that belong to the clause: "that we haven't ...". */
const CLAUSE_LEAD = new RegExp(String.raw`(?:\b(?:that|which|what|we|i|you|they|have|has|been|we've|we'?ve|${NEG.slice(3, -1)})\s+)*$`, "i");

function readPlayed(
  text: string,
  now: Date,
): { filter: PlayedFilter; start: number; end: number } | null {
  const verb = new RegExp(String.raw`\b${VERB}\b`, "i").exec(text);
  if (!verb) return null;
  const before = text.slice(0, verb.index);
  const lead = CLAUSE_LEAD.exec(before);
  const start = lead ? lead.index : verb.index;
  const after = text.slice(verb.index + verb[0].length);
  const negative =
    new RegExp(String.raw`\b${NEG}\b`, "i").test(text.slice(start, verb.index)) ||
    /^\s+(?:it|them)?\s*(?:not|never)\b/i.test(after);

  let best: { m: RegExpExecArray; read: (typeof PERIODS)[number]["read"] } | null = null;
  for (const p of PERIODS) {
    const m = p.re.exec(after);
    // The period has to follow the verb closely: "sing ... in G" is no period.
    if (m && m.index <= 24 && (!best || m.index < best.m.index)) best = { m, read: p.read };
  }
  if (!best && !negative) return null; // "songs to sing at a wedding": no filter
  const verbEnd = verb.index + verb[0].length;
  // "haven't played yet / ever / before": the adverb belongs to the clause.
  const adverb = /^\s+(?:it\s+|them\s+)?(?:yet|ever|before|at\s+all)\b/i.exec(after);
  const end = best ? verbEnd + best.m.index + best.m[0].length : verbEnd + (adverb?.[0].length ?? 0);
  if (!best) {
    return { filter: { mode: "not_in", label: "Never played" }, start, end };
  }
  const { what, ...window } = best.read(best.m, now);
  const mode = negative ? "not_in" : "in";
  const label = mode === "not_in" ? `Not played: ${what}` : what === "last service" ? "Played last service" : `Played: ${what}`;
  return { filter: { mode, label, ...window }, start, end };
}

// --- a song to fit with, an author -----------------------------------------

/** Where a title or a name ends: the next clause of the query. */
const CLAUSE_END = String.raw`(?=\s+(?:about|that|which|we|i|for|with|and\s+(?:is|are|that)|in\s+(?:the\s+key|[A-G](?:#|b)?m?\b|\d+\/\d+)|at\s+\d|around\s+\d)\b|\s*[?.!,;]|\s*$)`;
const FIT_RE = new RegExp(
  String.raw`(?:\b(?:that|which)\s+)?(?:\b(?:would|will|might|could|can)\s+)?\b(?:musically\s+)?(?:fits?|go(?:es)?|flows?|pairs?|works?|sits?|blends?)\s+(?:(?:well|nicely|musically|best)\s+)*(?:with|after|before|into|next\s+to|alongside)\s+(.+?)${CLAUSE_END}`,
  "i",
);
const SIMILAR_RE = new RegExp(
  String.raw`\b(?:(?:musically\s+)?similar\s+to|(?<=\b(?:songs?|hymns?|something|anything|ones?)\s+)like)\s+(.+?)${CLAUSE_END}`,
  "i",
);
const SAME_KEY_RE = new RegExp(String.raw`\b(?:in\s+)?(?:the\s+)?same\s+key\s+as\s+(.+?)${CLAUSE_END}`, "i");

function cleanName(text: string): string {
  return text.replace(/^["“'‘]+|["”'’]+$/g, "").replace(/\s+/g, " ").trim();
}

function readFit(text: string): { target: FitTarget; start: number; end: number } | null {
  for (const [re, how] of [[SAME_KEY_RE, "sameKey"], [FIT_RE, "fit"], [SIMILAR_RE, "fit"]] as const) {
    const m = re.exec(text);
    if (!m) continue;
    const title = cleanName(m[1]!);
    if (title.length < 2) continue;
    return { target: { title, how }, start: m.index, end: m.index + m[0].length };
  }
  return null;
}

const AUTHOR_RE = new RegExp(
  String.raw`\b(?:written\s+by|by|from)\s+(?!the\s+(?:last|past)\b)(.+?)${CLAUSE_END}`,
  "i",
);

function readAuthor(text: string): { name: string; start: number; end: number } | null {
  const m = AUTHOR_RE.exec(text);
  if (!m) return null;
  const name = cleanName(m[1]!);
  if (name.length < 2) return null;
  return { name, start: m.index, end: m.index + m[0].length };
}

/** Does a song's author or copyright line name this person or publisher?
 *  Every word of the name must start a word there: "tomlin", "chris tomlin",
 *  "hillsong" (in "Hillsong Music Publishing"). */
export function creditsMatch(name: string, credits: string): boolean {
  const words = (t: string) => t.toLowerCase().split(/[^\p{L}\p{N}]+/u).filter(Boolean);
  const have = words(credits);
  const want = words(name).filter((w) => !["the", "and", "of"].includes(w));
  return want.length > 0 && want.every((w) => have.some((h) => h.startsWith(w)));
}

// --- added ---------------------------------------------------------------

/** "New songs": added in the last three months. */
const NEW_DAYS = 90;

function readAdded(text: string, now: Date): { filter: AddedFilter; start: number; end: number } | null {
  const n = (w: string) => (/^\d+$/.test(w) ? Number(w) : NUMBERS[w.toLowerCase().replace(/\s+/g, " ")] ?? 1);
  const unitDays = (u: string) => (u.startsWith("week") ? 7 : u.startsWith("month") ? 30 : u.startsWith("year") ? 365 : 1);
  const label = (days: number, what: string) => ({ days, label: `Added: ${what}` });
  let m = new RegExp(String.raw`\b(?:(?:that|which)\s+)?(?:we\s+)?(?:have\s+|'ve\s+)?(?:been\s+)?added\s+(?:in\s+)?(?:the\s+)?(?:last|past)\s+${COUNT}\s+(days?|weeks?|months?|years?)\b`, "i").exec(text);
  if (m) {
    const k = n(m[1]!);
    const u = m[2]!.toLowerCase();
    return { filter: label(k * unitDays(u), `last ${k} ${u}`), start: m.index, end: m.index + m[0].length };
  }
  m = /\b(?:(?:that|which)\s+)?(?:we\s+)?(?:have\s+|'ve\s+)?(?:been\s+)?added\s+(?:in\s+)?(?:the\s+)?(this|last|past)\s+(week|month|year)\b/i.exec(text);
  if (m) {
    const which = m[1]!.toLowerCase() === "this" ? "this" : "last";
    const unit = m[2]!.toLowerCase();
    const days =
      which === "this" && unit === "year"
        ? Math.max(1, Math.ceil((now.getTime() - new Date(now.getFullYear(), 0, 1).getTime()) / 86_400_000))
        : which === "this" && unit === "month"
          ? Math.max(1, now.getDate())
          : unitDays(unit);
    return { filter: label(days, `${which} ${unit}`), start: m.index, end: m.index + m[0].length };
  }
  m = /\b(?:(?:recently|newly|just)\s+added|added\s+(?:recently|lately)|new(?:est|er)?|latest)\b(?:\s+(?=(?:songs|hymns|ones|music|additions)\b))?/i.exec(text);
  // "new" alone only before plural songs/hymns: "a new song" is a lyric.
  if (m && (/added/i.test(m[0]) || /\s$/.test(m[0]))) {
    return { filter: label(NEW_DAYS, "last 3 months"), start: m.index, end: m.index + m[0].trimEnd().length };
  }
  return null;
}

// --- tempo and time signature ---------------------------------------------

/** Worship tempos: most songs sit 65-130 bpm. */
export const SLOW_MAX = 80;
export const FAST_MIN = 110;
const BPM_SLACK = 6;

function readTempo(text: string): { filter: TempoFilter; start: number; end: number } | null {
  const span = (m: RegExpExecArray, filter: TempoFilter) => ({ filter, start: m.index, end: m.index + m[0].length });
  let m = /\bbetween\s+(\d{2,3})\s+(?:and|-|to)\s+(\d{2,3})\s*(?:bpm|beats\s+per\s+minute)\b/i.exec(text);
  if (m) {
    const [a, b] = [Number(m[1]), Number(m[2])].sort((x, y) => x - y);
    return span(m, { min: a, max: b, label: `${a}–${b} bpm` });
  }
  m = /\b(under|below|less\s+than|slower\s+than|over|above|more\s+than|faster\s+than)\s+(\d{2,3})\s*(?:bpm|beats\s+per\s+minute)?\b/i.exec(text);
  if (m && (/bpm|beats/i.test(m[0]) || /slower|faster/i.test(m[1]!))) {
    const v = Number(m[2]);
    const below = /under|below|less|slower/i.test(m[1]!);
    return span(m, below ? { max: v, label: `Under ${v} bpm` } : { min: v, label: `Over ${v} bpm` });
  }
  m = /\b(?:at\s+|around\s+|about\s+|~\s*)?(\d{2,3})\s*(?:bpm|beats\s+per\s+minute)\b/i.exec(text);
  if (m) {
    const v = Number(m[1]);
    return span(m, { min: v - BPM_SLACK, max: v + BPM_SLACK, label: `≈${v} bpm` });
  }
  m = /\b(?:mid[-\s]?tempo|medium[-\s]tempo|moderate(?:\s+tempo)?|medium)\b/i.exec(text);
  if (m) return span(m, { min: SLOW_MAX + 1, max: FAST_MIN - 1, label: `Mid-tempo (${SLOW_MAX + 1}–${FAST_MIN - 1} bpm)` });
  m = /\b(?:fast(?:er)?|up-?\s?tempo|upbeat|lively|energetic|high[-\s]energy)\b/i.exec(text);
  if (m) return span(m, { min: FAST_MIN, label: `Fast (${FAST_MIN}+ bpm)` });
  m = /\b(?:slow(?:er)?|ballads?|down-?\s?tempo)\b/i.exec(text);
  if (m) return span(m, { max: SLOW_MAX, label: `Slow (up to ${SLOW_MAX} bpm)` });
  return null;
}

/** Does a song's tempo fall in the window? Songs without one never do. */
export function tempoMatches(want: TempoFilter, tempo: number | null | undefined): boolean {
  if (tempo === null || tempo === undefined || !Number.isFinite(tempo)) return false;
  return (want.min === undefined || tempo >= want.min) && (want.max === undefined || tempo <= want.max);
}

const TIME_RE = /\b(?:in\s+)?(2|3|4|5|6|7|9|12)\s*\/\s*(2|4|8)\b(?:\s+(?:time|feel|signature|meter|metre))?/i;
const WALTZ_RE = /\b(?:in\s+)?(?:waltz(?:es)?|waltz\s+time)\b/i;

function readTime(text: string): { time: string; start: number; end: number } | null {
  const m = TIME_RE.exec(text);
  if (m) return { time: `${m[1]}/${m[2]}`, start: m.index, end: m.index + m[0].length };
  const w = WALTZ_RE.exec(text);
  if (w) return { time: "3/4", start: w.index, end: w.index + w[0].length };
  return null;
}

/** A chart's time signature written the usual way: "C" is 4/4, "¢" 2/2. */
export function normalizeTime(time: string | null | undefined): string | null {
  if (!time) return null;
  const t = time.trim().toLowerCase().replace(/\s+/g, "");
  if (t === "c" || t === "common") return "4/4";
  if (t === "¢" || t === "cut" || t === "c|") return "2/2";
  return /^\d{1,2}\/\d{1,2}$/.test(t) ? t : null;
}

// --- topic ----------------------------------------------------------------

function topicOf(work: string, partsCut: boolean): string | null {
  const clean = (text: string) => text.replace(/[^\p{L}\p{N}'’ -]+/gu, " ").replace(/\s+/g, " ").trim();
  const untouched = clean(work);
  let t = clean(untouched.replace(REQUEST_LEAD, " ").replace(REQUEST_TRAIL, " "));
  // Joining words are only left dangling where something was cut out ("about
  // the" after "songs"); a plain title keeps all its words ("In Christ Alone").
  const cutSomething = partsCut || t !== untouched;
  for (let i = 0; cutSomething && i < 4; i++) {
    const next = clean(t.replace(EDGE_WORDS, "").replace(REQUEST_LEAD, " ").replace(REQUEST_TRAIL, " "));
    if (next === t) break;
    t = next;
  }
  return t.length > 0 ? t : null;
}
