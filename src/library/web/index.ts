/**
 * The web app's library: an organization's records kept in this browser and
 * synced with the server (`store.ts`, `sync.ts`). It behaves like the
 * desktop's (`src-tauri/src/setlists.rs`, `db.rs`) so a service planned here
 * arrives on the slides computer as it was made.
 */

import { newId } from "@/core/ids";
import type { NewSongInput, ReadingRow, SetlistDetail, SetlistItemRow, SetlistRow, SongChart, SongRow } from "@/core/library";
import {
  changedFields,
  readingFromRecord,
  readingToFields,
  setlistFromRecord,
  setlistToFields,
  songFromRecord,
  songToFields,
  type Fields,
} from "@/core/records";
import { itemTitle, readingSlides, textSlides } from "@/lib/serviceItemContent";
import { isoDate } from "@/lib/setlistName";
import type { LibrarySource, ServiceItemKind } from "@/library/source";
import type { RecordStore } from "@/library/web/store";

export interface WebLibrary extends LibrarySource {
  /** The record store underneath (tests, and the sync loop). */
  readonly store: RecordStore;
}

// "Psalm 9" before "Psalm 24": numbers in titles sort as numbers.
const byTitle = (a: { title: string }, b: { title: string }) =>
  a.title.localeCompare(b.title, undefined, { sensitivity: "base", numeric: true });

