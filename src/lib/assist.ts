/**
 * AI-assisted mode, the screen's side: the one suggestion the sidecar's
 * assist layer currently stands behind, if any.
 *
 * The sidecar decides *when* to suggest (`decision/assist.py`); this only
 * mirrors it. `assist_suggestion` raises one or moves it forward (same id),
 * `assist_clear` takes it down. One at a time — a new id replaces the old.
 *
 * See docs/done/AI_ASSISTED_MODE.md.
 */

import { useEffect, useState } from "react";

import { requestSidecar } from "@/lib/sidecarClient";
import { getSharedConnection } from "@/lib/sidecarConnection";

export interface AssistSuggestion {
  id: number;
  /** "next": the next slide is due (highlight only). "place": the band is
   * somewhere else (highlight, and a notice when the tile isn't in view). */
  tier: "next" | "place";
  itemIdx: number;
  slideIdx: number;
  /** Other places the band might be, best first. */
  alternatives: { itemIdx: number; slideIdx: number }[];
}

export function useAssistSuggestion(enabled: boolean): AssistSuggestion | null {
  const [suggestion, setSuggestion] = useState<AssistSuggestion | null>(null);

  useEffect(() => {
    if (!enabled) {
      setSuggestion(null);
      return;
    }
    let cancelled = false;
    const unsubs: (() => void)[] = [];
    void getSharedConnection()
      .then((conn) => {
        if (cancelled) return;
        unsubs.push(
          conn.onMessage("assist_suggestion", (msg) => {
            setSuggestion({
              id: msg.suggestion_id,
              tier: msg.tier,
              itemIdx: msg.item_idx,
              slideIdx: msg.slide_idx,
              alternatives: (msg.alternatives ?? []).map((a) => ({
                itemIdx: a.item_idx,
                slideIdx: a.slide_idx,
              })),
            });
          }),
          conn.onMessage("assist_clear", (msg) => {
            setSuggestion((cur) => (cur && cur.id === msg.suggestion_id ? null : cur));
          }),
        );
      })
      .catch(() => {
        // No engine: no suggestions, and presenting by hand needs none.
      });
    return () => {
      cancelled = true;
      for (const u of unsubs) u();
      // Shared connection — only unsubscribe, never close it.
    };
  }, [enabled]);

  return suggestion;
}

/** The operator says the screen is right: the sidecar mutes the place for a
 * while and re-aims the engine on what's showing. */
export async function dismissAssistSuggestion(id: number): Promise<void> {
  await requestSidecar({ kind: "assist_dismiss", suggestion_id: id });
}
