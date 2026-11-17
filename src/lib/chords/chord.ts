/**
 * Chord symbols: recognise them, transpose them, spell them for a key.
 *
 * Deliberately permissive about the *quality* part of a symbol (everything
 * after the root) — charts in the wild write `Gmaj7`, `GM7`, `GΔ7`, `G2`,
 * `Gsus`, `G(add9)` — and strict about the root, which is the only part that
 * transposition touches. The suffix is carried through verbatim.
 */

const NOTE_INDEX: Record<string, number> = {
  C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11,
};

const SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
const FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"];

/** How each major key is conventionally written, by pitch class. */
const MAJOR_KEY_NAMES = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"];
/** …and each minor key. */
const MINOR_KEY_NAMES = ["Cm", "C#m", "Dm", "Ebm", "Em", "Fm", "F#m", "Gm", "G#m", "Am", "Bbm", "Bm"];

/** Keys whose signatures use flats; everything else spells with sharps. */
const FLAT_MAJOR_PCS = new Set([5, 10, 3, 8, 1]); // F Bb Eb Ab Db
const FLAT_MINOR_PCS = new Set([2, 7, 0, 5, 10, 3]); // Dm Gm Cm Fm Bbm Ebm

export interface Chord {
  /** Pitch class of the root, 0 = C. */
  root: number;
  /** Everything between the root and an optional slash bass, verbatim. */
  suffix: string;
  /** Pitch class of the slash bass, if any. */
  bass: number | null;
  /** How the root was written, so an untransposed chord round-trips exactly. */
  rootText: string;
  bassText: string | null;
  /** "m" when a sheet writes a minor bass ("G#m7/D#m"); kept so it round-trips. */
  bassSuffix?: string;
}

const ROOT = "([A-G])([#b♯♭]?)";
// A quality suffix: letters and symbols charts use, but no whitespace and no
// lowercase letters outside the recognised words (so lyric words like "Be" or
// "Go" never read as chords).
const SUFFIX =
  "((?:maj|ma|min|dim|aug|sus|add|no|alt|omit|m|M|°|ø|\\+|-|Δ|\\^|\\d|#|b|♯|♭|\\(|\\)|,)*)";
// The bass is a note; some sheets (SongSelect among them) write a minor one,
// "G#m7/D#m" — accept that "m" rather than drop the whole chord row as lyrics.
const CHORD_RE = new RegExp(`^${ROOT}${SUFFIX}(?:/${ROOT}(m(?!aj))?)?$`);

function pitchClass(letter: string, accidental: string): number {
  let pc = NOTE_INDEX[letter]!;
  if (accidental === "#" || accidental === "♯") pc += 1;
  if (accidental === "b" || accidental === "♭") pc -= 1;
  return (pc + 12) % 12;
}

/** Parse a chord symbol, or null if it isn't one. Brackets/parens are not stripped. */
export function parseChord(symbol: string): Chord | null {
  const m = CHORD_RE.exec(symbol.trim());
  if (!m) return null;
  const [, letter, acc, suffix, bassLetter, bassAcc, bassMinor] = m;
  // Unbalanced parentheses mean we matched something odd, e.g. "A)" at the end
  // of a lyric; a real suffix like "(add9)" is always balanced.
  const opens = (suffix!.match(/\(/g) ?? []).length;
  const closes = (suffix!.match(/\)/g) ?? []).length;
  if (opens !== closes) return null;
  return {
    root: pitchClass(letter!, acc!),
    suffix: suffix!,
    bass: bassLetter ? pitchClass(bassLetter, bassAcc!) : null,
    rootText: letter! + normalizeAccidental(acc!),
    bassText: bassLetter ? bassLetter + normalizeAccidental(bassAcc!) : null,
    ...(bassMinor ? { bassSuffix: bassMinor } : {}),
  };
}

function normalizeAccidental(acc: string): string {
  if (acc === "♯") return "#";
  if (acc === "♭") return "b";
  return acc;
}

/** Tokens that sit on chord lines without being chords. */
const CHORD_LINE_FILLER = /^(\|+|:?\|\|?:?|\/+|[-–—]+|\.+|%|x\d+|\(x\d+\)|\d+x|\(\d+x\)|n\.?c\.?|\(n\.?c\.?\)|\*+|→|>)$/i;

const NO_CHORD = /^\(?n\.?c\.?\)?$/i;

