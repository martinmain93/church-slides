/**
 * Keeping the web app's copy of a library in step with the server: the same
 * loop the desktop runs in Rust (`src-tauri/src/library_sync/mod.rs`).
 *
 * Changes go up about a second after they're made. Everyone else's come
 * down at once when the change-notice socket says so, when the page comes
 * back into view or online, and every minute regardless. There's no Sync
 * button: a status shows only when something's waiting or wrong.
 */

import { ApiError, NotMemberError, OfflineError, SignedOutError, socketUrlOk } from "@/lib/account/browserSignIn";
import type { PulledRecord, PushResult, Refusal, RecordStore } from "@/library/web/store";

/** The server calls sync needs (`BrowserSession.api`, or a fake in tests). */
export type Api = <T>(method: string, path: string, body?: unknown) => Promise<T>;

export interface SyncStatus {
  /** "loading" (first download), "ok", "offline", "signed_out" or "error". */
  state: "loading" | "ok" | "offline" | "signed_out" | "error";
  /** Changes made here that haven't gone up yet. */
  waiting: number;
  /** A change the server refused, or what went wrong, in words. */
  message: string | null;
}

const PUSH_AFTER_MS = 800;
const PULL_EVERY_MS = 60_000;

export function refusalText(r: Refusal, orgName: string): string {
  switch (r.code) {
    case "forbidden":
      return `Couldn't save a change: your role can't edit that in ${orgName}.`;
    case "in_use":
      return "Couldn't delete it: a service still uses it.";
    case "deleted":
      return "Someone deleted that on another device.";
    default:
      return `Couldn't save a change${r.message ? `: ${r.message}` : "."}`;
  }
}

export class LibrarySync {
  private status: SyncStatus;
  private statusListeners = new Set<(s: SyncStatus) => void>();
  private changeListeners = new Set<() => void>();
  private running: Promise<void> | null = null;
  private again = false;
  private pushTimer: ReturnType<typeof setTimeout> | null = null;
  private pullTimer: ReturnType<typeof setInterval> | null = null;
  private socket: WebSocket | null = null;
  private socketRetry: ReturnType<typeof setTimeout> | null = null;
  private socketBackoff = 2000;
  private stopped = false;
  private readonly wake = () => {
    if (document.visibilityState === "visible") void this.syncNow();
  };

  /** `server` is the account server's origin: the change-notice socket must
   * be on it. */
  constructor(
    readonly store: RecordStore,
    private readonly api: Api,
    readonly orgId: string,
    private readonly orgName: string,
    private readonly server?: string,
  ) {
    this.status = { state: store.downloaded ? "ok" : "loading", waiting: store.waiting, message: null };
  }

  private get org() {
    return `/orgs/${encodeURIComponent(this.orgId)}`;
  }

  // --- Listening ----------------------------------------------------------------

  getStatus(): SyncStatus {
    return this.status;
  }

  onStatus(listener: (s: SyncStatus) => void): () => void {
    this.statusListeners.add(listener);
    return () => this.statusListeners.delete(listener);
  }

  /** Called when records changed underneath (a pull, a refused change). */
  onChange(listener: () => void): () => void {
    this.changeListeners.add(listener);
    return () => this.changeListeners.delete(listener);
  }

  private setStatus(next: Partial<SyncStatus>): void {
    const s = { ...this.status, ...next, waiting: this.store.waiting };
    if (s.state === this.status.state && s.waiting === this.status.waiting && s.message === this.status.message) return;
    this.status = s;
    for (const l of this.statusListeners) l(s);
  }

  private changed(): void {
    for (const l of this.changeListeners) l();
  }

  // --- Running ------------------------------------------------------------------

  /** Start syncing: download (or catch up), then keep up. */
  start(): void {
    this.stopped = false;
    void this.syncNow();
    this.pullTimer = setInterval(() => void this.syncNow(), PULL_EVERY_MS);
    document.addEventListener("visibilitychange", this.wake);
    window.addEventListener("online", this.wake);
    this.openSocket();
  }

  stop(): void {
    this.stopped = true;
    if (this.pullTimer) clearInterval(this.pullTimer);
    if (this.pushTimer) clearTimeout(this.pushTimer);
    if (this.socketRetry) clearTimeout(this.socketRetry);
    document.removeEventListener("visibilitychange", this.wake);
    window.removeEventListener("online", this.wake);
    this.socket?.close();
    this.socket = null;
  }

  /** A change was made here: send it shortly. */
  changedHere(): void {
    this.setStatus({});
    if (this.pushTimer) clearTimeout(this.pushTimer);
    this.pushTimer = setTimeout(() => void this.syncNow(), PUSH_AFTER_MS);
  }

