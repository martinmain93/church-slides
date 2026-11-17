import type { ImportFileResult } from "@/lib/chords/import/types";

export type OtherAppId = "propresenter" | "freeshow" | "openlp" | "opensong";

/** A service from the other app: its name and songs in running order. */
export interface ImportedSetlist {
  name: string;
  /** Each song, as the `fileName` of its entry in `results`. */
  songs: string[];
  /** When it was last planned or used (ms), where the app records it. */
  date?: number | null;
}

/** How many of the newest dated services an import brings by default. */
export const RECENT_SERVICES = 8;

/**
 * The services an import brings unless asked for more: the newest
 * `RECENT_SERVICES` by date, plus every undated one (we can't tell those are
 * old). Years of past services would only crowd the Home screen.
 */
export function splitRecentServices(setlists: ImportedSetlist[]): { recent: ImportedSetlist[]; older: ImportedSetlist[] } {
  const dated = setlists.filter((s) => s.date != null).sort((a, b) => b.date! - a.date!);
  const older = new Set(dated.slice(RECENT_SERVICES));
  return { recent: setlists.filter((s) => !older.has(s)), older: setlists.filter((s) => older.has(s)) };
}

/**
 * What an app's library reads as: one "file" per song, so it goes through
 * the same preview and duplicate checks as song files, plus its setlists.
 */
export interface OtherAppImport {
  results: ImportFileResult[];
  setlists: ImportedSetlist[];
}
