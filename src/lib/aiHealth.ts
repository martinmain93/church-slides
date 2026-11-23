/**
 * `useAiHealth()` — one verdict on the live AI for a glance: getting ready,
 * all good, or a problem with a reason.
 *
 * The AI pill says everything in detail, but on a single screen it lives in
 * the bottom bar, which stays hidden so the congregation sees only lyrics. So
 * nobody could tell, without hovering, whether the AI had come up at all.
 * This feeds the small corner indicator (spinner → tick → gone, or a red X)
 * and the notice in the bottom bar that says why.
 *
 * A problem can be dismissed: someone who didn't want the AI anyway shouldn't
 * have a red X in the corner all service. Dismissals are kept (per problem)
 * until the AI next comes up fine, so a computer that can't run the AI isn't
 * re-flagged at every service, but a fresh failure after a good one is.
 */

import { useEffect, useState } from "react";

import { type AiAvailability, availabilitySummary, useAiAvailability } from "@/lib/aiAvailability";
import { type AiStatusSnapshot, useAiStatus } from "@/lib/aiDecisions";
import { createLiveStore, useLiveStore } from "@/lib/liveStore";
import { usePipelineHealth } from "@/lib/pipelineHealth";
import { type SessionStartFailure, useSessionStartFailure } from "@/lib/recordingClient";
import { getSharedConnection } from "@/lib/sidecarConnection";

export interface AiProblem {
  /** Stable per kind of problem — what a dismissal remembers. */
  id: string;
  title: string;
  detail: string;
  /** Try again, where there is something to try. */
  retry?: () => void;
}

export type AiHealth =
  /** Nothing to report: the AI is off where off is normal (operator view). */
  | { kind: "idle" }
  | { kind: "starting" }
  | { kind: "ok" }
  | { kind: "problem"; problem: AiProblem };

export interface AiHealthInputs {
  availability: AiAvailability;
  status: Pick<AiStatusSnapshot, "mode"> | null;
  startFailure: SessionStartFailure | null;
  /** The microphone is feeding levels right now. */
  micLive: boolean;
  /** …and has been silent (no levels at all, not just quiet) long enough to
   * call it gone rather than still opening. */
  micGone: boolean;
  /** Hosted speech is refusing requests (Groq's rate limit). */
  asrRefused: boolean;
  /** Single screen: the AI starts on, so off means someone or something
   * switched it off. In the operator view it starts off, and off is normal. */
  aiOffIsProblem: boolean;
}

const BY_HAND = "Slides only move when someone moves them.";

export function describeAiHealth(i: AiHealthInputs): AiHealth {
  const aiOff = i.status ? i.status.mode === "off" : i.startFailure?.aiMode === "off";
  // Operator view, AI off: none of what the AI would need is relevant yet.
  if (aiOff && !i.aiOffIsProblem) return { kind: "idle" };

  const a = i.availability;
  switch (a.kind) {
    case "checking":
    case "checking_model":
    case "checking_machine":
    case "downloading":
      return { kind: "starting" };
    case "engine_unavailable":
    case "too_slow":
    case "check_unavailable":
    case "download_failed":
    case "needs_download":
      return {
        kind: "problem",
        problem: {
          id: `availability:${a.kind}`,
          title: AVAILABILITY_TITLES[a.kind],
          detail: `${availabilitySummary(a)} ${BY_HAND}`,
        },
      };
    case "ready":
      break;
  }

  if (i.startFailure) {
    return {
      kind: "problem",
      problem: {
        id: "start_failed",
        title: "The AI couldn't start",
        detail:
          `${i.startFailure.message.replace(/\.$/, "")}. If it's the microphone, check it's plugged in and ` +
          `chosen in Settings → Audio input. ${BY_HAND}`,
        retry: i.startFailure.retry,
      },
    };
  }
  if (!i.status) return { kind: "starting" };
  if (aiOff) {
    return {
      kind: "problem",
      problem: {
        id: "ai_off",
        title: "The AI is off",
        detail: `${BY_HAND} Turn it back on with the AI button above.`,
      },
    };
  }
  // Deprecated (2026-11-23): only Groq's hosted speech refuses requests, and
  // it is no longer offered.
  if (i.asrRefused) {
    return {
      kind: "problem",
      problem: {
        id: "asr_refused",
        title: "Online speech is turning requests away",
        detail:
          "Groq's usage limit was reached, so the AI can't hear the band. Switch to speech on " +
          "this computer in Settings → AI that follows the songs.",
      },
    };
  }
  if (!i.micLive) {
    if (!i.micGone) return { kind: "starting" };
    return {
      kind: "problem",
      problem: {
        id: "no_audio",
        title: "No sound is coming in",
        detail:
          "The AI isn't getting any audio from the microphone or sound desk. Check it's " +
          `plugged in and chosen in Settings → Audio input. ${BY_HAND}`,
      },
    };
  }
  return { kind: "ok" };
}

