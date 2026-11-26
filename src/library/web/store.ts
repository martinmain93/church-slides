/**
 * The web app's copy of an organization's library: every record as the
 * server last sent it (its *shadow*), plus the changes made here that haven't
 * gone up yet. What a page sees is the shadow with the changes on top.
 *
 * It's the browser's version of what the desktop keeps in its library file
 * (`src-tauri/src/library_sync/local.rs`), and follows the same rules
 * (docs/server-protocol/README.md, "Sync"):
 *
 * - A change is kept as the fields it set, never the whole record, so a pull
 *   that brings someone else's edit to another field doesn't get overwritten
 *   when this change goes up.
 * - Each change remembers the version it was made on (`base`), so editing a
 *   song's words on top of an older version keeps the other words as a
 *   revision on the server instead of losing them.
 * - Each send has an `op` id. A retry after a lost reply sends the same one,
 *   so nothing is applied twice.
 */

import { applySet, changedFields, type Fields, type RecordType } from "@/core/records";
import { newId } from "@/core/ids";
import type { Persist } from "@/library/web/persist";

export interface Shadow {
  seq: number;
  deleted: boolean;
  fields: Fields;
}

export interface Pending {
  /** The id this change goes up with. A new edit gets a new one. */
  op: string;
  /** The record's change number when the change was made (0: made here). */
  base: number;
  /** The fields this change sets (`null` removes one). */
  set?: Fields;
  delete?: true;
  /** Order of changes, oldest first. */
  order: number;
}

export interface PushChange {
  op: string;
  type: RecordType;
  id: string;
  base: number;
  set?: Fields;
  delete?: true;
}

export interface PushResult {
  op: string;
  status: "applied" | "refused";
  seq?: number;
  conflict?: boolean;
  code?: string;
  message?: string;
}

export interface PulledRecord {
  type: string;
  id: string;
  seq: number;
  deleted: boolean;
  fields: Fields;
}

export interface Refusal {
  type: RecordType;
  id: string;
  code: string;
  message: string;
}

const SYNCED: readonly RecordType[] = ["song", "reading", "look", "setlist", "service", "performance"];
const key = (type: string, id: string) => `${type}/${id}`;
const split = (k: string): [RecordType, string] => {
  const i = k.indexOf("/");
  return [k.slice(0, i) as RecordType, k.slice(i + 1)];
};

export class RecordStore {
  private shadows = new Map<string, Shadow>();
  private pending = new Map<string, Pending>();
  /** Records whose change is on its way up (sent, no answer yet). A record
   * made here and deleted while its create is in flight needs a delete sent
   * after it, not forgetting. */
  private inFlight = new Set<string>();
  private nextOrder = 1;
  /** The last change number pulled. */
  cursor = 0;
  /** A stable id for this browser's copy of the library. */
  device = "";

  private constructor(private readonly persist: Persist) {}

  /** Open the library kept in `persist`. `owner` is the signed-in person's
   * id: a library left by someone else in this browser (signed out by the
   * server rather than by them) is wiped, so their unsent changes never go
   * up under this person's name. */
  static async open(persist: Persist, owner?: string): Promise<RecordStore> {
    const store = new RecordStore(persist);
    let all = await persist.load();
    const kept = all.find(([k]) => k === "m:owner")?.[1];
    if (owner && kept !== undefined && kept !== owner) {
      await persist.put(all.map(([k]) => [k, null]));
      all = [];
    }
    if (owner && kept !== owner) await persist.put([["m:owner", owner]]);
    for (const [k, v] of all) {
      if (k.startsWith("s:")) store.shadows.set(k.slice(2), v as Shadow);
      else if (k.startsWith("p:")) store.pending.set(k.slice(2), v as Pending);
      else if (k === "m:cursor") store.cursor = Number(v) || 0;
      else if (k === "m:device") store.device = String(v);
    }
    for (const p of store.pending.values()) store.nextOrder = Math.max(store.nextOrder, p.order + 1);
    if (!store.device) {
      store.device = `web-${newId("op").slice(3)}`;
      await persist.put([["m:device", store.device]]);
    }
    return store;
  }

