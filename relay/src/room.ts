/**
 * One service's room: the slides computer ("host") and the band's devices.
 *
 * Built to cost nothing while nothing happens. It uses WebSocket Hibernation
 * (the runtime holds the sockets while this object sleeps), keeps no timers,
 * and answers devices' keep-alive "ping" with an automatic "pong" that never
 * wakes it. So it's awake only for a moment per slide change.
 *
 * Because it can sleep — and lose its memory — at any time, everything it
 * needs lives in its own storage or on the sockets themselves:
 *  - the latest `setlist` and `position` snapshots and the leader PIN (storage);
 *  - each socket's role, id and whether it unlocked the leader controls
 *    (socket attachment).
 */

import type { Env } from "./worker";

interface Attachment {
  role: "host" | "device";
  id: string;
  leader: boolean;
  /** The device's address (CF-Connecting-IP), for counting wrong PINs. */
  ip?: string;
}

/** Wrong leader PINs allowed from one address before it has to wait. */
const PIN_TRIES = 5;
/** How long an address waits after `PIN_TRIES` wrong PINs. */
const PIN_LOCKOUT_MS = 5 * 60 * 1000;

/** Storage values are limited in size; long snapshots are kept in pieces. */
const CHUNK = 100_000;
/** After the last socket leaves, keep the snapshots this long, then forget the room. */
const FORGET_AFTER_MS = 6 * 60 * 60 * 1000;

export class Room implements DurableObject {
  constructor(
    private ctx: DurableObjectState,
    private env: Env,
  ) {
    // Answered by the runtime itself: keeps phones' connections alive without
    // waking (or billing) this object.
    ctx.setWebSocketAutoResponse(new WebSocketRequestResponsePair("ping", "pong"));
  }

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    const isHost = url.pathname.endsWith("/host");
    const pair = new WebSocketPair();
    const [client, server] = [pair[0], pair[1]];
    const att: Attachment = {
      role: isHost ? "host" : "device",
      id: crypto.randomUUID(),
      leader: false,
      ip: request.headers.get("CF-Connecting-IP") ?? undefined,
    };

    if (isHost) {
      // One slides computer per room: a reconnect replaces the old socket.
      for (const old of this.ctx.getWebSockets("host")) old.close(4000, "Replaced by a newer connection");
      const pin = url.searchParams.get("pin");
      if (pin) await this.ctx.storage.put("pin", pin);
    }
    this.ctx.acceptWebSocket(server, [att.role]);
    server.serializeAttachment(att);
    await this.ctx.storage.deleteAlarm();

