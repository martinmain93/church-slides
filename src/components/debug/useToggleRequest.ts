/**
 * A debug-panel checkbox that talks to the sidecar, and says so when it fails.
 *
 * Every one of these toggles was written as `void setSomething(checked)` with
 * no `.catch()`. A rejected request — sidecar down, session not running,
 * anything — left the checkbox snapping back to its old value with no
 * explanation, which reads as "the app ignored my click". The rest of the app
 * surfaces sidecar errors through the toast (`AISettings`, the recordings
 * views); these tabs simply never did.
 *
 * Same class of defect as the bare `catch {}` in `recordingClient.ts` that
 * hid a service session losing a contention race: the failure existed only in
 * an unread rejected promise.
 */

import { useCallback } from "react";

import { useToast } from "@/lib/toast";

/**
 * Wraps a sidecar toggle call so a failure is reported rather than swallowed.
 *
 * `label` names the setting in the error ("Could not turn on <label>").
 */
export function useToggleRequest(
  label: string,
  send: (enabled: boolean) => Promise<unknown>,
): (enabled: boolean) => void {
  const toast = useToast();
  return useCallback(
    (enabled: boolean) => {
      void send(enabled).catch((err: unknown) => {
        const message = err instanceof Error ? err.message : String(err);
        toast.error(
          `Could not turn ${enabled ? "on" : "off"} ${label}: ${message}`,
        );
      });
    },
    [label, send, toast],
  );
}