  /** One round now (or straight after the one running). */
  syncNow(): Promise<void> {
    if (this.running) {
      this.again = true;
      return this.running;
    }
    this.running = (async () => {
      try {
        do {
          this.again = false;
          await this.cycle();
        } while (this.again && !this.stopped);
      } finally {
        this.running = null;
      }
    })();
    return this.running;
  }

  private async cycle(): Promise<void> {
    try {
      let message: string | null = null;
      try {
        message = await this.push();
      } catch (err) {
        // Nothing else will work either; say so.
        if (err instanceof SignedOutError || err instanceof OfflineError || err instanceof NotMemberError) throw err;
        // The server couldn't take the changes (they stay waiting); still
        // bring everyone else's down.
        message = `Couldn't save changes: ${err instanceof Error ? err.message : String(err)}`;
      }
      await this.pull();
      // A refusal stays shown until the next round brings another, so it isn't missed.
      this.setStatus({ state: "ok", message: message ?? (this.status.state === "ok" ? this.status.message : null) });
    } catch (err) {
      if (err instanceof SignedOutError) this.setStatus({ state: "signed_out", message: null });
      else if (err instanceof OfflineError) this.setStatus({ state: "offline", message: null });
      else this.setStatus({ state: "error", message: err instanceof Error ? err.message : String(err) });
    }
  }

  /** Send what's waiting. Returns the last refusal, in words. */
  private async push(): Promise<string | null> {
    let message: string | null = null;
    for (let i = 0; i < 20; i++) {
      const batch = this.store.nextPush();
      if (batch.length === 0) break;
      let results: PushResult[];
      try {
        results =
          (await this.api<{ results: PushResult[] }>("POST", `${this.org}/sync/push`, { device: this.store.device, changes: batch })).results ?? [];
      } catch (err) {
        // The role can't push at all (a member, or demoted meanwhile): every
        // change is refused, as it would be one by one.
        if (!(err instanceof ApiError && err.status === 403)) throw err;
        results = batch.map((c) => ({ op: c.op, status: "refused" as const, code: "forbidden", message: err.message }));
      }
      const { refused } = await this.store.afterPush(batch, results);
      if (refused.length) {
        message = refusalText(refused[refused.length - 1]!, this.orgName);
        this.changed();
      }
    }
    return message;
  }

  /** Bring down everything after the last change pulled. */
  private async pull(): Promise<void> {
    let pulled = 0;
    let after = this.store.cursor;
    for (let i = 0; i < 1000; i++) {
      const page = await this.api<{ records: PulledRecord[]; seq: number; more: boolean }>("GET", `${this.org}/sync/pull?after=${after}`);
      if (page.seq < after) {
        await this.store.reset();
        this.changed();
        after = 0;
        continue;
      }
      pulled += await this.store.afterPull(page.records ?? [], page.seq);
      after = page.seq;
      if (!page.more) break;
    }
    if (pulled > 0) this.changed();
  }

  // --- Change notices -------------------------------------------------------------

  private openSocket(): void {
    if (this.stopped || typeof WebSocket === "undefined") return;
    void (async () => {
      let url: string;
      try {
        url = (await this.api<{ url: string }>("POST", `${this.org}/sync/ticket`)).url;
      } catch (err) {
        if (!(err instanceof SignedOutError)) this.retrySocket();
        return;
      }
      if (this.stopped) return;
      if (!socketUrlOk(url, this.server)) {
        this.retrySocket();
        return;
      }
      const ws = new WebSocket(url);
      this.socket = ws;
      let ping: ReturnType<typeof setInterval> | null = null;
      ws.onopen = () => {
        this.socketBackoff = 2000;
        ping = setInterval(() => ws.readyState === WebSocket.OPEN && ws.send("ping"), 30_000);
      };
      ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(String(e.data)) as { type?: string; seq?: number };
          // Any other number than ours: newer, or the library was reset.
          if (msg.type === "changed" && msg.seq !== this.store.cursor) void this.syncNow();
        } catch {
          // "pong", or something newer: nothing to do.
        }
      };
      ws.onclose = (e) => {
        if (ping) clearInterval(ping);
        if (this.socket === ws) this.socket = null;
        // 4001: removed from the organization or the role changed; the next
        // round says which.
        if (e.code === 4001) void this.syncNow();
        this.retrySocket();
      };
    })();
  }

  private retrySocket(): void {
    if (this.stopped) return;
    if (this.socketRetry) clearTimeout(this.socketRetry);
    this.socketRetry = setTimeout(() => {
      this.socketRetry = null;
      // Catch up on anything missed while the socket was down.
      void this.syncNow();
      this.openSocket();
    }, this.socketBackoff);
    this.socketBackoff = Math.min(this.socketBackoff * 2, 60_000);
  }
}

