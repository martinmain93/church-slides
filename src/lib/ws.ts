/**
 * Minimal request/response client for the Python sidecar.
 *
 * - JSON over a localhost WebSocket
 * - Every message has a `kind` discriminator
 * - Client requests carry an `id` so we can correlate replies
 * - Auto-reconnects with backoff if the socket drops while the page is open —
 *   re-resolving the port first, so it follows an engine the desktop app
 *   relaunched after a crash (new process, new port) and tells subscribers
 *   (`onEngineRestart`) that the engine's state is gone
 * - `request()` has a default 5 s timeout
 *
 * `onMessage`'s `kind` parameter and callback are linked through
 * `ServerMessage`'s discriminated union (`generated.ts`, generated from the
 * sidecar's own Pydantic models): passing `"slide_change"` types the
 * callback's argument as `SlideChangeMessage`, not a generic bag, and a
 * misspelled or retired `kind` string is a compile error instead of a
 * silently-never-firing subscription. Every call site used to do its own
 * `raw as unknown as SomeMessage` cast, which enforced nothing — the string
 * and the cast could disagree and nothing would catch it.
 */
import { logToFile } from "@/lib/uiLog";
import type {
  ClientMessage,
  ResponseForKind,
  ServerMessage,
} from "@/types/generated";

const REQUEST_TIMEOUT_MS = 5_000;
const RECONNECT_BASE_MS = 500;
const RECONNECT_MAX_MS = 8_000;

type WireMessage = { kind: string; id?: number | null };
type Reply = WireMessage & Record<string, unknown>;

//: Server messages, keyed by their own `kind` literal.
type ServerMessageOf<K extends ServerMessage["kind"]> = Extract<ServerMessage, { kind: K }>;

export interface SidecarConnection {
  /** The engine's current port — changes if the engine is relaunched. */
  readonly port: number;
  /** Current connection status (for hooks that mount after open). */
  readonly status: ConnectionStatus;
  /**
   * Send a request and await its reply, typed by what the server actually
   * answers that request with — see `ResponseForKind` in `generated.ts`,
   * which codegen derives from the protocol rather than anyone restating it.
   *
   * This used to return an opaque `Reply` that every call site re-asserted
   * with `as unknown as T`, i.e. ~12 unchecked claims about the server.
   *
   * A *notification* the server never answers (`record_slide_change`) maps to
   * `never`, so calling `request()` on it fails to compile — use `notify()`,
   * which is what it always meant.
   */
  request<M extends AnsweredMessage>(
    msg: M,
    timeoutMs?: number,
    onPartial?: (msg: ResponseForKind[M["kind"]]) => void,
  ): Promise<ResponseForKind[M["kind"]]>;
  /**
   * Fire a message the server does not reply to.
   *
   * `record_slide_change` was being sent through `request()`, so every slide
   * change in a service left a pending entry waiting the full timeout before
   * rejecting into a swallowed `.catch()`. Nothing broke, but nothing was
   * being awaited either.
   */
  notify(msg: ClientMessage): void;
  close(): void;
  /** Subscribe to connection-state changes — useful for UI status indicators. */
  onStatus(cb: (status: ConnectionStatus) => void): () => void;
  /**
   * Subscribe to unsolicited server messages of a given `kind`. The
   * callback fires every time the server broadcasts a matching message
   * (e.g. `transcript_partial`, `audio_session_state`). Returns an
   * unsubscribe function.
   */
  onMessage<K extends ServerMessage["kind"]>(
    kind: K,
    cb: (msg: ServerMessageOf<K>) => void,
  ): () => void;
  /**
   * Subscribe to "reconnected to a *new* engine process" — it crashed and the
   * desktop app relaunched it. Anything the old process held (the service's
   * audio session, its AI mode) is gone and has to be set up again. A plain
   * reconnect to the same process doesn't fire this; its state survived.
   */
  onEngineRestart(cb: () => void): () => void;
}

