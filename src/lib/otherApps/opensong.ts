/**
 * OpenSong: the Songs folder (one XML file per song, subfolders allowed) and
 * the Sets folder, as `other_apps.rs` reads them. Songs go through the same
 * reader as a dropped OpenSong file; a set names its songs by folder + name.
 */

import { importChartFile } from "@/lib/chords/import";
import type { ImportedSetlist, OtherAppImport } from "@/lib/otherApps/types";

interface OpenSongFile {
  /** Relative to Songs/ or Sets/, with "/" separators. */
  path: string;
  text: string;
  /** File modified time (ms): when a set was last planned. */
  modified?: number | null;
}

export interface OpenSongData {
  songs: OpenSongFile[];
  sets: OpenSongFile[];
}

/** "Favorites/" + "Be Thou My Vision", "/" + "X", "" + "X" → one key. */
function songKey(folder: string, name: string): string {
  return `${folder.replace(/^\/+|\/+$/g, "")}/${name}`.replace(/^\//, "").toLowerCase();
}

function parseSet(file: OpenSongFile, known: Map<string, string>): ImportedSetlist | null {
  const doc = new DOMParser().parseFromString(file.text, "application/xml");
  const set = doc.documentElement;
  if (doc.getElementsByTagName("parsererror").length > 0 || set.localName !== "set") return null;
  const songs = Array.from(doc.getElementsByTagName("slide_group"))
    .filter((g) => g.getAttribute("type") === "song")
    .map((g) => known.get(songKey(g.getAttribute("path") ?? "", g.getAttribute("name") ?? "")))
    .filter((k): k is string => k !== undefined);
  if (songs.length === 0) return null;
  return { name: set.getAttribute("name")?.trim() || file.path, songs, date: file.modified ?? null };
}

export function fromOpenSong(data: OpenSongData): OtherAppImport {
  // Only files OpenSong itself would show as songs: a stray text file in the
  // folder would otherwise read as a lyrics chart.
  const results = data.songs
    .map((f) => importChartFile(f.path, f.text))
    .filter((r) => r.songs.every((s) => s.format === "opensong"));
  const known = new Map(
    results.filter((r) => r.songs.length > 0).map((r) => {
      const slash = r.fileName.lastIndexOf("/");
      return [songKey(r.fileName.slice(0, slash + 1), r.fileName.slice(slash + 1)), r.fileName] as const;
    }),
  );
  const setlists = data.sets.map((f) => parseSet(f, known)).filter((s): s is ImportedSetlist => s !== null);
  return { results, setlists };
}