  // --- Reading ------------------------------------------------------------------

  /** A record as this page sees it, or null when it's absent or deleted. */
  get(type: RecordType, id: string): Fields | null {
    const k = key(type, id);
    const p = this.pending.get(k);
    if (p?.delete) return null;
    const s = this.shadows.get(k);
    if (!p) return s && !s.deleted ? s.fields : null;
    if (!s && !p.set) return null;
    return applySet(s && !s.deleted ? s.fields : {}, p.set ?? {});
  }

  /** Every record of a type as this page sees it. */
  all(type: RecordType): [string, Fields][] {
    const ids = new Set<string>();
    const prefix = `${type}/`;
    for (const k of this.shadows.keys()) if (k.startsWith(prefix)) ids.add(k.slice(prefix.length));
    for (const k of this.pending.keys()) if (k.startsWith(prefix)) ids.add(k.slice(prefix.length));
    const out: [string, Fields][] = [];
    for (const id of ids) {
      const f = this.get(type, id);
      if (f) out.push([id, f]);
    }
    return out;
  }

  /** How many changes made here are waiting to go up. */
  get waiting(): number {
    return this.pending.size;
  }

  /** The server's change number for a record as this page last pulled it
   * (0: never sent). An editor keeps it to say what its edit was made on. */
  version(type: RecordType, id: string): number {
    return this.shadows.get(key(type, id))?.seq ?? 0;
  }

  /** Whether the library has ever been downloaded here. */
  get downloaded(): boolean {
    return this.cursor > 0 || this.shadows.size > 0;
  }

  // --- Changing -------------------------------------------------------------------

  /** Save a record as it should now be (`null` deletes it). Only the known
   * fields that differ from what this page sees go into the change.
   * `madeOn` is the version the edit was made on, when older than the one
   * here (an editor opened before a pull): the server then keeps the words
   * it replaces as a revision. */
  async write(type: RecordType, id: string, fields: Fields | null, madeOn?: number): Promise<void> {
    const k = key(type, id);
    const before = this.get(type, id);
    const shadow = this.shadows.get(k);
    const existing = this.pending.get(k);
    let base = existing?.base ?? shadow?.seq ?? 0;
    if (madeOn !== undefined && shadow && madeOn < base) base = madeOn;
    let next: Pending;
    if (fields === null) {
      if (!before) return;
      if ((!shadow || shadow.deleted) && !this.inFlight.has(k)) {
        // Made here and never sent: forget it.
        this.pending.delete(k);
        await this.persist.put([[`p:${k}`, null]]);
        return;
      }
      next = { op: newId("op"), base, delete: true, order: this.nextOrder++ };
    } else {
      const diff = changedFields(type, fields, before ?? {});
      if (Object.keys(diff).length === 0 && before) return;
      const set = { ...(existing?.delete ? {} : existing?.set ?? {}), ...diff };
      next = { op: newId("op"), base, set, order: existing?.order ?? this.nextOrder++ };
    }
    this.pending.set(k, next);
    await this.persist.put([[`p:${k}`, next]]);
  }

  // --- Pushing --------------------------------------------------------------------

  /** The oldest changes waiting: up to `limit` of them, and about
   * `maxBytes` (always at least one). They count as on their way up until
   * the answer comes, or the next push. */
  nextPush(limit = 200, maxBytes = 512 * 1024): PushChange[] {
    this.inFlight.clear();
    const out: PushChange[] = [];
    let bytes = 0;
    for (const [k, p] of [...this.pending.entries()].sort((a, b) => a[1].order - b[1].order)) {
      if (out.length >= limit) break;
      const [type, id] = split(k);
      const change: PushChange = p.delete
        ? { op: p.op, type, id, base: p.base, delete: true }
        : { op: p.op, type, id, base: p.base, set: p.set ?? {} };
      const size = JSON.stringify(change).length;
      if (out.length > 0 && bytes + size > maxBytes) break;
      bytes += size;
      out.push(change);
      this.inFlight.add(k);
    }
    return out;
  }

