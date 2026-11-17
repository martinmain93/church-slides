/**
 * `useModelDownloadProgress()` — subscribes to `model_download_progress`
 * broadcasts from the sidecar and exposes the latest event per model
 * size, so the UI can render a progress bar during a Whisper load.
 *
 * The sidecar emits a `starting` event when a download begins, a
 * `downloading` event about once a second with the bytes on disk so far
 * (`ModelManager.watch_download`), and a `completed` or `failed` event when
 * the load resolves.
 * Once an event with state `completed` arrives, the UI typically hides
 * the bar after a short delay so the operator can confirm the size.
 */

import { useEffect, useState } from "react";

import { getSharedConnection } from "@/lib/sidecarConnection";
import { type SidecarConnection } from "@/lib/ws";
import type { ModelDownloadProgressMessage } from "@/types/generated";

export type ModelDownloadState = ModelDownloadProgressMessage["state"];

export interface ModelDownloadEvent {
  model_size: string;
  bytes_downloaded: number;
  bytes_total: number;
  fraction: number;
  state: ModelDownloadState;
  detail: string;
  received_at_ms: number;
}

export interface ModelDownloadStatus {
  /** Latest event, regardless of model size. */
  latest: ModelDownloadEvent | null;
  /** Whether the sidecar WS is currently connected. */
  connected: boolean;
}

export function useModelDownloadProgress(): ModelDownloadStatus {
  const [latest, setLatest] = useState<ModelDownloadEvent | null>(null);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let unsubProgress: (() => void) | null = null;
    let unsubStatus: (() => void) | null = null;
    let conn: SidecarConnection | null = null;

    (async () => {
      try {
        conn = await getSharedConnection();
        if (cancelled) return;
        setConnected(conn.status === "open");

        unsubStatus = conn.onStatus((s) => setConnected(s === "open"));

        unsubProgress = conn.onMessage("model_download_progress", (msg) => {
          setLatest({
            model_size: msg.model_size,
            bytes_downloaded: msg.bytes_downloaded,
            bytes_total: msg.bytes_total,
            fraction: msg.fraction,
            state: msg.state,
            detail: msg.detail ?? "",
            received_at_ms: Date.now(),
          });
        });
      } catch {
        if (!cancelled) setConnected(false);
      }
    })();

    return () => {
      cancelled = true;
      unsubProgress?.();
      unsubStatus?.();
      // Shared connection — only unsubscribe, never close it.
    };
  }, []);

  return { latest, connected };
}
