/**
 * XML song formats: OpenLyrics (OpenLP, Worship Extreme, …) and OpenSong.
 */

import { parseChord } from "@/lib/chords/chord";
import { CHART_VERSION, type ChartLine, type ChartSection } from "@/lib/chords/chart";
import type { ImportedChartSong } from "@/lib/chords/import/types";

function parseXml(text: string): Document | null {
  try {
    const doc = new DOMParser().parseFromString(text.replace(/^\uFEFF/, ""), "application/xml");
    if (doc.getElementsByTagName("parsererror").length > 0) return null;
    return doc;
  } catch {
    return null;
  }
}

/** First descendant with this local name (namespace-agnostic). */
function first(el: Element | Document, name: string): Element | null {
  return el.getElementsByTagNameNS("*", name)[0] ?? el.getElementsByTagName(name)[0] ?? null;
}

function all(el: Element | Document, name: string): Element[] {
  const ns = Array.from(el.getElementsByTagNameNS("*", name));
  return ns.length > 0 ? ns : Array.from(el.getElementsByTagName(name));
}

function textOf(el: Element | null): string | null {
  const t = el?.textContent?.trim();
  return t ? t : null;
}

function validKey(k: string | null): string | null {
  if (!k) return null;
  const token = k.trim().split(/\s+/)[0]!;
  return parseChord(token) ? token : null;
}

// --- OpenLyrics --------------------------------------------------------------

const OPENLYRICS_NAMES: Record<string, string> = {
  v: "Verse", c: "Chorus", b: "Bridge", p: "Pre-Chorus", e: "Ending", i: "Intro", o: "Other",
};

/** OpenLyrics verse name ("v1", "c", "c2a", "b") → a label. */
function openLyricsLabel(name: string): string {
  const m = /^([a-z]+)(\d*)([a-z]?)$/i.exec(name.trim());
  if (!m) return name;
  const base = OPENLYRICS_NAMES[m[1]!.toLowerCase()] ?? m[1]!;
  return m[2] ? `${base} ${m[2]}` : base;
}

/**
 * The name of an OpenLyrics chord element. 0.8 writes `name="G7"`; 0.9 writes
 * `root`/`structure`/`bass`, where structure is a code we can only partly map.
 */
function openLyricsChord(el: Element): string | null {
  const name = el.getAttribute("name");
  if (name) return name;
  const root = el.getAttribute("root");
  if (!root) return null;
  const structure = el.getAttribute("structure") ?? "";
  const quality =
    /^min/.test(structure) ? "m" :
    /^dim/.test(structure) ? "dim" :
    /^aug/.test(structure) ? "aug" : "";
  const seventh = /7/.test(structure) ? (/maj7/.test(structure) ? "maj7" : "7") : "";
  const bass = el.getAttribute("bass");
  return `${root}${quality}${seventh}${bass ? `/${bass}` : ""}`;
}

/** Walk a `<lines>` element into chart lines, splitting on `<br/>`. */
function openLyricsLines(linesEl: Element): ChartLine[] {
  const out: ChartLine[] = [];
  let cur: ChartLine = { text: "", chords: [] };
  const walk = (node: Node) => {
    for (const child of Array.from(node.childNodes)) {
      if (child.nodeType === 3 /* text */) {
        cur.text += (child.textContent ?? "").replace(/\s+/g, " ");
      } else if (child.nodeType === 1) {
        const el = child as Element;
        const local = el.localName;
        if (local === "br") {
          out.push(cur);
          cur = { text: "", chords: [] };
        } else if (local === "chord") {
          const c = openLyricsChord(el);
          if (c) cur.chords.push({ pos: cur.text.length, chord: c });
          walk(el); // 0.9 chords can wrap the syllable they're sung on
        } else if (local === "comment") {
          // performance notes — skip
        } else {
          walk(el); // <tag>, formatting
        }
      }
    }
  };
  walk(linesEl);
  out.push(cur);
  return out
    .map((l) => {
      const lead = l.text.length - l.text.trimStart().length;
      return {
        text: l.text.trim(),
        chords: l.chords.map((c) => ({ ...c, pos: Math.max(0, c.pos - lead) })),
      };
    })
    .filter((l) => l.text !== "" || l.chords.length > 0);
}

