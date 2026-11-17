/**
 * Display settings for the scripture pane (live passages: the operator's
 * Scripture tab and passages asked for out loud).
 *
 * Persisted inside the existing app settings blob as a single nested object,
 * so `AppSettings` grows one field however many display options accumulate
 * here. The Rust mirror is `scriptures.rs::ScriptureSettings`; the defaults
 * must match on both sides, and `DEFAULT_SCRIPTURE_SETTINGS` is the copy the
 * frontend falls back to when a settings blob predates the feature.
 */

/** Where the pane sits relative to the lyrics. */
export type ScripturePosition = "bottom" | "top" | "left" | "right";

/** A semantic size, resolved to real units per position by the renderer. */
export type ScriptureSize = "small" | "medium" | "large";

export type ScriptureFont = "sans" | "serif";

export interface ScriptureSettings {
  /**
   * No longer read or shown (2026-10-22): a passage wears the display look —
   * the same style as the lyrics, or scripture's own override
   * (`displayLook.ts`, `saveScriptureLook`) — and sits in a lower third beside
   * them. Kept so settings saved before then, and the Rust mirror, still load.
   */
  position: ScripturePosition;
  size: ScriptureSize;
  font: ScriptureFont;
  show_reference: boolean;
  show_translation: boolean;
  translation: string;
  /**
   * Seconds a cue is protected from replacement. **Zero by default, on
   * evidence**: measured over 19 minutes of real audio, following the slide
   * directly matches the human change rate and flashes once, while an 8s floor
   * removes that flash and costs 2.2 points of accuracy. Kept as a knob
   * because the freeze and lost-mode episodes it was designed for are absent
   * from the goldens that were measured. See `docs/done/BIBLE_INTEGRATION.md` §3.
   *
   * No longer read or shown (removed from Settings 2026-10-17); kept in the
   * type so settings saved before then still load.
   */
  dwell_floor_seconds: number;
}

export const DEFAULT_SCRIPTURE_SETTINGS: ScriptureSettings = {
  // Lower third: it needs no lyric reflow at all, and it is what broadcast
  // does for exactly this problem.
  position: "bottom",
  size: "medium",
  font: "sans",
  show_reference: true,
  show_translation: true,
  translation: "web",
  dwell_floor_seconds: 0,
};

/** Tolerate a settings blob written before this feature existed. */
export function withScriptureDefaults(
  partial: Partial<ScriptureSettings> | undefined | null,
): ScriptureSettings {
  return { ...DEFAULT_SCRIPTURE_SETTINGS, ...(partial ?? {}) };
}
