/**
 * State that has to outlive the component watching it.
 *
 * The debug panel renders its tabs as `{tab === "Transcript" && <TranscriptTab/>}`,
 * so switching tabs *unmounts* the tab and destroys the `useState` inside its
 * hook. The sidecar keeps broadcasting throughout — nothing is lost upstream —
 * but the hook restarts empty and stays empty until the next message, which
 * while singing is three to five seconds. From the operator's chair the panel
 * looks broken, and worse, looks like the tracker has been reset.
 *
 * So the latest value lives here instead, in module scope, and the subscription
 * is opened once and never torn down. A hook mounting later reads the current
 * value immediately and subscribes for subsequent ones. Nothing accumulates
 * without bound: each store holds exactly one value, and callers that want
 * history (the rolling word list) fold it themselves.
 */

import { useEffect, useState } from "react";

import { getSharedConnection } from "@/lib/sidecarConnection";
import type { ServerMessage } from "@/types/generated";

type Listener<T> = (value: T) => void;

export interface LiveStore<T> {
  get(): T;
  set(next: T | ((prev: T) => T)): void;
  subscribe(listener: Listener<T>): () => void;
}

export function createLiveStore<T>(initial: T): LiveStore<T> {
  let value = initial;
  const listeners = new Set<Listener<T>>();
  return {
    get: () => value,
    set(next) {
      value =
        typeof next === "function" ? (next as (prev: T) => T)(value) : next;
      for (const l of listeners) l(value);
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

/** Read a store, re-rendering when it changes. Seeded with the current value. */
export function useLiveStore<T>(store: LiveStore<T>): T {
  const [value, setValue] = useState<T>(store.get);
  useEffect(() => {
    setValue(store.get());
    return store.subscribe(setValue);
  }, [store]);
  return value;
}

/**
 * Feed a store from a sidecar broadcast, subscribing once for the process
 * lifetime.
 *
 * Deliberately never unsubscribed. The alternative is reference-counting
 * mounts, which reintroduces the bug the moment the count reaches zero between
 * a tab unmounting and the next mounting.
 */
export function pipeFromSidecar<T, K extends ServerMessage["kind"]>(
  kind: K,
  store: LiveStore<T>,
  reduce: (message: Extract<ServerMessage, { kind: K }>, previous: T) => T,
): void {
  void (async () => {
    try {
      const conn = await getSharedConnection();
      conn.onMessage(kind, (raw) => {
        store.set((prev) => reduce(raw, prev));
      });
    } catch {
      // Sidecar unavailable (browser dev). The store keeps its initial value.
    }
  })();
}