export function parseOpenLyrics(text: string, fallbackTitle: string): ImportedChartSong | null {
  const doc = parseXml(text);
  if (!doc) return null;
  const song = doc.documentElement;
  if (song.localName !== "song") return null;

  const warnings: string[] = [];
  const sections: ChartSection[] = [];
  const nameToLabel = new Map<string, string>();
  for (const verse of all(doc, "verse")) {
    const name = verse.getAttribute("name") ?? "v";
    const label = openLyricsLabel(name);
    // "v1a" and "v1b" are two halves of one verse — keep them one section.
    const baseName = name.replace(/[a-z]$/i, (s) => (/\d/.test(name) ? "" : s));
    nameToLabel.set(name.toLowerCase(), label);
    nameToLabel.set(baseName.toLowerCase(), label);
    const stanzas = all(verse, "lines").map(openLyricsLines).filter((s) => s.length > 0);
    const existing = sections.find((s) => s.label === label);
    if (existing) existing.stanzas.push(...stanzas);
    else sections.push({ label, stanzas });
  }

  const orderText = textOf(first(doc, "verseOrder"));
  let order: string[] | null = null;
  if (orderText) {
    order = [];
    for (const tok of orderText.split(/\s+/)) {
      const label = nameToLabel.get(tok.toLowerCase());
      if (!label) {
        warnings.push(`Arrangement refers to "${tok}", which isn't in the song`);
        continue;
      }
      if (order[order.length - 1] !== label) order.push(label);
    }
  }

  const authors = all(doc, "author").map((a) => a.textContent?.trim()).filter(Boolean);
  const tempoEl = first(doc, "tempo");
  const tempo = tempoEl && tempoEl.getAttribute("type") !== "text" ? parseFloat(tempoEl.textContent ?? "") : NaN;

  return {
    title: textOf(first(doc, "title")) ?? fallbackTitle,
    artist: authors.length ? authors.join(", ") : null,
    ccli: textOf(first(doc, "ccliNo")),
    copyright: textOf(first(doc, "copyright")),
    chart: {
      version: CHART_VERSION,
      key: validKey(textOf(first(doc, "key"))),
      tempo: Number.isFinite(tempo) ? tempo : null,
      time: null,
      sections,
    },
    order,
    format: "openlyrics",
    warnings,
  };
}

// --- OpenSong ----------------------------------------------------------------

const OPENSONG_NAMES: Record<string, string> = {
  V: "Verse", C: "Chorus", B: "Bridge", P: "Pre-Chorus", T: "Tag", I: "Intro", E: "Ending", O: "Other",
};

function openSongLabel(tag: string): string {
  const m = /^([A-Za-z]+)\s*(\d*)$/.exec(tag.trim());
  if (!m) return tag.trim();
  const base = OPENSONG_NAMES[m[1]!.toUpperCase()] ?? m[1]!;
  return m[2] ? `${base} ${m[2]}` : base;
}

/**
 * OpenSong lyrics: `[V1]` sections, `.` chord lines, ` ` lyric lines, `;`
 * comments, `||`/`---` page breaks, `|` in-line breaks. A verse block can hold
 * several numbered verses at once (`1Amazing grace` / `2'Twas grace`).
 */
export function openSongLyricsToChart(lyrics: string): { sections: ChartSection[]; warnings: string[] } {
  const sections: ChartSection[] = [];
  const warnings: string[] = [];
  let tag = "V";
  // Per numbered verse within the current [V] block.
  let numbered = new Map<string, ChartLine[][]>();
  let stanza: ChartLine[] = [];
  let pendingChords: string | null = null;
  let numberedChordsUsed = false;

  const pushStanzaTo = (label: string, lines: ChartLine[]) => {
    if (lines.length === 0) return;
    let s = sections.find((x) => x.label === label);
    if (!s) { s = { label, stanzas: [] }; sections.push(s); }
    s.stanzas.push(lines);
  };
  const flushStanza = () => {
    if (pendingChords !== null) {
      stanza.push(chordLineOnly(pendingChords));
      pendingChords = null;
    }
    pushStanzaTo(openSongLabel(tag), stanza);
    stanza = [];
  };
  const flushNumbered = () => {
    for (const [n, stanzas] of [...numbered.entries()].sort()) {
      for (const st of stanzas) pushStanzaTo(openSongLabel(`${tag.replace(/\d+$/, "")}${n}`), st);
    }
    numbered = new Map();
  };

  for (const raw of lyrics.replace(/\r\n?/g, "\n").split("\n")) {
    const sec = /^\[([^\]]+)\]\s*$/.exec(raw.trim());
    if (sec) {
      if (numberedChordsUsed) pendingChords = null;
      numberedChordsUsed = false;
      flushStanza();
      flushNumbered();
      tag = sec[1]!;
      continue;
    }
    if (raw.startsWith(";")) continue;
    if (raw.startsWith(".")) {
      pendingChords = raw.slice(1);
      numberedChordsUsed = false;
      continue;
    }
    // A chord line consumed by numbered verses doesn't also stand alone.
    const sharedChords = pendingChords !== null && /^\d/.test(raw);
    if (/^\s*(\|\||---)\s*$/.test(raw) || raw.trim() === "") {
      if (numberedChordsUsed) pendingChords = null;
      numberedChordsUsed = false;
      flushStanza();
      continue;
    }
    const num = /^(\d)(.*)$/.exec(raw);
    const body = num ? num[2]! : raw.startsWith(" ") ? raw.slice(1) : raw;
    // `|` is a line break inside a lyric line; keep chord columns relative to
    // the first segment only (charts rarely put chords past a `|`).
    const parts = body.split("|");
    const lines: ChartLine[] = parts.map((p, i) => ({
      text: p.replace(/_/g, "").replace(/\s+$/, ""),
      chords: i === 0 && pendingChords !== null ? columns(pendingChords, 0) : [],
    }));
    // Numbered verses stacked under one chord line all share it.
    if (!num) pendingChords = null;
    else if (sharedChords) numberedChordsUsed = true;
    if (num) {
      const key = num[1]!;
      const list = numbered.get(key) ?? [[]];
      list[list.length - 1]!.push(...lines);
      numbered.set(key, list);
    } else {
      stanza.push(...lines);
    }
  }
  flushStanza();
  flushNumbered();
  return { sections: sections.filter((s) => s.stanzas.length > 0), warnings };
}

