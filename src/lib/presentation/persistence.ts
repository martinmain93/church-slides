/**
 * Last-session persistence for crash recovery. Stores the currently
 * playing setlist + slide position so that re-launching the app inside
 * a 12-hour window offers "Continue last service?".
 *
 * In browser dev mode and inside the Tauri shell we both use
 * `localStorage` for v1 — Tauri's webview persists localStorage to disk
 * automatically per app identifier, which is good enough.
 */

const STORAGE_KEY = "church_slides_last_session_v1";
const RECENT_WINDOW_MS = 12 * 60 * 60 * 1000; // 12 hours

export interface LastSession {
  setlistId: string;
  itemIdx: number;
  slideIdx: number;
  /** unix ms */
  savedAt: number;
}

export function saveLastSession(snapshot: Omit<LastSession, "savedAt">): void {
  try {
    const payload: LastSession = { ...snapshot, savedAt: Date.now() };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
  } catch {
    // out of quota / private mode — ignore for v1
  }
}

export function loadLastSession(): LastSession | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as LastSession;
    if (
      typeof parsed.setlistId !== "string" ||
      typeof parsed.itemIdx !== "number" ||
      typeof parsed.slideIdx !== "number" ||
      typeof parsed.savedAt !== "number"
    ) {
      return null;
    }
    if (Date.now() - parsed.savedAt > RECENT_WINDOW_MS) return null;
    return parsed;
  } catch {
    return null;
  }
}

export function clearLastSession(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignore
  }
}
