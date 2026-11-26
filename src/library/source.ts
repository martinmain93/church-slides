/**
 * Where a client's library lives: one interface, one implementation per
 * platform (docs/roadmap/WEB_AND_MOBILE.md, "Layers").
 *
 * - desktop (and mobile, later): the app's own database, through Tauri;
 *   `desktop.ts`. Sync runs underneath in Rust.
 * - web: the organization's records from the server, kept in the browser.
 *
 * Shared views take their data from here, never from `invoke()`, and ask
 * `capabilities` what this platform can do rather than which platform it is.
 */

import type { DisplayLook } from "@/lib/look";
import type { NewSongInput, ReadingRow, SetlistDetail, SetlistRow, SongChart, SongRow, SongSort } from "@/core/library";

export interface Capabilities {
  /** Run a service: projector, AI following, band screens. */
  canPresent: boolean;
  /** Import song files and other presentation apps from disk. */
  canImportFiles: boolean;
  /** Edit chords and lead sheets. */
  canEditCharts: boolean;
  /** Fill in Bible text from Bibles on this device. */
  hasBibles: boolean;
  /** Search the library by meaning (needs the local search index). */
  canSearchByMeaning: boolean;
}

export type ServiceItemKind = "scripture" | "text";

export interface LibrarySource {
  readonly capabilities: Capabilities;

  /** Every song, by title unless another order is asked for. */
  listSongs(sort?: SongSort): Promise<SongRow[]>;
  getSong(id: string): Promise<SongRow | null>;
  createSong(input: NewSongInput): Promise<string>;
  /** Save a song. `opened` is the song as the editor opened it: then only
   * what was changed since goes up, so an edit made meanwhile on another
   * device to a field left alone here isn't overwritten. */
  updateSong(id: string, input: NewSongInput, opened?: SongRow): Promise<void>;
  deleteSong(id: string): Promise<void>;
  /** The song's chord chart, or null when it has none. */
  getChart(songId: string): Promise<SongChart | null>;
  /** Song id → its chart's written key, for every song with chords. */
  listChartKeys(): Promise<Record<string, string | null>>;

  listSetlists(): Promise<SetlistRow[]>;
  getSetlist(id: string): Promise<SetlistDetail | null>;
  /** `serviceDate` is the day it's for, `YYYY-MM-DD`. */
  createSetlist(name: string, serviceDate?: string | null): Promise<string>;
  renameSetlist(id: string, name: string): Promise<void>;
  setServiceDate(id: string, serviceDate: string | null): Promise<void>;
  deleteSetlist(id: string): Promise<void>;
  copySetlist(sourceId: string, name: string, serviceDate?: string | null): Promise<string>;

  addSong(setlistId: string, songId: string): Promise<string>;
  /** Add a passage or text slide, after `afterItemId` (else at the end),
   * with its own slides if they were already made. */
  addServiceItem(
    setlistId: string,
    kind: ServiceItemKind,
    content: object,
    opts?: { afterItemId?: string | null; slidesOverride?: string | null },
  ): Promise<string>;
  removeItem(setlistId: string, itemId: string): Promise<void>;
  reorderItems(setlistId: string, itemIds: string[]): Promise<void>;
  setItemKey(setlistId: string, itemId: string, key: string | null): Promise<void>;
  setItemSlides(setlistId: string, itemId: string, slidesOverride: string | null): Promise<void>;
  /** Change a scripture or text item: its content and, for text, its slides. */
  setItemContent(setlistId: string, itemId: string, content: object, slidesOverride?: string | null): Promise<void>;

  /** How the library's slides look on the screen (its display look), so a
   * slide card is drawn as the room will see it. */
  getLook(): Promise<DisplayLook>;

  listReadings(): Promise<ReadingRow[]>;
  saveReading(title: string, bodyText: string): Promise<string>;
  deleteReading(id: string): Promise<void>;

  /** Called whenever the library changes underneath (sync, another window,
   * an AI assistant). Returns a function that stops it. */
  onChange(listener: () => void): () => void;
}
