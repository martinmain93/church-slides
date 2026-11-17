/**
 * The online band room, app-wide.
 *
 * Like the local band server, it isn't owned by any one screen: the operator
 * can start it in the setlist editor so the band joins before the service,
 * and the same room — the same QR codes — carries on into the service and
 * between services. A service publishes into it while it runs and sends it
 * back to "waiting for the service" when it ends. "Close the online room"
 * (or quitting the app) ends it and wipes what it held — but the room itself
 * is this computer's, the same each time (`cloud.rs`), so the band's links
 * work again the moment it's reopened.
 */

import { CloudHost, type CloudConnection } from "@/lib/band/cloudHost";
import type { BandCommand } from "@/lib/band/protocol";
import {
  bandLibraryIndex,
  cloudRelayNewLinks,
  cloudRelayNewRoom,
  cloudRelayRedeploy,
  cloudRelayStatus,
} from "@/lib/tauri";

/**
 * After an app update the relay in the church's account still serves the old
 * band page (it carries its own copy), so devices joining online miss what the
 * update changed. Bring it up to date as the room opens — the moment the
 * operator is setting up the band link, so a keychain prompt makes sense —
 * and open the room on the old relay if that fails.
 */
async function updateRelayIfStale(): Promise<void> {
  try {
    const status = await cloudRelayStatus();
    if (status.configured && status.relay_built && status.needs_update) await cloudRelayRedeploy();
  } catch (e) {
    console.warn("couldn't update the online relay; opening the room on the old one", e);
  }
}

export interface OnlineRoomState {
  room: { bandUrl: string; leaderUrl: string } | null;
  connection: CloudConnection | "off";
  clients: number;
  error: string | null;
}

let state: OnlineRoomState = { room: null, connection: "off", clients: 0, error: null };
const listeners = new Set<() => void>();
let host: CloudHost | null = null;
let starting: Promise<void> | null = null;
/** The running service's handler for leader commands; none between services. */
let commandHandler: ((command: BandCommand) => void) | null = null;

function set(patch: Partial<OnlineRoomState>) {
  state = { ...state, ...patch };
  for (const l of listeners) l();
}

export const onlineRoom = {
  getState: (): OnlineRoomState => state,

  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },

  /** Open a room (no-op if one is open). `getPin` gives the leader PIN. */
  start(getPin: () => Promise<string>): Promise<void> {
    if (host) return Promise.resolve();
    starting ??= (async () => {
      set({ error: null });
      try {
        const pin = await getPin();
        await updateRelayIfStale();
        const r = await cloudRelayNewRoom(pin);
        host = new CloudHost(r.host_socket, {
          onCommand: (c) => commandHandler?.(c),
          onClients: (clients) => set({ clients }),
          onConnection: (connection) => set({ connection }),
          libraryIndex: bandLibraryIndex,
        });
        host.connect();
        const base = `${r.base_url}/r/${r.room}`;
        set({ room: { bandUrl: `${base}/band?k=${r.key}`, leaderUrl: `${base}/leader?k=${r.key}&pin=${pin}` } });
      } catch (e) {
        set({ error: e instanceof Error ? e.message : String(e) });
      } finally {
        starting = null;
      }
    })();
    return starting;
  },

  /** Close the room for good: devices go idle and the room is wiped. */
  stop(): void {
    host?.close(true);
    host = null;
    set({ room: null, connection: "off", clients: 0 });
  },

  /** Replace this computer's room (old links stop working), reopening it if it was open. */
  async newLinks(getPin: () => Promise<string>): Promise<void> {
    const wasOpen = host !== null;
    if (wasOpen) onlineRoom.stop();
    try {
      await cloudRelayNewLinks();
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
      return;
    }
    if (wasOpen) await onlineRoom.start(getPin);
  },

  publish(kind: "setlist" | "position", data: unknown): void {
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

/** Tests only: drop any room without telling the relay. */
export function resetOnlineRoomForTests(): void {
  host = null;
  starting = null;
  commandHandler = null;
  state = { room: null, connection: "off", clients: 0, error: null };
}