const AVAILABILITY_TITLES = {
  engine_unavailable: "The AI isn't available",
  too_slow: "This computer is too slow for the AI",
  check_unavailable: "The AI needs a computer check",
  download_failed: "The speech model couldn't download",
  needs_download: "The AI's speech model isn't downloaded",
} as const;

// --- microphone liveness ----------------------------------------------------

/** No level at all for this long, with the AI on, means the input is gone. */
const MIC_GONE_MS = 10_000;
/** A level within this long counts as the microphone feeding us now. */
const MIC_LIVE_MS = 3_000;

// The sidecar sends ~10 levels a second; only the time of the last one is
// kept, and read on a slow tick, so nothing here re-renders at that rate.
let lastLevelAt = 0;
let levelsWired = false;
function wireLevels() {
  if (levelsWired) return;
  levelsWired = true;
  getSharedConnection()
    .then((conn) => conn.onMessage("audio_level", () => (lastLevelAt = Date.now())))
    .catch(() => {
      levelsWired = false;
    });
}

function useMicLiveness(watching: boolean): { live: boolean; gone: boolean } {
  const [state, setState] = useState({ live: false, gone: false });
  useEffect(() => {
    wireLevels();
    if (!watching) {
      setState({ live: false, gone: false });
      return;
    }
    const since = Date.now();
    const tick = () => {
      const now = Date.now();
      const live = now - lastLevelAt < MIC_LIVE_MS;
      const gone = !live && now - Math.max(lastLevelAt, since) >= MIC_GONE_MS;
      setState((prev) => (prev.live === live && prev.gone === gone ? prev : { live, gone }));
    };
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [watching]);
  return state;
}

// --- dismissals -------------------------------------------------------------

const DISMISSED_KEY = "auto-presenter.ai-health-dismissed";

function loadDismissed(): string[] {
  try {
    const raw = localStorage.getItem(DISMISSED_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

// Shared by the corner indicator and the notice, which are separate
// components: dismissing in one has to clear the other.
const dismissedStore = createLiveStore<string[]>(loadDismissed());

function setDismissed(ids: string[]) {
  dismissedStore.set(ids);
  try {
    if (ids.length) localStorage.setItem(DISMISSED_KEY, JSON.stringify(ids));
    else localStorage.removeItem(DISMISSED_KEY);
  } catch {
    // Non-critical: the dismissal just won't outlive this run.
  }
}

/** For tests: forget dismissals made by an earlier test. */
export function resetAiHealthDismissals() {
  setDismissed([]);
}

// --- the hook ---------------------------------------------------------------

/** A problem must hold this long before it's shown: the AI's status from a
 * previous service, say, can read "off" for a moment before this one's
 * session reports in. */
const PROBLEM_SETTLE_MS = 2000;

export interface AiHealthApi {
  health: AiHealth;
  /** The problem shown, unless dismissed. */
  problem: AiProblem | null;
  dismiss: () => void;
}

export function useAiHealth({ aiOffIsProblem }: { aiOffIsProblem: boolean }): AiHealthApi {
  const { availability } = useAiAvailability();
  const status = useAiStatus();
  const startFailure = useSessionStartFailure();
  const pipeline = usePipelineHealth();
  const aiOn = status != null && status.mode !== "off";
  const mic = useMicLiveness(aiOn && availability.kind === "ready" && !startFailure);

  const raw = describeAiHealth({
    availability,
    status,
    startFailure,
    micLive: mic.live,
    micGone: mic.gone,
    asrRefused: pipeline?.asrRefused ?? false,
    aiOffIsProblem,
  });
  const rawProblem = raw.kind === "problem" ? raw.problem : null;

  // Debounce problems; everything else shows at once.
  const [settledId, setSettledId] = useState<string | null>(null);
  useEffect(() => {
    if (!rawProblem) {
      setSettledId(null);
      return;
    }
    const id = window.setTimeout(() => setSettledId(rawProblem.id), PROBLEM_SETTLE_MS);
    return () => window.clearTimeout(id);
  }, [rawProblem?.id]); // eslint-disable-line react-hooks/exhaustive-deps -- keyed on the id

  const health: AiHealth =
    raw.kind === "problem" && settledId !== raw.problem.id ? { kind: "starting" } : raw;

  const dismissed = useLiveStore(dismissedStore);
  // Working again: forget dismissals, so the next failure is flagged.
  useEffect(() => {
    if (health.kind === "ok" && dismissedStore.get().length) setDismissed([]);
  }, [health.kind]);

  const problem =
    health.kind === "problem" && !dismissed.includes(health.problem.id) ? health.problem : null;

  return {
    health,
    problem,
    dismiss: () => {
      if (problem && !dismissedStore.get().includes(problem.id)) {
        setDismissed([...dismissedStore.get(), problem.id]);
      }
    },
  };
}
