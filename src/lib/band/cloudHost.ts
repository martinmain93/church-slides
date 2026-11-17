/**
 * The slides computer's side of the online relay (`relay/src`): one socket to
 * this service's room, kept alive, publishing the same snapshots the local
 * band server gets.
 *
 * Robustness mirrors the band devices':
 *  - reconnect forever with back-off, and re-send the latest setlist and
 *    position on every (re)connect, so a dropped connection can't leave the
 *    band on a stale slide;
 *  - ping every 20 s (answered by Cloudflare without waking the room) and
 *    treat 45 s of silence as a dead socket.
 */

import type { LibrarySong } from "@/lib/band/librarySearch";
import { isBandCommand, type BandCommand } from "@/lib/band/protocol";

export type CloudConnection = "connecting" | "live" | "reconnecting" | "closed";

export interface CloudHostHandlers {
  onCommand: (command: BandCommand) => void;
  onClients: (count: number) => void;
  onConnection: (state: CloudConnection) => void;
  /** The library for a leader's search screen. */
  libraryIndex: () => Promise<LibrarySong[]>;
}

const PING_EVERY_MS = 20_000;
const SILENCE_LIMIT_MS = 45_000;
/** Keep each library message well under the relay's 1 MiB message limit. */
const LIBRARY_PART_CHARS = 400_000;

/** Split the library into parts that each serialise under `limit` characters. Exported for tests. */
export function libraryParts(songs: LibrarySong[], limit = LIBRARY_PART_CHARS): LibrarySong[][] {
  const parts: LibrarySong[][] = [];
  let current: LibrarySong[] = [];
  let size = 0;
  for (const s of songs) {
    const n = JSON.stringify(s).length + 1;
    if (current.length > 0 && size + n > limit) {
      parts.push(current);
      current = [];
      size = 0;
    }
    current.push(s);
    size += n;
  }
  parts.push(current);
  return parts;
}

export class CloudHost {
  private sock: WebSocket | null = null;
  private latest: Record<"setlist" | "position", unknown> = { setlist: null, position: null };
  private attempt = 0;
  private retryTimer: ReturnType<typeof setTimeout> | undefined;
  private pinger: ReturnType<typeof setInterval> | undefined;
  private watchdog: ReturnType<typeof setTimeout> | undefined;
  private closed = false;

  constructor(
    private url: string,
    private handlers: CloudHostHandlers,
  ) {}

  connect(): void {
    if (this.closed) return;
    clearTimeout(this.retryTimer);
    this.handlers.onConnection(this.attempt === 0 ? "connecting" : "reconnecting");
    let sock: WebSocket;
    try {
      sock = new WebSocket(this.url);
    } catch {
      this.scheduleRetry();
      return;
    }
    this.sock = sock;
    sock.onopen = () => {
      if (this.sock !== sock) return;
      this.attempt = 0;
      this.handlers.onConnection("live");
      this.armWatchdog(sock);
      for (const kind of ["setlist", "position"] as const) {
        if (this.latest[kind] !== null) sock.send(JSON.stringify({ type: kind, data: this.latest[kind] }));
      }
      clearInterval(this.pinger);
      this.pinger = setInterval(() => {
        if (sock.readyState === WebSocket.OPEN) sock.send("ping");
      }, PING_EVERY_MS);
    };
    sock.onmessage = (ev) => {
      if (this.sock !== sock) return;
      this.armWatchdog(sock);
      if (ev.data === "pong") return;
      let msg: Record<string, unknown>;
      try {
        msg = JSON.parse(String(ev.data));
      } catch {
        return;
      }
      if (msg.type === "clients" && typeof msg.count === "number") this.handlers.onClients(msg.count);
      else if (msg.type === "command" && isBandCommand(msg.command)) this.handlers.onCommand(msg.command);
      else if (msg.type === "library_request" && typeof msg.from === "string") void this.sendLibrary(msg.from);
    };
    // The socket is finished — however that's reported. Engines differ: a
    // failed connect may fire `error` and never `close` (Node), so either
    // event ends it, and the identity check makes the second one a no-op.
    const finished = () => {
      if (this.sock !== sock) return;
      this.sock = null;
      clearInterval(this.pinger);
      clearTimeout(this.watchdog);
      sock.onopen = sock.onmessage = sock.onclose = sock.onerror = null;
      // Close once (closing a socket that never opened can fire `error` again
      // straight away — in Node it recursed until the stack overflowed).
      if (sock.readyState !== WebSocket.CLOSED) {
        try {
          sock.close();
        } catch {
          // already gone
        }
      }
      if (!this.closed) this.scheduleRetry();
    };
    sock.onclose = finished;
    sock.onerror = finished;
  }

  /** Remember and send a snapshot. Remembered ones are re-sent on reconnect. */
  publish(kind: "setlist" | "position", data: unknown): void {
    this.latest[kind] = data;
    if (this.sock?.readyState === WebSocket.OPEN) this.sock.send(JSON.stringify({ type: kind, data }));
  }

  /**
   * The service ended but the room stays open (for the next one): forget the
   * snapshots here and in the room, and devices go back to waiting.
   */
  idle(): void {
    this.latest = { setlist: null, position: null };
    if (this.sock?.readyState === WebSocket.OPEN) this.sock.send(JSON.stringify({ type: "idle" }));
  }

  /** Stop. `endService` also tells the room to forget everything and devices to go idle. */
  close(endService: boolean): void {
    this.closed = true;
    clearTimeout(this.retryTimer);
    clearInterval(this.pinger);
    clearTimeout(this.watchdog);
    const sock = this.sock;
    this.sock = null;
    if (sock?.readyState === WebSocket.OPEN && endService) sock.send(JSON.stringify({ type: "close" }));
    sock?.close();
    this.handlers.onConnection("closed");
  }

  private async sendLibrary(to: string): Promise<void> {
    let songs: LibrarySong[];
    try {
      songs = await this.handlers.libraryIndex();
    } catch {
      return;
    }
    const parts = libraryParts(songs);
    parts.forEach((data, part) => {
      if (this.sock?.readyState === WebSocket.OPEN) {
        this.sock.send(JSON.stringify({ type: "library", to, data, part, parts: parts.length }));
      }
    });
  }

  private armWatchdog(sock: WebSocket): void {
    clearTimeout(this.watchdog);
    this.watchdog = setTimeout(() => sock.close(), SILENCE_LIMIT_MS);
  }

  private scheduleRetry(): void {
    if (this.closed) return;
    this.handlers.onConnection("reconnecting");
    const delay = Math.min(10_000, 500 * 2 ** this.attempt);
    this.attempt += 1;
    this.retryTimer = setTimeout(() => this.connect(), delay);
  }
}