export function webLibrary(
  store: RecordStore,
  hooks: { changedHere: () => void; onChange: (listener: () => void) => () => void },
): WebLibrary {
  const here = new Set<() => void>();
  const notify = () => {
    hooks.changedHere();
    for (const l of here) l();
  };

  // The version each song handed out was read at, so a save from an editor
  // opened on it says what it was made on.
  const versions = new WeakMap<SongRow, number>();
  const song = (id: string): SongRow | null => {
    const f = store.get("song", id);
    if (!f) return null;
    const row = songFromRecord(id, f);
    versions.set(row, store.version("song", id));
    return row;
  };

  const setlist = (id: string) => {
    const f = store.get("setlist", id);
    return f ? setlistFromRecord(id, f) : null;
  };

  const saveSetlist = async (s: SetlistRow, items: SetlistItemRow[]) => {
    const renumbered = items.map((i, position) => ({ ...i, position }));
    await store.write("setlist", s.id, setlistToFields(s, renumbered, store.get("setlist", s.id) ?? {}));
    notify();
  };

  /** Change only the given fields of a setlist. */
  const patchSetlist = async (id: string, fields: Fields) => {
    const now = store.get("setlist", id);
    if (!now) throw new Error("That service is gone.");
    await store.write("setlist", id, { ...now, ...fields });
    notify();
  };

  /** Change one setlist's items. */
  const editItems = async (setlistId: string, edit: (items: SetlistItemRow[]) => SetlistItemRow[]) => {
    const s = setlist(setlistId);
    if (!s) throw new Error("That service is gone.");
    await saveSetlist(s.setlist, edit([...s.items]));
  };

  return {
    store,
    capabilities: {
      canPresent: false,
      canImportFiles: false,
      canEditCharts: false,
      hasBibles: false,
      canSearchByMeaning: false,
    },

    // --- Songs ------------------------------------------------------------------

    async listSongs(sort = "alphabetical") {
      const songs = store
        .all("song")
        .map(([id]) => song(id)!)
        .sort(byTitle);
      if (sort === "recently_edited") return songs.sort((a, b) => b.updated_at - a.updated_at);
      if (sort === "popular") {
        // As the desktop counts use: the services a song is in.
        const uses = new Map<string, number>();
        for (const [id, f] of store.all("setlist")) {
          for (const i of new Set(setlistFromRecord(id, f).items.map((i) => i.song_id))) {
            if (i) uses.set(i, (uses.get(i) ?? 0) + 1);
          }
        }
        return songs.sort((a, b) => (uses.get(b.id) ?? 0) - (uses.get(a.id) ?? 0));
      }
      return songs;
    },

    async getSong(id) {
      return song(id);
    },

    async createSong(input: NewSongInput) {
      const id = newId("song");
      const now = Date.now();
      await store.write("song", id, songToFields(songInput(input, now, now)));
      notify();
      return id;
    },

    async updateSong(id, input, opened) {
      const before = store.get("song", id);
      if (!before) throw new Error("That song is gone.");
      const old = songFromRecord(id, before);
      const wanted = songToFields(songInput(input, old.created_at, Date.now()), before);
      if (!opened) {
        await store.write("song", id, wanted);
      } else {
        // Only what this editor changed since it opened; a field changed
        // meanwhile on another device and left alone here stays theirs.
        const was = songToFields(opened, before);
        const changed = changedFields("song", wanted, was);
        delete changed.updated_at;
        if (Object.keys(changed).length === 0) return;
        await store.write("song", id, { ...before, ...changed, updated_at: wanted.updated_at }, versions.get(opened));
      }
      notify();
    },

    async deleteSong(id) {
      // As on the desktop: it comes out of every service that has it.
      for (const [setlistId, f] of store.all("setlist")) {
        if (!Array.isArray(f.refs) || !(f.refs as string[]).includes(`song:${id}`)) continue;
        await editItems(setlistId, (items) => items.filter((i) => i.song_id !== id));
      }
      await store.write("song", id, null);
      notify();
    },

    async getChart(songId): Promise<SongChart | null> {
      const chart = store.get("song", songId)?.chart as Fields | undefined;
      if (!chart || typeof chart.chart_json !== "string") return null;
      return { chart_json: chart.chart_json, original_key: typeof chart.original_key === "string" ? chart.original_key : null };
    },

    async listChartKeys() {
      const keys: Record<string, string | null> = {};
      for (const [id, f] of store.all("song")) {
        const chart = f.chart as Fields | undefined;
        if (chart && typeof chart.chart_json === "string") {
          keys[id] = typeof chart.original_key === "string" ? chart.original_key : null;
        }
      }
      return keys;
    },

    // --- Setlists -----------------------------------------------------------------

    async listSetlists() {
      return store
        .all("setlist")
        .map(([id, f]) => {
          const { setlist: s, items } = setlistFromRecord(id, f);
          // As the desktop lists them: a song item whose song is gone is left out.
          const shown = items.filter((i) => i.kind !== "song" || (i.song_id && store.get("song", i.song_id)));
          return {
            ...s,
            song_titles: shown.map((i) => itemTitle(i, i.song_id ? song(i.song_id) : null)),
            song_count: shown.filter((i) => i.kind === "song").length,
          };
        })
        .sort(newestFirst);
    },

    async getSetlist(id): Promise<SetlistDetail | null> {
      const s = setlist(id);
      if (!s) return null;
      return { ...s.setlist, items: s.items.map((i) => ({ ...i, song: i.song_id ? song(i.song_id) : null })) };
    },

    async createSetlist(name, serviceDate = null) {
      const id = newId("setlist");
      await saveSetlist({ id, name, service_date: serviceDate, created_at: Date.now(), last_used_at: null }, []);
      return id;
    },

    async renameSetlist(id, name) {
      await patchSetlist(id, { name });
    },

    async setServiceDate(id, serviceDate) {
      await patchSetlist(id, { service_date: serviceDate ?? undefined });
    },

    async deleteSetlist(id) {
      await store.write("setlist", id, null);
      notify();
    },

    async copySetlist(sourceId, name, serviceDate = null) {
      const s = setlist(sourceId);
      if (!s) throw new Error("That service is gone.");
      const id = newId("setlist");
      await saveSetlist(
        { id, name, service_date: serviceDate, created_at: Date.now(), last_used_at: null },
        s.items.map((i) => ({ ...i, id: newId("item"), setlist_id: id })),
      );
      return id;
    },

    async addSong(setlistId, songId) {
      if (!song(songId)) throw new Error("That song is gone.");
      const itemId = newId("item");
      await editItems(setlistId, (items) => [
        ...items,
        { id: itemId, setlist_id: setlistId, kind: "song", song_id: songId, position: items.length, slides_override: null },
      ]);
      return itemId;
    },

    async addServiceItem(setlistId, kind: ServiceItemKind, content, { afterItemId, slidesOverride } = {}) {
      const itemId = newId("item");
      const item: SetlistItemRow = {
        id: itemId,
        setlist_id: setlistId,
        kind,
        song_id: null,
        position: 0,
        ...itemContent(kind, content),
        ...(slidesOverride ? { slides_override: slidesOverride } : {}),
      };
      await editItems(setlistId, (items) => {
        const at = afterItemId ? items.findIndex((i) => i.id === afterItemId) : -1;
        if (at < 0) return [...items, item];
        return [...items.slice(0, at + 1), item, ...items.slice(at + 1)];
      });
      return itemId;
    },

    async removeItem(setlistId, itemId) {
      await editItems(setlistId, (items) => items.filter((i) => i.id !== itemId));
    },

    async reorderItems(setlistId, itemIds) {
      await editItems(setlistId, (items) => {
        const byId = new Map(items.map((i) => [i.id, i]));
        const ordered = itemIds.map((id) => byId.get(id)).filter((i): i is SetlistItemRow => !!i);
        // Anything the caller didn't name (added elsewhere meanwhile) keeps its place at the end.
        return [...ordered, ...items.filter((i) => !itemIds.includes(i.id))];
      });
    },

    async setItemKey(setlistId, itemId, key) {
      await editItems(setlistId, (items) => items.map((i) => (i.id === itemId ? { ...i, song_key: key } : i)));
    },

    async setItemSlides(setlistId, itemId, slidesOverride) {
      await editItems(setlistId, (items) => items.map((i) => (i.id === itemId ? { ...i, slides_override: slidesOverride } : i)));
    },

    async setItemContent(setlistId, itemId, content, slidesOverride) {
      await editItems(setlistId, (items) =>
        items.map((i) =>
          i.id === itemId
            ? { ...i, content: JSON.stringify(content), ...(slidesOverride === undefined ? {} : { slides_override: slidesOverride }) }
            : i,
        ),
      );
    },

    // --- Readings -----------------------------------------------------------------

    async listReadings(): Promise<ReadingRow[]> {
      return store
        .all("reading")
        .map(([id, f]) => readingFromRecord(id, f))
        .sort(byTitle);
    },

    async saveReading(title, bodyText) {
      const t = title.trim();
      if (!t) throw new Error("A reading needs a title.");
      const same = store.all("reading").find(([, f]) => String(f.title ?? "").toLowerCase() === t.toLowerCase());
      const now = Date.now();
      const id = same?.[0] ?? newId("reading");
      const created = same ? readingFromRecord(id, same[1]).created_at : now;
      await store.write("reading", id, readingToFields({ title: t, body_text: bodyText, created_at: created, updated_at: now }));
      notify();
      return id;
    },

    async deleteReading(id) {
      await store.write("reading", id, null);
      notify();
    },

    onChange(listener) {
      here.add(listener);
      const off = hooks.onChange(listener);
      return () => {
        here.delete(listener);
        off();
      };
    },
  };
}

