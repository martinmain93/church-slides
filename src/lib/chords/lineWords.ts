/**
 * The words of a lyric line, and the lyric lines the chord model is shown.
 *
 * A chord is addressed by its word's place on the line, not by the word's
 * text: "Holy, holy, holy" has three places for a chord. The sidecar counts
 * words the same way (`sidecar/auto_presenter/chords/words.py`) — split on
 * whitespace, skip tokens with no letter or digit — so index 2 here is
 * index 2 there.
 *
 * `buildChordLines` is the one payload both the next-chord suggestion and
 * voice charting send.
 */

import { alignSlides, chartHasChords, type ChordChart, type PlacedChord } from "@/lib/chords/chart";
import { parseSong, sectionLabel } from "@/lib/parseSong";
import type { ChordSuggestLine } from "@/types/generated";

export interface LineWord {
  /** Place on the line, counting only real words. */
  index: number;
  /** The token as written, punctuation included ("holy,"). */
  text: string;
  norm: string;
  /** Where the letters start and end; a chord lands on `start`. */
  start: number;
  end: number;
  /** Where the whole token starts, leading punctuation included. */
  tokenStart: number;
}

export function normWord(word: string): string {
  return word.toLowerCase().replace(/[^a-z0-9']/g, "");
}

export function lineWords(line: string): LineWord[] {
  const out: LineWord[] = [];
  const re = /\S+/g;
  let match: RegExpExecArray | null;
  while ((match = re.exec(line)) !== null) {
    const text = match[0];
    const norm = normWord(text);
    if (!norm) continue;
    let first = 0;
    while (!/[A-Za-z0-9']/.test(text[first]!)) first++;
    let last = text.length - 1;
    while (!/[A-Za-z0-9']/.test(text[last]!)) last--;
    out.push({
      index: out.length,
      text,
      norm,
      start: match.index + first,
      end: match.index + last + 1,
      tokenStart: match.index,
    });
  }
  return out;
}

/**
 * The word a placement means. The index wins when its word matches the text;
 * otherwise the first matching word not in `avoid`, then the first match.
 */
export function findLineWord(
  line: string,
  word: string,
  index: number | undefined,
  avoid: ReadonlySet<number> = new Set(),
): LineWord | null {
  const words = lineWords(line);
  const want = normWord(word);
  const at = index ?? -1;
  if (at >= 0 && at < words.length && (!want || words[at]!.norm === want)) return words[at]!;
  if (!want) return null;
  const matches = words.filter((w) => w.norm === want);
  return matches.find((w) => !avoid.has(w.index)) ?? matches[0] ?? null;
}

/** Each chord paired with the word it belongs to: the last word starting at or before it. */
export function chordsOnWords(line: string, placed: PlacedChord[]): { word: LineWord; chord: string }[] {
  const words = lineWords(line);
  if (words.length === 0) return [];
  return placed.map((chord) => ({
    word: [...words].reverse().find((w) => w.tokenStart <= chord.pos) ?? words[0]!,
    chord: chord.chord,
  }));
}

/** One `s{slide}-l{line}` id per non-empty lyric line. */
export function parseLineId(lineId: string): { slideIdx: number; lineIdx: number } | null {
  const match = /^s(\d+)-l(\d+)$/.exec(lineId);
  return match ? { slideIdx: Number(match[1]), lineIdx: Number(match[2]) } : null;
}

/**
 * Every lyric line with the chords already on it. Past `maxLines`, a window
 * that starts a little before `focusSlide` — the model sees where you are
 * charting, not just the top of a long song.
 */
export function buildChordLines(
  body: string,
  chart: ChordChart | null,
  options: { maxLines?: number; focusSlide?: number } = {},
): ChordSuggestLine[] {
  const slides = parseSong(body).slides;
  const aligned =
    chart && chartHasChords(chart)
      ? alignSlides(chart, [{ lyrics: "" }, ...slides.map((slide) => ({ lyrics: slide.lyrics }))]).slice(1)
      : null;
  const lines: (ChordSuggestLine & { slideIdx: number })[] = [];
  slides.forEach((slide, si) => {
    const section = sectionLabel(slide.section);
    slide.lyrics.split("\n").forEach((text, li) => {
      const line = text.trim();
      if (!line) return;
      const pairs = chordsOnWords(text, aligned?.[si]?.lines[li]?.chords ?? []);
      const words = lineWords(text);
      const chorded = new Set(pairs.map((pair) => pair.word.index));
      lines.push({
        slideIdx: si,
        line_id: `s${si}-l${li}`,
        section,
        line,
        chords: pairs.map((pair) => pair.chord),
        chorded_words: pairs.map((pair) => pair.word.text),
        chorded_word_indexes: pairs.map((pair) => pair.word.index),
        role: words.length > 0 && words.every((w) => chorded.has(w.index)) ? "charted" : "target",
      });
    });
  });
  const max = options.maxLines ?? lines.length;
  let start = 0;
  if (lines.length > max && options.focusSlide !== undefined) {
    const focus = lines.findIndex((line) => line.slideIdx >= options.focusSlide!);
    start = Math.max(0, Math.min((focus < 0 ? lines.length : focus) - Math.floor(max / 3), lines.length - max));
  }
  return lines.slice(start, start + max).map(({ slideIdx: _slideIdx, ...line }) => line);
}
