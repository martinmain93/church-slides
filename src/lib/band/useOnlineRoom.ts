/**
 * The online band room for any screen that shows the band screens panel —
 * the fallback for when band devices can't reach the slides computer over the
 * church's Wi-Fi. The room itself is app-wide (`onlineRoom.ts`); this adds
 * whether it's set up on this computer, for the panel.
 */

import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

import type { CloudConnection } from "@/lib/band/cloudHost";
import { onlineRoom } from "@/lib/band/onlineRoom";
import { isInTauri } from "@/lib/invoke";
import { cloudRelayStatus, type CloudStatus } from "@/lib/tauri";

export interface OnlineRoomApi {
  /** Whether online band screens are set up on this computer (null: unknown yet). */
  status: CloudStatus | null;
  refreshStatus: () => Promise<void>;
  /** Links for the open room. */
  room: { bandUrl: string; leaderUrl: string } | null;
  connection: CloudConnection | "off";
  clients: number;
  error: string | null;
  start: () => Promise<void>;
  stop: () => void;
  /** New links for this computer's room; the old QR codes stop working. */
  newLinks: () => Promise<void>;
}

export function useOnlineRoom(opts: {
  /** The leader PIN (shared with the local band server, so a leader's device needs one). */
  getPin: () => Promise<string>;
}): OnlineRoomApi {
  const [status, setStatus] = useState<CloudStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const room = useSyncExternalStore(onlineRoom.subscribe, onlineRoom.getState);
  const getPinRef = useRef(opts.getPin);
  getPinRef.current = opts.getPin;

  const refreshStatus = useCallback(async () => {
    if (!isInTauri()) return;
    try {
      setStatus(await cloudRelayStatus());
    } catch (e) {
      setStatusError(e instanceof Error ? e.message : String(e));
    }
  }, []);
  useEffect(() => {
    void refreshStatus();
  }, [refreshStatus]);

  // opening may update the relay: refresh, so "Update the relay" goes away
  const start = useCallback(
    () => onlineRoom.start(() => getPinRef.current()).then(refreshStatus),
    [refreshStatus],
  );
  const stop = useCallback(() => onlineRoom.stop(), []);
  const newLinks = useCallback(() => onlineRoom.newLinks(() => getPinRef.current()), []);

  return useMemo(
    () => ({
      status,
      refreshStatus,
      room: room.room,
      connection: room.connection,
      clients: room.clients,
      error: room.error ?? statusError,
      start,
      stop,
      newLinks,
    }),
    [status, refreshStatus, room, statusError, start, stop, newLinks],
  );
}
