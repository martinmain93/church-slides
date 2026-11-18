/**
 * Device side of band screens: one WebSocket to the desktop app, kept alive.
 *
 * Church Wi-Fi drops, tablets sleep, people walk out of range. So:
 *  - reconnect forever, backing off to a few seconds between tries;
 *  - treat silence as death: the device pings every 10 s and the local
 *    server also ticks every 4 s; if nothing arrives for 12 s the socket is
 *    torn down and reopened (a phone that slept often holds a socket that
 *    *looks* open but is dead). On the online relay the "pong" comes from
 *    Cloudflare itself without waking the room, so keeping alive is free;
 *  - reconnect straight away when the page becomes visible again;
 *  - never merge updates — every (re)connect gets the full current state from
 *    the server, so there's nothing to get out of sync.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import type { LibrarySong } from "@/lib/band/librarySearch";
import type {
  BandCommand,
  BandPosition,
  BandSetlist,
  ClientMessage,
  ServerMessage,
} from "@/lib/band/protocol";

export type ConnectionStatus = "connecting" | "live" | "reconnecting";

export interface BandConnection {
  status: ConnectionStatus;
  /** Hosted room: the slides computer's address on the church's network. */
  lanUrl: string | null;
  /** Local band server: the hosted room is reachable again. */
  hostedLive: boolean;
  /** Hosted room: this band link was removed (or never worked); retrying won't help. */
  linkRemoved: boolean;
  setlist: BandSetlist | null;
  position: BandPosition | null;
  /** Server says no service is running. */
  idle: boolean;
  /** Leader controls unlocked (PIN accepted). */
  isLeader: boolean;
  pinRejected: boolean;
  /** Too many wrong PINs from this device: it has to wait a few minutes. */
  tooManyTries: boolean;
  lastError: string | null;
  send: (command: BandCommand) => boolean;
  /** The library, once `requestLibrary` has fetched it (leader only). */
  library: LibrarySong[] | null;
  /** Ask for a fresh copy of the library; the current one stays usable meanwhile. */
  requestLibrary: () => void;
}

const SILENCE_LIMIT_MS = 12_000;

/** Whether the server refuses this band link (removed, or its organization
 * deleted). `false` when it can't be asked: offline isn't removed. */
export async function bandLinkRefused(link: { server: string; token: string }): Promise<boolean> {
  try {
    const res = await fetch(`${link.server}/band/v1/me`, { headers: { Authorization: `Bearer ${link.token}` } });
    return res.status === 401;
  } catch {
    return false;
  }
}
const PING_EVERY_MS = 10_000;

/**
 * A band link to an organization's hosted live room:
 * `https://churchslides.app/live/#s=<server>&t=<band link token>`. The token
 * stays in the fragment, which browsers never send to a server.
 */