function songInput(input: NewSongInput, created: number, updated: number): Omit<SongRow, "id"> {
  return {
    title: input.title.trim(),
    body_text: input.body_text,
    ccli: input.ccli?.trim() || null,
    author: input.author?.trim() || null,
    copyright: input.copyright?.trim() || null,
    default_tempo_wps: input.default_tempo_wps ?? null,
    slide_order: input.slide_order?.trim() || null,
    created_at: created,
    updated_at: updated,
  };
}

/** Newest service first: by its date, else when it was last used or made. */
function newestFirst(a: SetlistRow, b: SetlistRow): number {
  // Both in the planner's own time zone, as service dates are.
  const day = (s: SetlistRow) => s.service_date ?? isoDate(new Date(s.last_used_at ?? s.created_at));
  return (
    day(b).localeCompare(day(a)) ||
    (b.last_used_at ?? b.created_at) - (a.last_used_at ?? a.created_at) ||
    a.name.localeCompare(b.name, undefined, { sensitivity: "base" })
  );
}

/** A new scripture or text item's content and slides. A text item's words
 * become its slides, one per blank-line-separated block, as a saved reading
 * does on the desktop; with no words, its title is the slide, as a text item
 * made there starts. */
export function itemContent(kind: ServiceItemKind, content: object): Pick<SetlistItemRow, "content" | "slides_override"> {
  if (kind === "scripture") return { content: JSON.stringify(content), slides_override: null };
  const { title, body } = content as { title?: string; body?: string };
  const t = title?.trim() || "Text";
  return { content: JSON.stringify({ title: t }), slides_override: body?.trim() ? readingSlides(body) : textSlides([t]) };
}
