/**
 * Live-tunable engine parameters.
 *
 * Two layers:
 *
 *   - Persistent defaults in localStorage (`loadTunables`/`saveTunables`).
 *     The next session start sends these on `start_audio_session`.
 *   - Live updates while a session is active, via the `setTunables` WS
 *     request. Server applies them to the running engine and broadcasts
 *     `tunables_updated` to all connected clients so multiple debug
 *     panels stay in sync.
 *
 * The defaults come from `ParticleFilterTunables` too, not just the type —
 * codegen emits both, so neither shape nor values can drift.
 */

import { useEffect, useState } from "react";

import { requestSidecar } from "@/lib/sidecarClient";
import { getSharedConnection } from "@/lib/sidecarConnection";
import { type SidecarConnection } from "@/lib/ws";
import {
  DEFAULT_PARTICLE_FILTER_TUNABLES,
  type ParticleFilterTunables,
} from "@/types/generated";

const STORAGE_KEY = "auto-presenter.tunables";

/**
 * Generated, never re-typed here.
 *
 * This was a hand-kept copy and it drifted: `lead_time_ms` stayed at 500
 * after a documented sweep moved the Python default to 1500. A session start
 * always sends tunables, so the stale copy won and the shipped app ran at
 * 500 ms — 0.297 displayed wrong-slide against 0.273, and a median 287 ms
 * later. Every other field still matched, which is what made it invisible.
 */
export const DEFAULT_TUNABLES: ParticleFilterTunables =
  DEFAULT_PARTICLE_FILTER_TUNABLES;

export function loadTunables(): ParticleFilterTunables {
  if (typeof localStorage === "undefined") return DEFAULT_TUNABLES;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_TUNABLES;
    const parsed = JSON.parse(raw);
    return { ...DEFAULT_TUNABLES, ...parsed } as ParticleFilterTunables;
  } catch {
    return DEFAULT_TUNABLES;
  }
}

export function saveTunables(t: ParticleFilterTunables): void {
  if (typeof localStorage === "undefined") return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(t));
  } catch {
    // ignore — non-critical
  }
}

export async function pushTunables(t: ParticleFilterTunables): Promise<void> {
  saveTunables(t);
  await requestSidecar({ kind: "set_tunables", tunables: t });
}

/** Subscribe to `tunables_updated` broadcasts so multi-panel views stay in sync. */
export function useTunables(): ParticleFilterTunables {
  const [tunables, setTunables] = useState<ParticleFilterTunables>(() =>
    loadTunables(),
  );

  useEffect(() => {
    let cancelled = false;
    let unsub: (() => void) | null = null;
    let conn: SidecarConnection | null = null;

    (async () => {
      try {
        conn = await getSharedConnection();
        if (cancelled) return;
        unsub = conn.onMessage("tunables_updated", (msg) => {
          setTunables(msg.tunables);
          saveTunables(msg.tunables);
        });
      } catch {
        // sidecar offline — stick with the loaded defaults
      }
    })();

    return () => {
      cancelled = true;
      unsub?.();
      // Shared connection — only unsubscribe, never close it.
    };
  }, []);

  return tunables;
}
