/**
 * The live AI's health at a glance, for whoever runs the service.
 *
 *   - `AiHealthCorner` — single screen only: a tiny mark in the bottom-left
 *     corner. A blue spinner while the AI gets ready, a green tick that draws
 *     and fades once it's listening, or a red X that stays while something's
 *     wrong. It's the one thing drawn over the lyrics, so it's small and dim.
 *   - `AiHealthNotice` — what's wrong, in words, with a dismiss (and a retry
 *     where one helps). On a single screen it lives in the bottom bar, so it's
 *     only seen when someone brings the bar up; in the operator view it sits
 *     under the header, on the operator's own screen — never the audience's.
 *
 * Dismissing the notice clears the X too: someone who didn't want the AI
 * shouldn't have to look at it all service.
 *
 * One `useAiHealth` per view, shared through `AiHealthProvider`, so the
 * corner and the notice always agree (the problem settles on a timer each
 * hook would run on its own).
 */

import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { X } from "lucide-react";

import { type AiHealthApi, useAiHealth } from "@/lib/aiHealth";
import { cn } from "@/lib/utils";

const AiHealthContext = createContext<AiHealthApi | null>(null);

export function AiHealthProvider({
  aiOffIsProblem,
  children,
}: {
  aiOffIsProblem: boolean;
  children: ReactNode;
}) {
  const api = useAiHealth({ aiOffIsProblem });
  return <AiHealthContext.Provider value={api}>{children}</AiHealthContext.Provider>;
}

/** How long the tick stays before it's gone (matches `animate-pop-fade`). */
const TICK_MS = 1600;

export function AiHealthCorner() {
  const api = useContext(AiHealthContext);
  const kind = api?.health.kind ?? "idle";
  const [tick, setTick] = useState(false);
  const prevKind = useRef(kind);

  // The tick marks the AI *becoming* ready, so it plays once on the way in.
  useEffect(() => {
    const was = prevKind.current;
    prevKind.current = kind;
    if (kind !== "ok" || was === "ok") return;
    setTick(true);
    const id = window.setTimeout(() => setTick(false), TICK_MS);
    return () => window.clearTimeout(id);
  }, [kind]);

  let mark: ReactNode = null;
  let label = "";
  if (kind === "starting") {
    label = "The AI is getting ready";
    mark = (
      <span className="block size-4 animate-spin rounded-full border-2 border-sky-400/25 border-t-sky-400" />
    );
  } else if (kind === "ok" && tick) {
    label = "The AI is listening";
    mark = (
      <span className="flex size-5 animate-pop-fade items-center justify-center rounded-full bg-emerald-500/90">
        <svg viewBox="0 0 16 16" className="size-3.5" fill="none" aria-hidden>
          <path
            d="M3.5 8.5l3 3 6-7"
            stroke="white"
            strokeWidth={2.2}
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeDasharray={24}
            className="animate-check-draw"
          />
        </svg>
      </span>
    );
  } else if (kind === "problem" && api?.problem) {
    label = `${api.problem.title} — bring up the bottom bar for more`;
    mark = (
      <span className="flex size-5 items-center justify-center rounded-full bg-red-600/90 text-white">
        <X className="size-3.5" strokeWidth={3} />
      </span>
    );
  }
  if (!mark) return null;

  return (
    <div
      data-testid="ai-health-corner"
      data-kind={kind}
      role="status"
      aria-label={label}
      title={label}
      // Gone while the bottom bar (its earlier sibling) is up — the bar is
      // translucent and carries the detail; and no pointer events, so it
      // never steals the hover that brings the bar up.
      className="pointer-events-none absolute bottom-3 left-3 z-10 opacity-80 transition-opacity duration-200 [[data-shown=true]~&]:opacity-0"
    >
      {mark}
    </div>
  );
}

export function AiHealthNotice({ className }: { className?: string }) {
  const api = useContext(AiHealthContext);
  const problem = api?.problem;
  if (!problem) return null;
  return (
    <div
      data-testid="ai-health-notice"
      role="alert"
      className={cn(
        "flex items-start gap-2.5 rounded-md border border-red-500/40 bg-red-500/15 px-3 py-2 text-xs",
        className,
      )}
    >
      <span className="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full bg-red-600 text-white">
        <X className="size-3" strokeWidth={3} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="font-medium">{problem.title}</div>
        <div className="mt-0.5 opacity-80">{problem.detail}</div>
      </div>
      {problem.retry && (
        <button
          type="button"
          onClick={(e) => {
            // Give the arrow keys back to the service.
            e.currentTarget.blur();
            problem.retry?.();
          }}
          className="shrink-0 rounded px-2 py-0.5 font-medium underline-offset-2 hover:underline"
        >
          Try again
        </button>
      )}
      <button
        type="button"
        aria-label="Dismiss"
        title="Dismiss"
        onClick={(e) => {
          e.currentTarget.blur();
          api?.dismiss();
        }}
        className="shrink-0 rounded p-0.5 opacity-70 transition-opacity hover:opacity-100"
      >
        <X className="size-3.5" />
      </button>
    </div>
  );
}
