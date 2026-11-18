/**
 * The window's half of organization library sync: the change-notice socket
 * and the status the sync loop reports (the loop itself runs in the app,
 * `src-tauri/src/library_sync`).
 *
 * The socket is only a nudge: on "changed" the app pulls at once. Without
 * it the app still pulls every minute, so a dropped connection just waits
 * and tries again.
 */

import { useEffect, useState } from "react";

import { isInTauri } from "@/lib/invoke";
import {
  LIBRARY_SYNC_EVENT,
  librarySyncNow,
  librarySyncStatus,
  librarySyncTicket,
  type LibrarySyncStatus,
} from "@/lib/tauri";

const OFF: LibrarySyncStatus = { org_id: null, state: "off", waiting: 0, message: null };
const PING_MS = 30_000;
const RETRY_MIN_MS = 5_000;
const RETRY_MAX_MS = 60_000;

export function useLibrarySync(): LibrarySyncStatus {
  const [status, setStatus] = useState<LibrarySyncStatus>(OFF);

  useEffect(() => {
    if (!isInTauri()) return;
    let unlisten: (() => void) | undefined;
    let cancelled = false;
    librarySyncStatus().then((s) => !cancelled && setStatus(s), () => undefined);
    void import("@tauri-apps/api/event").then(async ({ listen }) => {
      const off = await listen<LibrarySyncStatus>(LIBRARY_SYNC_EVENT, (e) => setStatus(e.payload));
      if (cancelled) off();
      else unlisten = off;
    });
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, []);

  const org = status.state === "signed_out" ? null : status.org_id;
  useEffect(() => {
    if (!org) return;
    let closed = false;
    let socket: WebSocket | null = null;
    let ping: ReturnType<typeof setInterval> | undefined;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let wait = RETRY_MIN_MS;
    const again = () => {
      if (closed) return;
      retry = setTimeout(connect, wait);
      wait = Math.min(wait * 2, RETRY_MAX_MS);
    };
    async function connect() {
      let url: string;
      try {
        url = await librarySyncTicket(org!);
      } catch {
        again();
        return;
      }
      if (closed) return;
      socket = new WebSocket(url);
      socket.onopen = () => {
        wait = RETRY_MIN_MS;
        ping = setInterval(() => socket?.readyState === WebSocket.OPEN && socket.send("ping"), PING_MS);
      };
      socket.onmessage = (e) => {
        try {
          if ((JSON.parse(String(e.data)) as { type?: string }).type === "changed") void librarySyncNow();
        } catch {
          // "pong", or anything else: nothing to do
        }
      };
      socket.onclose = () => {
        clearInterval(ping);
        socket = null;
        again();
      };
    }
    void connect();
    return () => {
      closed = true;
      clearTimeout(retry);
      clearInterval(ping);
      socket?.close();
    };
  }, [org]);

  return status;
}

/** What to tell the person, or null when there's nothing worth saying. */
export function syncNote(s: LibrarySyncStatus): { text: string; tone: "quiet" | "warn" } | null {
  if (!s.org_id) return null;
  const waiting = s.waiting === 1 ? "1 change waiting to go up" : `${s.waiting} changes waiting to go up`;
  switch (s.state) {
    case "signed_out":
      return { text: "Signed out: sign in to keep this library up to date", tone: "warn" };
    case "offline":
      return { text: s.waiting > 0 ? `Offline · ${waiting}` : "Offline", tone: "quiet" };
    case "error":
      return { text: s.message ?? "Couldn't sync this library", tone: "warn" };
    case "ok":
      return s.message ? { text: s.message, tone: "warn" } : null;
    default:
      return null;
  }
}