/** True if `token` is a chord, optionally wrapped in parentheses. */
export function isChordToken(token: string): boolean {
  // "(G)" and "(Em7(4))" — a chord in brackets is an optional chord.
  const t = /^\((.*)\)$/.test(token) ? token.slice(1, -1) : token;
  // "F(/G)": a bass note that's optional.
  const optionalBass = /^(.+)\(\/(.+)\)$/.exec(t);
  if (optionalBass) return parseChord(optionalBass[1]!) !== null && parseChord(optionalBass[2]!) !== null;
  return parseChord(t) !== null;
}

/**
 * True if a whole line is chords (plus bar lines, repeat marks, N.C.) — the
 * "chord line" of a chords-over-lyrics chart.
 */
export function isChordLine(line: string): boolean {
  const tokens = line.trim().split(/\s+/).filter(Boolean);
  if (tokens.length === 0) return false;
  let chords = 0;
  for (const t of tokens) {
    // "N.C." (no chord) on its own is still the band's line, never lyrics.
    if (isChordToken(t) || NO_CHORD.test(t)) chords += 1;
    else if (!CHORD_LINE_FILLER.test(t)) return false;
  }
  return chords > 0;
}

/** Semitone distance from key `from` to key `to` (0..11). Null if unparseable. */
export function semitonesBetween(from: string, to: string): number | null {
  const a = parseChord(from);
  const b = parseChord(to);
  if (!a || !b) return null;
  return (((b.root - a.root) % 12) + 12) % 12;
}

function isMinorSuffix(suffix: string): boolean {
  return /^(m(?!aj)|min|-)/.test(suffix);
}

/** Should chords in this key be spelled with flats? */
export function keyPrefersFlats(key: string | null | undefined): boolean {
  if (!key) return false;
  const k = parseChord(key);
  if (!k) return false;
  // A key written with an explicit flat (Gb, Ebm) is a request for flats.
  if (k.rootText.endsWith("b")) return true;
  if (k.rootText.endsWith("#")) return false;
  return isMinorSuffix(k.suffix) ? FLAT_MINOR_PCS.has(k.root) : FLAT_MAJOR_PCS.has(k.root);
}

/** The conventional name of the key `semitones` above `key` (e.g. G +3 → Bb). */
export function transposeKey(key: string, semitones: number): string {
  const k = parseChord(key);
  if (!k) return key;
  const pc = (((k.root + semitones) % 12) + 12) % 12;
  if (semitones % 12 === 0) return key;
  return isMinorSuffix(k.suffix) ? MINOR_KEY_NAMES[pc]! : MAJOR_KEY_NAMES[pc]!;
}

/**
 * Transpose one chord symbol. `targetKey` decides sharps vs flats; without it
 * we keep whichever the original chord used. A symbol that isn't a chord is
 * returned untouched, so it's safe to run over anything found in a chord slot.
 */
export function transposeChord(
  symbol: string,
  semitones: number,
  targetKey?: string | null,
): string {
  const wrapped = /^\((.*)\)$/.exec(symbol);
  if (wrapped) return `(${transposeChord(wrapped[1]!, semitones, targetKey)})`;
  const c = parseChord(symbol);
  if (!c) return symbol;
  if (((semitones % 12) + 12) % 12 === 0) return symbol;
  const flats =
    targetKey != null ? keyPrefersFlats(targetKey) : c.rootText.endsWith("b");
  const names = flats ? FLAT_NAMES : SHARP_NAMES;
  const shift = (pc: number) => names[(((pc + semitones) % 12) + 12) % 12]!;
  return shift(c.root) + c.suffix + (c.bass != null ? `/${shift(c.bass)}${c.bassSuffix ?? ""}` : "");
}

/** Every key a band would pick from, for key pickers. */
export const KEY_CHOICES = MAJOR_KEY_NAMES;

export interface KeySignatureOption {
  /** Pitch class of the major key (0 = C .. 11 = B). */
  pc: number;
  /** Major key name in this accidental spelling, e.g. "C", "Db", "C#". */
  major: string;
  /** Relative minor key name in this accidental spelling, e.g. "Am", "Bbm", "A#m". */
  minor: string;
  /** Combined display label, e.g. "C (Am)", "Db (Bbm)". */
  label: string;
}

const SHARP_KEY_PAIRS: [string, string][] = [
  ["C", "Am"],
  ["C#", "A#m"],
  ["D", "Bm"],
  ["D#", "Cm"],
  ["E", "C#m"],
  ["F", "Dm"],
  ["F#", "D#m"],
  ["G", "Em"],
  ["G#", "Fm"],
  ["A", "F#m"],
  ["A#", "Gm"],
  ["B", "G#m"],
];

