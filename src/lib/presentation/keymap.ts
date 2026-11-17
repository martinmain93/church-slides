/**
 * Keyboard map for the presentation view.
 *
 * Centralised so the same definition can drive the runtime handler, the
 * help overlay (later), and tests.
 */

export type PresentationAction =
  | "nextSlide"
  | "prevSlide"
  | "nextSong"
  | "prevSong"
  | "toggleBlankBlack"
  | "toggleBlankWhite"
  | "toggleFullscreen"
  | "exitFullscreenOrPresentation"
  | "toggleDebug";

export interface KeyBinding {
  /** `KeyboardEvent.key` value(s) that trigger the action. */
  keys: string[];
  action: PresentationAction;
  /** `cmd` on macOS, `ctrl` on Windows/Linux. */
  meta?: boolean;
  ctrl?: boolean;
  shift?: boolean;
  description: string;
}

export const PRESENTATION_KEYMAP: KeyBinding[] = [
  { keys: ["ArrowRight"], action: "nextSlide", description: "Next slide" },
  { keys: ["ArrowLeft"], action: "prevSlide", description: "Previous slide" },
  { keys: ["ArrowDown"], action: "nextSong", description: "Next song" },
  { keys: ["ArrowUp"], action: "prevSong", description: "Previous song" },
  { keys: ["b", "B"], action: "toggleBlankBlack", description: "Black blank" },
  { keys: ["w", "W"], action: "toggleBlankWhite", description: "White blank" },
  { keys: ["F11"], action: "toggleFullscreen", description: "Fullscreen" },
  {
    keys: ["Escape"],
    action: "exitFullscreenOrPresentation",
    description: "Exit fullscreen or presentation",
  },
  // Debug panel — Cmd+D on macOS, Ctrl+D elsewhere; we accept either.
  {
    keys: ["d", "D"],
    action: "toggleDebug",
    meta: true,
    description: "Toggle debug panel",
  },
  {
    keys: ["d", "D"],
    action: "toggleDebug",
    ctrl: true,
    description: "Toggle debug panel",
  },
];

export interface MatchInput {
  key: string;
  metaKey: boolean;
  ctrlKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
}

/** Return the first matching binding, or `null`. */
export function matchKey(
  bindings: KeyBinding[],
  ev: MatchInput,
): KeyBinding | null {
  for (const binding of bindings) {
    if (!binding.keys.includes(ev.key)) continue;
    if (Boolean(binding.meta) !== Boolean(ev.metaKey && !ev.ctrlKey)) {
      // require meta when set; forbid meta when unset (and no ctrl)
      if (binding.meta && !ev.metaKey) continue;
      if (!binding.meta && !binding.ctrl && (ev.metaKey || ev.ctrlKey)) continue;
    }
    if (binding.ctrl && !ev.ctrlKey) continue;
    if (binding.meta && !ev.metaKey) continue;
    if (binding.shift && !ev.shiftKey) continue;
    return binding;
  }
  return null;
}
