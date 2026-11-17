/**
 * Chord-sheet PDFs → the text chart format the text reader understands.
 *
 * A PDF has no lines or columns, only runs of text at (x, y) positions. Chord
 * sheets put each chord *above* the syllable it's played on, in a
 * proportional font, so turning the page into plain text and counting
 * columns (what `pdftotext -layout` does) puts chords a letter or two off. We
 * use the positions instead: every chord is placed at the lyric character
 * directly beneath it, and written inline (`I love [G]You Lord`) — the form
 * the text reader already handles exactly, so all its header, metadata and
 * footer rules apply to PDFs too.
 *
 * Pure: takes positioned text items, returns text. `pdf.ts` does the PDF.js
 * part.
 */

import { isChordLine, isChordToken, parseChord } from "@/lib/chords/chord";

/** A run of text on a page, in PDF units (y grows upwards). */
export interface PdfTextItem {
  str: string;
  x: number;
  y: number;
  width: number;
  /** Font size, used as the line height scale. */
  size: number;
  /** Set during layout: small raised text (a chord's "7", "sus", "2"). */
  sup?: boolean;
}

/** A lyric hyphen rectangle extracted from the PDF vector path operator list. */
export interface PdfHyphenRect {
  x: number;
  y: number;
}

/**
 * Relative advance widths of a typical sans-serif (Helvetica's metrics, per
 * 1000 em). PDF.js reports only a run's total width, not where each letter
 * sits; sharing that width out by these proportions puts letters — and so
 * the chords above them — far closer than an even split in a proportional
 * font, where "ill" and "mmm" differ threefold.
 */
const SANS_WIDTHS: Record<string, number> = {
  " ": 278, "!": 278, '"': 355, "'": 191, "’": 222, "‘": 222, "(": 333, ")": 333, ",": 278, "-": 333,
  ".": 278, ":": 278, ";": 278, "?": 556, "/": 278,
  A: 667, B: 667, C: 722, D: 722, E: 667, F: 611, G: 778, H: 722, I: 278, J: 500, K: 667, L: 556, M: 833,
  N: 722, O: 778, P: 667, Q: 778, R: 722, S: 667, T: 611, U: 722, V: 667, W: 944, X: 667, Y: 667, Z: 611,
  a: 556, b: 556, c: 500, d: 556, e: 556, f: 278, g: 556, h: 556, i: 222, j: 222, k: 500, l: 222, m: 833,
  n: 556, o: 556, p: 556, q: 556, r: 333, s: 500, t: 278, u: 556, v: 500, w: 722, x: 500, y: 500, z: 500,
};

function advance(ch: string): number {
  return SANS_WIDTHS[ch] ?? 556;
}

interface Glyph {
  ch: string;
  x: number;
}

interface Line {
  y: number;
  size: number;
  glyphs: Glyph[];
  text: string;
}

/** Split each item into characters with estimated x positions. */
function glyphsOf(item: PdfTextItem): Glyph[] {
  const chars = [...item.str];
  const total = chars.reduce((sum, ch) => sum + advance(ch), 0);
  const scale = total > 0 ? item.width / total : 0;
  let x = item.x;
  return chars.map((ch) => {
    const g = { ch, x };
    x += advance(ch) * scale;
    return g;
  });
}

/**
 * Group items into lines by baseline, then lay each line out as text: a gap
 * wider than a space becomes as many spaces as fit, so a chord line keeps its
 * rough shape (needed to recognise it as a chord line and to split tokens).
 */