const FLAT_KEY_PAIRS: [string, string][] = [
  ["C", "Am"],
  ["Db", "Bbm"],
  ["D", "Bm"],
  ["Eb", "Cm"],
  ["E", "C#m"],
  ["F", "Dm"],
  ["Gb", "Ebm"],
  ["G", "Em"],
  ["Ab", "Fm"],
  ["A", "F#m"],
  ["Bb", "Gm"],
  ["B", "G#m"],
];

/** The 12 combined Major (Relative minor) key options, spelled with sharps or flats. */
export function keySignatureOptions(preferFlats: boolean): KeySignatureOption[] {
  const pairs = preferFlats ? FLAT_KEY_PAIRS : SHARP_KEY_PAIRS;
  return pairs.map(([major, minor], pc) => ({
    pc,
    major,
    minor,
    label: `${major} (${minor})`,
  }));
}

/** Find the combined key option matching any major or relative minor key string. */
export function findKeySignatureOption(
  key: string | null | undefined,
  preferFlats: boolean,
): KeySignatureOption | null {
  if (!key) return null;
  const k = parseChord(key);
  if (!k) return null;
  const minor = isMinorSuffix(k.suffix);
  const majorPc = minor ? (k.root + 3) % 12 : k.root;
  return keySignatureOptions(preferFlats)[majorPc] ?? null;
}

/** Respell a chord symbol (root and optional slash bass) using sharps or flats. */
export function respellChord(symbol: string, preferFlats: boolean): string {
  const wrapped = /^\((.*)\)$/.exec(symbol);
  if (wrapped) return `(${respellChord(wrapped[1]!, preferFlats)})`;
  const c = parseChord(symbol);
  if (!c) return symbol;
  const names = preferFlats ? FLAT_NAMES : SHARP_NAMES;
  return names[c.root]! + c.suffix + (c.bass != null ? `/${names[c.bass]!}${c.bassSuffix ?? ""}` : "");
}

// --- Nashville Number System (1..7) ------------------------------------------

const DEGREE_OFFSETS: Record<number, number> = {
  1: 0,
  2: 2,
  3: 4,
  4: 5,
  5: 7,
  6: 9,
  7: 11,
};

