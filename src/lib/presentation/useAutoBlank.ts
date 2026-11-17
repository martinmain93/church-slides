/**
 * What the projected screen's blank state actually is: the operator's blank,
 * or the engine's "nobody is singing these words".
 *
 * The engine has decided this for a long time (`singing_state.blanking`) and
 * withheld its own slide moves while it was true, but no presenter view read
 * it — the debug panel showed "screen blanked" over a screen that wasn't. So a
 * live service sat on stale lyrics through prayer and long silences.
 *
 * Kept separate from the presentation state's `blankMode`, which is the
 * operator's: an automatic blank must not toggle their Blank button, and
 * lifting it must not undo a blank they chose.
 *
 *   - The operator's blank always wins.
 *   - Only while the AI is driving (`active`). Watching or off, the operator
 *     is driving, and the screen is theirs.
 *   - Never over a passage a person put up: someone reading Jeremiah 29:11
 *     aloud is exactly "nobody singing", and the verse is the point.
 */

import type { BlankMode } from "@/lib/presentation/state";
import { useSingingState } from "@/lib/singingState";

export interface AutoBlank {
  /** What the screen shows. */
  blankMode: BlankMode;
  /** Blank only because nobody is singing — for the operator's indicator. */
  auto: boolean;
}

export function useAutoBlank(
  manual: BlankMode,
  aiActive: boolean,
  passageUp: boolean,
): AutoBlank {
  const singing = useSingingState();
  if (manual !== "none") return { blankMode: manual, auto: false };
  const auto = aiActive && singing.blanking && !passageUp;
  return { blankMode: auto ? "black" : "none", auto };
}
