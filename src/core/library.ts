/**
 * The library's shapes, shared by every client: the desktop app, the web app
 * and (later) mobile. No React, no Tauri, no platform: see
 * docs/roadmap/WEB_AND_MOBILE.md, "Layers".
 */

export interface SongRow {
  id: string;
  title: string;
  body_text: string;
  ccli: string | null;
  author: string | null;
  copyright: string | null;
  default_tempo_wps: number | null;
  /** Compact section-reference spec defining the default play order, e.g.
   * "V1, V2, C, V3, C, B, C". Null/empty = each section once, document order. */
  slide_order: string | null;
  created_at: number;
  updated_at: number;
}

export interface SetlistRow {
  id: string;
  name: string;
  created_at: number;
  last_used_at: number | null;
  /** Item names in order (song titles, passage references, text titles) —
   * set by `listSetlists` only. */
  song_titles?: string[];
  /** How many of `song_titles` are songs. */
  song_count?: number;
}

export interface SetlistItemRow {
  id: string;
  setlist_id: string;
  /** A song, a Bible passage, or one-off text — see `serviceItems.ts`. */
  kind: "song" | "scripture" | "text";
  song_id: string | null;
  position: number;
  /** JSON array of slides edited for this setlist only, or null to use the
   * library song's body + slide_order. See `slideSequence.parseSlidesOverride`. */
  slides_override: string | null;
  /** A scripture or text item's content (JSON); null for a song. */
  content?: string | null;
  /** This item's own display look (JSON, `displayLook.ts`), or null for the
   * default style. Older builds leave it out. */
  look?: string | null;
  /** The key the song is played in for this service ("Bb", "Em"), or null
   * for its chart's own key. Older builds leave it out. */
  song_key?: string | null;
}

export interface SetlistItemWithSong extends SetlistItemRow {
  /** The library song — or, for a scripture or text item, the song-shaped
   * stand-in `getSetlist` gives it (`serviceItems.withServiceItems`). */
  song: SongRow | null;
}

export interface ReadingRow {
  id: string;
  title: string;
  body_text: string;
  created_at: number;
  updated_at: number;
}

export interface SetlistDetail extends SetlistRow {
  items: SetlistItemWithSong[];
}

export interface NewSongInput {
  title: string;
  body_text: string;
  ccli?: string | null;
  author?: string | null;
  copyright?: string | null;
  default_tempo_wps?: number | null;
  slide_order?: string | null;
}
