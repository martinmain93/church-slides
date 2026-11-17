/**
 * Audience projection surface — the contents of the second-screen window.
 *
 * It holds **no** presentation logic: it subscribes to the operator window's
 * slide broadcasts and renders the existing `SlideStage` verbatim. On mount
 * it asks the operator to re-send the current slide, so a freshly opened
 * projection window syncs immediately instead of waiting for the next change.
 */

import { useEffect, useMemo, useState } from "react";

import { DisplayLookContext, normalizeLook } from "@/lib/displayLook";
import { isInTauri } from "@/lib/invoke";
import {
  REQUEST_STATE_EVENT,
  SLIDE_EVENT,
  type SlidePayload,
} from "@/lib/presentation/projection";
import { flowLyrics } from "@/components/presentation/FlowStage";
import { ScriptureStage } from "@/components/scripture/ScriptureStage";
import { withScriptureDefaults } from "@/lib/scripture/settings";

export function AudienceView() {
  const [payload, setPayload] = useState<SlidePayload>({
    slide: null,
    blankMode: "none",
  });

  useEffect(() => {
    if (!isInTauri()) return;
    let unlisten: (() => void) | undefined;
    let cancelled = false;

    (async () => {
      const { listen, emit } = await import("@tauri-apps/api/event");
      const un = await listen<SlidePayload>(SLIDE_EVENT, (event) => {
        setPayload(event.payload);
      });
      if (cancelled) {
        un();
        return;
      }
      unlisten = un;
      // Ask the operator window to push the current slide right away.
      await emit(REQUEST_STATE_EVENT).catch(() => undefined);
    })();

    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, []);

  // Keyed on the fields, not the object: every payload is a new object, and
  // a new look would make the flow column re-measure on every slide.
  const { theme, font, size, weight } = normalizeLook(payload.look);
  const look = useMemo(() => ({ theme, font, size, weight }), [theme, font, size, weight]);

  return (
    <DisplayLookContext.Provider value={look}>
      <div className="h-screen w-screen bg-black">
        {/* `ScriptureStage` with no passage renders exactly the `SlideStage`
            this used to render directly, so a payload from a build without the
            feature — or with it switched off — looks identical. */}
        <ScriptureStage
          slide={payload.slide ?? undefined}
          blankMode={payload.blankMode}
          passage={payload.scripture ?? null}
          settings={withScriptureDefaults(payload.scriptureSettings)}
          fullscreen={payload.scriptureFullscreen ?? false}
          lyrics={flowLyrics(payload.flow, payload.blankMode)}
        />
      </div>
    </DisplayLookContext.Provider>
  );
}