export function layoutLines(items: PdfTextItem[]): Line[] {
  // Space-only runs are dropped: gaps are rebuilt from positions, and a stray
  // one at superscript height would otherwise form a "line" of its own that
  // the superscripts then join instead of their chords.
  const sorted = items
    .filter((it) => it.str.trim().length > 0)
    .sort((a, b) => b.y - a.y || a.x - b.x);
  // Chord sheets set a chord's extension ("7", "2", "sus", "7(4)") as small
  // raised text. Left alone it forms a line of its own and the chord loses
  // it ("Dmaj7" → "Dmaj" plus a stray "7" line), so small text joins the
  // line just below it.
  // The body text size, weighted by characters: a chord-heavy page has more
  // tiny superscript *runs* than lyric runs, but lyrics hold the characters.
  const bySize = new Map<number, number>();
  for (const i of sorted) {
    const n = i.str.trim().length;
    if (n) bySize.set(i.size, (bySize.get(i.size) ?? 0) + n);
  }
  const body = [...bySize.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] ?? 10;
  // Only text shaped like a chord extension: headings and footers set in a
  // small font are lines of their own.
  const EXTENSION = /^\(?(?:sus|maj|ma|add|min|dim|aug|alt|no|m|[#b♯♭+-])?\d{0,2}(?:\(\d+\))?(?:sus\d?)?\)?$/i;
  const small = (it: PdfTextItem) =>
    it.str.trim() !== "" && it.size < body * 0.85 && EXTENSION.test(it.str.trim());

  const rows: { y: number; size: number; items: PdfTextItem[] }[] = [];
  for (const it of sorted.filter((i) => !small(i))) {
    const row = rows.find((r) => Math.abs(r.y - it.y) < Math.max(r.size, it.size) * 0.35);
    if (row) {
      row.items.push(it);
      row.size = Math.max(row.size, it.size);
    } else {
      rows.push({ y: it.y, size: it.size, items: [it] });
    }
  }
  for (const it of sorted.filter(small)) {
    // The nearest line whose baseline is a little below this text. (Safe
    // without checking x: only extension-shaped text gets here.)
    const host = rows
      .filter((r) => it.y - r.y > -r.size * 0.3 && it.y - r.y < r.size)
      .sort((a, b) => Math.abs(a.y - it.y) - Math.abs(b.y - it.y))[0];
    if (host) host.items.push({ ...it, sup: true });
    else rows.push({ y: it.y, size: it.size, items: [it] });
  }
  rows.sort((a, b) => b.y - a.y);

  return rows.map((row) => {
    const items = row.items.sort((a, b) => a.x - b.x);
    // A space's width, estimated from the line's own text.
    // Some PDFs report runs with zero width; never let the estimate collapse.
    const widths = items
      .filter((i) => i.str.trim().length > 0 && i.width > 0)
      .map((i) => i.width / [...i.str].length);
    const median = widths.length ? widths.sort((a, b) => a - b)[Math.floor(widths.length / 2)]! : 0;
    const charW = Math.max(median, row.size * 0.25);
    const spaceW = charW * 0.9;
    const glyphs: Glyph[] = [];
    let cursor: number | null = null;
    for (const it of items) {
      const gs = glyphsOf(it);
      if (cursor !== null && gs.length > 0) {
        // A word space in a proportional font is ~0.25em — narrower than an
        // average letter — so judge gaps against the font size, not letters.
        const gap = gs[0]!.x - cursor;
        // A raised extension hugs its chord even across a small gap.
        const threshold = it.sup ? row.size * 0.6 : row.size * 0.15;
        const spaces = gap > threshold ? Math.min(200, Math.max(1, Math.round(gap / spaceW))) : 0;
        for (let s = 0; s < spaces; s++) glyphs.push({ ch: " ", x: cursor + s * spaceW });
      }
      glyphs.push(...gs);
      if (gs.length) cursor = it.x + it.width;
    }
    return { y: row.y, size: row.size, glyphs, text: glyphs.map((g) => g.ch).join("") };
  });
}

/**
 * Performance notes that sit on chord lines — "(1.)", "(To Tag)", "[x2]" —
 * and aren't chords. Removed before deciding whether a line is chords.
 */
/**
 * A slash bass sometimes arrives as its own run ("G  /B"). Pull it back onto
 * its chord, moving the gap after it so every character keeps its index (and
 * so its x position).
 */
function joinSlashBass(line: string): string {
  return line.replace(/(\S)( +)(\/[A-G](?:#|b|♯|♭)?)(?=\s|$)/g, (_m, a: string, gap: string, bass: string) => a + bass + gap);
}

function stripAnnotations(line: string): string {
  // Only free-standing brackets: the "(4)" in "D(4)" is part of the chord.
  return line.replace(/(^|\s)([([](?:[^()[\]]|\([^()]*\))*[)\]])/g, (m, lead: string, group: string) =>
    isChordToken(group) ? m : lead + " ".repeat(group.length),
  );
}

function isChordRow(text: string): boolean {
  const stripped = joinSlashBass(stripAnnotations(text)).trim();
  return stripped !== "" && isChordLine(stripped);
}

/** Chord tokens of a chord line with the x of their first letter. */
function chordTokens(line: Line): { chord: string; x: number }[] {
  const stripped = joinSlashBass(stripAnnotations(line.text));
  const out: { chord: string; x: number }[] = [];
  const re = /\S+/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(stripped))) {
    if (!isChordToken(m[0])) continue; // bar lines, "x2"
    out.push({ chord: m[0], x: line.glyphs[m.index]!.x });
  }
  return out;
}

/** Write chords inline into a lyric line at the character under each chord. */
function inlineChords(chords: { chord: string; x: number }[], lyric: Line): string {
  const glyphs = lyric.glyphs;
  const at = new Map<number, string[]>();
  const tol = lyric.size * 0.15;
  for (const c of chords) {
    let idx = glyphs.findIndex((g) => g.x >= c.x - tol);
    if (idx < 0) idx = glyphs.length; // past the end of the lyric
    // Land on a letter, not the space before it.
    while (idx < glyphs.length && glyphs[idx]!.ch === " ") idx++;
    at.set(idx, [...(at.get(idx) ?? []), c.chord]);
  }
  let out = "";
  for (let i = 0; i <= glyphs.length; i++) {
    for (const ch of at.get(i) ?? []) out += `[${ch}]`;
    if (i < glyphs.length) out += glyphs[i]!.ch;
  }
  let result = out.replace(/\s+$/, "");
  // Rejoin SongSelect spaced syllable hyphens (`Al - le - lu - ia`, `Al - [D/A]might - [A]y`)
  // while keeping any inline `[Chord]` tags between syllables intact!
  let prev = "";
  while (result !== prev) {
    prev = result;
    result = result.replace(
      /([A-Za-z])((?:(?:\[[^\]\n]+\])|\s)*)-(?:(?:\s+|(?=\[)))((?:(?:\[[^\]\n]+\])|\s)*)([a-z])/g,
      (_m, left: string, preChords: string, postChords: string, right: string) => {
        const chordsStr = (preChords + postChords).replace(/\s+/g, "");
        return `${left}${chordsStr}${right}`;
      },
    );
  }
  // Collapse multiple layout spaces between words into single spaces (preserving inline chords).
  result = result.replace(/ {2,}/g, " ");
  // Repair truncated "Allelui" at end of line (common in SongSelect PDFs where trailing 'a' is clipped).
  result = result.replace(/\b(Al(?:\[[^\]\n]+\])*le(?:\[[^\]\n]+\])*lu(?:\[[^\]\n]+\])*i)(?=\s|$)/g, "$1a");
  return result;
}

