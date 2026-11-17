/**
 * `useAudioLevels()` — subscribes to `audio_level` broadcasts from the
 * sidecar and exposes a small rolling buffer for the VU meter.
 *
 * The sidecar emits one level event per audio frame (~10 Hz). We keep
 * the most recent reading plus a short history so the meter can render
 * a smoothed bar without flickering on a single quiet frame. When no
 * audio session is running, the sidecar emits nothing and `latest`
 * stays at the initial silent state.
 */

import { useEffect, useRef, useState } from "react";

import { getSharedConnection } from "@/lib/sidecarConnection";
import { type SidecarConnection } from "@/lib/ws";

export interface AudioLevelSample {
  t_seconds: number;
  rms_dbfs: number;
  peak: number;
}

export interface AudioLevels {
  /** Most recent level event, or null until the first arrives. */
  latest: AudioLevelSample | null;
  /** Rolling buffer of the last `historySize` samples. */
  history: AudioLevelSample[];
  connected: boolean;
}

/**
 * @param historySize how many samples to retain in the rolling buffer.
 *                    At ~10 Hz, 50 samples is ~5 s of history.
 */
export function useAudioLevels(historySize: number = 50): AudioLevels {
  const [latest, setLatest] = useState<AudioLevelSample | null>(null);
  const [connected, setConnected] = useState(false);
  const historyRef = useRef<AudioLevelSample[]>([]);
  const [, forceRender] = useState(0);
  const connRef = useRef<SidecarConnection | null>(null);

  useEffect(() => {
    let cancelled = false;
    let unsubLevel: (() => void) | null = null;
    let unsubStatus: (() => void) | null = null;

    (async () => {
      try {
        const conn = await getSharedConnection();
        if (cancelled) return;
        connRef.current = conn;
        setConnected(conn.status === "open");

        unsubStatus = conn.onStatus((s) => setConnected(s === "open"));

        unsubLevel = conn.onMessage("audio_level", (msg) => {
          const sample: AudioLevelSample = {
            t_seconds: msg.t_seconds,
            rms_dbfs: msg.rms_dbfs,
            peak: msg.peak,
          };
          setLatest(sample);
          const next = historyRef.current.concat(sample);
          if (next.length > historySize) {
            next.splice(0, next.length - historySize);
          }
          historyRef.current = next;
          forceRender((n) => (n + 1) % 1024);
        });
      } catch {
        if (!cancelled) setConnected(false);
      }
    })();

    return () => {
      cancelled = true;
      unsubLevel?.();
      unsubStatus?.();
      // Shared connection — only unsubscribe, never close it.
      connRef.current = null;
    };
  }, [historySize]);

  return { latest, history: historyRef.current, connected };
}
