/**
 * Single shared sidecar WebSocket connection for the whole app.
 *
 * Previously every hook (`useTranscriptStream`, `useAudioLevels`,
 * `useBeliefState`, `useAiStatus`, …) and every one-shot `requestSidecar`
 * opened its *own* WebSocket. With ~10 live hooks plus React StrictMode
 * double-mounting in dev that meant a storm of connect/disconnect churn and
 * a lot of redundant per-socket JSON parsing of the ~10 Hz `audio_level`
 * broadcast — which showed up as UI lag. It also made the present-mode
 * start/stop session lifecycle racy: start and stop landed on different
 * sockets and could be processed out of order.
 *
 * This module hands every caller the *same* `SidecarConnection`. The
 * underlying socket auto-reconnects (see `connectSidecar`), so the shared
 * handle survives drops. Consumers subscribe via `onMessage`/`onStatus` and,
 * on cleanup, only unsubscribe — they must NOT `close()` the shared socket.
 *
 * Bonus: because all messages now flow over one socket, the server processes
 * them in send order — so a StrictMode `start → stop → start` sequence is
 * applied deterministically instead of racing across sockets.
 */

import { getSidecarPort } from "@/lib/tauri";
import { connectSidecar, type SidecarConnection } from "@/lib/ws";

let connPromise: Promise<SidecarConnection> | null = null;

/**
 * Resolve the process-wide sidecar connection, opening it on first use.
 * Rejects (and clears the cache so a later call can retry) if the sidecar
 * is unavailable — e.g. browser dev mode without a running sidecar.
 */
export function getSharedConnection(): Promise<SidecarConnection> {
  if (!connPromise) {
    connPromise = (async () => {
      const port = await getSidecarPort();
      // Re-resolved on every reconnect: a crashed engine comes back on a
      // new port (see `run_sidecar` in src-tauri/src/sidecar.rs).
      return connectSidecar(port, getSidecarPort);
    })().catch((err) => {
      // Allow a retry on the next call rather than caching the failure.
      connPromise = null;
      throw err;
    });
  }
  return connPromise;
}

/**
 * Tear down the shared connection. Only for app shutdown / tests — normal
 * consumers never close it (they share it).
 */
export function closeSharedConnection(): void {
  const pending = connPromise;
  connPromise = null;
  if (pending) {
    pending.then((conn) => conn.close()).catch(() => undefined);
  }
}
