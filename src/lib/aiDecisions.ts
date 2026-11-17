/**
 * `useAiDecisions()` / `useAiStatus()` — WebSocket subscribers for the
 * matcher's two server-side broadcasts:
 *
 *   - `slide_change` with `source="ai"` — the matcher just committed
 *      to a slide. Phase 5's `PresentationView` applies it via the
 *      existing `jumpTo()` transition (the same path arrow keys use),
 *      but only when the operator has opted into `active` AI mode.
 *   - `slide_change` with `source="voice"` — a spoken instruction
 *      (`decision.instructions`) resolved to a slide. Carries explicit
 *      human intent, "the same authority as an operator override" per
 *      the sidecar's own framing (`server._observe_spoken_instructions`),
 *      so callers apply it without the `"active"` gate `"ai"` gets, or a
 *      spoken command silently does nothing in shadow mode. (With the AI
 *      off the sidecar doesn't listen at all, so none arrive.)
 *   - `ai_status` — director's `idle / tracking / following_human`
 *      pill state plus the operator's mode (off/shadow/active).
 *
 * Both hooks subscribe over the shared connection (`getSharedConnection()`
 * — same pattern as `transcriptStream.ts`), not a connection of their own.
 */

import { useEffect, useRef, useState } from "react";

import { requestSidecar } from "@/lib/sidecarClient";
import { getSharedConnection } from "@/lib/sidecarConnection";
import { type SidecarConnection } from "@/lib/ws";
import type { AIStatusEvent } from "@/types/generated";

export interface AiDecision {
  itemIdx: number;
  slideIdx: number;
  confidence: number;
  source: "ai" | "voice";
}

/**
 * Whether a broadcast decision should actually move the display, given the
 * operator's current AI mode.
 *
 * `"ai"` is the matcher's continuous fuzzy tracking — shadow mode's whole
 * point is that it decides without touching the screen, so it's gated on
 * `mode === "active"`. `"voice"` is a spoken instruction, "the same
 * authority as an operator override" per the sidecar
 * (`server._observe_spoken_instructions`) — an arrow key moves the slide
 * regardless of AI mode, and a voice command must too, or it silently does
 * nothing in the shadow mode most operators would reasonably run first.
 * With the AI off the sidecar stops transcribing, so no voice decisions are
 * sent then (`server_state.transcription_needed`).
 *
 * Pulled out of `PresentationView`/`OperatorView` (which used to each
 * inline `if (initialAiMode !== "active") return;` unconditionally,
 * including for voice) so the rule has one tested definition instead of
 * two copies that can drift — that drift is exactly how a spoken command
 * ended up silently doing nothing in shadow mode. See
 * `docs/done/SPOKEN_INSTRUCTIONS.md`'s 2026-09-03 entry.
 */
export function shouldApplyDecision(
  decision: Pick<AiDecision, "source">,
  aiMode: AIStatusEvent["mode"],
): boolean {
  if (decision.source === "voice") return true;
  return aiMode === "active";
}

/**
 * Subscribe to `slide_change` broadcasts sourced from the sidecar
 * (`"ai"` or `"voice"` — see the module docstring). The `onDecision`
 * callback fires with every one; see `shouldApplyDecision` for how
 * `PresentationView`/`OperatorView` decide whether to actually apply it.
 *
 * No return value: earlier versions exposed `latest`/`connected`, but
 * nothing ever consumed them — every caller only wants the callback.
 */
export function useAiDecisions(opts: {
  onDecision?: (decision: AiDecision) => void;
} = {}): void {
  const { onDecision } = opts;
  const callbackRef = useRef(onDecision);
  callbackRef.current = onDecision;

  useEffect(() => {
    let cancelled = false;
    let unsubSlide: (() => void) | null = null;
    let conn: SidecarConnection | null = null;

    (async () => {
      try {
        conn = await getSharedConnection();
        if (cancelled) return;
        unsubSlide = conn.onMessage("slide_change", (msg) => {
          if (msg.source !== "ai" && msg.source !== "voice") return;
          const decision: AiDecision = {
            itemIdx: msg.item_idx,
            slideIdx: msg.slide_idx,
            confidence: msg.confidence ?? 1.0,
            source: msg.source,
          };
          callbackRef.current?.(decision);
        });
      } catch {
        // sidecar offline — no decisions will arrive; nothing to clean up
      }
    })();

    return () => {
      cancelled = true;
      unsubSlide?.();
      // Shared connection — only unsubscribe, never close it.
    };
  }, []);
}