/** "(E)", "('C)": a key in brackets after a title. */
const KEY_SUFFIX = String.raw`\(\W?[A-G][#b]?m?\)`;

/**
 * Page furniture repeated on continuation pages: "Song Title - 2", or the
 * title again over the same song in another key: "Song Title (E)". Never the
 * bare title — that's often a lyric line too ("Here I Am to Worship").
 */
function isPageHeader(text: string, title: string | null): boolean {
  if (!title) return false;
  const t = text.trim();
  const base = title.replace(new RegExp(`\\s*${KEY_SUFFIX}$`), "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`^${base}(?:\\s*\\([^)]*\\))?\\s*(?:[-–]\\s*\\d+|${KEY_SUFFIX})$`, "i").test(t);
}

/**
 * Split a two-column page (PraiseCharts and similar print two columns to fit
 * a song on one page) into reading order: anything spanning the gutter (the
 * heading) first, then the left column, then the right.
 *
 * The gutter is the vertical band that the fewest runs cross, searched in
 * the middle of the page; it only counts if both columns hold a real share of
 * the text and almost nothing below the heading crosses it.
 */
export function splitColumns(items: PdfTextItem[]): PdfTextItem[][] {
  const text = items.filter((i) => i.str.trim() !== "");
  if (text.length < 20) return [items];
  const left = Math.min(...text.map((i) => i.x));
  const right = Math.max(...text.map((i) => i.x + i.width));
  const width = right - left;
  let best: { g: number; crossing: PdfTextItem[] } | null = null;
  for (let f = 0.3; f <= 0.7; f += 0.01) {
    const g = left + width * f;
    const crossing = text.filter((i) => i.x < g && i.x + i.width > g);
    const leftShare = text.filter((i) => i.x + i.width <= g).length / text.length;
    if (leftShare < 0.2 || leftShare > 0.8) continue;
    if (!best || crossing.length < best.crossing.length) best = { g, crossing };
  }
  if (!best || best.crossing.length > 8) return [items];
  // Whatever crosses the gutter must be a full-width heading above the
  // columns or a footer (copyright, licence) below them.
  const inColumns = text.filter((i) => !best!.crossing.includes(i));
  const colTop = Math.max(...inColumns.map((i) => i.y));
  const colBottom = Math.min(...inColumns.map((i) => i.y));
  const heading = best.crossing.filter((i) => i.y >= colTop - 1);
  const footer = best.crossing.filter((i) => i.y <= colBottom + 1);
  if (heading.length + footer.length !== best.crossing.length) return [items];
  const g = best.g;
  // Heading and footer lines are whole lines: take their other runs along.
  const onLineOf = (set: PdfTextItem[]) => (i: PdfTextItem) => set.some((o) => Math.abs(o.y - i.y) < i.size * 0.35);
  const isHeading = onLineOf(heading);
  const isFooter = onLineOf(footer);
  const rest = items.filter((i) => !isHeading(i) && !isFooter(i));
  const cols = [items.filter(isHeading), rest.filter((i) => i.x < g), rest.filter((i) => i.x >= g), items.filter(isFooter)];
  // Only a real split if both columns hold text.
  return cols[1]!.some((i) => i.str.trim()) && cols[2]!.some((i) => i.str.trim()) ? cols.filter((c) => c.length) : [items];
}

/**
 * Turn the pages of a chord sheet or notation lead sheet into chart text.
 * If the PDF is a notation lead sheet (contains SMuFL music glyphs and staff
 * systems), it is converted via `leadSheetPagesToChartText`; otherwise chord
 * lines directly over a lyric line are merged into it, and any other chord
 * line (an intro, a turnaround) is written as a chords-only line.
 */
export function pagesToChartText(
  pages: PdfTextItem[][],
  pageHyphens?: PdfHyphenRect[][],
): string {
  if (isLeadSheetPages(pages)) {
    return leadSheetPagesToChartText(pages, pageHyphens);
  }
  const out: string[] = [];
  let title: string | null = null;
  const blocks = pages.flatMap(splitColumns);
  for (const items of blocks) {
    const lines = layoutLines(items);
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i]!;
      const text = line.text.replace(/\s+$/, "");
      if (text.trim() === "") continue;
      if (title === null) title = text.trim();
      else if (isPageHeader(text, title)) continue;

      const prev = lines[i - 1];
      // A gap bigger than ~1.8 lines is a stanza or section break.
      if (prev && prev.y - line.y > Math.max(prev.size, line.size) * 1.9) out.push("");

      if (isChordRow(text)) {
        const next = lines[i + 1];
        const close = next && line.y - next.y < Math.max(line.size, next.size) * 1.9;
        if (
          next && close && next.text.trim() !== "" && !isChordRow(next.text) &&
          !looksLikeHeader(next.text) && !looksLikeFooter(next.text)
        ) {
          out.push(inlineChords(chordTokens(line), next));
          i += 1;
        } else {
          out.push(chordTokens(line).map((c) => `[${c.chord}]`).join(" "));
        }
        continue;
      }
      out.push(text.trim());
    }
    out.push("");
  }
  return out.join("\n").replace(/\n{3,}/g, "\n\n").trim() + "\n";
}

