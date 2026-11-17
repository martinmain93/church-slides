/**
 * Unified AI / Smart Suggestion & "Cursor Tab" Next-Edit Engine for SongEditor.
 *
 * Powers both:
 * 1. Explicit "Suggest" button (`computeSongSuggestions` with `mode: "all"`) —
 *    scans the song for missing cross-verse chords, harmonic progression patterns,
 *    likely OCR/out-of-key chord typos, over-long projector slides needing breath splits,
 *    OCR hyphenation / lyric ripple fixes, and standard worship arrangement order completion.
 * 2. Reactive "Cursor Tab" next-edit prediction (`computeSongSuggestions` with `mode: "reactive"`) —
 *    proposes the next edits based on the user's most recent action (placing a chord,
 *    splitting a slide, or editing a lyric line). The UI highlights the immediate next
 *    chord and shows later chord predictions faded.
 */

import { alignSlides, chartHasChords, type ChordChart } from "@/lib/chords/chart";
import {
  keyPrefersFlats,
  majorTonicPitchClass,
  parseChord,
  respellChord,
} from "@/lib/chords/chord";
import {
  diatonicChordsForKey,
  fromChordText,
  setWordChordInBody,
  toChordText,
} from "@/lib/chords/editorText";
import { findLineWord, parseLineId } from "@/lib/chords/lineWords";
import { parseSong, sectionLabel, type ParsedSection } from "@/lib/parseSong";
import { formatSlideOrder, parseSlideOrder, slidesToBody, type OverrideSlide } from "@/lib/slideSequence";
import type { ChordPlacementInfo } from "@/types/generated";

export type SuggestionCategory =
  | "chord"
  | "chord-fix"
  | "slide-split"
  | "lyric-fix"
  | "arrangement";

export interface SongSuggestion {
  id: string;
  category: SuggestionCategory;
  /** Short human label for the review bar, e.g. `C2 on "stand" (Verse 2)` */
  label: string;
  /** Reason shown in tooltip, e.g. `Mirrored from Verse 1` */
  reason: string;
  /** Slide index (0-based into `parseSong(body).slides`) if tied to a slide card */
  slideIdx?: number;
  /** Line index (0-based within the slide) if tied to a specific line */
  lineIdx?: number;
  /** For `chord` and `chord-fix`: character offset on `lineIdx` where the chord lands */
  chordPos?: number;
  /** Word start & end offsets on `lineIdx` */
  wordStart?: number;
  wordEnd?: number;
  /** Suggested chord symbol (e.g. `"G"`, `"C2"`, `"F#m"`) */
  chord?: string;
  /** Existing chord being replaced (for `chord-fix`) */
  replacedChord?: string;
  /** For `slide-split`: line index after which to split the slide into 2 cards (0-based, splits after `splitAfterLineIdx`) */
  splitAfterLineIdx?: number;
  /** For `lyric-fix`: replacement lyrics for `slideIdx` */
  nextSlideLyrics?: string;
  /** Highlighted snippet describing the lyric change */
  lyricDiffPreview?: { from: string; to: string };
  /** For `arrangement`: extra sections to append to the arrangement order */
  appendOrderSections?: ParsedSection[];
  /** Formatted string for the resulting `slideOrder` when accepted */
  nextSlideOrder?: string;
  /** For a model phrase: Tab accepts every suggestion that shares this id. */
  phraseGroup?: string;
}

export interface EditContext {
  kind: "chord" | "slide-split" | "lyric";
  slideIdx: number;
  lineIdx?: number;
  chordPos?: number;
  chord?: string | null;
}

export interface SuggestionSnapshot {
  body: string;
  chordText: string;
  chart: ChordChart | null;
  slideOrder: string;
  preferFlats: boolean;
}

interface WordSpan {
  word: string;
  norm: string;
  start: number;
  end: number;
}

