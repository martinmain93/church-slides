/**
 * Single-shot WebSocket request helper.
 *
 * `requestSidecar(payload)` sends one request over the shared sidecar
 * connection and resolves with the matching reply. It reuses the
 * process-wide connection (`getSharedConnection`) rather than opening and
 * closing a fresh socket per call, which previously added connection churn.
 *
 * If the sidecar is unavailable (browser dev mode, missing PortAudio,
 * etc.), the promise rejects with a descriptive error and callers fall
 * back to a sensible empty state.
 */

import { getSharedConnection } from "@/lib/sidecarConnection";
import type { ResponseForKind } from "@/types/generated";
import type { AnsweredMessage } from "@/lib/ws";

export async function requestSidecar<M extends AnsweredMessage>(
  payload: M,
  options: { timeoutMs?: number } = {},
): Promise<ResponseForKind[M["kind"]]> {
  const conn = await getSharedConnection();
  return conn.request(payload, options.timeoutMs);
}