// --- Notation Lead Sheet PDF -> Chart Text -----------------------------------

const LEAD_SECTION_LABEL_RE =
  /^(INTRO|VERSE|CHORUS|BRIDGE|TURNAROUND|INSTRUMENTAL|INSTRUMENTA|INTERLUDE|ENDING|TAG|OUTRO|PRE-?CHORUS|VAMP|REFRAIN)(\s+\d+[a-z]?(?:\s*&\s*\d+[a-z]?)?)?$/i;

const LEAD_NAV_NOISE_RE =
  /^(?:D\.S\.?(?:\s+al\s+Coda)?|D\.C\.?(?:\s+al\s+Coda)?|Coda|To\s+Coda|(?:\d+(?:st|nd|rd|th)\s+time\s+to\s+Coda)|repeat\s+.*|\((?:1st|repeat|Instr).*)$/i;

const LEAD_VOLTA_RE = /^\(?[1-9](?:,[1-9])*\.?\)?$/;

function normalizeLeadSectionLabel(raw: string): string {
  return raw.replace(/^INSTRUMENTA\b/i, "INSTRUMENTAL");
}

function isLeadChordToken(str: string): boolean {
  if (str === "N.C." || str === "NC") return true;
  return str.split(/\s+/).every((tok) => parseChord(tok) !== null);
}

/**
 * Detect whether the PDF pages come from a musical notation lead sheet
 * (e.g. SongSelect / Verovio lead sheets with SMuFL glyphs or left-margin
 * measure numbers and spaced syllables under staves).
 */
export function isLeadSheetPages(pages: PdfTextItem[][]): boolean {
  let smuflCount = 0;
  let barNumberCount = 0;
  let tempoMarkCount = 0;
  for (const page of pages) {
    for (const it of page) {
      if (/[\uE000-\uF8FF]|\uD834[\uDD00-\uDDFF]/.test(it.str)) {
        smuflCount += 1;
      }
      if (it.x >= 44 && it.x < 60 && /^\d{1,3}$/.test(it.str.trim()) && it.size >= 7 && it.size < 9) {
        barNumberCount += 1;
      }
      if (/^=\s*\d{2,3}$/.test(it.str.trim())) {
        tempoMarkCount += 1;
      }
    }
  }
  return smuflCount >= 1 || (barNumberCount >= 2 && tempoMarkCount >= 1);
}

function mergeLeadRowItems(items: PdfTextItem[]): PdfTextItem[] {
  const sorted = [...items].sort((a, b) => a.x - b.x);
  const out: PdfTextItem[] = [];
  for (const it of sorted) {
    const s = it.str
      .replace(/\uEA66/g, "#")
      .replace(/\uEA64/g, "b")
      .replace(/\u266F/g, "#")
      .replace(/\u266D/g, "b")
      .replace(/\uD834[\uDD00-\uDDFF]/g, "")
      .replace(/[\uE000-\uF8FF]/g, "")
      .trim();
    if (!s) continue;
    const prev = out[out.length - 1];
    if (
      prev &&
      it.x - (prev.x + prev.width) < 12 &&
      (s === "#" ||
        s === "b" ||
        s.startsWith("/") ||
        ((prev.str.endsWith("#") || prev.str.endsWith("b") || prev.str.endsWith("/")) &&
          /^[A-G0-9mMsuaddnodimaug(+/]/i.test(s)))
    ) {
      prev.str += s;
      prev.width = it.x + it.width - prev.x;
      continue;
    }
    out.push({ ...it, str: s });
  }
  return out;
}

function buildLeadChordAndLyricPair(
  chords: { str: string; x: number }[],
  lyricItems: PdfTextItem[],
  rowY: number,
  hyphens: PdfHyphenRect[],
): { chordLine: string; lyricLine: string } {
  const filteredLyrics = lyricItems.filter((it, idx) => !(idx === 0 && /^\d+\.$/.test(it.str)));
  if (filteredLyrics.length === 0) {
    return { chordLine: chords.map((c) => c.str).join("   "), lyricLine: "" };
  }
  const rowHyphens = hyphens.filter((h) => Math.abs(h.y - rowY - 2.3) < 3.5);
  let lyricLine = "";
  const tokenCols: { x: number; col: number }[] = [];
  for (let i = 0; i < filteredLyrics.length; i++) {
    const cur = filteredLyrics[i]!;
    const next = filteredLyrics[i + 1];
    const col = lyricLine.length;
    tokenCols.push({ x: cur.x, col });
    const gap = next ? next.x - (cur.x + cur.width) : Infinity;
    const hasHy =
      next !== undefined &&
      (rowHyphens.some((h) => h.x > cur.x && h.x < next.x) ||
        next.str.startsWith("'") ||
        next.str.startsWith("’") ||
        next.str === "n't" ||
        next.str === "n’t" ||
        (gap < 3.8 && !/[,.;:!?")\]]$/.test(cur.str) && /^[a-z]/.test(next.str)));
    lyricLine += cur.str + (hasHy ? "" : " ");
  }
  lyricLine = lyricLine.trimEnd();
  if (chords.length === 0) {
    return { chordLine: "", lyricLine };
  }
  const buf: string[] = [];
  for (const c of chords) {
    let bestCol = 0;
    let bestDist = Infinity;
    for (const tc of tokenCols) {
      const d = Math.abs(tc.x - c.x);
      if (d < bestDist) {
        bestDist = d;
        bestCol = tc.col;
      }
    }
    const lastTc = tokenCols[tokenCols.length - 1];
    if (lastTc && c.x > lastTc.x + 28) {
      bestCol = lyricLine.length + 2;
    }
    let col = bestCol;
    while (col < buf.length && buf[col] !== " ") col++;
    if (col > 0 && buf[col - 1] && buf[col - 1] !== " ") col++;
    while (buf.length < col) buf.push(" ");
    for (let k = 0; k < c.str.length; k++) {
      if (col + k < buf.length) buf[col + k] = c.str[k]!;
      else buf.push(c.str[k]!);
    }
  }
  return { chordLine: buf.join("").trimEnd(), lyricLine };
}

/**
 * Convert notation lead-sheet PDF pages into clean chord-over-lyric chart text
 * with metadata headers, rejoined multi-row lyrics, and aligned chords.
 */
export function leadSheetPagesToChartText(
  pages: PdfTextItem[][],
  pageHyphens?: PdfHyphenRect[][],
): string {
  let title = "";
  const artistLines: string[] = [];
  let ccli = "";
  let copyright = "";
  let tempo = "";

  interface BodyRow {
    y: number;
    items: PdfTextItem[];
    labels: PdfTextItem[];
    isSectionRow: boolean;
    isChordRow: boolean;
    pageHyphens: PdfHyphenRect[];
  }

  interface LeadSystem {
    labels: PdfTextItem[];
    chords: { str: string; x: number }[];
    lyricRows: BodyRow[];
    hyphens: PdfHyphenRect[];
  }

  const systems: LeadSystem[] = [];

  for (let pi = 0; pi < pages.length; pi++) {
    const items = pages[pi]!;
    const hyphens = pageHyphens?.[pi] ?? [];
    const rawRows: { y: number; items: PdfTextItem[] }[] = [];
    for (const it of [...items].sort((a, b) => b.y - a.y || a.x - b.x)) {
      const r = rawRows.find((row) => Math.abs(row.y - it.y) < 3.0);
      if (r) r.items.push(it);
      else rawRows.push({ y: it.y, items: [it] });
    }
    const rows = rawRows
      .map((r) => ({ y: r.y, items: mergeLeadRowItems(r.items) }))
      .filter((r) => r.items.length > 0);

    const bodyRows: BodyRow[] = [];
    for (const r of rows) {
      const fullText = r.items.map((i) => i.str).join(" ").trim();
      if (pi > 0 && r.y > 720 && /\s[-–]\s*\d+$/.test(fullText)) continue;
      if (r.y < 95 || r.items.every((i) => i.size < 6.5)) {
        const ccliM = fullText.match(/CCLI\s+Song\s*#?\s*(\d{4,9})/i);
        if (ccliM && !ccli) ccli = ccliM[1]!;
        if ((fullText.includes("©") || /copyright/i.test(fullText)) && !copyright) {
          copyright = fullText;
        }
        continue;
      }
      if (pi === 0 && r.y > 682 && !r.items.some((i) => LEAD_SECTION_LABEL_RE.test(i.str))) {
        if (!title && r.items[0]!.size >= 13) {
          title = fullText;
          continue;
        }
        if (/^(Words|Music|\(as published|\(based on)/i.test(fullText)) continue;
        if (r.items[0]!.size <= 9.5) {
          artistLines.push(fullText);
          continue;
        }
      }
      const tempoItem = r.items.find((i) => /^=\s*\d{2,3}$/.test(i.str));
      if (tempoItem && !tempo) {
        tempo = tempoItem.str.replace(/^=\s*/, "");
      }
      const cleaned = r.items.filter((i) => {
        if (i.x < 60 && /^\d{1,3}$/.test(i.str) && i.size < 9.0) return false;
        if (/^=\s*\d{2,3}$/.test(i.str)) return false;
        if (LEAD_NAV_NOISE_RE.test(i.str)) return false;
        return true;
      });
      if (cleaned.length === 0) continue;
      const labels = cleaned
        .filter((i) => LEAD_SECTION_LABEL_RE.test(i.str))
        .map((i) => ({ ...i, str: normalizeLeadSectionLabel(i.str) }));
      const nonLabels = cleaned.filter(
        (i) => !LEAD_SECTION_LABEL_RE.test(i.str) && !LEAD_VOLTA_RE.test(i.str),
      );
      const chordItems = nonLabels.filter((i) => isLeadChordToken(i.str));
      const isSectionRow = labels.length > 0 && nonLabels.length === 0;
      const isVoltaRow = cleaned.every((i) => LEAD_VOLTA_RE.test(i.str));
      if (isVoltaRow) continue;
      const isChordRowItem = nonLabels.length > 0 && chordItems.length === nonLabels.length;
      bodyRows.push({
        y: r.y,
        items: cleaned,
        labels,
        isSectionRow,
        isChordRow: isChordRowItem,
        pageHyphens: hyphens,
      });
    }

    let pendingLabels: PdfTextItem[] = [];
    let currentSystem: LeadSystem | null = null;
    for (const br of bodyRows) {
      if (br.isSectionRow) {
        pendingLabels.push(...br.labels);
        continue;
      }
      if (br.isChordRow || (br.labels.length > 0 && br.items.some((i) => isLeadChordToken(i.str)))) {
        const inlineLabels = br.items
          .filter((i) => LEAD_SECTION_LABEL_RE.test(i.str))
          .map((i) => ({ ...i, str: normalizeLeadSectionLabel(i.str) }));
        const chordTokensList: { str: string; x: number }[] = [];
        for (const it of br.items.filter(
          (i) => !LEAD_SECTION_LABEL_RE.test(i.str) && !LEAD_VOLTA_RE.test(i.str),
        )) {
          if (it.str === "N.C." || it.str === "NC") continue;
          const parts = it.str.split(/\s+/);
          parts.forEach((p, idx) => {
            chordTokensList.push({ str: p, x: it.x + idx * 18 });
          });
        }
        if (
          chordTokensList.length === 0 &&
          currentSystem &&
          currentSystem.lyricRows.length === 0 &&
          inlineLabels.length === 0 &&
          pendingLabels.length === 0
        ) {
          continue;
        }
        currentSystem = {
          labels: [...pendingLabels, ...inlineLabels].sort((a, b) => a.x - b.x),
          chords: chordTokensList,
          lyricRows: [],
          hyphens: br.pageHyphens,
        };
        pendingLabels = [];
        systems.push(currentSystem);
      } else if (currentSystem) {
        currentSystem.lyricRows.push(br);
      }
    }
  }

  interface LeadSegment {
    label: string;
    chords: { str: string; x: number }[];
    lyricRows: BodyRow[];
    hyphens: PdfHyphenRect[];
  }

  const segments: LeadSegment[] = [];
  let currentLabel = "Verse";
  for (const sys of systems) {
    const sortedLabels = [...sys.labels].sort((a, b) => a.x - b.x);
    let minXAfterFirstChord = 195;
    if (sortedLabels.length > 0 && sys.chords.length > 0) {
      minXAfterFirstChord = Math.max(195, sys.chords[0]!.x + 35);
    }
    if (sys.labels.length === 0) {
      segments.push({
        label: currentLabel,
        chords: sys.chords,
        lyricRows: sys.lyricRows,
        hyphens: sys.hyphens,
      });
    } else {
      const firstLblX = sortedLabels[0]!.x;
      const startsMidSystem = firstLblX >= minXAfterFirstChord;
      if (startsMidSystem) {
        const splitX = firstLblX - 12;
        const leftChords = sys.chords.filter((c) => c.x < splitX);
        const leftLyrics = sys.lyricRows
          .map((lr) => ({
            ...lr,
            items: lr.items.filter((i) => i.x < splitX),
          }))
          .filter((lr) => lr.items.length > 0);
        if (leftChords.length > 0 || leftLyrics.length > 0) {
          segments.push({
            label: currentLabel,
            chords: leftChords,
            lyricRows: leftLyrics,
            hyphens: sys.hyphens,
          });
        }
      }
      for (let li = 0; li < sortedLabels.length; li++) {
        const lbl = sortedLabels[li]!;
        currentLabel = lbl.str;
        const minX = li === 0 && !startsMidSystem ? 0 : lbl.x - 12;
        const maxX = li + 1 < sortedLabels.length ? sortedLabels[li + 1]!.x - 12 : Infinity;
        const segChords = sys.chords.filter((c) => c.x >= minX && c.x < maxX);
        const segLyrics = sys.lyricRows
          .map((lr) => ({
            ...lr,
            items: lr.items.filter((i) => i.x >= minX && i.x < maxX),
          }))
          .filter((lr) => lr.items.length > 0);
        segments.push({
          label: currentLabel,
          chords: segChords,
          lyricRows: segLyrics,
          hyphens: sys.hyphens,
        });
      }
    }
  }

  const sectionGroups: { label: string; segments: LeadSegment[] }[] = [];
  for (const seg of segments) {
    const prev = sectionGroups[sectionGroups.length - 1];
    if (prev && prev.label === seg.label) {
      prev.segments.push(seg);
    } else {
      sectionGroups.push({ label: seg.label, segments: [seg] });
    }
  }

  const outLines: string[] = [];
  if (title) outLines.push(title);
  if (artistLines.length) outLines.push(artistLines.join(", "));
  if (tempo) outLines.push(`Tempo: ${tempo}`);
  outLines.push("");

  for (const grp of sectionGroups) {
    const maxRows = Math.max(1, ...grp.segments.map((s) => s.lyricRows.length));
    const baseMatch = grp.label.match(/^([A-Za-z-]+)(?:\s+(\d+)\s*&\s*(\d+))?$/);
    if (maxRows > 1 && baseMatch) {
      const kindWord = baseMatch[1]!;
      for (let ri = 0; ri < maxRows; ri++) {
        const secNum =
          baseMatch[2] && ri === 0
            ? baseMatch[2]
            : baseMatch[3] && ri === 1
              ? baseMatch[3]
              : String(ri + 1);
        outLines.push(`${kindWord} ${secNum}`);
        for (const seg of grp.segments) {
          const lr = seg.lyricRows[ri];
          if (!lr) continue;
          const { chordLine, lyricLine } = buildLeadChordAndLyricPair(
            seg.chords,
            lr.items,
            lr.y,
            seg.hyphens,
          );
          if (chordLine) outLines.push(chordLine);
          if (lyricLine) outLines.push(lyricLine);
        }
        outLines.push("");
      }
    } else {
      outLines.push(grp.label.replace(/\s+\d+[a-z]$/i, (m) => m.slice(0, -1)));
      for (const seg of grp.segments) {
        if (seg.lyricRows.length === 0) {
          if (seg.chords.length > 0) {
            outLines.push(seg.chords.map((c) => c.str).join("   "));
          }
        } else {
          for (const lr of seg.lyricRows) {
            const { chordLine, lyricLine } = buildLeadChordAndLyricPair(
              seg.chords,
              lr.items,
              lr.y,
              seg.hyphens,
            );
            if (chordLine) outLines.push(chordLine);
            if (lyricLine) outLines.push(lyricLine);
          }
        }
      }
      outLines.push("");
    }
  }
  if (ccli) outLines.push(`CCLI Song # ${ccli}`);
  if (copyright) outLines.push(copyright);
  return outLines.join("\n").replace(/\n{3,}/g, "\n\n").trim() + "\n";
}

/** Copyright/licensing lines — never lyrics for a chord line to sit on. */
function looksLikeFooter(text: string): boolean {
  return /(©|\bCCLI\b|\bcopyright\b|for\s*use\s*solely|all rights reserved)/i.test(text);
}

function looksLikeHeader(text: string): boolean {
  return /^\s*[[(]?(verse|chorus|pre[- ]?chorus|bridge|tag|intro|outro|ending|interlude|instrumental|vamp|turnaround)\b[^a-z]*$/i.test(text);
}
