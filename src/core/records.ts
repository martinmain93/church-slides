/**
 * Sync records and the library's shapes, both ways
 * (docs/server-protocol/records.md).
 *
 * The desktop app converts in Rust (`src-tauri/src/library_sync/local.rs`);
 * the web app, which has no local database, converts here. Both are tested
 * against the same samples (`docs/server-protocol/records/`), so a setlist
 * planned on a phone arrives on the slides computer as it was made.
 */

import type { ReadingRow, SetlistItemRow, SetlistRow, SongRow } from "@/core/library";

export type RecordType = "song" | "reading" | "look" | "setlist" | "service" | "performance";
export type Fields = Record<string, unknown>;

export interface SyncRecord {
  type: RecordType;
  id: string;
  fields: Fields;
}

/** The fields this client reads and writes. Others (from a newer client)
 * are kept and never sent back as removed. Matches `known` in local.rs. */
export const KNOWN_FIELDS: Record<RecordType, readonly string[]> = {
  song: [
    "title", "body_text", "ccli", "author", "copyright", "default_tempo_wps", "slide_order", "created_at", "updated_at",
    "chart", "scriptures", "references",
  ],
  setlist: ["name", "service_date", "created_at", "last_used_at", "items", "refs"],
  reading: ["title", "body_text", "created_at", "updated_at"],
  service: ["setlist_id", "started_at", "songs"],
  look: ["display", "scripture", "updated_at"],
  performance: ["song_id", "started_at_ms", "provenance", "weight", "data_json", "updated_at_ms"],
};

const ITEM_FIELDS = ["id", "kind", "song_id", "position", "slides_override", "content", "look", "song_key"] as const;

/** Absent means empty: drop null and undefined values and empty lists. */
function compact(o: Record<string, unknown>): Fields {
  const out: Fields = {};
  for (const [k, v] of Object.entries(o)) {
    if (v === null || v === undefined) continue;
    if (Array.isArray(v) && v.length === 0) continue;
    out[k] = v;
  }
  return out;
}

const str = (v: unknown): string | null => (typeof v === "string" ? v : null);
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

// --- Songs ----------------------------------------------------------------------

export function songFromRecord(id: string, f: Fields): SongRow {
  return {
    id,
    title: str(f.title) ?? "",
    body_text: str(f.body_text) ?? "",
    ccli: str(f.ccli),
    author: str(f.author),
    copyright: str(f.copyright),
    default_tempo_wps: num(f.default_tempo_wps),
    slide_order: str(f.slide_order),
    created_at: num(f.created_at) ?? 0,
    updated_at: num(f.updated_at) ?? 0,
  };
}

/** A song's fields. `kept` is the record as it was: the parts this shape
 * doesn't carry (chords, scripture links, lead-sheet data) come from it
 * unchanged. */
export function songToFields(song: Omit<SongRow, "id">, kept: Fields = {}): Fields {
  return compact({
    title: song.title,
    body_text: song.body_text,
    ccli: song.ccli || null,
    author: song.author || null,
    copyright: song.copyright || null,
    default_tempo_wps: song.default_tempo_wps,
    slide_order: song.slide_order || null,
    created_at: song.created_at,
    updated_at: song.updated_at,
    chart: kept.chart,
    scriptures: kept.scriptures,
    references: kept.references,
  });
}

// --- Setlists -------------------------------------------------------------------

export function setlistFromRecord(id: string, f: Fields): { setlist: SetlistRow; items: SetlistItemRow[] } {
  const raw = Array.isArray(f.items) ? (f.items as Record<string, unknown>[]) : [];
  const items: SetlistItemRow[] = raw
    .filter((i) => i && typeof i.id === "string")
    .map((i) => ({
      id: i.id as string,
      setlist_id: id,
      kind: i.kind === "scripture" || i.kind === "text" ? (i.kind as "scripture" | "text") : ("song" as const),
      song_id: str(i.song_id),
      position: num(i.position) ?? 0,
      slides_override: str(i.slides_override),
      content: str(i.content),
      look: str(i.look),
      song_key: str(i.song_key),
    }))
    .sort((a, b) => a.position - b.position || a.id.localeCompare(b.id));
  return {
    setlist: {
      id,
      name: str(f.name) ?? "",
      service_date: serviceDate(f.service_date),
      created_at: num(f.created_at) ?? 0,
      last_used_at: num(f.last_used_at),
    },
    items,
  };
}

/** A service's date as stored: `YYYY-MM-DD`, or null when it has none or
 * it's malformed. */
export function serviceDate(v: unknown): string | null {
  return typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v) ? v : null;
}

/** A setlist's fields. `kept` is the record as it was: an item keeps any
 * fields this client doesn't know (from a newer app). */