export interface AiStatusSnapshot {
  mode: AIStatusEvent["mode"];
  state: AIStatusEvent["state"];
  lastDecisionTSeconds: number | null;
  lastOverrideTSeconds: number | null;
}

function sameStatus(a: AiStatusSnapshot, b: AiStatusSnapshot): boolean {
  return (
    a.mode === b.mode &&
    a.state === b.state &&
    a.lastDecisionTSeconds === b.lastDecisionTSeconds &&
    a.lastOverrideTSeconds === b.lastOverrideTSeconds
  );
}

export function useAiStatus(): AiStatusSnapshot | null {
  const [snapshot, setSnapshot] = useState<AiStatusSnapshot | null>(null);

  useEffect(() => {
    let cancelled = false;
    let unsub: (() => void) | null = null;
    let conn: SidecarConnection | null = null;

    (async () => {
      try {
        conn = await getSharedConnection();
        if (cancelled) return;
        unsub = conn.onMessage("ai_status", (msg) => {
          const next: AiStatusSnapshot = {
            mode: msg.mode,
            state: msg.state,
            lastDecisionTSeconds: msg.last_decision_t_seconds ?? null,
            lastOverrideTSeconds: msg.last_override_t_seconds ?? null,
          };
          // The director re-broadcasts status on every manual move. Keep the
          // old object when nothing changed, or every consumer (OperatorView
          // via `useLiveAiMode`) re-renders once per keypress for nothing.
          setSnapshot((prev) => (prev && sameStatus(prev, next) ? prev : next));
        });
      } catch {
        // sidecar offline — return null forever
      }
    })();

    return () => {
      cancelled = true;
      unsub?.();
      // Shared connection — only unsubscribe, never close it.
    };
  }, []);

  return snapshot;
}


/**
 * Send a `set_ai_mode` request to the sidecar. Returns a promise that
 * resolves when the server acknowledges. Errors propagate.
 */
export async function setAiMode(mode: AIStatusEvent["mode"]): Promise<void> {
  await requestSidecar({ kind: "set_ai_mode", mode });
}

/**
 * The AI mode to gate `shouldApplyDecision` on: the sidecar's live mode
 * once an `ai_status` broadcast has arrived, falling back to a
 * caller-supplied initial value until then.
 *
 * The AI status pill flips the mode *while a session is already running*
 * and fires `set_ai_mode` immediately so the sidecar picks it up right away.
 * A view that instead reads the mode once at mount (via
 * `useMemo(..., [])`) never sees that change — the sidecar starts
 * broadcasting `"ai"`-sourced decisions under the new mode, and the view
 * keeps silently refusing every one of them under the stale mode it
 * captured at mount. Use this instead of a raw snapshot anywhere a
 * decision is gated on AI mode.
 */
export function useLiveAiMode(initialMode: AIStatusEvent["mode"]): AIStatusEvent["mode"] {
  // Only the mode, held as a string: `ai_status` arrives on every manual move
  // with a fresh override timestamp, and the views calling this re-render
  // their whole slide grid when it changes. A primitive state lets React skip
  // the render when the mode is the same.
  const [mode, setMode] = useState<AIStatusEvent["mode"] | null>(null);

  useEffect(() => {
    let cancelled = false;
    let unsub: (() => void) | null = null;
    (async () => {
      try {
        const conn = await getSharedConnection();
        if (cancelled) return;
        unsub = conn.onMessage("ai_status", (msg) => setMode(msg.mode));
      } catch {
        // sidecar offline — the initial mode stands
      }
    })();
    return () => {
      cancelled = true;
      unsub?.();
    };
  }, []);

  return mode ?? initialMode;
}
