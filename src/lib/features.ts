/**
 * Features still in development: on in `pnpm tauri:dev` (and tests), hidden
 * in release builds.
 *
 * Hiding means the switch that turns a feature on isn't shown; the code still
 * ships. Each flag's consumers also treat the feature as off, so a setting
 * saved while it was visible (say, by a 0.0.1 install) can't leave it on.
 */
export const FEATURES = {
  /** "Go back to verse one" said out loud: Settings → AI → Spoken requests.
   * Shipped 2026-10-17: on by itself once a Groq key is saved, unless the
   * operator switched it off. */
  spokenInstructions: true,
  /** A verse on screen now, from the operator's Scripture tab or by asking
   * for it out loud, and the Bibles it reads from: Settings → Scripture's
   * translations (download, import) and how passages look. Shipped
   * 2026-10-17. */
  liveScripture: true,
  /** A spoken instruction in the song editor that renames, splits, merges,
   * moves or rewrites slides, not just charts them. Not shipped: it's a
   * second slide editor with one level of undo. Charting by voice ships. */
  voiceSlideEdits: import.meta.env.DEV,
  /** The online model's next-chord suggestions in the song editor. Not
   * shipped: measured 2026-10-21 (`research/eval/eval_chord_suggest.py`, 30 songs)
   * it answered 7/28 and only 1 of 8 placements was right — worse than
   * copying an identical earlier line. The local Suggest rules ship. */
  chordModelSuggestions: import.meta.env.DEV,
} as const;
