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

import type { NewSongInput, ReadingRow, SetlistDetail, SetlistRow, SongRow } from "@/core/library";

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

  listSongs(): Promise<SongRow[]>;
  getSong(id: string): Promise<SongRow | null>;
  createSong(input: NewSongInput): Promise<string>;
  updateSong(id: string, input: NewSongInput): Promise<void>;
  deleteSong(id: string): Promise<void>;

  listSetlists(): Promise<SetlistRow[]>;
  getSetlist(id: string): Promise<SetlistDetail | null>;
  createSetlist(name: string): Promise<string>;
  renameSetlist(id: string, name: string): Promise<void>;
  deleteSetlist(id: string): Promise<void>;
  copySetlist(sourceId: string, name: string): Promise<string>;

  addSong(setlistId: string, songId: string): Promise<string>;
  addServiceItem(setlistId: string, kind: ServiceItemKind, content: object, afterItemId?: string | null): Promise<string>;
  removeItem(setlistId: string, itemId: string): Promise<void>;
  reorderItems(setlistId: string, itemIds: string[]): Promise<void>;
  setItemKey(setlistId: string, itemId: string, key: string | null): Promise<void>;
  setItemSlides(setlistId: string, itemId: string, slidesOverride: string | null): Promise<void>;

  listReadings(): Promise<ReadingRow[]>;
  saveReading(title: string, bodyText: string): Promise<string>;
  deleteReading(id: string): Promise<void>;

  /** Called whenever the library changes underneath (sync, another window,
   * an AI assistant). Returns a function that stops it. */
  onChange(listener: () => void): () => void;
}
