/**
 * Body-text → slides parser. Mirror of `sidecar/auto_presenter/library/parser.py`.
 *
 * Used for **live editor preview only**. Python is the canonical source of
 * truth; on save the body text goes to SQLite and the AI engine reads slides
 * via the Python parser. So the TS parser:
 *
 * - must produce the same `section`, `lyrics`, and `words` as Python (parity
 *   test enforces this)
 * - does NOT generate stable SHA1-based slide IDs — those need real crypto
 *   sync, which jsdom + browsers don't have. We emit `preview_<n>` IDs.
 *
 * See `docs/SONG_FORMAT.md` for the format spec.
 */

export type SectionType =
  | "verse"
  | "chorus"
  | "bridge"
  | "tag"
  | "intro"
  | "outro"
  | "pre-chorus"
  | "instrumental"
  | "other";

export interface ParsedSection {
  type: SectionType;
  number: number;
}

export interface ParsedSlide {
  id: string;
  section: ParsedSection;
  order_in_song: number;
  order_in_section: number;
  lyrics: string;
  words: string[];
}

export interface ParseResult {
  slides: ParsedSlide[];
  section_order: ParsedSection[];
}

const SECTION_RE = /^\s*\[([^\]]+)\]\s*$/;

const SMART_QUOTE_MAP: Record<string, string> = {
  "‘": "'", // left single
  "’": "'", // right single
  "“": '"', // left double
  "”": '"', // right double
  "–": "-", // en dash
  "—": "-", // em dash
};

const TAG_ALIASES: Record<string, SectionType> = {
  v: "verse",
  verse: "verse",
  c: "chorus",
  chorus: "chorus",
  b: "bridge",
  bridge: "bridge",
  p: "pre-chorus",
  prechorus: "pre-chorus",
  "pre-chorus": "pre-chorus",
  t: "tag",
  tag: "tag",
  i: "intro",
  intro: "intro",
  o: "outro",
  outro: "outro",
  ending: "outro",
  instrumental: "instrumental",
  other: "other",
};

const KNOWN_TYPES = new Set<string>([
  "verse",
  "chorus",
  "bridge",
  "tag",
  "intro",
  "outro",
  "pre-chorus",
  "instrumental",
  "other",
]);

function normalizeSmartQuotes(s: string): string {
  let out = "";
  for (const ch of s) {
    out += SMART_QUOTE_MAP[ch] ?? ch;
  }
  return out;
}

export function parseSectionTag(raw: string): ParsedSection {
  const text = raw.trim().toLowerCase();
  const m = /^(.*?)\s*(\d+)?\s*$/.exec(text);
  const body = (m?.[1] ?? text).trim();
  const number = Math.max(1, m?.[2] ? parseInt(m[2], 10) : 1);

  const key = body.replace(/\s+/g, "");
  const keyNoHyphen = key.replace(/-/g, "");

  let type: SectionType = "other";
  if (key in TAG_ALIASES) {
    type = TAG_ALIASES[key]!;
  } else if (keyNoHyphen in TAG_ALIASES) {
    type = TAG_ALIASES[keyNoHyphen]!;
  } else if (KNOWN_TYPES.has(key)) {
    type = key as SectionType;
  }

  return { type, number };
}

const PUNCT_NO_QUOTE = ".,!?;:()[]{}…";

export function tokenize(lyrics: string): string[] {
  const text = normalizeSmartQuotes(lyrics).toLowerCase();
  const out: string[] = [];
  for (const raw of text.split(/\s+/)) {
    if (!raw) continue;
    let word = raw;
    // Strip outer punctuation (excluding apostrophes inside the word).
    while (word.length && PUNCT_NO_QUOTE.includes(word[0]!)) word = word.slice(1);
    while (word.length && PUNCT_NO_QUOTE.includes(word[word.length - 1]!))
      word = word.slice(0, -1);
    while (word.length && (word[0] === '"' || word[word.length - 1] === '"')) {
      if (word[0] === '"') word = word.slice(1);
      if (word.length && word[word.length - 1] === '"') word = word.slice(0, -1);
    }
    while (word.length && (word[0] === "'" || word[word.length - 1] === "'")) {
      if (word[0] === "'") word = word.slice(1);
      if (word.length && word[word.length - 1] === "'") word = word.slice(0, -1);
    }
    if (word) out.push(word);
  }
  return out;
}

export function parseSong(body: string): ParseResult {
  const normalized = normalizeSmartQuotes(body)
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n");

  let currentSection: ParsedSection = { type: "other", number: 1 };
  const slidesRaw: { section: ParsedSection; lyrics: string }[] = [];
  const sectionOrder: ParsedSection[] = [];
  let buffer: string[] = [];
  let sectionSeen = false;

  const flush = () => {
    if (buffer.length === 0) return;
    const text = buffer.map((l) => l.replace(/\s+$/, "")).join("\n").trim();
    if (text) {
      slidesRaw.push({ section: currentSection, lyrics: text });
    }
    buffer = [];
  };

  for (const line of normalized.split("\n")) {
    const m = SECTION_RE.exec(line);
    if (m) {
      flush();
      currentSection = parseSectionTag(m[1]!);
      const last = sectionOrder[sectionOrder.length - 1];
      if (
        !last ||
        last.type !== currentSection.type ||
        last.number !== currentSection.number
      ) {
        sectionOrder.push(currentSection);
      }
      sectionSeen = true;
      continue;
    }
    if (line.trim() === "") {
      flush();
      continue;
    }
    if (!sectionSeen && sectionOrder.length === 0) {
      sectionOrder.push(currentSection);
      sectionSeen = true;
    }
    buffer.push(line);
  }
  flush();

  const slides: ParsedSlide[] = [];
  const sectionCounts = new Map<string, number>();
  for (let i = 0; i < slidesRaw.length; i++) {
    const { section, lyrics } = slidesRaw[i]!;
    const key = `${section.type}:${section.number}`;
    const order_in_section = sectionCounts.get(key) ?? 0;
    sectionCounts.set(key, order_in_section + 1);
    slides.push({
      id: `preview_${i}`,
      section,
      order_in_song: i,
      order_in_section,
      lyrics,
      words: tokenize(lyrics),
    });
  }

  return { slides, section_order: sectionOrder };
}

/** Human-readable section label (e.g. "verse 1", "chorus", "pre chorus"). */
export function sectionLabel(s: ParsedSection): string {
  const base = s.type.replace("-", " ");
  if (s.type === "verse" || s.number > 1) return `${base} ${s.number}`;
  return base;
}

/** Semantic colour classes for section headers so choruses/bridges stand out at a glance. */
export function sectionLabelClass(type: SectionType): string {
  switch (type) {
    case "chorus":
      return "text-amber-400 font-semibold";
    case "bridge":
      return "text-violet-400 font-semibold";
    case "pre-chorus":
      return "text-sky-400 font-medium";
    case "tag":
    case "outro":
      return "text-emerald-400 font-medium";
    case "intro":
    case "instrumental":
      return "text-rose-400 font-medium";
    default:
      return "text-muted-foreground font-medium";
  }
}

