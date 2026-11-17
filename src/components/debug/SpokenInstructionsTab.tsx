/**
 * "Let's go back to verse one" as a slide change, via a local model.
 *
 * Off by default and unmeasured on real utterances — see
 * `docs/done/SPOKEN_INSTRUCTIONS.md`. The toggle lives here rather than gated
 * behind an active matcher (the belief-state tab's placement, before this
 * tab existed) so it can be turned on before a session starts, not only
 * once one is already running.
 */

import { cn } from "@/lib/utils";
import { setSpokenInstructions, useSpokenInstructions } from "@/lib/spokenInstructions";
import { InstructionModelPicker } from "@/components/debug/InstructionModelPicker";
import { useToggleRequest } from "@/components/debug/useToggleRequest";

export function SpokenInstructionsTab() {
  const state = useSpokenInstructions();
  const onToggle = useToggleRequest(
    "spoken instructions",
    setSpokenInstructions,
  );
  return (
    <div className="flex-1 overflow-y-auto px-4 py-3 text-xs">
      <label className="mb-3 flex items-start gap-2 rounded border border-white/10 bg-white/5 px-2 py-2">
        <input
          type="checkbox"
          className="mt-0.5"
          checked={state.enabled}
          onChange={(e) => onToggle(e.target.checked)}
        />
        <span className="font-sans leading-tight">
          <span className="text-zinc-200">Act on spoken instructions</span>
          <span className="block text-[10px] text-zinc-400">
            Moves the slide with the same authority as an operator override.
          </span>
        </span>
      </label>

      <InstructionModelPicker />

      <div className="mb-1 mt-3 font-sans text-[11px] uppercase tracking-wide text-zinc-500">
        Utterances heard, most recent first
      </div>
      {state.recent.length === 0 ? (
        <p className="text-zinc-500">nothing yet</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {state.recent.map((e) => (
            <li
              // `recent` is prepended to (newest first), so an index key
              // would relabel every existing row on each new utterance
              // instead of recognizing one row was inserted. `tSeconds` is
              // the utterance's own timestamp — stable identity regardless
              // of where it sits in the list.
              key={e.tSeconds}
              className="rounded border border-white/10 bg-white/5 px-2 py-1.5"
            >
              <div className="flex items-center gap-2">
                <span className="tabular-nums text-zinc-500">
                  {e.tSeconds.toFixed(1)}s
                </span>
                <span
                  className={cn(
                    "rounded px-1.5 py-0.5 text-[10px]",
                    e.applied
                      ? "bg-sky-500/15 text-sky-300"
                      : "bg-white/10 text-zinc-400",
                  )}
                >
                  {!e.applied
                    ? "no action"
                    : e.scriptureDisplay === "off"
                      ? "scripture down"
                      : e.scriptureReference
                        ? `${e.scriptureReference} (${e.scriptureDisplay})`
                        : `item ${e.itemIdx} slide ${e.slideIdx}`}
                </span>
                {e.title && (
                  <span className="text-zinc-400">
                    {e.title}
                    {e.score != null ? ` · ${e.score.toFixed(2)}` : ""}
                  </span>
                )}
              </div>
              <div className="mt-1 text-zinc-300">
                &ldquo;{e.utterance}&rdquo;
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