export function hostedLink(loc: Pick<Location, "hash"> = window.location): { server: string; token: string } | null {
  const params = new URLSearchParams((loc.hash ?? "").replace(/^#/, ""));
  const server = params.get("s");
  const token = params.get("t");
  if (!server || !token || !/^https?:\/\//.test(server)) return null;
  return { server: server.replace(/\/+$/, ""), token };
}

/** Whether this band page has something to connect to: a hosted band link,
 * an online relay room, or (anywhere else) the slides computer serving it.
 * Only the website's `/live/` can be opened with none. */
export function bandPageHasLink(loc: Pick<Location, "hash" | "pathname"> = window.location): boolean {
  return hostedLink(loc) !== null || /^\/r\/[a-z0-9]+\/(band|leader)$/.test(loc.pathname);
}

/**
 * Where the socket lives: same origin normally; the band port in dev (served
 * by Vite); `/r/<room>/ws?k=<key>` on the online relay; the server's
 * `/band/v1/live` for a hosted room.
 */
export function bandSocketUrl(loc: Location = window.location): string {
  const hosted = hostedLink(loc);
  if (hosted) return `${hosted.server.replace(/^http/, "ws")}/band/v1/live`;
  const params = new URLSearchParams(loc.search);
  const devPort = params.get("bandPort");
  const proto = loc.protocol === "https:" ? "wss:" : "ws:";
  const room = /^\/r\/([a-z0-9]+)\/(band|leader)$/.exec(loc.pathname);
  if (room) return `${proto}//${loc.host}/r/${room[1]}/ws?k=${encodeURIComponent(params.get("k") ?? "")}`;
  return devPort ? `${proto}//${loc.hostname}:${devPort}/ws` : `${proto}//${loc.host}/ws`;
}

/**
 * The band link to hand to the next musician, from any band or leader page:
 * `/band` on the slides computer's server, `/r/<room>/band?k=<key>` on the
 * online relay. Never the leader link — that carries the PIN.
 */
export function bandInviteUrl(loc: Location = window.location): string {
  // A hosted band link is the invite (a leader link never shows this).
  if (hostedLink(loc)) return loc.href;
  const params = new URLSearchParams(loc.search);
  const room = /^\/r\/([a-z0-9]+)\/(band|leader)$/.exec(loc.pathname);
  if (room) return `${loc.origin}/r/${room[1]}/band?k=${encodeURIComponent(params.get("k") ?? "")}`;
  return `${loc.origin}/band`;
}

/** A signed-in member's way into an organization's live room (the band page
 * on the website): each connection needs a fresh one-use address. */
export interface MemberSource {
  /** Resolves to the socket address; rejects with `removed: true` when
   * the person can't join any more (signed out, no longer a member). */
  socketUrl: () => Promise<string>;
}

export function useBandConnection(role: "band" | "leader", pin: string | null, member?: MemberSource): BandConnection {
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const [setlist, setSetlist] = useState<BandSetlist | null>(null);
  const [position, setPosition] = useState<BandPosition | null>(null);
  const [idle, setIdle] = useState(false);
  const [isLeader, setIsLeader] = useState(false);
  const [pinRejected, setPinRejected] = useState(false);
  const [tooManyTries, setTooManyTries] = useState(false);
  const [lastError, setLastError] = useState<string | null>(null);
  const [library, setLibrary] = useState<LibrarySong[] | null>(null);
  const [lanUrl, setLanUrl] = useState<string | null>(null);
  const [hostedLive, setHostedLive] = useState(false);
  const [linkRemoved, setLinkRemoved] = useState(false);
  const socketRef = useRef<WebSocket | null>(null);
  const reconnectNow = useRef<() => void>(() => undefined);

  useEffect(() => {
    let disposed = false;
    let attempt = 0;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let watchdog: ReturnType<typeof setTimeout> | undefined;
    let pinger: ReturnType<typeof setInterval> | undefined;
    // The leader's library can arrive in parts (the relay limits message size).
    let libraryParts: LibrarySong[][] = [];

    const armWatchdog = (sock: WebSocket) => {
      clearTimeout(watchdog);
      watchdog = setTimeout(() => sock.close(), SILENCE_LIMIT_MS);
    };

    // A socket we've moved on from. Browsers deliver `close` asynchronously,
    // so without this a replaced socket's late `onclose` scheduled yet another
    // connection, and the one in between was left open for good: a second
    // live socket per wake, double state updates, an extra device on the
    // operator's count, and the watchdog re-armed for the wrong socket.
    const retire = (sock: WebSocket | null) => {
      if (!sock) return;
      sock.onopen = null;
      sock.onmessage = null;
      sock.onclose = null;
      sock.onerror = null;
      sock.close();
    };

    const connect = () => {
      if (disposed) return;
      clearTimeout(retryTimer);
      if (!member) {
        openSocket(null);
        return;
      }
      member.socketUrl().then(
        (url) => {
          if (!disposed) openSocket(url);
        },
        (err: unknown) => {
          if (disposed) return;
          if ((err as { removed?: boolean })?.removed) setLinkRemoved(true);
          else scheduleRetry();
        },
      );
    };

    const openSocket = (memberUrl: string | null) => {
      let sock: WebSocket;
      try {
        const hosted = hostedLink();
        // The band link's token goes as a subprotocol: browsers can't set
        // headers on a WebSocket, and this keeps it out of the URL.
        sock = memberUrl
          ? new WebSocket(memberUrl)
          : hosted
            ? new WebSocket(bandSocketUrl(), ["csb", hosted.token])
            : new WebSocket(bandSocketUrl());
      } catch {
        scheduleRetry();
        return;
      }
      socketRef.current = sock;
      armWatchdog(sock);

      let opened = false;
      sock.onopen = () => {
        if (socketRef.current !== sock) return;
        opened = true;
        attempt = 0;
        setStatus("live");
        clearInterval(pinger);
        pinger = setInterval(() => {
          if (sock.readyState === WebSocket.OPEN) sock.send("ping");
        }, PING_EVERY_MS);
        // In a hosted room the link decides: ask for leader controls and the
        // room grants them to a leader link.
        const hello: ClientMessage = memberUrl || hostedLink()
          ? { type: "hello", role: "leader" }
          : role === "leader" && pin
            ? { type: "hello", role, pin }
            : { type: "hello", role: "band" };
        sock.send(JSON.stringify(hello));
      };
      sock.onmessage = (ev) => {
        if (socketRef.current !== sock) return;
        armWatchdog(sock);
        if (ev.data === "pong") return;
        let msg: ServerMessage;
        try {
          msg = JSON.parse(String(ev.data)) as ServerMessage;
        } catch {
          return;
        }
        switch (msg.type) {
          // Slides arriving means the slides computer is back: drop any
          // "isn't connected right now" left from before.
          case "setlist":
            setSetlist(msg.data);
            setIdle(false);
            setLastError(null);
            break;
          case "position":
            setPosition(msg.data);
            setIdle(false);
            setLastError(null);
            break;
          case "idle":
            setIdle(true);
            setSetlist(null);
            setPosition(null);
            break;
          case "welcome":
            setIsLeader(msg.role === "leader");
            setPinRejected(Boolean(msg.pinRejected));
            setTooManyTries(Boolean(msg.tooManyTries));
            break;
          case "error":
            setLastError(msg.message);
            break;
          case "lan":
            setLanUrl(msg.url);
            break;
          case "hosted":
            setHostedLive(msg.live);
            break;
          case "library": {
            const parts = msg.parts ?? 1;
            const part = msg.part ?? 0;
            if (part === 0) libraryParts = [];
            libraryParts[part] = msg.data;
            if (libraryParts.filter(Boolean).length === parts) setLibrary(libraryParts.flat());
            break;
          }
        }
      };
      sock.onclose = (ev) => {
        // Only the current socket's death means "reconnect".
        if (socketRef.current !== sock) return;
        clearTimeout(watchdog);
        clearInterval(pinger);
        socketRef.current = null;
        if (disposed) return;
        setStatus("reconnecting");
        // A hosted room drops a removed link (4001) and refuses it at the
        // door after that. Ask once, then stop rather than retry forever.
        const hosted = hostedLink();
        if (hosted && (ev.code === 4001 || !opened)) {
          void bandLinkRefused(hosted).then((refused) => {
            if (disposed) return;
            if (refused) setLinkRemoved(true);
            else scheduleRetry();
          });
          return;
        }
        scheduleRetry();
      };
      // Close once: closing a socket that never opened can fire `error` again
      // straight away in some engines, recursing.
      sock.onerror = () => {
        sock.onerror = null;
        if (sock.readyState !== WebSocket.CLOSED) sock.close();
      };
    };

    const scheduleRetry = () => {
      if (disposed) return;
      const delay = Math.min(5000, 400 * 2 ** attempt);
      attempt += 1;
      retryTimer = setTimeout(connect, delay);
    };

    reconnectNow.current = () => {
      const sock = socketRef.current;
      if (!sock || sock.readyState !== WebSocket.OPEN) {
        attempt = 0;
        socketRef.current = null;
        retire(sock);
        connect();
      }
    };

    const onVisible = () => {
      if (document.visibilityState === "visible") reconnectNow.current();
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("online", onVisible);

    connect();
    return () => {
      disposed = true;
      clearTimeout(retryTimer);
      clearTimeout(watchdog);
      clearInterval(pinger);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("online", onVisible);
      const last = socketRef.current;
      socketRef.current = null;
      retire(last);
    };
  }, [role, pin, member]);

  const send = useCallback((command: BandCommand) => {
    const sock = socketRef.current;
    if (!sock || sock.readyState !== WebSocket.OPEN) return false;
    const msg: ClientMessage = { type: "command", command };
    sock.send(JSON.stringify(msg));
    return true;
  }, []);

  const requestLibrary = useCallback(() => {
    const sock = socketRef.current;
    if (!sock || sock.readyState !== WebSocket.OPEN) return;
    const msg: ClientMessage = { type: "library_request" };
    sock.send(JSON.stringify(msg));
  }, []);

  return {
    status,
    lanUrl,
    hostedLive,
    linkRemoved,
    setlist,
    position,
    idle,
    isLeader,
    pinRejected,
    tooManyTries,
    lastError,
    send,
    library,
    requestLibrary,
  };
}