/**
 * The request kinds the server actually answers.
 *
 * `ResponseForKind` maps a notification to `never`, and `never` is assignable
 * to everything — so mapping alone does not stop anyone calling `request()`
 * on one. Filtering the kinds does: `record_slide_change` is not a member,
 * so passing it fails to compile and the author is pushed to `notify()`.
 */
export type AnsweredKind = {
  [K in ClientMessage["kind"]]: [ResponseForKind[K]] extends [never] ? never : K;
}[ClientMessage["kind"]];

/** A client message the server replies to. */
export type AnsweredMessage = Extract<ClientMessage, { kind: AnsweredKind }>;

export type ConnectionStatus = "connecting" | "open" | "closed";

interface PendingRequest {
  resolve: (msg: Reply) => void;
  reject: (err: Error) => void;
  timer: ReturnType<typeof setTimeout>;
  /** A partial reply means the work is still going, so the wait starts over. */
  refresh: () => void;
  onPartial?: (msg: Reply) => void;
}

/**
 * @param resolvePort asked for the engine's port before each reconnect. A
 *   different answer means a relaunched engine (it binds a fresh port). May
 *   wait while the engine restarts; a rejection just schedules another try.
 */
export async function connectSidecar(
  port: number,
  resolvePort?: () => Promise<number>,
): Promise<SidecarConnection> {
  let url = `ws://127.0.0.1:${port}/`;
  const restartListeners = new Set<() => void>();

  let socket: WebSocket | null = null;
  let nextId = 1;
  let closedByUser = false;
  let reconnectAttempt = 0;
  const pending = new Map<number, PendingRequest>();
  const statusListeners = new Set<(s: ConnectionStatus) => void>();
  const messageListeners = new Map<string, Set<(msg: Reply) => void>>();

  let currentStatus: ConnectionStatus = "connecting";
  const setStatus = (s: ConnectionStatus) => {
    currentStatus = s;
    for (const cb of statusListeners) cb(s);
  };

  const cleanup = (sock: WebSocket) => {
    sock.onopen = null;
    sock.onclose = null;
    sock.onerror = null;
    sock.onmessage = null;
  };

  const failPending = (err: Error) => {
    for (const p of pending.values()) {
      clearTimeout(p.timer);
      p.reject(err);
    }
    pending.clear();
  };

  const open = (): Promise<WebSocket> =>
    new Promise((resolve, reject) => {
      setStatus("connecting");
      const sock = new WebSocket(url);
      let opened = false;
      sock.onopen = () => {
        opened = true;
        reconnectAttempt = 0;
        setStatus("open");
        resolve(sock);
      };
      sock.onerror = () => {
        // An open socket's error is the engine going away: `close` follows
        // and reconnects. Detaching it here (as this did for every error)
        // left a crashed engine's screen disconnected for good.
        if (opened) return;
        cleanup(sock);
        reject(new Error(`failed to open WebSocket at ${url}`));
      };
      sock.onmessage = (ev) => {
        let parsed: Reply;
        try {
          parsed = JSON.parse(ev.data) as Reply;
        } catch {
          return;
        }
        const id = parsed["id"];
        if (typeof id === "number") {
          const entry = pending.get(id);
          if (entry) {
            // A prefix of a long analysis. The request stays open, and the
            // timeout starts over — progress means the sidecar is still working.
            if (parsed["partial"] === true) {
              entry.refresh();
              entry.onPartial?.(parsed);
              return;
            }
            pending.delete(id);
            clearTimeout(entry.timer);
            entry.resolve(parsed);
            return;
          }
        }
        // Unsolicited or untracked message — fan out to kind subscribers.
        const kind = typeof parsed.kind === "string" ? parsed.kind : null;
        if (kind) {
          const subs = messageListeners.get(kind);
          if (subs) {
            for (const cb of subs) cb(parsed);
          }
        }
      };
      sock.onclose = (ev?: CloseEvent) => {
        setStatus("closed");
        failPending(new Error("sidecar connection closed"));
        if (closedByUser) return;
        // What a "sidecar not connected" error means in practice: requests
        // fail until this reconnects. In the log, next to the engine's lines,
        // it shows whether the engine crashed or only the socket dropped.
        logToFile("warn", `AI engine connection lost (code ${ev?.code ?? "?"}); reconnecting`);
        const delay = Math.min(
          RECONNECT_MAX_MS,
          RECONNECT_BASE_MS * Math.pow(2, reconnectAttempt),
        );
        reconnectAttempt += 1;
        setTimeout(() => void reconnect(), delay);
      };
    });

  // One reconnect attempt: find the engine (maybe on a new port), open, and
  // if it's a new process tell subscribers once the socket is usable.
  const reconnect = async () => {
    if (closedByUser) return;
    const retryLater = () => {
      const delay = Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * Math.pow(2, reconnectAttempt));
      reconnectAttempt += 1;
      setTimeout(() => void reconnect(), delay);
    };
    let nextPort = port;
    if (resolvePort) {
      try {
        nextPort = await resolvePort();
      } catch {
        retryLater(); // engine not back (or given up on)
        return;
      }
    }
    const restarted = nextPort !== port;
    port = nextPort;
    url = `ws://127.0.0.1:${port}/`;
    try {
      socket = await open();
    } catch {
      // A failed open detaches its own handlers (`cleanup` in onerror), so
      // no onclose will come to retry for us — this used to be where
      // reconnecting quietly stopped for good.
      retryLater();
      return;
    }
    logToFile("info", `AI engine connection back${restarted ? ` (engine restarted, port ${port})` : ""}`);
    if (restarted) for (const cb of restartListeners) cb();
  };

  socket = await open();

  return {
    get port() {
      return port;
    },
    get status() {
      return currentStatus;
    },
  request<M extends AnsweredMessage>(
    msg: M,
    timeoutMs = REQUEST_TIMEOUT_MS,
    onPartial?: (msg: ResponseForKind[M["kind"]]) => void,
  ): Promise<ResponseForKind[M["kind"]]> {
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return Promise.reject(new Error("sidecar not connected"));
    }
    const id = nextId++;
    return new Promise<Reply>((resolve, reject) => {
      const entry: PendingRequest = {
        resolve,
        reject,
        timer: 0 as unknown as ReturnType<typeof setTimeout>,
        refresh: () => undefined,
        onPartial: onPartial as ((msg: Reply) => void) | undefined,
      };
      entry.refresh = () => {
        clearTimeout(entry.timer);
        entry.timer = setTimeout(() => {
          pending.delete(id);
          reject(new Error(`sidecar request timed out after ${timeoutMs} ms`));
        }, timeoutMs);
      };
      entry.refresh();
      pending.set(id, entry);
      socket!.send(JSON.stringify({ ...msg, id }));
    }) as unknown as Promise<ResponseForKind[M["kind"]]>;
  },
    notify(msg: ClientMessage): void {
      // No id, no pending entry, no timer — there is no reply to correlate.
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify(msg));
      }
    },
    close() {
      closedByUser = true;
      socket?.close();
      failPending(new Error("connection closed by client"));
    },
    onStatus(cb) {
      statusListeners.add(cb);
      return () => {
        statusListeners.delete(cb);
      };
    },
    onEngineRestart(cb) {
      restartListeners.add(cb);
      return () => {
        restartListeners.delete(cb);
      };
    },
    onMessage(kind, cb) {
      // The internal registry is one untyped bag per `kind` string — real
      // per-`kind` typing only exists at the public signature above
      // (`ServerMessageOf<K>`), which is what every call site actually
      // sees. This cast is the one place that boundary gets crossed.
      const typelessCb = cb as unknown as (msg: Reply) => void;
      let subs = messageListeners.get(kind);
      if (!subs) {
        subs = new Set();
        messageListeners.set(kind, subs);
      }
      subs.add(typelessCb);
      return () => {
        const set = messageListeners.get(kind);
        if (set) {
          set.delete(typelessCb);
          if (set.size === 0) messageListeners.delete(kind);
        }
      };
    },
  };
}