function columns(chordLine: string, shift: number): { pos: number; chord: string }[] {
  const out: { pos: number; chord: string }[] = [];
  const re = /\S+/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(chordLine))) out.push({ pos: Math.max(0, m.index - shift), chord: m[0] });
  return out;
}

function chordLineOnly(line: string): ChartLine {
  return { text: "", chords: columns(line, 0) };
}

export function parseOpenSong(text: string, fallbackTitle: string): ImportedChartSong | null {
  const doc = parseXml(text);
  if (!doc) return null;
  const song = doc.documentElement;
  if (song.localName !== "song" || !first(doc, "lyrics")) return null;
  const { sections, warnings } = openSongLyricsToChart(first(doc, "lyrics")!.textContent ?? "");

  const presentation = textOf(first(doc, "presentation"));
  let order: string[] | null = null;
  if (presentation) {
    order = presentation.split(/[\s,]+/).filter(Boolean).map(openSongLabel);
    for (const l of order) {
      if (!sections.some((s) => s.label === l)) warnings.push(`Arrangement refers to "${l}", which isn't in the song`);
    }
    order = order.filter((l) => sections.some((s) => s.label === l));
  }
  const tempo = parseFloat(textOf(first(doc, "tempo")) ?? "");

  return {
    title: textOf(first(doc, "title")) ?? fallbackTitle,
    artist: textOf(first(doc, "author")),
    ccli: textOf(first(doc, "ccli")),
    copyright: textOf(first(doc, "copyright")),
    chart: {
      version: CHART_VERSION,
      key: validKey(textOf(first(doc, "key"))),
      tempo: Number.isFinite(tempo) ? tempo : null,
      time: textOf(first(doc, "time_sig")),
      sections,
    },
    order,
    format: "opensong",
    warnings,
  };
}

// --- SongSelect .usr ---------------------------------------------------------

/**
 * SongSelect's lyrics-only download: an INI-ish file with `Fields=` naming the
 * sections and `Words=` holding them, `/t` between sections and `/n` for new
 * lines.
 */
export function parseSongSelectUsr(text: string, fallbackTitle: string): ImportedChartSong | null {
  const fields: Record<string, string> = {};
  for (const line of text.replace(/\r\n?/g, "\n").split("\n")) {
    const m = /^([A-Za-z]+)=(.*)$/.exec(line);
    if (m) fields[m[1]!.toLowerCase()] = m[2]!;
  }
  if (fields.words === undefined) return null;
  const names = (fields.fields ?? "").split("/t");
  const bodies = fields.words.split("/t");
  const sections: ChartSection[] = [];
  bodies.forEach((body, i) => {
    const label = names[i]?.trim() || `Verse ${i + 1}`;
    const stanzas: ChartLine[][] = [];
    let cur: ChartLine[] = [];
    for (const line of body.split("/n")) {
      if (line.trim() === "") {
        if (cur.length) stanzas.push(cur);
        cur = [];
      } else {
        cur.push({ text: line.trim(), chords: [] });
      }
    }
    if (cur.length) stanzas.push(cur);
    if (stanzas.length) sections.push({ label, stanzas });
  });
  if (sections.length === 0) return null;
  const ccli = /^\[S\s*A?(\d+)\]/m.exec(text)?.[1] ?? null;
  return {
    title: fields.title?.trim() || fallbackTitle,
    artist: fields.author?.replace(/\s*\|\s*/g, ", ").trim() || null,
    ccli,
    copyright: fields.copyright?.replace(/\s*\|\s*/g, ", ").trim() || null,
    chart: {
      version: CHART_VERSION,
      key: validKey(fields.keys ?? null),
      tempo: null,
      time: null,
      sections,
    },
    order: null,
    format: "songselect-usr",
    warnings: ["SongSelect .usr files carry lyrics only — no chords"],
  };
}
