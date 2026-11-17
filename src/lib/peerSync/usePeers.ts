/**
 * Other Church Slides operators on this network, as the Rust side finds them
 * (Bonjour) or as they're added by address. Updates live.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { isInTauri } from "@/lib/invoke";
import { listPeers, peerSyncInfo, PEERS_EVENT, type Peer, type PeerSyncInfo } from "@/lib/tauri";

/** Subscribe to a Tauri event for the life of a component. */
export function useTauriEvent<T>(event: string, handler: (payload: T) => void) {
  const latest = useRef(handler);
  latest.current = handler;
  useEffect(() => {
    if (!isInTauri()) return;
    let unlisten: (() => void) | undefined;
    let cancelled = false;
    void import("@tauri-apps/api/event").then(async ({ listen }) => {
      const un = await listen<T>(event, (e) => latest.current(e.payload));
      if (cancelled) un();
      else unlisten = un;
    });
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, [event]);
}

export function usePeers(): { peers: Peer[]; me: PeerSyncInfo | null; refreshMe: () => void } {
  const [peers, setPeers] = useState<Peer[]>([]);
  const [me, setMe] = useState<PeerSyncInfo | null>(null);
  // This computer's addresses and network code change when it joins other Wi-Fi.
  const refreshMe = useCallback(() => {
    peerSyncInfo().then(setMe).catch(() => undefined);
  }, []);
  useEffect(() => {
    listPeers().then(setPeers).catch(() => undefined);
    refreshMe();
  }, [refreshMe]);
  useTauriEvent<Peer[]>(PEERS_EVENT, setPeers);
  return { peers, me, refreshMe };
}
