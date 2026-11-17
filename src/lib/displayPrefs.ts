/**
 * Slide vs continuous display preference — localStorage-backed like
 * `aiPrefs.ts`, but live: the operator can flip it mid-service from the top
 * bar and the stage must follow without a remount.
 */

import { useEffect, useState } from "react";

import type { DisplayPref } from "@/lib/presentation/flow";

const STORAGE_KEY = "auto-presenter.display-pref";
const DEFAULT_PREF: DisplayPref = "auto";

const listeners = new Set<(pref: DisplayPref) => void>();

export function loadDisplayPref(): DisplayPref {
  if (typeof localStorage === "undefined") return DEFAULT_PREF;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw === "auto" || raw === "slide" || raw === "continuous" ? raw : DEFAULT_PREF;
  } catch {
    return DEFAULT_PREF;
  }
}

export function saveDisplayPref(pref: DisplayPref): void {
  try {
    localStorage.setItem(STORAGE_KEY, pref);
  } catch {
    // Non-critical — the in-memory listeners below still switch this session.
  }
  for (const fn of listeners) fn(pref);
}

export function useDisplayPref(): DisplayPref {
  const [pref, setPref] = useState<DisplayPref>(loadDisplayPref);
  useEffect(() => {
    listeners.add(setPref);
    return () => {
      listeners.delete(setPref);
    };
  }, []);
  return pref;
}