  /** Write the server's answer to a push. Returns what it refused. */
  async afterPush(sent: PushChange[], results: PushResult[]): Promise<{ refused: Refusal[]; conflicts: number }> {
    const byOp = new Map(results.map((r) => [r.op, r]));
    const writes: [string, unknown][] = [];
    const refused: Refusal[] = [];
    let conflicts = 0;
    for (const c of sent) {
      const k = key(c.type, c.id);
      this.inFlight.delete(k);
      const r = byOp.get(c.op);
      if (!r) continue;
      const now = this.pending.get(k);
      const unchanged = now?.op === c.op;
      if (r.status === "applied") {
        if (r.conflict) conflicts++;
        const old = this.shadows.get(k);
        const shadow: Shadow = c.delete
          ? { seq: r.seq ?? old?.seq ?? 0, deleted: true, fields: old?.fields ?? {} }
          : { seq: r.seq ?? old?.seq ?? 0, deleted: false, fields: applySet(old && !old.deleted ? old.fields : {}, c.set ?? {}) };
        this.shadows.set(k, shadow);
        writes.push([`s:${k}`, shadow]);
        if (unchanged) {
          this.pending.delete(k);
          writes.push([`p:${k}`, null]);
        } else if (now) {
          // Edited again while this was in flight: what's left goes up on
          // top of the version the server now holds.
          const left = { ...now, base: shadow.seq };
          this.pending.set(k, left);
          writes.push([`p:${k}`, left]);
        }
      } else {
        refused.push({ type: c.type, id: c.id, code: r.code ?? "invalid", message: r.message ?? "" });
        // Final: drop it and keep the server's version (the next pull brings it).
        if (unchanged) {
          this.pending.delete(k);
          writes.push([`p:${k}`, null]);
        }
      }
    }
    await this.persist.put(writes);
    return { refused, conflicts };
  }

  // --- Pulling --------------------------------------------------------------------

  /** Write a page of the server's records. Returns how many changed here. */
  async afterPull(records: PulledRecord[], seq: number): Promise<number> {
    const writes: [string, unknown][] = [];
    let n = 0;
    for (const r of records) {
      if (!SYNCED.includes(r.type as RecordType)) continue;
      const k = key(r.type, r.id);
      const shadow: Shadow = { seq: r.seq, deleted: r.deleted, fields: r.fields ?? {} };
      this.shadows.set(k, shadow);
      writes.push([`s:${k}`, shadow]);
      n++;
    }
    writes.push(["m:cursor", seq]);
    await this.persist.put(writes);
    // Only once it's kept: a failed write (storage full) is pulled again.
    this.cursor = seq;
    return n;
  }

  /** The server's library was reset: download it again (changes here stay). */
  async reset(): Promise<void> {
    const writes: [string, unknown][] = [];
    // Changes made on top of versions that are gone go up as whole records:
    // the server may not have the rest of the fields any more.
    for (const [k, p] of this.pending) {
      const s = this.shadows.get(k);
      const left: Pending = p.delete
        ? { ...p, base: 0 }
        : { ...p, base: 0, set: applySet(s && !s.deleted ? s.fields : {}, p.set ?? {}) };
      this.pending.set(k, left);
      writes.push([`p:${k}`, left]);
    }
    for (const k of this.shadows.keys()) writes.push([`s:${k}`, null]);
    this.shadows.clear();
    this.inFlight.clear();
    this.cursor = 0;
    writes.push(["m:cursor", 0]);
    await this.persist.put(writes);
  }

  /** Stop using the database (closing, or signing out). */
  close(): void {
    this.persist.close?.();
  }
}
