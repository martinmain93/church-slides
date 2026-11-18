/**
 * What travels between the operator and the band screens.
 *
 * The operator publishes two snapshots through the desktop app's band server
 * (`src-tauri/src/band.rs`), which keeps the latest of each and hands both to
 * any device the moment it connects:
 *
 *  - `setlist`: every song, its slides as the projector will show them, its
 *    chart and the key the band is playing it in. Changes rarely.
 *  - `position`: which slide is up. Changes every few seconds.
 *
 * Commands go the other way, from the leader's screen to the operator.
 */

import type { LibrarySong } from "@/lib/band/librarySearch";
import type { ChordChart } from "@/lib/chords/chart";
import type { ParsedSection } from "@/lib/parseSong";
import type { BlankMode } from "@/lib/presentation/state";

export interface BandSong {
  itemId: string;
  /** Library song id — lets the leader's library search mark songs already in the set. */
  songId: string;
  title: string;
  author: string | null;
  /** Slides exactly as the projector plays them, leading blank included. */
  slides: { section: ParsedSection; lyrics: string }[];
  chart: ChordChart | null;
  /** Semitones the band is moving this song from its written key. */
  transpose: number;
}

export interface BandSetlist {
  setlistId: string;
  name: string;
  songs: BandSong[];
  /** The operator's view can add library songs to the set (single-screen mode can't). */
  canAddSongs: boolean;
}

export interface BandPosition {
  itemIdx: number;
  slideIdx: number;
  /** Anchors the position to a song even if the setlist was just reordered. */
  itemId: string | null;
  blankMode: BlankMode;
}

export type BandCommand =
  /** Move the projector to the blank slide that opens this song. */
  | { kind: "go_to_song"; itemId: string }
  /** Move the projector to one slide of a song — the leader tapped it. */
  | { kind: "go_to_slide"; itemId: string; slideIdx: number }
  /** Blank the projector (black), or bring the lyrics back. */
  | { kind: "set_blank"; on: boolean }
  /** Play this song in a different key (band screens only — lyrics don't change). */
  | { kind: "set_transpose"; itemId: string; semitones: number }
  /** Add a library song to the set right after the current one; "now" also goes to it.
   *  `key`: play it in this key in this service ("fits next" moved it). */
  | { kind: "add_song"; songId: string; when: "next" | "now"; key?: string };

/** Messages a device receives. */
export type ServerMessage =
  | { type: "setlist"; data: BandSetlist }
  | { type: "position"; data: BandPosition }
  | { type: "idle" }
  | { type: "tick" }
  | { type: "welcome"; role: "band" | "leader"; pinRejected?: boolean; tooManyTries?: boolean }
  | { type: "error"; message: string }
  /** Hosted room only: where this device can reach the slides computer on
   * the church's network if the hosted room can't be reached. */
  | { type: "lan"; url: string }
  /** Local band server only: whether the hosted room is reachable again. */
  | { type: "hosted"; live: boolean }
  /** The library, whole or (through the online relay) in parts 0…parts-1. */
  | { type: "library"; data: LibrarySong[]; part?: number; parts?: number };

/** Messages a device sends. */
export type ClientMessage =
  | { type: "hello"; role: "band" | "leader"; pin?: string }
  | { type: "library_request" }
  | { type: "command"; command: BandCommand };

/** Event names the desktop app emits (must match `band.rs`). */
export const BAND_COMMAND_EVENT = "band://command";
export const BAND_CLIENTS_EVENT = "band://clients";

export function isBandCommand(v: unknown): v is BandCommand {
  if (!v || typeof v !== "object") return false;
  const c = v as Record<string, unknown>;
  if (c.kind === "go_to_song") return typeof c.itemId === "string";
  if (c.kind === "go_to_slide") {
    return typeof c.itemId === "string" && Number.isInteger(c.slideIdx) && (c.slideIdx as number) >= 0;
  }
  if (c.kind === "set_blank") return typeof c.on === "boolean";
  if (c.kind === "add_song") {
    return (
      typeof c.songId === "string" &&
      (c.when === "next" || c.when === "now") &&
      (c.key === undefined || typeof c.key === "string")
    );
  }
  if (c.kind === "set_transpose") {
    return typeof c.itemId === "string" && typeof c.semitones === "number" && Number.isFinite(c.semitones);
  }
  return false;
}

/** Keep a transpose within an octave either way. */
export function clampTranspose(semitones: number): number {
  const s = Math.round(semitones) % 12;
  return s > 6 ? s - 12 : s < -6 ? s + 12 : s;
}
