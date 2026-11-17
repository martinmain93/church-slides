/**
 * The app-wide colour for a song's key — one hue per major key, walking the
 * rainbow chromatically (C red → Db orange → … → B magenta → back to C), so
 * neighbouring keys sit next to each other in colour. A minor key takes its
 * relative major's colour (Am = C, Em = G): same key signature, same chords.
 *
 * This is for the key of a *song*, not for individual chords within one.
 *
 * Generated in OKLCH at a fixed lightness (0.545) and hue step (30°, starting
 * at 25°), chroma clipped to the sRGB gamut, so every swatch reads equally
 * bright and white text on each clears WCAG AA (4.6–5.4 : 1). Stored as hex
 * because the band screens run on whatever phone a musician brings, and old
 * WebKit has no `oklch()`.
 */

import { parseChord } from "./chord";

/** Indexed by the major key's pitch class, 0 = C. */
export const KEY_COLORS = [
  "#bb3f3d", // C
  "#a95703", // Db
  "#8d6a06", // D
  "#6e7805", // Eb
  "#1a872b", // E
  "#04836d", // F
  "#067f8a", // F#
  "#0779a9", // G
  "#4169cc", // Ab
  "#7957c2", // A
  "#9d49a2", // Bb
  "#b33f75", // B
] as const;

/** For a key we can't read — neutral, and still legible under white text. */
export const UNKNOWN_KEY_COLOR = "#5b6270";

/** Pitch class of the major key sharing this key's signature, or null. */
export function keyFamily(key: string | null | undefined): number | null {
  if (!key) return null;
  const k = parseChord(key);
  if (!k) return null;
  const minor = /^(m(?!aj)|min|-)/.test(k.suffix);
  return minor ? (k.root + 3) % 12 : k.root;
}

export function keyColor(key: string | null | undefined): string {
  const pc = keyFamily(key);
  return pc == null ? UNKNOWN_KEY_COLOR : KEY_COLORS[pc]!;
}
