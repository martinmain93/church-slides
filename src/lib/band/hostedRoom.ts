/**
 * The organization's hosted live room, app-wide.
 *
 * When this computer is signed in to an organization whose role runs the
 * live set, every service goes to the organization's room on the server
 * (`docs/server-protocol/`), with no band screens button. Band devices with
 * a band link follow it from anywhere. Like the self-hosted relay's room
 * (`onlineRoom.ts`), it isn't owned by one screen: it stays open between
 * services and goes back to "waiting for the service" when one ends.
 *
 * Each (re)connection asks the server for a fresh one-use ticket, so a
 * dropped connection (or a sleeping laptop) comes back on its own.
 */

import { CloudHost, type CloudConnection, type HostSnapshot } from "@/lib/band/cloudHost";
import type { BandCommand } from "@/lib/band/protocol";
import { createLiveTicket } from "@/lib/account";
import { bandLibraryIndex } from "@/lib/tauri";

export interface HostedRoomState {
  org: { orgId: string; orgName: string } | null;
  connection: CloudConnection | "off";
  clients: number;
}

let state: HostedRoomState = { org: null, connection: "off", clients: 0 };
const listeners = new Set<() => void>();
let host: CloudHost | null = null;
let commandHandler: ((command: BandCommand) => void) | null = null;

function set(patch: Partial<HostedRoomState>) {
  state = { ...state, ...patch };
  for (const l of listeners) l();
}

export const hostedRoom = {
  getState: (): HostedRoomState => state,

  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },

  /** Open the organization's room (no-op if it's already the open one). */
  start(org: { orgId: string; orgName: string }): void {
    if (host && state.org?.orgId === org.orgId) return;
    hostedRoom.stop();
    set({ org, clients: 0 });
    host = new CloudHost(async () => (await createLiveTicket(org.orgId)).url, {
      onCommand: (c) => commandHandler?.(c),
      onClients: (clients) => set({ clients }),
      onConnection: (connection) => set({ connection }),
      libraryIndex: bandLibraryIndex,
    });
    host.connect();
  },

  /** Disconnect (signed out, or another organization). The room keeps the
   * last service for devices until the server forgets it. */
  stop(): void {
    host?.close(false);
    host = null;
    set({ org: null, connection: "off", clients: 0 });
  },

  publish(kind: HostSnapshot, data: unknown): void {
    host?.publish(kind, data);
  },

  /** The service ended; the room stays open for the next one. */
  idle(): void {
    host?.idle();
  },

  setCommandHandler(handler: ((command: BandCommand) => void) | null): void {
    commandHandler = handler;
  },
};

/** Tests only. */
export function resetHostedRoomForTests(): void {
  host = null;
  commandHandler = null;
  state = { org: null, connection: "off", clients: 0 };
}
