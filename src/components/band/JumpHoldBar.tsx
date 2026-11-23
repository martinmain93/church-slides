/**
 * The leader's say on a surprising AI jump: "Going to <song>", the words
 * that set it off, a countdown, and Go now / Stop. Shown only while a jump
 * waits (`jumpHold.ts`); when the countdown ends, the slides go on their own.
 */

import type { JumpHoldNotice } from "@/lib/presentation/jumpHold";

export function JumpHoldBar({
  hold,
  onAnswer,
}: {
  hold: JumpHoldNotice;
  onAnswer: (go: boolean) => void;
}) {
  return (
    <div
      role="alert"
      data-testid="jump-hold-bar"
      className="fixed inset-x-0 bottom-0 z-40 border-t-2 border-yellow-300 bg-yellow-400 text-black shadow-[0_-8px_24px_rgba(250,204,21,0.45)]"
    >
      {/* The pulse: a glow behind the text, so the words themselves stay steady. */}
      <div aria-hidden className="pointer-events-none absolute inset-0 animate-pulse bg-yellow-200/60" />
      <div className="relative flex items-center gap-3 px-4 py-3">
        <div className="min-w-0 flex-1">
          <p className="truncate text-[1.05em] font-semibold">Going to {hold.title}</p>
          {hold.words && <p className="truncate text-[0.85em] text-black/70">&ldquo;{hold.words}&rdquo;</p>}
        </div>
        <button
          type="button"
          onClick={() => onAnswer(false)}
          className="shrink-0 rounded-md border-2 border-black/70 px-4 py-2 text-[0.95em] font-semibold"
        >
          Stop
        </button>
        <button
          type="button"
          onClick={() => onAnswer(true)}
          className="shrink-0 rounded-md bg-black px-4 py-2 text-[0.95em] font-semibold text-yellow-300"
        >
          Go now
        </button>
      </div>
      <div className="relative h-1.5 bg-black/15">
        {/* Keyed by the hold, so a new jump starts a fresh countdown. */}
        <div
          key={hold.id}
          data-testid="jump-hold-progress"
          className="h-full bg-black/70"
          style={{ animation: `jump-hold-countdown ${hold.durationMs}ms linear forwards` }}
        />
      </div>
      <style>{"@keyframes jump-hold-countdown { from { width: 100%; } to { width: 0%; } }"}</style>
    </div>
  );
}
