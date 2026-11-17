/**
 * Whether services use the AI, and what "turn the AI on" means —
 * localStorage-backed.
 *
 * `useAiEnabled` is the setting (on by default): the pill beside "Start
 * service" and Settings both switch it, and every service — operator view or
 * single screen — starts with the AI on when it's set and off when it isn't.
 * Mid-service the AI status pill still switches it live, for that service
 * only. Then there's a developer choice of *how* it runs once on — "active"
 * (it moves the slides) or "shadow" (it follows and logs, but never moves
 * them) — kept in the debug panel's Tunables tab for validating the matcher.
 *
 * Stored under a new key: the old Settings picker saved Off/Shadow/Active as
 * the service default, and an install that once chose Shadow there must not
 * silently keep an AI that never moves the slides.
 */

import { createLiveStore, useLiveStore } from "@/lib/liveStore";
import type { AIStatusEvent } from "@/types/generated";

export type AIMode = AIStatusEvent["mode"];
/** How the AI runs when switched on. */
export type AIOnMode = Exclude<AIMode, "off">;

const STORAGE_KEY = "auto-presenter.ai-on-mode";

export interface AIPrefs {
  onMode: AIOnMode;
}

const DEFAULT_PREFS: AIPrefs = { onMode: "active" };

export function loadAiPrefs(): AIPrefs {
  if (typeof localStorage === "undefined") return DEFAULT_PREFS;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw === "shadow" ? { onMode: "shadow" } : DEFAULT_PREFS;
  } catch {
    return DEFAULT_PREFS;
  }
}

export function saveAiPrefs(prefs: AIPrefs): void {
  if (typeof localStorage === "undefined") return;
  try {
    localStorage.setItem(STORAGE_KEY, prefs.onMode);
  } catch {
    // Non-critical — falls back to active.
  }
}

/** The mode "turn the AI on" means: Active, unless a developer chose Shadow. */
export function aiOnMode(prefs: AIPrefs = loadAiPrefs()): AIOnMode {
  return prefs.onMode;
}

// --- the on/off setting -----------------------------------------------------

const ENABLED_KEY = "auto-presenter.ai-enabled";

function loadAiEnabled(): boolean {
  try {
    return localStorage.getItem(ENABLED_KEY) !== "0";
  } catch {
    return true;
  }
}

// Shared so the setlist pill and Settings agree the moment either changes.
const enabledStore = createLiveStore<boolean>(loadAiEnabled());

/** Whether services start with the AI on. */
export function isAiEnabled(): boolean {
  return enabledStore.get();
}

export function setAiEnabled(on: boolean): void {
  enabledStore.set(on);
  try {
    if (on) localStorage.removeItem(ENABLED_KEY);
    else localStorage.setItem(ENABLED_KEY, "0");
  } catch {
    // Non-critical — the choice just won't outlive this run.
  }
}

export function useAiEnabled(): boolean {
  return useLiveStore(enabledStore);
}

/** The mode a service starts in: the setting's on-mode, or off. */
export function serviceStartAiMode(): AIMode {
  return isAiEnabled() ? aiOnMode() : "off";
}

// --- what "on" means in the operator view -----------------------------------

const OPERATOR_ON_KEY = "auto-presenter.operator-ai-on-mode";

/** Turning the AI on in the operator view: Assist (the operator drives, the
 * AI points out where the band went) or Auto (the AI drives). Assist unless
 * the operator last chose Auto — the operator view assumes someone is at the
 * controls. Single-screen and leader mode always use `aiOnMode`. */
export function operatorOnMode(): AIOnMode {
  try {
    if (localStorage.getItem(OPERATOR_ON_KEY) === "active") return aiOnMode();
  } catch {
    // Unreadable storage: the default.
  }
  return "assist";
}

export function saveOperatorOnMode(mode: AIOnMode): void {
  try {
    localStorage.setItem(OPERATOR_ON_KEY, mode === "assist" ? "assist" : "active");
  } catch {
    // Non-critical — the choice just won't outlive this run.
  }
}

/** The mode an operator-view service starts in. */
export function operatorStartAiMode(): AIMode {
  return isAiEnabled() ? operatorOnMode() : "off";
}
