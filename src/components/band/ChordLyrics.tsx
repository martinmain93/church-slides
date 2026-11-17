/**
 * Chords above lyrics that wrap like ordinary text.
 *
 * The old layout printed two monospace rows (chords, then lyrics) that
 * couldn't wrap, so a long line ran off a phone's screen — worse at larger
 * text sizes. Here each chord is tied to the piece of lyric it's played on,
 * and whole words wrap: a word is never split between its syllables, so a
 * chord never ends up on a different line from its syllable.
 */

import type { PlacedChord } from "@/lib/chords/chart";

interface Piece {
  chord: string | null;
  text: string;
}

/**
 * Break a line into words (each keeping its trailing space), and each word
 * into pieces that start where a chord does. Exported for tests.
 */
export function chordWords(text: string, chords: PlacedChord[]): Piece[][] {
  const sorted = [...chords].sort((a, b) => a.pos - b.pos);
  // A chord past the end of the text still needs somewhere to sit.
  const padded = sorted.length && sorted[sorted.length - 1]!.pos >= text.length ? text + " " : text;
  const words: Piece[][] = [];
  const re = /\S+\s*|\s+/g;
  let m: RegExpExecArray | null;
  let ci = 0;
  while ((m = re.exec(padded))) {
    const start = m.index;
    const end = start + m[0].length;
    const word: Piece[] = [];
    let cursor = start;
    // Chords before this word's first character (e.g. at a leading space) go on it.
    while (ci < sorted.length && sorted[ci]!.pos < end) {
      const pos = Math.max(sorted[ci]!.pos, start);
      if (pos > cursor) word.push({ chord: null, text: padded.slice(cursor, pos) });
      // Several chords on one spot: stack them into one label.
      const here: string[] = [];
      while (ci < sorted.length && Math.max(sorted[ci]!.pos, start) === pos) here.push(sorted[ci++]!.chord);
      const next = ci < sorted.length && sorted[ci]!.pos < end ? Math.max(sorted[ci]!.pos, start) : end;
      word.push({ chord: here.join(" "), text: padded.slice(pos, next) });
      cursor = next;
    }
    if (cursor < end) word.push({ chord: null, text: padded.slice(cursor, end) });
    words.push(word);
  }
  // Chords beyond even the padded end.
  if (ci < sorted.length) words.push([{ chord: sorted.slice(ci).map((c) => c.chord).join(" "), text: "" }]);
  return words;
}

export function ChordLyrics({
  text,
  chords,
  showChords = true,
  chordClassName = "font-bold text-sky-400",
}: {
  text: string;
  chords: PlacedChord[];
  showChords?: boolean;
  chordClassName?: string;
}) {
  const withChords = showChords && chords.length > 0;
  if (!withChords) {
    return text === "" ? null : <div className="whitespace-pre-wrap break-words">{text}</div>;
  }
  // A chords-only line (intro, turnaround): the chords, spaced out, wrapping.
  if (text.trim() === "") {
    return (
      <div className={`flex flex-wrap gap-x-4 ${chordClassName}`}>
        {chords.map((c, i) => (
          <span key={i}>{c.chord}</span>
        ))}
      </div>
    );
  }
  return (
    <div className="flex flex-wrap items-end" data-testid="chord-line">
      {chordWords(text, chords).map((word, wi) => (
        <span key={wi} className="inline-flex items-end">
          {word.map((p, pi) => (
            <span key={pi} className="inline-flex flex-col">
              {/* Padding only under a chord, so a chord wider than its
                  syllable pushes the next one along instead of touching it. */}
              <span className={`min-h-[1.25em] whitespace-pre leading-tight ${p.chord ? `pr-[0.35em] ${chordClassName}` : ""}`}>
                {p.chord ?? "\u200b"}
              </span>
              <span className="whitespace-pre leading-snug">{p.text}</span>
            </span>
          ))}
        </span>
      ))}
    </div>
  );
}