export function setlistToFields(setlist: Omit<SetlistRow, "id">, items: readonly SetlistItemRow[], kept: Fields = {}): Fields {
  const ordered = [...items].sort((a, b) => a.position - b.position || a.id.localeCompare(b.id));
  const songs = [...new Set(ordered.map((i) => i.song_id).filter((s): s is string => !!s))].sort();
  const keptItems = new Map(
    (Array.isArray(kept.items) ? (kept.items as Record<string, unknown>[]) : [])
      .filter((i) => i && typeof i.id === "string")
      .map((i) => [i.id as string, i]),
  );
  const unknown = (id: string): Fields =>
    Object.fromEntries(Object.entries(keptItems.get(id) ?? {}).filter(([k]) => !(ITEM_FIELDS as readonly string[]).includes(k)));
  return compact({
    name: setlist.name,
    service_date: setlist.service_date ?? null,
    created_at: setlist.created_at,
    last_used_at: setlist.last_used_at,
    items: ordered.map((i) => compact({ ...unknown(i.id), ...Object.fromEntries(ITEM_FIELDS.map((k) => [k, i[k]])) })),
    refs: songs.map((s) => `song:${s}`).sort(),
  });
}

// --- Readings -------------------------------------------------------------------

export function readingFromRecord(id: string, f: Fields): ReadingRow {
  return {
    id,
    title: str(f.title) ?? "",
    body_text: str(f.body_text) ?? "",
    created_at: num(f.created_at) ?? 0,
    updated_at: num(f.updated_at) ?? 0,
  };
}

export function readingToFields(r: Omit<ReadingRow, "id">): Fields {
  return compact({ title: r.title, body_text: r.body_text, created_at: r.created_at, updated_at: r.updated_at });
}

// --- Changes ----------------------------------------------------------------------

/** What to push: the known fields of `now` that differ from `before`, with
 * `null` for one that's gone. Same rule as `diff` in local.rs. */
export function changedFields(type: RecordType, now: Fields, before: Fields): Fields {
  const out: Fields = {};
  for (const k of KNOWN_FIELDS[type]) {
    const a = now[k];
    const b = before[k];
    if (!sameValue(a, b)) out[k] = a === undefined ? null : a;
  }
  return out;
}

/** JSON with object keys sorted, so two clients' copies of the same value
 * compare equal whatever order their keys came in. */
export function canonical(v: unknown): string {
  return JSON.stringify(v, (_k, x: unknown) =>
    x && typeof x === "object" && !Array.isArray(x)
      ? Object.fromEntries(Object.entries(x as Record<string, unknown>).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)))
      : x,
  );
}

export const sameValue = (a: unknown, b: unknown): boolean => canonical(a) === canonical(b);

/** A record after a push's `set` (null removes a field). */
export function applySet(base: Fields, set: Fields): Fields {
  const out = { ...base };
  for (const [k, v] of Object.entries(set)) {
    if (v === null) delete out[k];
    else out[k] = v;
  }
  return out;
}

// --- Checking --------------------------------------------------------------------

/** What's wrong with a record's fields, as far as this client can tell
 * (empty when nothing is). For tests and for refusing to push nonsense. */
export function recordProblems(r: SyncRecord): string[] {
  const p: string[] = [];
  const f = r.fields;
  const need = (k: string, kind: "string" | "number") => {
    if (typeof f[k] !== kind) p.push(`${r.type} ${r.id}: ${k} must be a ${kind}`);
  };
  for (const [k, v] of Object.entries(f)) if (v === null) p.push(`${r.type} ${r.id}: ${k} is null (leave it out)`);
  switch (r.type) {
    case "song":
      need("title", "string");
      need("body_text", "string");
      need("created_at", "number");
      need("updated_at", "number");
      break;
    case "setlist": {
      need("name", "string");
      need("created_at", "number");
      const items = (f.items ?? []) as Record<string, unknown>[];
      if (!Array.isArray(items)) p.push(`setlist ${r.id}: items must be a list`);
      else {
        for (const i of items) {
          if (!i || typeof i !== "object") {
            p.push(`setlist ${r.id}: an item isn't an object`);
            continue;
          }
          if (typeof i.id !== "string") p.push(`setlist ${r.id}: an item has no id`);
          if (!["song", "scripture", "text"].includes(String(i.kind))) p.push(`setlist ${r.id}: item ${String(i.id)} has kind ${String(i.kind)}`);
          if (i.kind === "song" && typeof i.song_id !== "string") p.push(`setlist ${r.id}: song item ${String(i.id)} has no song_id`);
        }
        const expected = setlistToFields(setlistFromRecord(r.id, f).setlist, setlistFromRecord(r.id, f).items).refs ?? [];
        if (JSON.stringify(f.refs ?? []) !== JSON.stringify(expected)) p.push(`setlist ${r.id}: refs must list its songs`);
      }
      break;
    }
    case "reading":
      need("title", "string");
      need("body_text", "string");
      break;
    case "service":
      need("setlist_id", "string");
      need("started_at", "number");
      break;
    case "look":
      if (r.id !== "default") p.push(`look ${r.id}: the id is "default"`);
      break;
    case "performance":
      need("song_id", "string");
      need("data_json", "string");
      break;
  }
  return p;
}
