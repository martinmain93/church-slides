/**
 * "Split slide": cut one slide into two at the most natural place near its
 * middle. Used by the split button on slide cards (library song editor and
 * the setlist's slides) and by the editor's "split this crowded slide"
 * suggestion, so there is one rule for where a slide breaks.
 *
 * The rule, in order:
 *  1. Several lines: break at the line break that best halves the words,
 *     weighed by characters rather than line count, keeping line pairs
 *     together, and much preferring the end of a sentence over a comma.
 *  2. One long line: break after a sentence or clause mark (. ! ? ; : , —)
 *     in the middle half of the line, the one nearest the middle.
 *  3. Otherwise: break at the space nearest the middle.
 *  4. A single word (or nothing) can't be split.
 *
 * Rules 2 and 3 cut inside a line. A song with chords has them placed on its
 * lines, and a cut line's chords don't follow it into the second half, so
 * there the split keeps to line breaks (`midLine: false`).
 */

export interface SplitOptions {
  /** Allow cutting one long line in two (rules 2 and 3). Default true. */
  midLine?: boolean;
}

/** How much a break is preferred, as a share of the slide's length: after
 * a sentence ends far more than after a comma, so a split never leaves
 * "Was blind, but now I see." on its own when the sentence ended a line
 * earlier. */
const SENTENCE_BONUS = 0.3;
const CLAUSE_BONUS = 0.05;
/** Lines in hymns and songs go in pairs: cutting a pair costs this much. */
const COUPLET_PENALTY = 0.25;

const SENTENCE_END = /[.!?;]["'”’)]*$/;
const CLAUSE_END = /[,:—–-]["'”’)]*$/;
const CLAUSE_MARK = /[.!?;:,—–]["'”’)]*\s+/g;

function trimLines(lyrics: string): string[] {
  const lines = lyrics.split("\n").map((l) => l.trimEnd());
  while (lines.length > 0 && !lines[0]!.trim()) lines.shift();
  while (lines.length > 0 && !lines[lines.length - 1]!.trim()) lines.pop();
  return lines;
}

/**
 * Of the breaks between `lines`, the index of the line the first half ends
 * on, or null for fewer than two non-empty lines.
 */
export function splitLineIndex(lines: string[]): number | null {
  if (lines.filter((l) => l.trim()).length < 2) return null;
  const lengths = lines.map((l) => l.trim().length);
  const total = lengths.reduce((a, b) => a + b, 0) || 1;
  let best: number | null = null;
  let bestScore = Infinity;
  let left = 0;
  for (let i = 0; i < lines.length - 1; i++) {
    left += lengths[i]!;
    // Never leave a half with no words in it.
    if (left === 0 || left === total) continue;
    const imbalance = Math.abs(left - (total - left)) / total;
    const end = lines[i]!.trim();
    const breath = SENTENCE_END.test(end) ? SENTENCE_BONUS : CLAUSE_END.test(end) ? CLAUSE_BONUS : 0;
    const splitsPair = lines.length % 2 === 0 && (i + 1) % 2 === 1 ? COUPLET_PENALTY : 0;
    const score = imbalance + splitsPair - breath;
    // On a tie the later break wins: the first slide takes the extra line.
    if (score <= bestScore) {
      bestScore = score;
      best = i;
    }
  }
  return best;
}

/** Where a single line breaks: the character offset the second half starts at. */
function splitLineOffset(line: string): number | null {
  const mid = line.length / 2;
  let best: number | null = null;
  for (const m of line.matchAll(CLAUSE_MARK)) {
    const at = m.index! + m[0].length;
    if (at < line.length * 0.25 || at > line.length * 0.75) continue;
    if (best === null || Math.abs(at - mid) < Math.abs(best - mid)) best = at;
  }
  if (best !== null) return best;
  for (const m of line.matchAll(/\s+/g)) {
    const at = m.index! + m[0].length;
    if (best === null || Math.abs(at - mid) < Math.abs(best - mid)) best = at;
  }
  return best;
}

/** The slide's words as two slides, or null when it can't be split. */
export function smartSplit(lyrics: string, opts: SplitOptions = {}): [string, string] | null {
  const lines = trimLines(lyrics);
  const lineIdx = splitLineIndex(lines);
  if (lineIdx !== null) {
    return [
      trimLines(lines.slice(0, lineIdx + 1).join("\n")).join("\n"),
      trimLines(lines.slice(lineIdx + 1).join("\n")).join("\n"),
    ];
  }
  if (opts.midLine === false) return null;
  const line = lines.find((l) => l.trim())?.trim();
  if (!line) return null;
  const at = splitLineOffset(line);
  if (at === null) return null;
  const first = line.slice(0, at).trim();
  const second = line.slice(at).trim();
  return first && second ? [first, second] : null;
}

export function canSplit(lyrics: string, opts: SplitOptions = {}): boolean {
  return smartSplit(lyrics, opts) !== null;
}

/** Replace slide `idx` with its two halves, both in its section. */
export function splitAt<S extends { lyrics: string; section: object }>(
  slides: S[],
  idx: number,
  copy: (slide: S, lyrics: string) => S = (slide, lyrics) => ({ ...slide, section: { ...slide.section }, lyrics }),
  opts: SplitOptions = {},
): S[] {
  const target = slides[idx];
  if (!target) return slides;
  const halves = smartSplit(target.lyrics, opts);
  if (!halves) return slides;
  return [...slides.slice(0, idx), copy(target, halves[0]), copy(target, halves[1]), ...slides.slice(idx + 1)];
}
