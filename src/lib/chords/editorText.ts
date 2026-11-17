/**
 * The song editor's "chords" view: natural two-line Chords-over-Lyrics
 * (`G` on the line above `Amazing`) plus backwards-compatible inline `[G]`
 * brackets when someone types or pastes ChordPro.
 *
 * The body stays the source of truth for the projector and the AI. Turning
 * chords on merges the chart into the body as chord lines above each lyric
 * line; every edit is split back into a plain body (chord lines and any
 * inline `[G]` tags removed — byte-identical when only chords changed) and
 * a fresh chart read from the annotated text.
 *
 * Chord-only lines (an intro, a turnaround) are shown on lines of their own.
 * They're chart-only, so splitting drops them from the body — along with a
 * section header like `[Intro]` whose section held nothing but chords.
 */

import { alignSlides, chartHasChords, CHART_VERSION, type ChordChart, type PlacedChord } from "@/lib/chords/chart";
import {
  chordToNashville,
  isChordLine,
  isChordToken,
  keyPrefersFlats,
  majorTonicPitchClass,
  parseChord,
  parseNashvilleChord,
  respellChord,
  semitonesBetween,
  transposeChord,
  transposeKey,
} from "@/lib/chords/chord";
import { parseTextChart } from "@/lib/chords/import/text";

const SECTION_RE = /^\s*\[([^\]]+)\]\s*$/;
const INLINE = /\[([^\]\n]{1,24})\]/g;

/** A `[verse 1]` header — and not a `[C]` chord standing alone. */
function isHeader(line: string): boolean {
  const m = SECTION_RE.exec(line);
  return m !== null && !isChordToken(m[1]!.trim());
}

