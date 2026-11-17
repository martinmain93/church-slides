/**
 * A passage put on screen *now* — by the operator, or by someone asking for it
 * out loud ("bring up Jeremiah 29:11").
 *
 * A live passage is a person's decision, so it stays up until someone takes it
 * down:
 *
 *   - **full** replaces the lyrics. A slide change made by a person — a click,
 *     an arrow key, a spoken "go to the chorus" — takes it down, because that
 *     person evidently wants the lyrics back. The AI following the song
 *     underneath does not: it has no idea a verse is up.
 *   - **beside** keeps the lyrics up with the passage next to them, and stays
 *     through slide changes until cleared.
 *
 * Module-scoped like the other live stores, so the operator panel and the
 * presenter views read one value, and a spoken request lands the same way a
 * typed one does.
 */

import { useEffect, useMemo, useRef } from "react";

import { createLiveStore, useLiveStore } from "@/lib/liveStore";
import type { PresentationActions } from "@/lib/presentation/hook";
import { resolvePassages } from "@/lib/scripture/client";
import { withScriptureDefaults, type ScriptureSettings } from "@/lib/scripture/settings";
import { getSharedConnection } from "@/lib/sidecarConnection";
import { getSettings } from "@/lib/tauri";
import type { ScripturePassage } from "@/types/generated";

export type LiveScriptureDisplay = "full" | "beside";

export interface LiveScripture {
  passage: ScripturePassage;
  display: LiveScriptureDisplay;
  source: "operator" | "voice";
}

const store = createLiveStore<LiveScripture | null>(null);

// Tell the engine what is up, so a spoken request can be relative to it ("and
// the next few verses"). Whoever put it up — typed, or asked for out loud.
store.subscribe((live) => {
  void getSharedConnection()
    .then((conn) =>
      conn.notify({
        kind: "live_scripture",
        reference: live?.passage.reference ?? "",
        display: live?.display ?? "off",
      }),
    )
    .catch(() => {
      // No engine: nothing is listening for requests to be relative to.
    });
});

export function useLiveScripture(): LiveScripture | null {
  return useLiveStore(store);
}

/** The running service's "is this passage already prepared?" — see
 * `usePreparedScripture`. */
let goToPrepared: ((passage: ScripturePassage) => boolean) | null = null;

export function showLiveScripture(
  passage: ScripturePassage,
  display: LiveScriptureDisplay,
  source: LiveScripture["source"] = "operator",
): void {
  // Prepared in the service (Plan/SERVICE_ITEMS.md): go to the slides the
  // team made — their translation, their edits — instead of laying a second
  // copy over the lyrics.
  if (goToPrepared?.(passage)) return;
  store.set({ passage, display, source });
}

/**
 * While a service runs, a passage asked for — typed in the Scripture tab or
 * said out loud — that is already prepared in the service goes there:
 * `handler` moves to it and returns true, or returns false to show it live.
 */
export function usePreparedScripture(handler: (passage: ScripturePassage) => boolean): void {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => {
    const call = (p: ScripturePassage) => ref.current(p);
    goToPrepared = call;
    return () => {
      if (goToPrepared === call) goToPrepared = null;
    };
  }, []);
}

export function clearLiveScripture(): void {
  store.set(null);
}

/**
 * The translation this service is reading from, when the operator (or a
 * speaker, "read it from the King James") picked one other than the default
 * in Settings. Null means the default. Lives as long as the process, like the
 * live passage, and is cleared with it when the presenter view closes — the
 * next service starts from the default again.
 */
const sessionTranslation = createLiveStore<string | null>(null);

export function useSessionTranslation(): string | null {
  return useLiveStore(sessionTranslation);
}

export function setSessionTranslation(translation: string | null): void {
  sessionTranslation.set(translation);
}

/** The translation in use: this service's pick, else the Settings default. */
export function useActiveTranslation(defaultTranslation: string): string {
  return useSessionTranslation() || defaultTranslation;
}

/** A person moved the slide: a full-screen passage gives way to the lyrics. */
export function onHumanSlideChange(): void {
  store.set((prev) => (prev?.display === "full" ? null : prev));
}

/**
 * What the screen shows for scripture: the live passage, when one is up.
 * Passages are put up by the operator's Scripture tab or asked for out loud.
 */
export function useScriptureOnScreen(
  settings: ScriptureSettings,
): { passage: ScripturePassage | null; settings: ScriptureSettings; fullscreen: boolean } {
  const live = useLiveScripture();
  // The store outlives a service; a passage left up must not open the next
  // one. Found by leaving a verse beside the lyrics and starting again.
  useEffect(
    () => () => {
      clearLiveScripture();
      setSessionTranslation(null);
    },
    [],
  );
  return useMemo(
    () =>
      live
        ? { passage: live.passage, settings, fullscreen: live.display === "full" }
        : { passage: null, settings, fullscreen: false },
    [live, settings],
  );
}

/**
 * The presenter actions, as driven by a person: each slide move also takes
 * down a full-screen passage. Give these to the keyboard, the slide grid and
 * the band leader — never to the AI, which follows the song underneath a
 * passage without knowing one is up.
 */
export function useHumanActions(actions: PresentationActions): PresentationActions {
  return useMemo(() => {
    const human =
      <A extends unknown[]>(move: (...args: A) => void) =>
      (...args: A) => {
        onHumanSlideChange();
        move(...args);
      };
    return {
      ...actions,
      nextSlide: human(actions.nextSlide),
      prevSlide: human(actions.prevSlide),
      nextSong: human(actions.nextSong),
      prevSong: human(actions.prevSong),
      jumpTo: human(actions.jumpTo),
    };
  }, [actions]);
}

/** The translation in use: this service's pick, else the Settings default,
 * read fresh — a request is rare enough not to cache it. */
async function chosenTranslation(): Promise<string> {
  const picked = sessionTranslation.get();
  if (picked) return picked;
  try {
    return withScriptureDefaults((await getSettings()).scripture).translation;
  } catch {
    return "";
  }
}

// Spoken requests. The sidecar has already checked the reference is real; the
// text is looked up here, in the operator's translation, like a typed one.
// Subscribed once for the process lifetime, like `pipeFromSidecar`.
void (async () => {
  try {
    const conn = await getSharedConnection();
    // A newer request supersedes one still resolving.
    let seq = 0;
    conn.onMessage("show_scripture", (msg) => {
      const mine = ++seq;
      const reference = msg.reference ?? "";
      if (msg.display === "off" || !reference) {
        clearLiveScripture();
        return;
      }
      const display = msg.display === "beside" ? "beside" : "full";
      // A translation the speaker named (and the sidecar found installed)
      // becomes this service's: "read it from the King James" means from
      // now on, not just this once.
      if (msg.translation) setSessionTranslation(msg.translation);
      void (async () => {
        const resolved = await resolvePassages([reference], await chosenTranslation());
        const passage = resolved.byReference.get(reference);
        if (passage && mine === seq) showLiveScripture(passage, display, "voice");
      })();
    });
    conn.onMessage("spoken_instruction", (msg) => {
      // A spoken slide move is a person asking for lyrics.
      if (msg.applied && msg.item_idx != null) onHumanSlideChange();
    });
  } catch {
    // No sidecar: only the operator can put a passage up.
  }
})();
