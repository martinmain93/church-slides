/**
 * Beside "Start service": whether the service will have the AI, before it
 * starts — and the switch for it (`useAiEnabled`).
 *
 *   - "AI Ready"         — on, model here, audio input chosen. Click: off.
 *   - "Turn on AI"       — switched off. Click: on.
 *   - "AI Not Ready"     — on, but setup isn't finished (no audio input
 *                          chosen, no speech model). Click: finish it — the
 *                          audio setup if that's what's missing, else Settings.
 *   - "AI Not available" — this computer can't run it (too slow, or no
 *                          engine). The reason is on hover.
 *
 * No spinner: this is a glance before a service, not a progress report.
 * While the engine is still answering it reads as ready, which it almost
 * always turns out to be.
 */

import { useEffect, useState } from "react";
import { Sparkles } from "lucide-react";

import { type AiAvailability, availabilitySummary, useAiAvailability } from "@/lib/aiAvailability";
import { setAiEnabled, useAiEnabled } from "@/lib/aiPrefs";
import { AUDIO_PREFS_CHANGED_EVENT, loadAudioInputPrefs } from "@/lib/audioInputPrefs";
import { cn } from "@/lib/utils";

const OPEN_WIZARD_EVENT = "auto-presenter:open-audio-wizard";
/** Dispatch on `window` to go to the Settings page (AppShell listens). */
export const OPEN_SETTINGS_EVENT = "auto-presenter:open-settings";

export type AiReadiness =
  | { kind: "ready"; detail: string }
  | { kind: "not_ready"; detail: string; fix: "audio_setup" | "settings" }
  | { kind: "unavailable"; detail: string };

export function describeAiReadiness(a: AiAvailability, audioChosen: boolean): AiReadiness {
  switch (a.kind) {
    case "engine_unavailable":
    case "too_slow":
      return { kind: "unavailable", detail: availabilitySummary(a) };
    case "needs_download":
    case "downloading":
    case "download_failed":
    case "check_unavailable":
      return {
        kind: "not_ready",
        detail: availabilitySummary(a),
        // The audio setup downloads the model as it finishes.
        fix: audioChosen ? "settings" : "audio_setup",
      };
    case "checking":
    case "checking_model":
    case "checking_machine":
    case "ready":
      if (!audioChosen) {
        return {
          kind: "not_ready",
          detail: "No audio input is set up yet — the AI needs to hear the band.",
          fix: "audio_setup",
        };
      }
      return {
        kind: "ready",
        detail: a.kind === "ready" ? availabilitySummary(a) : "",
      };
  }
}

function useAudioChosen(): boolean {
  const [chosen, setChosen] = useState(() => loadAudioInputPrefs().setup_completed);
  useEffect(() => {
    const update = () => setChosen(loadAudioInputPrefs().setup_completed);
    window.addEventListener(AUDIO_PREFS_CHANGED_EVENT, update);
    return () => window.removeEventListener(AUDIO_PREFS_CHANGED_EVENT, update);
  }, []);
  return chosen;
}

const PILL =
  "inline-flex h-8 shrink-0 items-center gap-1.5 rounded-full border px-3 text-[10px] uppercase tracking-wide transition-colors";

export function AiReadyPill({ className }: { className?: string }) {
  const enabled = useAiEnabled();
  const { availability } = useAiAvailability();
  const readiness = describeAiReadiness(availability, useAudioChosen());

  if (readiness.kind === "unavailable") {
    return (
      <span
        data-testid="ai-ready-pill"
        className={cn(PILL, "cursor-default border-border text-muted-foreground", className)}
        title={`${readiness.detail}\n\nServices run by hand — slides move when you move them.`}
      >
        AI Not available
      </span>
    );
  }

  if (!enabled) {
    return (
      <button
        type="button"
        data-testid="ai-ready-pill"
        onClick={() => setAiEnabled(true)}
        className={cn(
          PILL,
          "border-violet-400/60 bg-violet-500/15 text-violet-700 hover:bg-violet-500/30 dark:text-violet-100",
          className,
        )}
        title="The AI is off — services run by hand. Click to let the AI follow the singing and change the slides."
      >
        <Sparkles className="size-3" />
        Turn on AI
      </button>
    );
  }

  if (readiness.kind === "not_ready") {
    const where =
      readiness.fix === "audio_setup" ? "set up the audio input" : "finish setting up in Settings";
    return (
      <button
        type="button"
        data-testid="ai-ready-pill"
        onClick={() =>
          window.dispatchEvent(
            new CustomEvent(readiness.fix === "audio_setup" ? OPEN_WIZARD_EVENT : OPEN_SETTINGS_EVENT),
          )
        }
        className={cn(
          PILL,
          "border-red-500/50 bg-red-500/15 text-red-600 hover:bg-red-500/25 dark:text-red-200",
          className,
        )}
        title={`${readiness.detail}\n\nClick to ${where}. Until then the AI can't follow the songs.`}
      >
        AI Not Ready
      </button>
    );
  }

  return (
    <button
      type="button"
      data-testid="ai-ready-pill"
      onClick={() => setAiEnabled(false)}
      className={cn(
        PILL,
        "border-emerald-500/40 bg-emerald-500/15 text-emerald-700 hover:bg-emerald-500/25 dark:text-emerald-100",
        className,
      )}
      title={`Click to disable AI Tracking${readiness.detail ? `\n\n${readiness.detail}` : ""}`}
    >
      <Sparkles className="size-3" />
      AI Ready
    </button>
  );
}