/** Normalize a mid-typing slash chord like `G/` to `G` so it stays a valid chord line while typing `G/B`. */
function normalizeTypingChordLine(line: string): string {
  return line.replace(/\b([A-G][#b♯♭]?[^\s/]*)\/(?=\s|$)/g, (_whole, base: string) =>
    isChordToken(base) ? `${base} ` : _whole,
  );
}

/** A line of nothing but chords (either two-line `G  C  D` or bracketed `[G] [C] [D]`). */
function isChordOnly(line: string): boolean {
  const normalized = normalizeTypingChordLine(line);
  if (isChordLine(normalized)) return true;
  let any = false;
  const rest = normalized.replace(INLINE, (whole, sym: string) => {
    if (!isChordToken(sym.trim())) return whole;
    any = true;
    return "";
  });
  return any && /^[\s|/.:x\d]*$/i.test(rest);
}

function stripChords(line: string): string {
  return line.replace(INLINE, (whole, sym: string) => (isChordToken(sym.trim()) ? "" : whole));
}

/** Place chords on a row above a lyric line at their character columns. */
export function formatChordLineAbove(chords: PlacedChord[]): string {
  let row = "";
  for (const c of [...chords].sort((a, b) => a.pos - b.pos)) {
    const col = row.length === 0 ? Math.max(0, c.pos) : Math.max(c.pos, row.length + 1);
    row = row.padEnd(col, " ") + c.chord;
  }
  return row;
}

function extractStanzas(lines: string[]): number[][] {
  const stanzas: number[][] = [];
  let cur: number[] = [];
  lines.forEach((l, i) => {
    if (l.trim() === "" || isHeader(l)) {
      if (cur.length) stanzas.push(cur);
      cur = [];
    } else cur.push(i);
  });
  if (cur.length) stanzas.push(cur);
  return stanzas;
}

const INSTRUMENTAL_LABEL_RE =
  /^(intro|outro|ending|end|instrumental|interlude|turnaround|turn|vamp|coda|break|solo|link)\b/i;

function formatAfterRuns(after: { label: string; lines: { chords: PlacedChord[] }[] }[]): string[] {
  const out: string[] = [];
  for (const r of after) {
    const chordLines = r.lines
      .map((l) => l.chords.map((c) => c.chord).join("  "))
      .filter((s) => s.trim() !== "");
    if (chordLines.length === 0) continue;
    if (INSTRUMENTAL_LABEL_RE.test(r.label.trim())) {
      out.push("", `[${r.label.trim()}]`, ...chordLines);
    } else {
      out.push(...chordLines);
    }
  }
  return out;
}

/** The body with the chart's chords written on lines above the lyrics. */
export function toChordText(body: string, chart: ChordChart | null): string {
  if (!chart || !chartHasChords(chart)) return body;
  const lines = body.split("\n");
  const stanzas = extractStanzas(lines);

  // Slot 0 is an empty "slide" so the chart's intro comes back too.
  const aligned = alignSlides(chart, [
    { lyrics: "" },
    ...stanzas.map((st) => ({ lyrics: st.map((i) => lines[i]).join("\n") })),
  ]);

  const above = new Map<number, string>();
  const after = new Map<number, string[]>();
  stanzas.forEach((st, si) => {
    const a = aligned[si + 1]!;
    st.forEach((lineIdx, k) => {
      const placed = a.lines[k];
      if (placed && placed.chords.length > 0) {
        above.set(lineIdx, formatChordLineAbove(placed.chords));
      }
    });
    const runs = formatAfterRuns(a.after);
    if (runs.length) after.set(st[st.length - 1]!, runs);
  });

  const result: string[] = [];
  for (const r of aligned[0]!.after) {
    const chordLines = r.lines
      .map((l) => l.chords.map((c) => c.chord).join("  "))
      .filter((s) => s.trim() !== "");
    if (chordLines.length > 0) {
      result.push(`[${r.label.trim() || "Intro"}]`, ...chordLines, "");
    }
  }
  lines.forEach((l, i) => {
    const chordRow = above.get(i);
    if (chordRow !== undefined) result.push(chordRow);
    result.push(l);
    for (const run of after.get(i) ?? []) result.push(run);
  });
  return result.join("\n");
}

/** Split chord text back into the plain body and a chart (null if no chords). */
export function fromChordText(
  text: string,
  keep: Pick<ChordChart, "key" | "tempo" | "time"> | null,
): { body: string; chart: ChordChart | null } {
  const lines = text.split("\n");
  // Drop chord-only lines, and headers of sections that held only those.
  const drop = new Set<number>();
  for (let i = 0; i < lines.length; i++) if (isChordOnly(lines[i]!)) drop.add(i);
  for (let i = 0; i < lines.length; i++) {
    if (!isHeader(lines[i]!)) continue;
    let j = i + 1;
    let chordLines = 0;
    let lyricLines = 0;
    while (j < lines.length && !isHeader(lines[j]!)) {
      if (drop.has(j)) chordLines++;
      else if (lines[j]!.trim() !== "") lyricLines++;
      j++;
    }
    if (chordLines > 0 && lyricLines === 0) {
      drop.add(i);
      // …and the blank line that separated it from what follows (or preceded it at EOF).
      if (j > i + 1 && lines[j - 1]!.trim() === "") {
        drop.add(j - 1);
      } else if (i > 0 && lines[i - 1]!.trim() === "") {
        drop.add(i - 1);
      }
    }
  }
  const body = lines
    .filter((_, i) => !drop.has(i))
    .map((l) => (isHeader(l) ? l : stripChords(l)))
    .join("\n");

  const normalizedForParse = lines
    .map((l, i) => (drop.has(i) ? normalizeTypingChordLine(l) : l))
    .join("\n");
  const [song] = parseTextChart(normalizedForParse, "", { bodyOnly: true });
  const chart = song && chartHasChords(song.chart)
    ? { ...song.chart, version: CHART_VERSION, key: keep?.key ?? song.chart.key, tempo: keep?.tempo ?? null, time: keep?.time ?? null }
    : null;
  return { body, chart };
}

/**
 * Place, change, or remove a chord on a word or syllable of a slide in `SongPreview`,
 * returning the updated `chordText` and `chart`.
 *
 * Notice we mutate a structured `ChordChart` directly (via `parseTextChart` on the
 * current body + existing chords) instead of round-tripping through 2-line ASCII
 * `formatChordLineAbove`, so placing multiple chords across the syllables of a word
 * (like `A`, `A2sus`, `A`, `D/A`, `A` on `"Alleluia"`) or multiple chords on a single
 * syllable (`"G Gsus2"`) never pushes later chords past `line.text.length` into
 * trailing chords!
 */
export function setWordChordInBody(
  body: string,
  chart: ChordChart | null,
  slideLyrics: string,
  lineIdxInSlide: number,
  wordStart: number,
  wordEnd: number,
  chordPos: number,
  nextChord: string | null,
): { chordText: string; chart: ChordChart | null } {
  const lines = body.split("\n");
  const stanzas = extractStanzas(lines);
  const stanzaLyrics = stanzas.map((st) => ({ lyrics: st.map((i) => lines[i]).join("\n") }));
  const aligned = chart && chartHasChords(chart)
    ? alignSlides(chart, [{ lyrics: "" }, ...stanzaLyrics])
    : null;

  const above = new Map<number, PlacedChord[]>();
  stanzas.forEach((st, si) => {
    const a = aligned ? aligned[si + 1]! : null;
    st.forEach((lineIdx, k) => {
      const placed = a?.lines[k]?.chords ? [...a.lines[k]!.chords] : [];
      above.set(lineIdx, placed);
    });
  });

  // Update every stanza in `body` whose lyrics match `slideLyrics` (typically 1).
  const trimmedTarget = slideLyrics.trim();
  stanzas.forEach((st, si) => {
    if (stanzaLyrics[si]!.lyrics.trim() !== trimmedTarget) return;
    const targetLineIdx = st[lineIdxInSlide];
    if (targetLineIdx === undefined) return;
    const current = (above.get(targetLineIdx) ?? []).filter(
      (c) => c.pos < wordStart || c.pos >= wordEnd,
    );
    const clean = nextChord?.trim() ?? "";
    if (clean) {
      const chordTokens = clean.split(/\s+/).filter(Boolean);
      for (const tok of chordTokens) {
        current.push({ pos: chordPos, chord: tok });
      }
      current.sort((a, b) => a.pos - b.pos);
    }
    above.set(targetLineIdx, current);
  });

  return buildUpdatedChartAndText(lines, stanzas, aligned, above, chart);
}

function buildUpdatedChartAndText(
  lines: string[],
  stanzas: number[][],
  aligned: ReturnType<typeof alignSlides> | null,
  above: Map<number, PlacedChord[]>,
  keep: Pick<ChordChart, "key" | "tempo" | "time"> | null,
): { chordText: string; chart: ChordChart | null } {
  // Build inline-bracketed text `[G]Al[Gsus2]le[C]luia` for `parseTextChart` so exact
  // character offsets inside words are preserved even when chords are wider than syllables!
  const inlineLines: string[] = [];
  if (aligned) {
    for (const r of aligned[0]!.after) {
      const chordLines = r.lines
        .map((l) => l.chords.map((c) => c.chord).join("  "))
        .filter((s) => s.trim() !== "");
      if (chordLines.length > 0) {
        inlineLines.push(`[${r.label.trim() || "Intro"}]`, ...chordLines, "");
      }
    }
  }

  const after = new Map<number, string[]>();
  if (aligned) {
    stanzas.forEach((st, si) => {
      const a = aligned[si + 1]!;
      const runs = formatAfterRuns(a.after);
      if (runs.length) after.set(st[st.length - 1]!, runs);
    });
  }

  lines.forEach((l, i) => {
    const chords = above.get(i);
    if (chords && chords.length > 0 && !isHeader(l)) {
      const sorted = [...chords].sort((a, b) => a.pos - b.pos);
      let annotated = "";
      let cursor = 0;
      for (const c of sorted) {
        if (c.pos <= l.length) {
          const clampPos = Math.max(cursor, Math.min(c.pos, l.length));
          annotated += l.slice(cursor, clampPos) + `[${c.chord}]`;
          cursor = clampPos;
        }
      }
      annotated += l.slice(cursor);
      const trailing = sorted.filter((c) => c.pos > l.length);
      if (trailing.length > 0) {
        annotated += " " + trailing.map((c) => `[${c.chord}]`).join(" ");
      }
      inlineLines.push(annotated);
    } else {
      inlineLines.push(l);
    }
    for (const run of after.get(i) ?? []) inlineLines.push(run);
  });

  const [song] = parseTextChart(inlineLines.join("\n"), "", { bodyOnly: true });
  const nextChart: ChordChart | null =
    song && chartHasChords(song.chart)
      ? {
          ...song.chart,
          version: CHART_VERSION,
          key: keep?.key ?? song.chart.key,
          tempo: keep?.tempo ?? null,
          time: keep?.time ?? null,
        }
      : null;
  const body = lines.join("\n");
  return {
    chordText: toChordText(body, nextChart),
    chart: nextChart,
  };
}

/**
 * Move a chord from `(fromLineIdx, fromPos)` to `(toLineIdx, toPos)` within a
 * slide in `SongPreview`, supporting both word-to-word and arbitrary character
 * placement inside a word.
 */
export function moveChordInBody(
  body: string,
  chart: ChordChart | null,
  slideLyrics: string,
  fromLineIdx: number,
  fromPos: number,
  toLineIdx: number,
  toPos: number,
  chordSymbol: string,
): { chordText: string; chart: ChordChart | null } {
  const lines = body.split("\n");
  const stanzas = extractStanzas(lines);
  const stanzaLyrics = stanzas.map((st) => ({ lyrics: st.map((i) => lines[i]).join("\n") }));
  const aligned = chart && chartHasChords(chart)
    ? alignSlides(chart, [{ lyrics: "" }, ...stanzaLyrics])
    : null;

  const above = new Map<number, PlacedChord[]>();
  stanzas.forEach((st, si) => {
    const a = aligned ? aligned[si + 1]! : null;
    st.forEach((lineIdx, k) => {
      const placed = a?.lines[k]?.chords ? [...a.lines[k]!.chords] : [];
      above.set(lineIdx, placed);
    });
  });

  const trimmedTarget = slideLyrics.trim();
  stanzas.forEach((st, si) => {
    if (stanzaLyrics[si]!.lyrics.trim() !== trimmedTarget) return;
    const srcLine = st[fromLineIdx];
    const dstLine = st[toLineIdx];
    if (srcLine === undefined || dstLine === undefined) return;
    const srcChords = [...(above.get(srcLine) ?? [])];
    const movingTokens = chordSymbol.trim().split(/\s+/).filter(Boolean);
    for (const sym of movingTokens) {
      const removeIdx = srcChords.findIndex((c) => c.pos === fromPos && c.chord === sym);
      if (removeIdx !== -1) {
        srcChords.splice(removeIdx, 1);
      } else {
        const fallbackIdx = srcChords.findIndex((c) => c.pos === fromPos);
        if (fallbackIdx !== -1) srcChords.splice(fallbackIdx, 1);
      }
    }
    above.set(srcLine, srcChords);

    const dstChords = (srcLine === dstLine ? srcChords : [...(above.get(dstLine) ?? [])]).filter(
      (c) => c.pos !== toPos,
    );
    for (const sym of movingTokens) {
      dstChords.push({ pos: Math.max(0, toPos), chord: sym });
    }
    dstChords.sort((a, b) => a.pos - b.pos);
    above.set(dstLine, dstChords);
  });

  return buildUpdatedChartAndText(lines, stanzas, aligned, above, chart);
}

/** Respell every chord (and key) in a chart and editor text to sharps or flats. */
export function respellChartAndText(
  body: string,
  chart: ChordChart | null,
  preferFlats: boolean,
): { chordText: string; chart: ChordChart | null } {
  if (!chart) return { chordText: body, chart: null };
  const nextChart: ChordChart = {
    ...chart,
    key: chart.key ? respellChord(chart.key, preferFlats) : null,
    sections: chart.sections.map((sec) => ({
      ...sec,
      stanzas: sec.stanzas.map((stanza) =>
        stanza.map((line) => ({
          ...line,
          chords: line.chords.map((c) => ({
            ...c,
            chord: respellChord(c.chord, preferFlats),
          })),
        })),
      ),
    })),
  };
  return {
    chordText: toChordText(body, nextChart),
    chart: nextChart,
  };
}

const MAJOR_PALETTE_IN_C = ["C", "Dm", "Em", "F", "G", "Am", "G/B", "F2", "Csus4"];
const MINOR_PALETTE_IN_AM = ["Am", "C", "Dm", "Em", "F", "G", "E7", "Am7", "Fmaj7"];

/** Quick diatonic chord choices for a song's key (defaults to G major when unset). */
export function diatonicChordsForKey(
  key: string | null | undefined,
  preferFlats?: boolean,
): string[] {
  const effectiveKey = key && parseChord(key) ? key : "G";
  const parsed = parseChord(effectiveKey)!;
  const isMinor = /^(m(?!aj)|min|-)/.test(parsed.suffix);
  const raw = isMinor
    ? MINOR_PALETTE_IN_AM.map((c) =>
        transposeChord(c, semitonesBetween("A", parsed.rootText) ?? 0, effectiveKey),
      )
    : MAJOR_PALETTE_IN_C.map((c) =>
        transposeChord(c, semitonesBetween("C", parsed.rootText) ?? 0, effectiveKey),
      );
  return preferFlats !== undefined ? raw.map((c) => respellChord(c, preferFlats)) : raw;
}

export interface DiatonicChordItem {
  chord: string;
  number: string;
  shortcutDigit?: string;
}

/**
 * Diatonic chord palette paired with Nashville Number System labels (`1`, `2m`, `3m`, `4`, `5`, `6m`, `1/3`, ...).
 */
export function diatonicChordItemsForKey(
  key: string | null | undefined,
  preferFlats?: boolean,
): DiatonicChordItem[] {
  const effectiveKey = key && parseChord(key) ? key : "G";
  const chords = diatonicChordsForKey(effectiveKey, preferFlats);
  return chords.map((chord, idx) => ({
    chord,
    number: chordToNashville(chord, effectiveKey),
    shortcutDigit: idx < 7 ? String(idx + 1) : undefined,
  }));
}

/**
 * Resolve user chord input — supports both standard chord names (`G`, `D/F#`, `Cmaj7`, `ebm7`),
 * Nashville Numbers (`1`, `4`, `5`, `6m`, `2m7`, `1/3`, `5/7`, `b7`, `4sus2`), and
 * space-separated multi-chord sequences (`G Gsus2`, `1 4/1`).
 */
export function resolveChordInput(
  rawInput: string,
  key: string | null | undefined,
  preferFlats?: boolean,
): string {
  const trimmed = rawInput.trim();
  if (!trimmed) return "";
  if (/\s/.test(trimmed)) {
    return trimmed
      .split(/\s+/)
      .filter(Boolean)
      .map((part) => resolveSingleChordToken(part, key, preferFlats))
      .join(" ");
  }
  return resolveSingleChordToken(trimmed, key, preferFlats);
}

function resolveSingleChordToken(
  trimmed: string,
  key: string | null | undefined,
  preferFlats?: boolean,
): string {
  const effectiveKey = key && parseChord(key) ? key : "G";
  const nashville = parseNashvilleChord(trimmed, effectiveKey, preferFlats);
  if (nashville) return nashville;

  // Normalize lowercase roots like "em7" -> "Em7" or "f#m" -> "F#m"
  const normalized = trimmed.replace(/^([a-g])([#b♯♭]?)/, (_, r: string, acc: string) =>
    r.toUpperCase() + acc.replace("♯", "#").replace("♭", "b"),
  );
  if (parseChord(normalized)) {
    return preferFlats !== undefined ? respellChord(normalized, preferFlats) : normalized;
  }
  return trimmed;
}

/**
 * Transpose all chords in `chart` from its current key to `targetKey`, returning
 * both the updated `chordText` and `chart`.
 */
export function transposeChartAndText(
  body: string,
  chart: ChordChart | null,
  targetKey: string,
  preferFlats?: boolean,
): { chordText: string; chart: ChordChart | null } {
  if (!chart) return { chordText: body, chart: null };
  const fromKey = chart.key && parseChord(chart.key) ? chart.key : "G";
  const toParsed = parseChord(targetKey);
  if (!toParsed) {
    return { chordText: toChordText(body, { ...chart, key: targetKey }), chart: { ...chart, key: targetKey } };
  }
  // Use the roots directly (or major tonics when both are major/minor) so G -> A is +2 semitones.
  const fromParsed = parseChord(fromKey)!;
  const fromIsMinor = /^(m(?!aj)|min|-)/.test(fromParsed.suffix);
  const toIsMinor = /^(m(?!aj)|min|-)/.test(toParsed.suffix);
  const delta =
    fromIsMinor === toIsMinor
      ? (semitonesBetween(fromParsed.rootText, toParsed.rootText) ?? 0)
      : (((majorTonicPitchClass(targetKey) ?? toParsed.root) -
          (majorTonicPitchClass(fromKey) ?? fromParsed.root) +
          12) %
        12);

  const effectiveFlats = preferFlats ?? keyPrefersFlats(targetKey);
  const nextKey = respellChord(targetKey, effectiveFlats);
  const nextChart: ChordChart = {
    ...chart,
    key: nextKey,
    sections: chart.sections.map((sec) => ({
      ...sec,
      stanzas: sec.stanzas.map((stanza) =>
        stanza.map((line) => ({
          ...line,
          chords: line.chords.map((c) => ({
            ...c,
            chord: respellChord(transposeChord(c.chord, delta, nextKey), effectiveFlats),
          })),
        })),
      ),
    })),
  };
  return {
    chordText: toChordText(body, nextChart),
    chart: nextChart,
  };
}

/**
 * Transpose the song's key and all chords by `semitones` (e.g. -1 or +1).
 */
export function stepTransposeChartAndText(
  body: string,
  chart: ChordChart | null,
  semitones: number,
): { chordText: string; chart: ChordChart | null; nextKey: string; preferFlats: boolean } {
  const currentKey = chart?.key && parseChord(chart.key) ? chart.key : "G";
  const nextKey = transposeKey(currentKey, semitones);
  const preferFlats = keyPrefersFlats(nextKey);
  if (!chart) {
    return { chordText: body, chart: null, nextKey, preferFlats };
  }
  const nextChart: ChordChart = {
    ...chart,
    key: nextKey,
    sections: chart.sections.map((sec) => ({
      ...sec,
      stanzas: sec.stanzas.map((stanza) =>
        stanza.map((line) => ({
          ...line,
          chords: line.chords.map((c) => ({
            ...c,
            chord: respellChord(transposeChord(c.chord, semitones, nextKey), preferFlats),
          })),
        })),
      ),
    })),
  };
  return {
    chordText: toChordText(body, nextChart),
    chart: nextChart,
    nextKey,
    preferFlats,
  };
}

/**
 * Add, update, or remove an instrumental chord section (`Intro` at slot 0, or
 * `Turnaround` / `Interlude` / `Outro` after a lyric stanza).
 * Pass `chords = []` to remove that instrumental run.
 */
export function setInstrumentalChordsInBody(
  body: string,
  chart: ChordChart | null,
  slotIdx: number,
  runIdxInSlot: number,
  label: string,
  chords: string[],
): { chordText: string; chart: ChordChart | null } {
  const lines = body.split("\n");
  const stanzas = extractStanzas(lines);
  const stanzaLyrics = stanzas.map((st) => ({
    lyrics: st.map((i) => lines[i]).join("\n"),
  }));

  const aligned =
    chart && chartHasChords(chart)
      ? alignSlides(chart, [{ lyrics: "" }, ...stanzaLyrics])
      : Array.from({ length: stanzas.length + 1 }, () => ({
          lines: [] as { text: string; chords: PlacedChord[] }[],
          after: [] as { label: string; lines: { text: string; chords: PlacedChord[] }[] }[],
        }));

  const cleanChords = chords.map((c) => c.trim()).filter(Boolean);
  let pos = 0;
  const placedChords: PlacedChord[] = cleanChords.map((chord) => {
    const p = { pos, chord };
    pos += chord.length + 2;
    return p;
  });

  const targetSlot = aligned[slotIdx];
  if (targetSlot) {
    const nextAfter = [...targetSlot.after];
    if (cleanChords.length === 0) {
      if (runIdxInSlot >= 0 && runIdxInSlot < nextAfter.length) {
        nextAfter.splice(runIdxInSlot, 1);
      }
    } else {
      const nextRun = {
        label: label.trim() || (slotIdx === 0 ? "Intro" : "Turnaround"),
        lines: [{ text: "", chords: placedChords }],
      };
      if (runIdxInSlot >= 0 && runIdxInSlot < nextAfter.length) {
        nextAfter[runIdxInSlot] = nextRun;
      } else {
        nextAfter.push(nextRun);
      }
    }
    aligned[slotIdx] = { ...targetSlot, after: nextAfter };
  }

  const above = new Map<number, PlacedChord[]>();
  stanzas.forEach((st, si) => {
    const a = aligned[si + 1];
    st.forEach((lineIdx, k) => {
      const placed = a?.lines[k];
      if (placed && placed.chords.length > 0) {
        above.set(lineIdx, placed.chords.map((c) => ({ ...c })));
      }
    });
  });

  return buildUpdatedChartAndText(
    lines,
    stanzas,
    aligned as ReturnType<typeof alignSlides>,
    above,
    chart,
  );
}
