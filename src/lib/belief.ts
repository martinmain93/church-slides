/**
 * `useBeliefState()` — subscribe to the tracker's `belief_state` broadcasts:
 * top-3 hypotheses with marginals, the effective sample size, and a "lost"
 * flag.
 */

import { useEffect, useState } from "react";

import {
  createLiveStore,
  pipeFromSidecar,
  useLiveStore,
} from "@/lib/liveStore";
import { getSharedConnection } from "@/lib/sidecarConnection";
import { type SidecarConnection } from "@/lib/ws";
import type { BeliefStateMessage } from "@/types/generated";

export interface PfHypothesis {
  itemIdx: number;
  slideIdx: number;
  marginal: number;
}

export interface PfBelief {
  top3: PfHypothesis[];
  ess: number;
  isLost: boolean;
  /** Local wall clock at receive time — used for the sparkline ring buffer. */
  receivedAtMs: number;
}

export interface PfBeliefStream {
  latest: PfBelief | null;
  /** Recent ESS samples for the sparkline (most recent first). */
  essHistory: number[];
  connected: boolean;
}

const HISTORY_LENGTH = 60;  // ~ last 60 events (~6-12 s depending on cadence)

function toBelief(msg: BeliefStateMessage): PfBelief {
  return {
    // The sidecar packs `item_idx` into `song_id` as a string — a Phase 6 v1
    // shortcut that keeps the existing BeliefStateMessage schema.
    top3: msg.top_3.map((h) => ({
      itemIdx: Number.isFinite(Number(h.song_id)) ? Number(h.song_id) : 0,
      slideIdx: h.slide_idx,
      marginal: h.marginal,
    })),
    ess: msg.ess,
    isLost: msg.is_lost,
    receivedAtMs: Date.now(),
  };
}

// Lives outside the component: the debug panel unmounts a tab on switch, and a
// hook-local `useState` comes back empty until the next broadcast — three to
// five seconds while singing, which reads as a tracker that has been reset.
// Latest and history move together, so they share one store. See `lib/liveStore`.
const store = createLiveStore<{ latest: PfBelief | null; essHistory: number[] }>(
  { latest: null, essHistory: [] },
);
pipeFromSidecar("belief_state", store, (raw, prev) => {
  const latest = toBelief(raw);
  return {
    latest,
    essHistory: [latest.ess, ...prev.essHistory].slice(0, HISTORY_LENGTH),
  };
});

export function useBeliefState(): PfBeliefStream {
  const { latest, essHistory } = useLiveStore(store);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let unsubStatus: (() => void) | null = null;
    let conn: SidecarConnection | null = null;

    (async () => {
      try {
        conn = await getSharedConnection();
        if (cancelled) return;
        setConnected(conn.status === "open");
        unsubStatus = conn.onStatus((s) => setConnected(s === "open"));
      } catch {
        if (!cancelled) setConnected(false);
      }
    })();

    return () => {
      cancelled = true;
      unsubStatus?.();
      // Shared connection — only unsubscribe, never close it.
    };
  }, []);

  return { latest, essHistory, connected };
}
