/**
 * How the projected lyrics look — colour scheme, typeface, size and weight.
 *
 * Stored per device in localStorage like `displayPrefs.ts`, and live: the
 * operator can change it mid-service and every stage in this window follows.
 * The audience window doesn't read storage for it; the operator sends the
 * look in the slide payload, so both windows always agree.
 *
 * Stages read the look from `DisplayLookContext` rather than a prop, so the
 * seams between the views and the stages (`ScriptureStage`, `flowLyrics`)
 * don't all grow a parameter. Without a provider a stage gets the default —
 * white on black, the app's sans — which is what it rendered before looks
 * existed.
 */

import { useEffect, useMemo, useState } from "react";

import { isInTauri } from "@/lib/invoke";
import { DEFAULT_LOOK, lookForItem, normalizeLook, parseLook, type DisplayLook } from "@/lib/look";
import { getLibraryLook, listLibraries, setLibraryLook } from "@/lib/tauri";

// The look itself (types, options, style) is shared with every client.
export * from "@/lib/look";

// --- storage -----------------------------------------------------------------

const STORAGE_KEY = "auto-presenter.display-look";

const listeners = new Set<(look: DisplayLook) => void>();

export function loadDisplayLook(): DisplayLook {
  if (typeof localStorage === "undefined") return DEFAULT_LOOK;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? normalizeLook(JSON.parse(raw)) : DEFAULT_LOOK;
  } catch {
    return DEFAULT_LOOK;
  }
}

function cacheDisplayLook(look: DisplayLook): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(look));
  } catch {
    // Non-critical — the listeners below still switch this session.
  }
  for (const fn of listeners) fn(look);
}

/** Change the open library's look (it syncs with an organization's). */
export function saveDisplayLook(look: DisplayLook): void {
  cacheDisplayLook(look);
  void storeInLibrary();
}

export function useDisplayLook(): DisplayLook {
  const [look, setLook] = useState<DisplayLook>(loadDisplayLook);
  useEffect(() => {
    listeners.add(setLook);
    return () => {
      listeners.delete(setLook);
    };
  }, []);
  return look;
}

// --- style overrides -------------------------------------------------------
//
// One style system: the look above is the default for everything on screen.
// Scripture may have its own (Settings → Scripture → Override default style,
// kept per device like the default), and any song or passage in a setlist may
// have its own for that service (`setlist_items.look`). The most specific wins.

const SCRIPTURE_KEY = "auto-presenter.scripture-look";
const scriptureListeners = new Set<(look: DisplayLook | null) => void>();

export function loadScriptureLook(): DisplayLook | null {
  if (typeof localStorage === "undefined") return null;
  try {
    return parseLook(localStorage.getItem(SCRIPTURE_KEY));
  } catch {
    return null;
  }
}

function cacheScriptureLook(look: DisplayLook | null): void {
  try {
    if (look) localStorage.setItem(SCRIPTURE_KEY, JSON.stringify(look));
    else localStorage.removeItem(SCRIPTURE_KEY);
  } catch {
    // Non-critical — the listeners below still switch this session.
  }
  for (const fn of scriptureListeners) fn(look);
}

/** Scripture's own look, or `null` to use the default style again. */
export function saveScriptureLook(look: DisplayLook | null): void {
  cacheScriptureLook(look);
  void storeInLibrary();
}

// --- the library's copy --------------------------------------------------------
//
// The look belongs to the open library (migration 013), so everyone showing
// an organization's library projects the same one. This window keeps a copy
// in local storage so the first frame draws in it before the library answers.

async function storeInLibrary(): Promise<void> {
  if (!isInTauri()) return;
  const scripture = loadScriptureLook();
  await setLibraryLook(JSON.stringify(loadDisplayLook()), scripture ? JSON.stringify(scripture) : null).catch(
    () => undefined,
  );
}

/**
 * Take the open library's look (on start, and after a switch or a sync).
 * A library with no look yet: this computer's own takes the look this window
 * already had; an organization's shows the defaults until someone sets one,
 * so a newcomer's first download never overwrites the team's.
 */
export async function loadLibraryLook(): Promise<void> {
  if (!isInTauri()) return;
  let stored: Awaited<ReturnType<typeof getLibraryLook>>;
  let libraries: Awaited<ReturnType<typeof listLibraries>>;
  try {
    [stored, libraries] = await Promise.all([getLibraryLook(), listLibraries()]);
  } catch {
    return;
  }
  if (stored) {
    cacheDisplayLook(parseLook(stored.display) ?? DEFAULT_LOOK);
    cacheScriptureLook(parseLook(stored.scripture));
  } else if (libraries.find((l) => l.open)?.kind === "organization") {
    cacheDisplayLook(DEFAULT_LOOK);
    cacheScriptureLook(null);
  } else {
    await storeInLibrary();
  }
}

export function useScriptureLook(): DisplayLook | null {
  const [look, setLook] = useState<DisplayLook | null>(loadScriptureLook);
  useEffect(() => {
    scriptureListeners.add(setLook);
    return () => {
      scriptureListeners.delete(setLook);
    };
  }, []);
  return look;
}


/**
 * The look on the projector right now: a passage put up full screen wears
 * scripture's; otherwise the item on screen decides (`lookForItem`).
 */
export function useScreenLook(
  item: { kind?: string; look?: string | null } | null | undefined,
  scriptureFullscreen: boolean,
): DisplayLook {
  const base = useDisplayLook();
  const scripture = useScriptureLook();
  const kind = item?.kind;
  const own = item?.look;
  return useMemo(
    () => (scriptureFullscreen ? (scripture ?? base) : lookForItem(base, scripture, { kind, look: own })),
    [base, scripture, kind, own, scriptureFullscreen],
  );
}

/** Each item's look, in order — for a grid of thumbnails. Stable while
 * nothing it depends on changes, so memoised rows stay memoised. */
export function useItemLooks(items: { kind?: string; look?: string | null }[]): DisplayLook[] {
  const base = useDisplayLook();
  const scripture = useScriptureLook();
  return useMemo(() => items.map((item) => lookForItem(base, scripture, item)), [items, base, scripture]);
}