    if (isHost) {
      server.send(JSON.stringify({ type: "clients", count: this.ctx.getWebSockets("device").length }));
    } else {
      // Catch the device up before anything else.
      const snapshots = await this.snapshots();
      if (snapshots.length === 0) server.send(JSON.stringify({ type: "idle" }));
      for (const s of snapshots) server.send(s);
      this.tellHostCount();
    }
    return new Response(null, { status: 101, webSocket: client });
  }

  async webSocketMessage(ws: WebSocket, raw: string | ArrayBuffer): Promise<void> {
    if (typeof raw !== "string") return;
    let msg: Record<string, unknown>;
    try {
      msg = JSON.parse(raw);
    } catch {
      return;
    }
    const att = ws.deserializeAttachment() as Attachment;
    if (att.role === "host") await this.fromHost(msg);
    else await this.fromDevice(ws, att, msg);
  }

  private async fromHost(msg: Record<string, unknown>): Promise<void> {
    switch (msg.type) {
      case "setlist":
      case "position": {
        const out = JSON.stringify({ type: msg.type, data: msg.data });
        await this.putSnapshot(msg.type, out);
        this.broadcast(out);
        return;
      }
      case "idle": {
        await this.clearSnapshots();
        this.broadcast(JSON.stringify({ type: "idle" }));
        return;
      }
      case "library": {
        // A reply to one device's library request, possibly in parts.
        const to = this.ctx.getWebSockets("device").find((d) => (d.deserializeAttachment() as Attachment).id === msg.to);
        to?.send(JSON.stringify({ type: "library", data: msg.data, part: msg.part, parts: msg.parts }));
        return;
      }
      case "close": {
        // The service is over: forget everything and let devices know.
        await this.ctx.storage.deleteAll();
        this.broadcast(JSON.stringify({ type: "idle" }));
        return;
      }
    }
  }

  private async fromDevice(ws: WebSocket, att: Attachment, msg: Record<string, unknown>): Promise<void> {
    const host = this.ctx.getWebSockets("host")[0];
    switch (msg.type) {
      case "hello": {
        if (msg.role !== "leader") {
          ws.send(JSON.stringify({ type: "welcome", role: "band" }));
          return;
        }
        // Four digits are 10,000 tries: each address gets PIN_TRIES, then
        // waits PIN_LOCKOUT_MS, so a guess at the whole range takes days.
        const triesKey = `pin_wrong:${att.ip ?? "unknown"}`;
        const tries = (await this.ctx.storage.get<{ n: number; until?: number }>(triesKey)) ?? { n: 0 };
        if (tries.until !== undefined && Date.now() < tries.until) {
          ws.send(JSON.stringify({ type: "welcome", role: "band", pinRejected: true, tooManyTries: true }));
          return;
        }
        const pin = await this.ctx.storage.get<string>("pin");
        const ok = typeof msg.pin === "string" && pin !== undefined && msg.pin === pin;
        if (ok) {
          await this.ctx.storage.delete(triesKey);
        } else {
          const n = (tries.until !== undefined ? 0 : tries.n) + 1;
          await this.ctx.storage.put(triesKey, n >= PIN_TRIES ? { n, until: Date.now() + PIN_LOCKOUT_MS } : { n });
        }
        ws.serializeAttachment({ ...att, leader: ok });
        ws.send(JSON.stringify({ type: "welcome", role: ok ? "leader" : "band", pinRejected: !ok }));
        return;
      }
      case "library_request":
      case "command": {
        if (!att.leader) {
          ws.send(JSON.stringify({ type: "error", message: "Leader controls need the PIN" }));
          return;
        }
        if (!host) {
          ws.send(JSON.stringify({ type: "error", message: "The slides computer isn't connected right now" }));
          return;
        }
        host.send(JSON.stringify(msg.type === "command" ? { type: "command", command: msg.command } : { type: "library_request", from: att.id }));
        return;
      }
    }
  }

  async webSocketClose(ws: WebSocket): Promise<void> {
    await this.left(ws);
  }

  async webSocketError(ws: WebSocket): Promise<void> {
    await this.left(ws);
  }

  private async left(ws: WebSocket): Promise<void> {
    const att = ws.deserializeAttachment() as Attachment | null;
    if (att?.role === "device") this.tellHostCount(ws);
    const remaining = this.ctx.getWebSockets().filter((s) => s !== ws && s.readyState === WebSocket.OPEN);
    if (remaining.length === 0) await this.ctx.storage.setAlarm(Date.now() + FORGET_AFTER_MS);
  }

  /** Nobody came back: forget the room entirely. */
  async alarm(): Promise<void> {
    if (this.ctx.getWebSockets().length === 0) await this.ctx.storage.deleteAll();
  }

  private tellHostCount(leaving?: WebSocket): void {
    const count = this.ctx.getWebSockets("device").filter((d) => d !== leaving && d.readyState === WebSocket.OPEN).length;
    for (const h of this.ctx.getWebSockets("host")) h.send(JSON.stringify({ type: "clients", count }));
  }

  private broadcast(text: string): void {
    for (const d of this.ctx.getWebSockets("device")) {
      try {
        d.send(text);
      } catch {
        // A socket closing under us; its close handler tidies up.
      }
    }
  }

  private async putSnapshot(kind: string, text: string): Promise<void> {
    const parts = Math.max(1, Math.ceil(text.length / CHUNK));
    const entries: Record<string, string | number> = { [`${kind}:parts`]: parts };
    for (let i = 0; i < parts; i++) entries[`${kind}:${i}`] = text.slice(i * CHUNK, (i + 1) * CHUNK);
    await this.ctx.storage.put(entries);
  }

  private async getSnapshot(kind: string): Promise<string | null> {
    const parts = await this.ctx.storage.get<number>(`${kind}:parts`);
    if (!parts) return null;
    const got = await this.ctx.storage.get<string>(Array.from({ length: parts }, (_, i) => `${kind}:${i}`));
    let text = "";
    for (let i = 0; i < parts; i++) text += got.get(`${kind}:${i}`) ?? "";
    return text;
  }

  private async snapshots(): Promise<string[]> {
    const out: string[] = [];
    for (const kind of ["setlist", "position"]) {
      const s = await this.getSnapshot(kind);
      if (s) out.push(s);
    }
    return out;
  }

  private async clearSnapshots(): Promise<void> {
    const keys = [...(await this.ctx.storage.list({ prefix: "setlist:" })).keys(), ...(await this.ctx.storage.list({ prefix: "position:" })).keys()];
    if (keys.length) await this.ctx.storage.delete(keys);
  }
}