const NASHVILLE_RE =
  /^([b#♭♯]?)([1-7])((?:maj|ma|min|dim|aug|sus|add|no|alt|omit|m|M|°|ø|\+|-|Δ|\^|\d|#|b|♯|♭|\(|\)|,)*)(?:\/([b#♭♯]?)([1-7]))?$/;

/** Major tonic pitch class (0 = C .. 11 = B) for any major or relative minor key. */
export function majorTonicPitchClass(key: string | null | undefined): number {
  if (!key) return 7; // Default to G major
  const k = parseChord(key);
  if (!k) return 7;
  return isMinorSuffix(k.suffix) ? (k.root + 3) % 12 : k.root;
}

/**
 * Semitones up (0..11) from a song's own key to the key a service plays it
 * in. Compares major tonics, so "Em" → "G" is no change and a minor chart can
 * be moved with a major key name. 0 when either key is missing or unreadable.
 */
export function semitonesToKey(fromKey: string | null | undefined, toKey: string | null | undefined): number {
  if (!fromKey || !toKey || !parseChord(fromKey) || !parseChord(toKey)) return 0;
  return (((majorTonicPitchClass(toKey) - majorTonicPitchClass(fromKey)) % 12) + 12) % 12;
}

/**
 * Convert a Nashville number token (e.g. `1`, `4`, `6m`, `2m7`, `1/3`, `5/7`, `b7`, `4sus2`)
 * into a concrete chord symbol in `key` (defaults to G major).
 * Returns `null` if `token` is not a valid Nashville number chord.
 */
export function parseNashvilleChord(
  token: string,
  key: string | null | undefined,
  preferFlats?: boolean,
): string | null {
  const trimmed = token.trim();
  const wrapped = /^\((.*)\)$/.exec(trimmed);
  if (wrapped) {
    const inner = parseNashvilleChord(wrapped[1]!, key, preferFlats);
    return inner ? `(${inner})` : null;
  }

  const m = NASHVILLE_RE.exec(trimmed);
  if (!m) return null;
  const [, acc, degStr, rawSuffix, bassAcc, bassDegStr] = m;
  const deg = Number(degStr);
  let offset = DEGREE_OFFSETS[deg]!;
  if (acc === "b" || acc === "♭") offset -= 1;
  if (acc === "#" || acc === "♯") offset += 1;

  const tonicPc = majorTonicPitchClass(key);
  const flats = preferFlats ?? keyPrefersFlats(key || "G");
  const names = flats ? FLAT_NAMES : SHARP_NAMES;
  const rootName = names[((tonicPc + offset) % 12 + 12) % 12]!;

  // Diatonic default qualities when no explicit quality is given on a natural scale degree:
  // 2, 3, 6 are minor by default; 7 is diminished by default; 1, 4, 5 (and flat/sharp degrees) are major.
  let suffix = rawSuffix ?? "";
  if (!acc) {
    if (deg === 2 || deg === 3 || deg === 6) {
      if (suffix === "") {
        suffix = "m";
      } else if (/^7$/.test(suffix)) {
        suffix = "m7";
      } else if (/^(?:M|maj)$/.test(suffix)) {
        suffix = "";
      }
    } else if (deg === 7 && suffix === "") {
      suffix = "dim";
    }
  }
  if (/^(M|maj)$/.test(suffix)) suffix = "";

  let bassPart = "";
  if (bassDegStr) {
    const bassDeg = Number(bassDegStr);
    let bassOffset = DEGREE_OFFSETS[bassDeg]!;
    if (bassAcc === "b" || bassAcc === "♭") bassOffset -= 1;
    if (bassAcc === "#" || bassAcc === "♯") bassOffset += 1;
    const bassName = names[((tonicPc + bassOffset) % 12 + 12) % 12]!;
    bassPart = `/${bassName}`;
  }

  return `${rootName}${suffix}${bassPart}`;
}

/**
 * Convert a chord symbol (e.g. `G`, `Em7`, `G/B`, `D/F#`) into its Nashville
 * Number representation relative to `key`. Non-chord tokens are returned unchanged.
 */
export function chordToNashville(
  symbol: string,
  key: string | null | undefined,
): string {
  const wrapped = /^\((.*)\)$/.exec(symbol.trim());
  if (wrapped) return `(${chordToNashville(wrapped[1]!, key)})`;
  const c = parseChord(symbol);
  if (!c) return symbol;

  const tonicPc = majorTonicPitchClass(key);
  const preferFlats = keyPrefersFlats(key || "G");

  const pcToDegree = (pc: number): string => {
    const rel = (((pc - tonicPc) % 12) + 12) % 12;
    const mapSharp: Record<number, string> = {
      0: "1",
      1: "#1",
      2: "2",
      3: "b3",
      4: "3",
      5: "4",
      6: "#4",
      7: "5",
      8: "b6",
      9: "6",
      10: "b7",
      11: "7",
    };
    const mapFlat: Record<number, string> = {
      0: "1",
      1: "b2",
      2: "2",
      3: "b3",
      4: "3",
      5: "4",
      6: "b5",
      7: "5",
      8: "b6",
      9: "6",
      10: "b7",
      11: "7",
    };
    return (preferFlats ? mapFlat : mapSharp)[rel] ?? "1";
  };

  const rootDeg = pcToDegree(c.root);
  const bassPart = c.bass != null ? `/${pcToDegree(c.bass)}${c.bassSuffix ?? ""}` : "";
  return `${rootDeg}${c.suffix}${bassPart}`;
}

// --- Syllable segmentation ---------------------------------------------------

export interface WordSyllable {
  text: string;
  /** Offset from the start of the word token. */
  start: number;
  /** End offset from the start of the word token. */
  end: number;
}

const DIGRAPH_ONSETS = new Set([
  "bl", "br", "ch", "cl", "cr", "dr", "fl", "fr", "gh", "gl", "gr",
  "ph", "pl", "pr", "qu", "sc", "sh", "sk", "sl", "sm", "sn", "sp",
  "st", "sw", "th", "tr", "tw", "wh", "wr",
]);

function isVowelChar(ch: string, idx: number, word: string): boolean {
  const lower = ch.toLowerCase();
  if ("aeiou".includes(lower)) return true;
  if (lower === "y") return idx > 0 && idx < word.length;
  return false;
}

/**
 * Split a lyric word into natural singing syllables so chords snap to syllable
 * onsets (e.g. `A·ma·zing`, `sur·ren·der`, `ho·ly`) rather than individual letters.
 */
export function splitWordIntoSyllables(word: string): WordSyllable[] {
  if (word.length <= 3) {
    return [{ text: word, start: 0, end: word.length }];
  }

  // Locate the alphabetic core (leaving leading/trailing punctuation attached to outer syllables).
  const leadMatch = /^[^A-Za-z]+/.exec(word);
  const trailMatch = /[^A-Za-z]+$/.exec(word);
  const coreStart = leadMatch ? leadMatch[0].length : 0;
  const coreEnd = trailMatch ? word.length - trailMatch[0].length : word.length;
  const core = word.slice(coreStart, coreEnd);

  if (core.length <= 3) {
    return [{ text: word, start: 0, end: word.length }];
  }

  // Find vowel groups in `core` (splitting hiatus vowel pairs like 'u-ia' in 'Alleluia',
  // 'i-a' in 'Hosanna/Gloria', 'e-a' in 'creat-or').
  const HIATUS_PAIRS = new Set(["ia", "ea", "io", "ua", "eo"]);
  const groups: { start: number; end: number }[] = [];
  let i = 0;
  while (i < core.length) {
    if (isVowelChar(core[i]!, i, core)) {
      let start = i;
      i++;
      while (i < core.length && isVowelChar(core[i]!, i, core)) {
        const pair = (core[i - 1]! + core[i]!).toLowerCase();
        // Split 'u-ia' at the end of words like "Allelu-ia", or two-vowel hiatus pairs like 'i-a' in "Glor-ia"
        const isTrailingUia =
          pair === "ui" &&
          start >= 2 &&
          i + 2 === core.length &&
          core[i + 1]?.toLowerCase() === "a";
        const isTwoVowelHiatus =
          HIATUS_PAIRS.has(pair) &&
          start === i - 1 &&
          start >= 2 &&
          !(i >= 2 && core.slice(i - 2, i + 1).toLowerCase() === "uia");
        if (isTrailingUia || isTwoVowelHiatus) {
          groups.push({ start, end: i });
          start = i;
        }
        i++;
      }
      groups.push({ start, end: i });
    } else {
      i++;
    }
  }

  // Merge a silent final 'e' (or '-es'/'-ed' unless preceded by t/d for '-ed') into the previous vowel group.
  if (groups.length > 1) {
    const last = groups[groups.length - 1]!;
    const tail = core.slice(last.start).toLowerCase();
    const beforeLast = core.slice(0, last.start).toLowerCase();
    const isSilentE =
      tail === "e" && !beforeLast.endsWith("l"); // "grace", "save", "name" vs "table"/"humble"
    const isSilentEd =
      tail === "ed" && !/[td]$/.test(beforeLast); // "saved", "loved" vs "lifted", "shouted"
    const isSilentEs =
      tail === "es" && !/(s|sh|ch|x|z|c|g)$/.test(beforeLast); // "makes", "comes"
    if (isSilentE || isSilentEd || isSilentEs) {
      groups.pop();
    }
  }

  if (groups.length <= 1) {
    return [{ text: word, start: 0, end: word.length }];
  }

  const splitPointsInCore: number[] = [];
  for (let g = 0; g < groups.length - 1; g++) {
    const v1End = groups[g]!.end;
    const v2Start = groups[g + 1]!.start;
    const consonantCount = v2Start - v1End;
    let cut: number;
    if (consonantCount <= 0) {
      cut = v1End;
    } else if (consonantCount === 1) {
      // V-CV (e.g. A-ma-zing, ho-ly)
      cut = v1End;
    } else if (consonantCount === 2) {
      const pair = core.slice(v1End, v2Start).toLowerCase();
      // Keep onset digraphs together if after a short prefix, otherwise VC-CV (sur-ren-der, wor-ship)
      cut = DIGRAPH_ONSETS.has(pair) && v1End === 1 ? v1End : v1End + 1;
    } else {
      const lastTwo = core.slice(v2Start - 2, v2Start).toLowerCase();
      cut = DIGRAPH_ONSETS.has(lastTwo) ? v2Start - 2 : v2Start - 1;
    }
    if (cut > 0 && cut < core.length) {
      splitPointsInCore.push(coreStart + cut);
    }
  }

  if (splitPointsInCore.length === 0) {
    return [{ text: word, start: 0, end: word.length }];
  }

  const out: WordSyllable[] = [];
  let cursor = 0;
  for (const pt of splitPointsInCore) {
    if (pt > cursor) {
      out.push({ text: word.slice(cursor, pt), start: cursor, end: pt });
      cursor = pt;
    }
  }
  if (cursor < word.length) {
    out.push({ text: word.slice(cursor), start: cursor, end: word.length });
  }
  return out;
}


