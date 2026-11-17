/**
 * Chord charts: the band's copy of a song.
 *
 * A chart is stored beside the song (table `song_charts`), never inside
 * `body_text`: the lyrics the projector shows and the AI matches against stay
 * exactly as they are, and a song with no chart behaves as it always has.
 *
 * The chart does not have to agree with the song's slides line for line. The
 * band view finds each slide's words *inside* the chart's word stream (see
 * `alignSlide`), so a library song whose lyrics were typed with different
 * line breaks, a per-setlist edited slide, or a repeated chorus all still pick
 * up their chords.
 */

import { tokenize } from "@/lib/parseSong";
import { transposeChord, transposeKey } from "@/lib/chords/chord";

export const CHART_VERSION = 1;

/** A chord placed at a character offset into a line's text. */
export interface PlacedChord {
  pos: number;
  chord: string;
}

/** One line of a chart. `text` is empty for a chord-only (instrumental) line. */
export interface ChartLine {
  text: string;
  chords: PlacedChord[];
}

export interface ChartSection {
  /** The label as written in the source ("Verse 1", "Pre-Chorus", "Intro"). */
  label: string;
  /** Blank-line separated groups of lines — one slide's worth each. */
  stanzas: ChartLine[][];
}

export interface ChordChart {
  version: number;
  /** Written key, e.g. "G", "Bb", "F#m". Null when the source didn't say. */
  key: string | null;
  tempo: number | null;
  time: string | null;
  sections: ChartSection[];
}

export function emptyChart(): ChordChart {
  return { version: CHART_VERSION, key: null, tempo: null, time: null, sections: [] };
}

export function chartHasChords(chart: ChordChart): boolean {
  return chart.sections.some((s) => s.stanzas.some((st) => st.some((l) => l.chords.length > 0)));
}

/** Parse a stored chart, or null for absent/malformed JSON. */
export function parseStoredChart(json: string | null | undefined): ChordChart | null {
  if (!json) return null;
  try {
    const c = JSON.parse(json) as ChordChart;
    if (!c || !Array.isArray(c.sections)) return null;
    return c;
  } catch {
    return null;
  }
}

// --- Alignment: find a slide's chords in the chart ---------------------------

/** A word of the chart's lyric stream, with the chords sung on it. */
interface StreamWord {
  word: string;
  /** Chords that land on this word, each with its offset into the raw token. */
  chords: { offset: number; chord: string }[];
}

/**
 * A chord-only run between words: an intro, a turnaround, an instrumental.
 * `beforeWord` is the stream index of the next sung word (stream length if
 * it's at the very end).
 */
interface InstrumentalRun {
  beforeWord: number;
  label: string;
  lines: ChartLine[];
}

interface ChartStream {
  words: StreamWord[];
  instrumentals: InstrumentalRun[];
}

/** Tokens that are performance notes, not lyrics — dropped before matching. */
const REPEAT_MARK = /^\(?(x|×)\s?\d+\)?$|^\(?\d+x\)?$|^\(repeat\)$/i;

/**
 * Split a line's text into whitespace-delimited raw tokens with their start
 * offsets, so chords (placed by character offset) can be attached to words.
 */
function rawTokens(text: string): { start: number; end: number; raw: string }[] {
  const out: { start: number; end: number; raw: string }[] = [];
  const re = /\S+/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) out.push({ start: m.index, end: m.index + m[0].length, raw: m[0] });
  return out;
}

function normWord(raw: string): string | null {
  if (REPEAT_MARK.test(raw)) return null;
  const t = tokenize(raw);
  return t.length > 0 ? t.join("") : null;
}

function buildStream(chart: ChordChart): ChartStream {
  const words: StreamWord[] = [];
  const instrumentals: InstrumentalRun[] = [];
  for (const section of chart.sections) {
    for (const stanza of section.stanzas) {
      let run: ChartLine[] = [];
      const flushRun = () => {
        if (run.length === 0) return;
        instrumentals.push({ beforeWord: words.length, label: section.label, lines: run });
        run = [];
      };
      for (const line of stanza) {
        const toks = rawTokens(line.text)
          .map((t) => ({ ...t, word: normWord(t.raw) }))
          .filter((t): t is typeof t & { word: string } => t.word !== null);
        if (toks.length === 0) {
          if (line.chords.length > 0) run.push(line);
          continue;
        }
        flushRun();
        const lineWords: StreamWord[] = toks.map((t) => ({ word: t.word, chords: [] }));
        for (const c of line.chords) {
          // The word the chord sits on: the last token starting at or before
          // it; a chord before the first word goes on the first word.
          let idx = 0;
          for (let i = 0; i < toks.length; i++) if (toks[i]!.start <= c.pos) idx = i;
          // A chord in the gap between two words is played on the next one.
          if (c.pos >= toks[idx]!.end && idx + 1 < toks.length) idx += 1;
          const tok = toks[idx]!;
          const isTrailingAfterLine = idx === toks.length - 1 && c.pos >= tok.end;
          const offset = isTrailingAfterLine
            ? Math.max(tok.raw.length, c.pos - tok.start)
            : Math.max(0, Math.min(c.pos - tok.start, tok.raw.length));
          lineWords[idx]!.chords.push({ offset, chord: c.chord });
        }
        words.push(...lineWords);
      }
      flushRun();
    }
  }
  return { words, instrumentals };
}