function tokenizeWords(line: string): WordSpan[] {
  const out: WordSpan[] = [];
  const re = /\S+/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(line)) !== null) {
    const word = m[0];
    const norm = word.toLowerCase().replace(/[^a-z0-9']/g, "");
    out.push({
      word,
      norm,
      start: m.index,
      end: m.index + word.length,
    });
  }
  return out;
}

/**
 * Given a target line and a relative position `[0..1]` (or word index ratio) from a
 * source line, snap to the nearest word start in the target line so mirrored chords
 * always land cleanly on a word onset.
 */
function snapRelativePosToWord(
  sourceLine: string,
  sourcePos: number,
  targetLine: string,
): { wordStart: number; wordEnd: number; chordPos: number; wordText: string } | null {
  const targetWords = tokenizeWords(targetLine);
  if (targetWords.length === 0) return null;

  const sourceWords = tokenizeWords(sourceLine);
  if (sourceWords.length === 0) {
    const first = targetWords[0]!;
    return { wordStart: first.start, wordEnd: first.end, chordPos: first.start, wordText: first.word };
  }

  // Find which word index in `sourceWords` owns or is closest to `sourcePos`
  let srcWordIdx = 0;
  let bestSrcDist = Infinity;
  for (let i = 0; i < sourceWords.length; i++) {
    const w = sourceWords[i]!;
    const dist =
      sourcePos >= w.start && sourcePos <= w.end
        ? 0
        : Math.min(Math.abs(sourcePos - w.start), Math.abs(sourcePos - w.end));
    if (dist < bestSrcDist) {
      bestSrcDist = dist;
      srcWordIdx = i;
    }
  }

  // First: if the same normalized word exists at a similar position in targetWords, snap to it
  const srcNorm = sourceWords[srcWordIdx]!.norm;
  const ratio = sourceWords.length === 1 ? 0 : srcWordIdx / (sourceWords.length - 1);
  const expectedTargetIdx = Math.round(ratio * (targetWords.length - 1));

  if (srcNorm.length >= 3) {
    for (const delta of [0, -1, 1]) {
      const candIdx = expectedTargetIdx + delta;
      if (candIdx >= 0 && candIdx < targetWords.length && targetWords[candIdx]!.norm === srcNorm) {
        const w = targetWords[candIdx]!;
        return { wordStart: w.start, wordEnd: w.end, chordPos: w.start, wordText: w.word };
      }
    }
  }

  const clampedIdx = Math.max(0, Math.min(targetWords.length - 1, expectedTargetIdx));
  const targetWord = targetWords[clampedIdx]!;
  return {
    wordStart: targetWord.start,
    wordEnd: targetWord.end,
    chordPos: targetWord.start,
    wordText: targetWord.word,
  };
}

/**
 * Identical lyric lines (same words, in order). Used for a later copy of a
 * finished line, such as a second chorus. A near-match is not enough.
 */
function linesAreExactCopy(lineA: string, lineB: string): boolean {
  const wordsA = tokenizeWords(lineA).map((w) => w.norm).filter(Boolean);
  const wordsB = tokenizeWords(lineB).map((w) => w.norm).filter(Boolean);
  return wordsA.length > 0 && wordsA.join(" ") === wordsB.join(" ");
}

/**
 * Check if two lyric lines are the same (or near-identical refrain/anaphora lines,
 * e.g. `"Alleluia, Alleluia"` or `"Worthy is the Lamb"`).
 * Never matches two unrelated lines with different lyrics (e.g. `"Alleluia, Alleluia"` vs `"For the Lord God Almighty"`).
 */
function linesAreRepeatedRefrain(lineA: string, lineB: string): boolean {
  const wordsA = tokenizeWords(lineA).map((w) => w.norm).filter(Boolean);
  const wordsB = tokenizeWords(lineB).map((w) => w.norm).filter(Boolean);
  if (wordsA.length === 0 || wordsB.length === 0) return false;
  if (wordsA.join(" ") === wordsB.join(" ")) return true;
  if (Math.abs(wordsA.length - wordsB.length) > 1 || wordsA.length < 3) return false;
  let matches = 0;
  const minLen = Math.min(wordsA.length, wordsB.length);
  for (let i = 0; i < minLen; i++) {
    if (wordsA[i] === wordsB[i]) matches++;
  }
  return matches / Math.max(wordsA.length, wordsB.length) >= 0.8;
}

/**
 * 1. High-Confidence Cross-Section & Repeated-Line Chord Predictions:
 *    - Mirrors chords from a charted section (e.g. Verse 1) onto sibling sections
 *      of the same type (e.g. Verse 2, Verse 3) or repeated Choruses that are missing chords.
 *    - Within the same section (or across the song), only mirrors chords onto another line
 *      when that line has the **same repeated lyrics** (e.g. repeated `"Alleluia, Alleluia"`),
 *      never guessing chords on unrelated lyrics.
 */
function computeChordMirrorAndPatternSuggestions(
  slides: { section: ParsedSection; lyrics: string }[],
  aligned: ReturnType<typeof alignSlides> | null,
  _effectiveKey: string,
  preferFlats: boolean,
  live = false,
): SongSuggestion[] {
  if (!aligned) return [];
  const suggestions: SongSuggestion[] = [];
  const seenKeys = new Set<string>();

  const addChordSuggestion = (s: SongSuggestion) => {
    const key = `${s.slideIdx}:${s.lineIdx}:${s.chordPos}`;
    if (seenKeys.has(key)) return;
    seenKeys.add(key);
    suggestions.push(s);
  };

  // Group slides by `(section.type, section.number)` so multi-slide verses align slide-by-slide
  const sectionGroups = new Map<
    string,
    { type: ParsedSection["type"]; number: number; label: string; slideIndices: number[] }
  >();
  slides.forEach((s, idx) => {
    const k = `${s.section.type}:${s.section.number}`;
    const existing = sectionGroups.get(k);
    if (existing) {
      existing.slideIndices.push(idx);
    } else {
      sectionGroups.set(k, {
        type: s.section.type,
        number: s.section.number,
        label: sectionLabel(s.section),
        slideIndices: [idx],
      });
    }
  });

  const byType = new Map<ParsedSection["type"], typeof sectionGroups extends Map<string, infer V> ? V[] : never>();
  for (const grp of sectionGroups.values()) {
    const list = byType.get(grp.type) ?? [];
    list.push(grp);
    byType.set(grp.type, list);
  }

  for (const [, groups] of byType) {
    if (live) continue;
    const templateGroup = groups.find((grp) =>
      grp.slideIndices.some((si) =>
        (aligned[si]?.lines ?? []).some((l) => l.chords.length > 0),
      ),
    );
    if (!templateGroup) continue;

    // A. Mirror across sibling groups of the same type (e.g. Verse 1 -> Verse 2, Verse 3)
    for (const targetGroup of groups) {
      if (targetGroup === templateGroup) continue;

      targetGroup.slideIndices.forEach((targetSlideIdx, offsetInGroup) => {
        const sourceSlideIdx =
          templateGroup.slideIndices[
            Math.min(offsetInGroup, templateGroup.slideIndices.length - 1)
          ]!;
        const sourceAligned = aligned[sourceSlideIdx];
        const targetAligned = aligned[targetSlideIdx];
        if (!sourceAligned || !targetAligned) return;

        targetAligned.lines.forEach((targetLine, li) => {
          if (targetLine.text.trim() === "") return;

          const sourceLine = sourceAligned.lines[li];
          if (!sourceLine || sourceLine.chords.length === 0) return;

          if (targetLine.chords.length >= sourceLine.chords.length) return;

          for (const srcChord of sourceLine.chords) {
            const snapped = snapRelativePosToWord(
              sourceLine.text,
              srcChord.pos,
              targetLine.text,
            );
            if (!snapped) continue;
            const alreadyHasChordOnWord = targetLine.chords.some(
              (c) => c.pos >= snapped.wordStart && c.pos <= snapped.wordEnd,
            );
            if (alreadyHasChordOnWord) continue;
            const spelled = respellChord(srcChord.chord, preferFlats);
            addChordSuggestion({
              id: `chord-mirror-${targetSlideIdx}-${li}-${snapped.chordPos}`,
              category: "chord",
              label: `${spelled} on "${snapped.wordText}" (${targetGroup.label})`,
              reason: `Mirrored from ${templateGroup.label}`,
              slideIdx: targetSlideIdx,
              lineIdx: li,
              chordPos: snapped.chordPos,
              wordStart: snapped.wordStart,
              wordEnd: snapped.wordEnd,
              chord: spelled,
            });
          }
        });
      });
    }
  }

  // B. Repeated Lyric Line Completion (Intra-slide or Cross-slide):
  // Only mirror chords onto another line when the two lines share repeated refrain lyrics
  // (e.g. `"Alleluia, Alleluia"` on line 1 and `"Alleluia, Alleluia"` later in the stanza/song).
  slides.forEach((slide, slideIdx) => {
    const slideAligned = aligned[slideIdx];
    if (!slideAligned) return;
    const lines = slideAligned.lines;

    lines.forEach((targetLine, li) => {
      if (targetLine.text.trim() === "") return;

      // Find an already-charted line in the same slide or any earlier slide with matching lyrics
      let sourceLine: { text: string; chords: { pos: number; chord: string }[] } | null = null;
      let sourceLineLabel = "";
      // While charting, a repeated line inside the same verse is not evidence.
      // The closing "Blessed be Your name" is often a different chord from the opening.
      if (!live) {
        for (let prevLi = 0; prevLi < lines.length; prevLi++) {
          if (prevLi === li) continue;
          const cand = lines[prevLi]!;
          if (cand.chords.length > 0 && linesAreRepeatedRefrain(cand.text, targetLine.text)) {
            sourceLine = cand;
            sourceLineLabel = `line ${prevLi + 1}`;
            break;
          }
        }
      }
      if (!sourceLine) {
        for (let prevSi = 0; prevSi < slides.length; prevSi++) {
          if (prevSi === slideIdx) continue;
          const prevSlideAligned = aligned[prevSi];
          if (!prevSlideAligned) continue;
          // A later chorus may repeat a line exactly. A later verse that
          // reuses the title line usually does not — that closing chord is
          // the turnaround, and the model is the one that may suggest it.
          const sameRepeatingSection =
            slides[prevSi]!.section.type === slides[slideIdx]!.section.type &&
            slides[slideIdx]!.section.type !== "verse";
          for (let prevLi = 0; prevLi < prevSlideAligned.lines.length; prevLi++) {
            const cand = prevSlideAligned.lines[prevLi]!;
            if (
              cand.chords.length > 0 &&
              (live
                ? sameRepeatingSection && linesAreExactCopy(cand.text, targetLine.text)
                : linesAreRepeatedRefrain(cand.text, targetLine.text))
            ) {
              sourceLine = cand;
              sourceLineLabel = `${sectionLabel(slides[prevSi]!.section)} line ${prevLi + 1}`;
              break;
            }
          }
          if (sourceLine) break;
        }
      }

      if (!sourceLine || targetLine.chords.length >= sourceLine.chords.length) return;

      for (const srcChord of sourceLine.chords) {
        const snapped = snapRelativePosToWord(
          sourceLine.text,
          srcChord.pos,
          targetLine.text,
        );
        if (!snapped) continue;
        const alreadyHasChordOnWord = targetLine.chords.some(
          (c) => c.pos >= snapped.wordStart && c.pos <= snapped.wordEnd,
        );
        if (alreadyHasChordOnWord) continue;
        const spelled = respellChord(srcChord.chord, preferFlats);
        addChordSuggestion({
          id: `chord-pattern-${slideIdx}-${li}-${snapped.chordPos}`,
          category: "chord",
          label: `${spelled} on "${snapped.wordText}" (${sectionLabel(slide.section)})`,
          reason: `Matches repeated lyric from ${sourceLineLabel}`,
          slideIdx,
          lineIdx: li,
          chordPos: snapped.chordPos,
          wordStart: snapped.wordStart,
          wordEnd: snapped.wordEnd,
          chord: spelled,
        });
      }
    });
  });

  return suggestions;
}

/**
 * 2. Harmonic Sanity Check / OCR Chord Spellcheck:
 *    Detects chords that are outside the song's diatonic key and look like classic
 *    PDF/OCR misreads or accidental typos (e.g. `Bb` instead of `Bm` in Key of G,
 *    `F` instead of `F#m` in Key of D/A, or wrong accidental spelling).
 */
function computeChordSpellcheckSuggestions(
  slides: { section: ParsedSection; lyrics: string }[],
  aligned: ReturnType<typeof alignSlides> | null,
  effectiveKey: string | null | undefined,
  preferFlats: boolean,
): SongSuggestion[] {
  if (!aligned || !effectiveKey) return [];
  const parsedKey = parseChord(effectiveKey);
  if (!parsedKey) return [];

  const tonicPc = majorTonicPitchClass(effectiveKey);
  const diatonic = diatonicChordsForKey(effectiveKey, preferFlats);
  if (diatonic.length < 6) return [];

  // Pitch classes belonging to the major scale of `effectiveKey`
  const scaleOffsets = [0, 2, 4, 5, 7, 9, 11];
  const scalePcs = new Set(scaleOffsets.map((off) => (tonicPc + off) % 12));

  // Map each scale degree pitch class to its standard diatonic triad (`G`, `Am`, `Bm`, `C`, `D`, `Em`)
  const diatonicByRootPc = new Map<number, string>();
  diatonic.forEach((ch) => {
    const p = parseChord(ch);
    if (p) diatonicByRootPc.set(p.root, ch);
  });

  const out: SongSuggestion[] = [];

  slides.forEach((slide, slideIdx) => {
    const slideAligned = aligned[slideIdx];
    if (!slideAligned) return;
    slideAligned.lines.forEach((line, li) => {
      const words = tokenizeWords(line.text);
      for (const placed of line.chords) {
        const parsed = parseChord(placed.chord);
        if (!parsed) continue;

        const isMinor = /^(m(?!aj)|min|-)/.test(parsed.suffix);
        const relPc = (((parsed.root - tonicPc) % 12) + 12) % 12;

        let suggestedReplacement: string | null = null;
        let reason = "";

        // Case 1: Root pitch class is 1 semitone off a diatonic root with the same letter name!
        // e.g. in Key of G (F#), `F` or `Fm` -> `F#m` or `D/F#`; in Key of D (F#, C#), `C`m -> `C#m`, `Bb` -> `Bm`
        const letterRootName = parsed.rootText[0]!;
        for (const diaChord of diatonic) {
          if (diaChord[0] === letterRootName) {
            const diaParsed = parseChord(diaChord);
            if (diaParsed && diaParsed.root !== parsed.root) {
              // e.g. `Bb` vs `Bm` in Key of G/D, or `F` vs `F#m` in Key of D/A
              // Only flag if `placed.chord` is NOT the common bVII borrowed major chord (like `F` in G or `C` in D)
              // OR if it's `Bb` in G/D (`relPc === 3` written as `Bb` instead of `Bm`).
              if (placed.chord === "Bb" && diaChord === "Bm") {
                suggestedReplacement = "Bm";
                reason = `Likely OCR misread of Bm in Key of ${effectiveKey}`;
              } else if (relPc !== 10) {
                // Not bVII borrowed chord
                suggestedReplacement = diaChord;
                reason = `${placed.chord} is out of key in ${effectiveKey}; ${diaChord} is diatonic`;
              }
            } else if (
              diaParsed &&
              diaParsed.root === parsed.root &&
              placed.chord === "B" &&
              diaChord === "Bm" &&
              parsed.suffix === ""
            ) {
              // Bare `B` major where `Bm` (iii or vi) is diatonic and `b` from `Bm` was dropped
              suggestedReplacement = "Bm";
              reason = `In Key of ${effectiveKey}, ${diaChord} is the diatonic chord`;
            }
          }
        }

        // Case 2: Non-scale root that wasn't caught above
        if (!suggestedReplacement && !scalePcs.has(parsed.root) && relPc !== 10) {
          // Find closest diatonic chord by semitone
          const upPc = (parsed.root + 1) % 12;
          const downPc = (parsed.root + 11) % 12;
          const cand =
            (isMinor ? diatonicByRootPc.get(upPc) : null) ??
            diatonicByRootPc.get(upPc) ??
            diatonicByRootPc.get(downPc);
          if (cand) {
            suggestedReplacement = cand;
            reason = `${placed.chord} is outside Key of ${effectiveKey}`;
          }
        }

        if (suggestedReplacement && suggestedReplacement !== placed.chord) {
          const ownerWord =
            words.find((w) => placed.pos >= w.start && placed.pos <= w.end) ??
            words.find((w) => w.start >= placed.pos) ??
            words[words.length - 1];
          const wordStart = ownerWord?.start ?? placed.pos;
          const wordEnd = ownerWord?.end ?? placed.pos + 1;
          out.push({
            id: `chord-fix-${slideIdx}-${li}-${placed.pos}`,
            category: "chord-fix",
            label: `Change ${placed.chord} → ${suggestedReplacement} (${sectionLabel(slide.section)})`,
            reason,
            slideIdx,
            lineIdx: li,
            chordPos: placed.pos,
            wordStart,
            wordEnd,
            chord: suggestedReplacement,
            replacedChord: placed.chord,
          });
        }
      }
    });
  });

  return out;
}

/**
 * 3. Slide Split Suggestions (16:9 Projector Readability):
 *    Finds slides with >= 4 lines (or > 140 characters across >= 3 lines) and proposes
 *    splitting them at the best musical/grammatical breath boundary (typically halfway,
 *    preferring lines ending with punctuation `,` `;` `.` `!` `?`).
 */
function computeSlideSplitSuggestions(
  slides: { section: ParsedSection; lyrics: string }[],
): SongSuggestion[] {
  const out: SongSuggestion[] = [];

  slides.forEach((slide, slideIdx) => {
    const lines = slide.lyrics.split("\n").map((l) => l.trimEnd());
    if (lines.length < 4) return;

    const mid = Math.floor(lines.length / 2) - 1;
    let bestSplitIdx = mid;
    let bestScore = -Infinity;

    for (let i = 1; i < lines.length - 1; i++) {
      const line = lines[i]!.trim();
      // Prefer balanced halves (e.g. 2+2 for 4 lines, 2+2 or 3+3 for 6 lines)
      const balancePenalty = Math.abs(i - mid) * 2;
      const endsWithPunct = /[,;.!?—–-]$/.test(line) ? 3 : 0;
      const evenSplitBonus = (i + 1) % 2 === 0 ? 1.5 : 0;
      const score = endsWithPunct + evenSplitBonus - balancePenalty;
      if (score > bestScore) {
        bestScore = score;
        bestSplitIdx = i;
      }
    }

    out.push({
      id: `slide-split-${slideIdx}-${bestSplitIdx}`,
      category: "slide-split",
      label: `Split ${sectionLabel(slide.section)} (${lines.length} lines → ${bestSplitIdx + 1} + ${lines.length - (bestSplitIdx + 1)})`,
      reason: `${lines.length}-line slide is crowded on 16:9 projectors; split at breath mark after line ${bestSplitIdx + 1}`,
      slideIdx,
      splitAfterLineIdx: bestSplitIdx,
    });
  });

  return out;
}

/**
 * 4. Lyric Cleanup & Cross-Section Lyric Ripple Suggestions:
 *    - Detects leftover PDF/notation syllable hyphens inside words (e.g. `"Wor-thy"`, `"sur-ren-der"`, `"Ho-ly"`).
 *    - Detects excessive trailing punctuation on slide lines (`"Praise the Lord,,,"` or trailing commas on final lines).
 */
const LEGIT_HYPHENATED_WORDS = new Set([
  "pre-chorus",
  "ever-living",
  "all-sufficient",
  "first-born",
  "well-pleased",
  "self-same",
  "blood-bought",
  "spirit-filled",
  "two-edged",
  "loving-kindness",
  "non-stop",
  "co-heir",
  "co-heirs",
  "re-ignited",
]);

function cleanLyricLineArtifacts(line: string): string {
  // 1. Rejoin obvious mid-word syllable hyphens from notation PDFs (e.g. "Wor-thy" -> "Worthy", "sur-ren-der" -> "surrender")
  let cleaned = line.replace(/\b([A-Za-z]{1,6}(?:-[a-z]{1,6})+)\b/g, (whole) => {
    if (LEGIT_HYPHENATED_WORDS.has(whole.toLowerCase())) return whole;
    return whole.replace(/-/g, "");
  });
  // 2. Strip repeated exclamation/question marks or stray trailing commas at end of line
  cleaned = cleaned.replace(/\s+([,;.!?])/g, "$1");
  return cleaned;
}

function computeLyricCleanupSuggestions(
  slides: { section: ParsedSection; lyrics: string }[],
  lastEdit?: EditContext | null,
): SongSuggestion[] {
  const out: SongSuggestion[] = [];

  // A. OCR / Hyphenation artifact cleanup
  slides.forEach((slide, slideIdx) => {
    const lines = slide.lyrics.split("\n");
    const cleanedLines = lines.map(cleanLyricLineArtifacts);
    const nextLyrics = cleanedLines.join("\n");
    if (nextLyrics !== slide.lyrics) {
      const diffIdx = lines.findIndex((l, i) => l !== cleanedLines[i]);
      const fromLine = lines[diffIdx] ?? lines[0] ?? "";
      const toLine = cleanedLines[diffIdx] ?? cleanedLines[0] ?? "";
      out.push({
        id: `lyric-fix-${slideIdx}`,
        category: "lyric-fix",
        label: `Clean hyphenation in ${sectionLabel(slide.section)}`,
        reason: `Rejoins split syllables ("${fromLine.trim()}" → "${toLine.trim()}")`,
        slideIdx,
        lineIdx: Math.max(0, diffIdx),
        nextSlideLyrics: nextLyrics,
        lyricDiffPreview: { from: fromLine.trim(), to: toLine.trim() },
      });
    }
  });

  // B. Reactive Lyric Ripple: if the user just edited a slide's lyrics (`lastEdit?.kind === "lyric"`),
  // check if another slide of the same section type has a near-identical line that differs by 1-2 words
  if (lastEdit?.kind === "lyric" && slides[lastEdit.slideIdx]) {
    const editedSlide = slides[lastEdit.slideIdx]!;
    const editedLines = editedSlide.lyrics.split("\n");

    slides.forEach((otherSlide, otherIdx) => {
      if (otherIdx === lastEdit.slideIdx) return;
      if (otherSlide.section.type !== editedSlide.section.type) return;
      const otherLines = otherSlide.lyrics.split("\n");
      if (otherLines.length !== editedLines.length) return;

      // If all lines except 1 are identical, or lines share >=75% words, offer to sync
      let changedCount = 0;
      let diffLineIdx = -1;
      for (let li = 0; li < editedLines.length; li++) {
        if (editedLines[li]!.trim() !== otherLines[li]!.trim()) {
          const wA = tokenizeWords(editedLines[li]!).map((w) => w.norm);
          const wB = tokenizeWords(otherLines[li]!).map((w) => w.norm);
          const shared = wA.filter((w) => wB.includes(w)).length;
          const maxLen = Math.max(wA.length, wB.length, 1);
          if (shared / maxLen >= 0.6) {
            changedCount++;
            diffLineIdx = li;
          } else {
            changedCount = 99;
          }
        }
      }

      if (changedCount === 1 && diffLineIdx >= 0) {
        const nextOtherLines = [...otherLines];
        nextOtherLines[diffLineIdx] = editedLines[diffLineIdx]!;
        out.push({
          id: `lyric-ripple-${otherIdx}-${diffLineIdx}`,
          category: "lyric-fix",
          label: `Update matching line in ${sectionLabel(otherSlide.section)}`,
          reason: `Ripple lyric edit from ${sectionLabel(editedSlide.section)}`,
          slideIdx: otherIdx,
          lineIdx: diffLineIdx,
          nextSlideLyrics: nextOtherLines.join("\n"),
          lyricDiffPreview: {
            from: otherLines[diffLineIdx]!.trim(),
            to: editedLines[diffLineIdx]!.trim(),
          },
        });
      }
    });
  }

  return out;
}

/**
 * 5. Arrangement Order Completion:
 *    When `slideOrder` is empty and the song defines unique sections like
 *    `[Verse 1, Chorus, Verse 2, Bridge]` once each, suggests the standard worship
 *    arrangement repeats (e.g. inserting `Chorus` after `Verse 2` and after `Bridge`).
 */
function computeArrangementSuggestion(
  body: string,
  slideOrder: string,
): SongSuggestion | null {
  if (slideOrder.trim() !== "") return null;
  const parsed = parseSong(body);
  const seq = parsed.section_order;
  if (seq.length < 3) return null;

  const chorusSec = seq.find((s) => s.type === "chorus");
  if (!chorusSec) return null;

  // Check if Chorus only appears once even though there are later verses or a bridge
  const chorusCount = seq.filter((s) => s.type === "chorus").length;
  if (chorusCount > 1) return null;

  const expanded: ParsedSection[] = [];
  const addedLabels: string[] = [];

  for (let i = 0; i < seq.length; i++) {
    const cur = seq[i]!;
    const next = seq[i + 1];
    expanded.push(cur);

    // If a Verse (after Verse 1) or a Bridge is not followed by a Chorus (or Pre-Chorus),
    // insert Chorus repeat in the arrangement!
    const isLaterVerse = cur.type === "verse" && cur.number > 1;
    const isBridge = cur.type === "bridge";
    const nextIsChorusOrPre =
      next?.type === "chorus" || next?.type === "pre-chorus";

    if ((isLaterVerse || isBridge) && !nextIsChorusOrPre) {
      expanded.push({ ...chorusSec });
      addedLabels.push(sectionLabel(chorusSec));
    }
  }

  if (addedLabels.length === 0) return null;

  const nextOrderStr = formatSlideOrder(expanded);
  return {
    id: `arrangement-order-complete`,
    category: "arrangement",
    label: `Complete Arrangement Order (+${addedLabels.length} ${sectionLabel(chorusSec)} repeat${addedLabels.length === 1 ? "" : "s"})`,
    reason: `Adds repeated ${sectionLabel(chorusSec)} after Verse/Bridge without duplicating slide text`,
    appendOrderSections: expanded,
    nextSlideOrder: nextOrderStr,
  };
}

/** Map a verified model reply onto ghost suggestions. `phrase` marks a Tab-all run. */
export function modelPlacementsToSuggestions(
  body: string,
  placements: ChordPlacementInfo[],
  phrase: boolean,
): SongSuggestion[] {
  const slides = parseSong(body).slides;
  const out: SongSuggestion[] = [];
  for (const placement of placements) {
    const at = parseLineId(placement.line_id);
    if (!at) continue;
    const { slideIdx, lineIdx } = at;
    const line = slides[slideIdx]?.lyrics.split("\n")[lineIdx];
    if (line === undefined) continue;
    const word = findLineWord(line, placement.word, placement.word_index);
    if (!word || !placement.chord.trim()) continue;
    out.push({
      id: `model-${placement.line_id}-${word.start}`,
      category: "chord",
      label: `${placement.chord} on "${word.text}"`,
      reason: phrase ? "Suggested with the next phrase" : "Suggested next chord",
      slideIdx,
      lineIdx,
      chordPos: word.start,
      wordStart: word.start,
      wordEnd: word.end,
      chord: placement.chord.trim(),
      phraseGroup: phrase ? "model-phrase" : undefined,
    });
  }
  return out;
}

/**
 * Compute suggestions for the current song state.
 * - `mode: "all"`: returns all high-confidence suggestions across the song (used by the `Suggest` button).
 * - `mode: "reactive"`: returns only the immediate next-step suggestions triggered by `lastEdit` (Cursor Tab flow).
 */
export function computeSongSuggestions(params: {
  body: string;
  chart: ChordChart | null;
  chartKey?: string | null;
  preferFlats?: boolean;
  slideOrder?: string;
  showChords?: boolean;
  mode: "all" | "reactive";
  lastEdit?: EditContext | null;
}): SongSuggestion[] {
  const {
    body,
    chart,
    chartKey,
    preferFlats = false,
    slideOrder = "",
    showChords = false,
    mode,
    lastEdit,
  } = params;

  if (!body.trim()) return [];

  const parsed = parseSong(body);
  const slides = parsed.slides.map((s) => ({
    section: s.section,
    lyrics: s.lyrics,
  }));
  if (slides.length === 0) return [];

  const effectiveKey =
    chartKey || chart?.key || "G";
  const effectiveFlats =
    preferFlats !== undefined ? preferFlats : keyPrefersFlats(effectiveKey);

  const alignedWithIntro = chart
    ? alignSlides(chart, [{ lyrics: "" }, ...slides])
    : null;
  const aligned = alignedWithIntro ? alignedWithIntro.slice(1) : null;

  if (mode === "reactive") {
    if (!lastEdit) return [];

    if (lastEdit.kind === "chord") {
      // After placing a chord, prioritize chord predictions on the same slide or sibling sections!
      const allChord = computeChordMirrorAndPatternSuggestions(
        slides,
        aligned,
        effectiveKey,
        effectiveFlats,
        true,
      );
      // Sort so predictions on the current slide (or immediately following slide) come first
      return allChord.sort((a, b) => {
        const distA =
          (a.slideIdx ?? 0) >= lastEdit.slideIdx
            ? (a.slideIdx ?? 0) - lastEdit.slideIdx
            : (a.slideIdx ?? 0) + 100;
        const distB =
          (b.slideIdx ?? 0) >= lastEdit.slideIdx
            ? (b.slideIdx ?? 0) - lastEdit.slideIdx
            : (b.slideIdx ?? 0) + 100;
        if (distA !== distB) return distA - distB;
        return (a.lineIdx ?? 0) - (b.lineIdx ?? 0);
      });
    }

    if (lastEdit.kind === "slide-split") {
      return computeSlideSplitSuggestions(slides);
    }

    if (lastEdit.kind === "lyric") {
      return computeLyricCleanupSuggestions(slides, lastEdit);
    }

    return [];
  }

  // `mode === "all"` (Explicit `Suggest` button)
  const all: SongSuggestion[] = [];

  if (showChords || (chart && chartHasChords(chart))) {
    all.push(
      ...computeChordMirrorAndPatternSuggestions(
        slides,
        aligned,
        effectiveKey,
        effectiveFlats,
      ),
    );
    all.push(
      ...computeChordSpellcheckSuggestions(
        slides,
        aligned,
        chartKey || chart?.key,
        effectiveFlats,
      ),
    );
  }

  all.push(...computeSlideSplitSuggestions(slides));
  all.push(...computeLyricCleanupSuggestions(slides, lastEdit));

  const arr = computeArrangementSuggestion(body, slideOrder);
  if (arr) all.push(arr);

  return all;
}

/**
 * Apply a single `SongSuggestion` to `(body, chart, slideOrder)` and return the updated state.
 */
export function applySingleSuggestion(
  suggestion: SongSuggestion,
  state: {
    body: string;
    chart: ChordChart | null;
    slideOrder: string;
    preferFlats: boolean;
  },
): {
  body: string;
  chordText: string;
  chart: ChordChart | null;
  slideOrder: string;
  showChords?: boolean;
} {
  const { body, chart, slideOrder } = state;
  const parsed = parseSong(body);
  const slides: OverrideSlide[] = parsed.slides.map((s) => ({
    section: s.section,
    lyrics: s.lyrics,
  }));

  if (
    (suggestion.category === "chord" || suggestion.category === "chord-fix") &&
    suggestion.slideIdx !== undefined &&
    suggestion.lineIdx !== undefined &&
    suggestion.chordPos !== undefined &&
    suggestion.chord
  ) {
    const targetSlide = slides[suggestion.slideIdx];
    if (!targetSlide) {
      return {
        body,
        chordText: toChordText(body, chart),
        chart,
        slideOrder,
      };
    }
    const wordStart = suggestion.wordStart ?? suggestion.chordPos;
    const wordEnd = suggestion.wordEnd ?? suggestion.chordPos + 1;
    const next = setWordChordInBody(
      body,
      chart,
      targetSlide.lyrics,
      suggestion.lineIdx,
      wordStart,
      wordEnd,
      suggestion.chordPos,
      suggestion.chord,
    );
    return {
      body,
      chordText: next.chordText,
      chart: next.chart,
      slideOrder,
      showChords: true,
    };
  }

  if (
    suggestion.category === "slide-split" &&
    suggestion.slideIdx !== undefined &&
    suggestion.splitAfterLineIdx !== undefined
  ) {
    const targetSlide = slides[suggestion.slideIdx];
    if (!targetSlide) {
      return { body, chordText: toChordText(body, chart), chart, slideOrder };
    }
    const lines = targetSlide.lyrics.split("\n");
    const splitIdx = suggestion.splitAfterLineIdx;
    if (splitIdx < 0 || splitIdx >= lines.length - 1) {
      return { body, chordText: toChordText(body, chart), chart, slideOrder };
    }
    const firstPart = lines.slice(0, splitIdx + 1).join("\n");
    const secondPart = lines.slice(splitIdx + 1).join("\n");
    const nextSlides: OverrideSlide[] = [
      ...slides.slice(0, suggestion.slideIdx),
      { section: { ...targetSlide.section }, lyrics: firstPart },
      { section: { ...targetSlide.section }, lyrics: secondPart },
      ...slides.slice(suggestion.slideIdx + 1),
    ];
    const nextBody = slidesToBody(nextSlides);
    const nextChordText = toChordText(nextBody, chart);
    const reparsed = fromChordText(nextChordText, chart);
    return {
      body: nextBody,
      chordText: nextChordText,
      chart: reparsed.chart ?? chart,
      slideOrder,
    };
  }

  if (
    suggestion.category === "lyric-fix" &&
    suggestion.slideIdx !== undefined &&
    suggestion.nextSlideLyrics !== undefined
  ) {
    const targetSlide = slides[suggestion.slideIdx];
    if (!targetSlide) {
      return { body, chordText: toChordText(body, chart), chart, slideOrder };
    }
    const nextSlides = slides.map((s, idx) =>
      idx === suggestion.slideIdx ? { ...s, lyrics: suggestion.nextSlideLyrics! } : s,
    );
    const nextBody = slidesToBody(nextSlides);
    const nextChordText = toChordText(nextBody, chart);
    const reparsed = fromChordText(nextChordText, chart);
    return {
      body: nextBody,
      chordText: nextChordText,
      chart: reparsed.chart ?? chart,
      slideOrder,
    };
  }

  if (
    suggestion.category === "arrangement" &&
    suggestion.nextSlideOrder !== undefined
  ) {
    return {
      body,
      chordText: toChordText(body, chart),
      chart,
      slideOrder: suggestion.nextSlideOrder,
    };
  }

  return {
    body,
    chordText: toChordText(body, chart),
    chart,
    slideOrder,
  };
}

/**
 * Apply all suggestions in batch (`Accept all`), handling chord placements first,
 * then lyric fixes, then slide splits (from bottom slide index to top so indices stay valid),
 * then arrangement order.
 */
export function applyAllSuggestions(
  suggestions: SongSuggestion[],
  initialState: {
    body: string;
    chart: ChordChart | null;
    slideOrder: string;
    preferFlats: boolean;
  },
): {
  body: string;
  chordText: string;
  chart: ChordChart | null;
  slideOrder: string;
  showChords?: boolean;
} {
  let current = {
    body: initialState.body,
    chordText: toChordText(initialState.body, initialState.chart),
    chart: initialState.chart,
    slideOrder: initialState.slideOrder,
    showChords: false as boolean | undefined,
  };

  const chordsAndFixes = suggestions.filter(
    (s) => s.category === "chord" || s.category === "chord-fix",
  );
  const lyricFixes = suggestions.filter((s) => s.category === "lyric-fix");
  const slideSplits = [...suggestions.filter((s) => s.category === "slide-split")].sort(
    (a, b) => (b.slideIdx ?? 0) - (a.slideIdx ?? 0),
  );
  const arrangements = suggestions.filter((s) => s.category === "arrangement");

  for (const s of [...chordsAndFixes, ...lyricFixes, ...slideSplits, ...arrangements]) {
    const next = applySingleSuggestion(s, {
      body: current.body,
      chart: current.chart,
      slideOrder: current.slideOrder,
      preferFlats: initialState.preferFlats,
    });
    current = {
      ...next,
      showChords: current.showChords || next.showChords,
    };
  }

  return current;
}

/**
 * Helper to check if a custom `slideOrder` string differs from `parsedSong.section_order`.
 */
export function getExtraArrangementGhostSections(
  body: string,
  currentSlideOrder: string,
  suggestion: SongSuggestion | null | undefined,
): ParsedSection[] {
  if (!suggestion || suggestion.category !== "arrangement" || !suggestion.nextSlideOrder) {
    return [];
  }
  const current =
    currentSlideOrder.trim() !== ""
      ? parseSlideOrder(currentSlideOrder)
      : parseSong(body).section_order;
  const proposed = parseSlideOrder(suggestion.nextSlideOrder);
  if (proposed.length <= current.length) return [];
  return proposed.slice(current.length);
}