/** A slide line with its chords placed, ready to render. */
export interface AlignedLine {
  text: string;
  chords: PlacedChord[];
}

export interface AlignedSlide {
  lines: AlignedLine[];
  /** Chord-only lines played right after this slide (turnarounds, instrumentals). */
  after: { label: string; lines: ChartLine[] }[];
  /** False if the slide's words weren't found in the chart — show lyrics only. */
  matched: boolean;
}

/**
 * Where in the chart stream a slide's words appear: exact run first; failing
 * that, the window with the most words in common (lyrics edited slightly).
 */
function locate(stream: StreamWord[], target: string[], hint: number): number | null {
  const n = target.length;
  if (n === 0 || stream.length < n) return null;
  let best: number | null = null;
  let bestScore = 0;
  for (let i = 0; i + n <= stream.length; i++) {
    let score = 0;
    for (let j = 0; j < n; j++) if (stream[i + j]!.word === target[j]) score += 1;
    // Prefer the occurrence nearest the hint (the previous slide's end), so a
    // chorus written out twice with different chords picks the right copy.
    if (score > bestScore || (score === bestScore && best !== null && Math.abs(i - hint) < Math.abs(best - hint))) {
      bestScore = score;
      best = i;
    }
  }
  return bestScore / n >= 0.6 ? best : null;
}

/**
 * Place chords onto every slide of a song.
 *
 * `slides` is the resolved sequence as the projector plays it (leading blank
 * included, lyrics as shown). The synthetic blank slide (empty lyrics) at the
 * start of the song gets the chart's intro — whatever chord-only lines come
 * before the first sung word.
 */
export function alignSlides(chart: ChordChart, slides: { lyrics: string }[]): AlignedSlide[] {
  const stream = buildStream(chart);
  let hint = 0;
  return slides.map((slide) => {
    const lines = slide.lyrics.split("\n");
    if (slide.lyrics.trim() === "") {
      const intro = stream.instrumentals.filter((r) => r.beforeWord === 0);
      return { lines: [], after: intro.map(({ label, lines }) => ({ label, lines })), matched: intro.length > 0 };
    }
    const lineToks = lines.map((l) =>
      rawTokens(l)
        .map((t) => ({ ...t, word: normWord(t.raw) }))
        .filter((t): t is typeof t & { word: string } => t.word !== null),
    );
    const target = lineToks.flat().map((t) => t.word);
    const at = locate(stream.words, target, hint);
    if (at === null) {
      return { lines: lines.map((text) => ({ text, chords: [] })), after: [], matched: false };
    }
    hint = at + target.length;
    let k = at;
    const aligned = lines.map((text, li) => {
      const chords: PlacedChord[] = [];
      const rowToks = lineToks[li]!;
      rowToks.forEach((tok, ti) => {
        const sw = stream.words[k++];
        if (!sw || sw.word !== tok.word) return;
        const isLastOnLine = ti === rowToks.length - 1;
        for (const c of sw.chords) {
          const off = isLastOnLine && c.offset >= tok.raw.length
            ? c.offset
            : Math.min(c.offset, tok.raw.length);
          chords.push({ pos: tok.start + off, chord: c.chord });
        }
      });
      return { text, chords };
    });
    const end = at + target.length;
    const after = stream.instrumentals
      .filter((r) => r.beforeWord === end)
      .map(({ label, lines }) => ({ label, lines }));
    return { lines: aligned, after, matched: true };
  });
}

// --- Transposition -----------------------------------------------------------

export function transposeLines<T extends { chords: PlacedChord[] }>(
  lines: T[],
  semitones: number,
  targetKey: string | null,
): T[] {
  if (((semitones % 12) + 12) % 12 === 0) return lines;
  return lines.map((l) => ({
    ...l,
    chords: l.chords.map((c) => ({ ...c, chord: transposeChord(c.chord, semitones, targetKey) })),
  }));
}

/** The key a chart sounds in after `semitones` of transposition. */
export function displayKey(chart: ChordChart | null, semitones: number): string | null {
  if (!chart?.key) return null;
  return transposeKey(chart.key, semitones);
}

// --- Rendering helper --------------------------------------------------------

/**
 * Lay chords out over lyrics as two monospace rows. Chords never overlap: one
 * that would collide with the previous is pushed right and the lyric padded,
 * which is what a musician would pencil in.
 */
export function renderChordRows(line: AlignedLine | ChartLine): { chords: string; lyrics: string } {
  let chordRow = "";
  let lyricRow = line.text;
  let shift = 0;
  const sorted = [...line.chords].sort((a, b) => a.pos - b.pos);
  for (const c of sorted) {
    let col = c.pos + shift;
    if (chordRow.length > 0 && col < chordRow.length + 1) {
      const push = chordRow.length + 1 - col;
      // Pad the lyric at the chord's position so the words stay under it.
      const at = Math.min(c.pos + shift, lyricRow.length);
      lyricRow = lyricRow.slice(0, at) + " ".repeat(push) + lyricRow.slice(at);
      shift += push;
      col += push;
    }
    chordRow = chordRow.padEnd(col, " ") + c.chord;
  }
  return { chords: chordRow, lyrics: lyricRow };
}
