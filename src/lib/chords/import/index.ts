/**
 * Import any song file we can read: sniff the content, pick the reader.
 *
 * Detection is by *content*, with the file extension only as a tiebreak —
 * people rename `.cho` to `.txt`, and SongSelect hands out `.txt` files in two
 * unrelated formats.
 */

import { fromFileName } from "@/lib/chords/import/fileName";
import { inferKey } from "@/lib/chords/import/inferKey";
import type { ReadLeadSheet } from "@/lib/chords/import/leadSheet";
import { parseTextChart } from "@/lib/chords/import/text";
import type { ImportFileResult, ImportedChartSong } from "@/lib/chords/import/types";
import { parseOpenLyrics, parseOpenSong, parseSongSelectUsr } from "@/lib/chords/import/xml";
import { importOsz } from "@/lib/otherApps/osz";
import { importProFile, importProPlaylist, looksLikeProPresenterFile } from "@/lib/otherApps/proFile";
import type { ImportedSetlist } from "@/lib/otherApps/types";
import { looksLikeProPresenterExport } from "@/lib/propresenterImport";

export type { ImportFileResult, ImportedChartSong, ImportedLeadSheet } from "@/lib/chords/import/types";
export { FORMAT_LABELS } from "@/lib/chords/import/types";

/** File types the picker offers. Anything else can still be dropped in. */
export const SONG_FILE_EXTENSIONS = [
  ".pdf", ".cho", ".chopro", ".chordpro", ".crd", ".pro", ".onsong", ".txt", ".usr",
  ".xml", ".openlyrics", ".opensong", ".song",
  // Another app's playlist or service: its songs, and the running order.
  ".proplaylist", ".osz",
];
export const IMPORT_ACCEPT = [...SONG_FILE_EXTENSIONS, ".json"].join(",");

/**
 * Decode file bytes: honour a BOM (SongSelect and Windows tools write
 * UTF-16), otherwise try UTF-8 and fall back to Windows-1252 for the legacy
 * files that aren't valid UTF-8.
 */
export function decodeBytes(bytes: Uint8Array): string {
  if (bytes[0] === 0xff && bytes[1] === 0xfe) return new TextDecoder("utf-16le").decode(bytes.subarray(2));
  if (bytes[0] === 0xfe && bytes[1] === 0xff) return new TextDecoder("utf-16be").decode(bytes.subarray(2));
  if (bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) return new TextDecoder("utf-8").decode(bytes.subarray(3));
  // UTF-16 without a BOM: every other byte of ASCII text is zero.
  if (bytes.length >= 4 && bytes[1] === 0 && bytes[3] === 0 && bytes[0] !== 0) {
    return new TextDecoder("utf-16le").decode(bytes);
  }
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    return decodeWindows1252(bytes);
  }
}

/** 0x80–0x9F in Windows-1252 (the rest is Latin-1). Not every runtime's
 * TextDecoder implements this label faithfully, so it's done by hand. */
const CP1252_HIGH =
  "\u20ac\u0081\u201a\u0192\u201e\u2026\u2020\u2021\u02c6\u2030\u0160\u2039\u0152\u008d\u017d\u008f" +
  "\u0090\u2018\u2019\u201c\u201d\u2022\u2013\u2014\u02dc\u2122\u0161\u203a\u0153\u009d\u017e\u0178";

function decodeWindows1252(bytes: Uint8Array): string {
  let out = "";
  for (const b of bytes) out += b >= 0x80 && b <= 0x9f ? CP1252_HIGH[b - 0x80]! : String.fromCharCode(b);
  return out;
}


/**
 * What an unreadable file looks like, without its contents: extension, size
 * and its first 16 bytes (enough to spot a format: `PK` zip, `%PDF`, `<?xml`).
 */
export function fileSignature(name: string, bytes: Uint8Array): string {
  const ext = name.includes(".") ? name.split(".").pop()!.toLowerCase() : "";
  const head = Array.from(bytes.subarray(0, 16), (b) => b.toString(16).padStart(2, "0")).join(" ");
  return `.${ext || "(none)"} · ${bytes.length} bytes · starts ${head}`;
}

/**
 * A whole-library file: a Church Slides backup (JSON with a `songs` list) or
 * a ProPresenter `Export.txt`. Checked by parsing, not by a leading `{` —
 * ChordPro files start with `{title: …}` too.
 */
export function isWholeLibraryFile(text: string): boolean {
  const t = text.replace(/^\uFEFF/, "").trimStart();
  if (looksLikeProPresenterExport(t)) return true;
  if (!t.startsWith("{")) return false;
  try {
    const v: unknown = JSON.parse(t);
    return typeof v === "object" && v !== null && Array.isArray((v as { songs?: unknown }).songs);
  } catch {
    return false;
  }
}

/**
 * Decoded "text" that's really binary (a database, an app's own file): NULs
 * or a run of control characters. Real text — UTF-16 and Windows-1252
 * included, once decoded — has neither beyond tabs and line breaks.
 */
function looksBinary(text: string): boolean {
  const sample = text.slice(0, 4096);
  if (sample.includes("\u0000")) return true;
  const controls = sample.match(/[\u0001-\u0008\u000e-\u001f]/g)?.length ?? 0;
  return controls > Math.max(2, sample.length / 100);
}

/** File kinds we recognise but can't read, with a reason the operator can act on. */
function unsupported(name: string, text: string): string | null {
  const ext = name.toLowerCase().split(".").pop() ?? "";
  if (["doc", "docx", "ppt", "pptx", "key", "pages"].includes(ext) || text.startsWith("PK\u0003\u0004")) {
    return "Office documents aren't supported — copy the chart into a .txt file";
  }
  if (["pro6", "pro"].includes(ext) && /<RVPresentationDocument/.test(text)) {
    return "ProPresenter 6 files aren't supported — use ProPresenter's text export";
  }
  if (ext === "pro6") {
    return "ProPresenter 6 files aren't supported — use ProPresenter's text export";
  }
  return null;
}

/**
 * Read one file. Library JSON and ProPresenter `Export.txt` are *not* handled
 * here — those are whole-library formats with their own importer, and the
 * caller routes them before getting this far.
 */
export function importChartFile(fileName: string, text: string): ImportFileResult {
  const fallback = fromFileName(fileName).title;
  const stripped = text.replace(/^\uFEFF/, "");
  const head = stripped.trimStart().slice(0, 400);

  const why = unsupported(fileName, stripped);
  if (why) return { fileName, songs: [], error: why };
  if (looksBinary(stripped)) return { fileName, songs: [], error: "Not a format Church Slides recognises" };

  let songs: ImportedChartSong[] = [];
  if (head.startsWith("<")) {
    const isOpenLyrics = /openlyrics\.info|<song[^>]*\bversion="0\.\d"/i.test(stripped.slice(0, 2000));
    const song = isOpenLyrics
      ? parseOpenLyrics(stripped, fallback) ?? parseOpenSong(stripped, fallback)
      : parseOpenSong(stripped, fallback) ?? parseOpenLyrics(stripped, fallback);
    if (!song) return { fileName, songs: [], error: "Unrecognised XML — expected OpenLyrics or OpenSong" };
    songs = [song];
  } else if (/^\[S\s*A?\d*\]/m.test(head) || (/^Title=/m.test(stripped) && /^Words=/m.test(stripped))) {
    const song = parseSongSelectUsr(stripped, fallback);
    if (song) songs = [song];
  } else {
    songs = parseTextChart(stripped, fallback);
  }

  if (songs.length === 0) return { fileName, songs: [], error: "No lyrics or chords found in this file" };
  return { fileName, songs, error: null };
}

export interface ReadProgress {
  done: number;
  total: number;
}

export interface ReadFilesResult {
  results: ImportFileResult[];
  /** Set when the only file was a whole-library export (JSON / ProPresenter). */
  wholeLibraryText: string | null;
  /** Copies of the same file (same name and size) read once. */
  duplicateFiles: number;
  /** Running orders from playlist and service files, naming songs by `fileName`. */
  setlists: ImportedSetlist[];
}

/**
 * Read any mix of song files — from the picker, a dropped selection or a
 * whole folder. Copies of one file (a sheet saved into every week's setlist
 * folder) are read once. PDFs go through PDF.js; everything else is decoded
 * as text. `isWholeLibrary` spots exports that belong to the library importer.
 */
export async function readImportFiles(
  files: File[],
  opts: {
    isWholeLibrary: (text: string) => boolean;
    onProgress?: (p: ReadProgress) => void;
    /** From a folder: skip files that aren't song files (images, docs, .DS_Store). */
    songFilesOnly?: boolean;
    /**
     * Ask whether a PDF is a lead sheet (the engine reads notation; see
     * `leadSheet.ts`). A lead sheet becomes a reference on its song instead
     * of a chart. Without it, every PDF is read as a chord chart.
     */
    readLeadSheet?: ReadLeadSheet;
  },
): Promise<ReadFilesResult> {
  const seen = new Set<string>();
  const unique: File[] = [];
  let duplicateFiles = 0;
  for (const f of files) {
    const lower = f.name.toLowerCase();
    if (opts.songFilesOnly && !SONG_FILE_EXTENSIONS.some((e) => lower.endsWith(e))) continue;
    const k = `${lower}:${f.size}`;
    if (seen.has(k)) {
      duplicateFiles += 1;
      continue;
    }
    seen.add(k);
    unique.push(f);
  }

  const results: ImportFileResult[] = [];
  const setlists: ImportedSetlist[] = [];
  let wholeLibraryText: string | null = null;
  const { importPdfFile } = await import("@/lib/chords/import/pdf");
  for (let i = 0; i < unique.length; i++) {
    const f = unique[i]!;
    opts.onProgress?.({ done: i, total: unique.length });
    const bytes = new Uint8Array(await f.arrayBuffer());
    const before = results.length;
    try {
      const isPdf = f.name.toLowerCase().endsWith(".pdf") || (bytes[0] === 0x25 && bytes[1] === 0x50 && bytes[2] === 0x44 && bytes[3] === 0x46);
      if (isPdf) {
        const check = opts.readLeadSheet ? await opts.readLeadSheet(f.name, bytes) : null;
        if (check?.kind === "lead_sheet") {
          results.push({ fileName: f.name, songs: [], leadSheet: check.sheet, error: null });
          continue;
        }
        const chart = await importPdfFile(f.name, bytes);
        if (check?.kind === "unavailable") {
          // Read as a chart as before, but say so: if this was sheet music it
          // needs the engine to become a reference. Never silently drop it.
          for (const song of chart.songs) {
            song.warnings.push("Not checked for sheet music (the engine isn't running) — if this is a lead sheet, import it again once it is");
          }
        }
        results.push(chart);
        continue;
      }
      // Binary formats from other presentation apps.
      const lower = f.name.toLowerCase();
      if (lower.endsWith(".proplaylist") || lower.endsWith(".osz")) {
        const read = lower.endsWith(".osz") ? importOsz(f.name, bytes) : importProPlaylist(f.name, bytes);
        results.push(...read.results);
        setlists.push(...read.setlists);
        continue;
      }
      if (lower.endsWith(".pro") && looksLikeProPresenterFile(bytes)) {
        results.push(importProFile(f.name, bytes));
        continue;
      }
      const text = decodeBytes(bytes);
      if (opts.isWholeLibrary(text)) {
        if (unique.length === 1) wholeLibraryText = text;
        else results.push({ fileName: f.name, songs: [], error: "A whole-library file — import it on its own" });
        continue;
      }
      results.push(importChartFile(f.name, text));
  
    } finally {
      for (const r of results.slice(before)) {
        // A file we couldn't read carries its shape, for the "we don't
        // recognise that format" fallback and its report.
        if (r.error) r.signature = fileSignature(f.name, bytes);
        // No key on the sheet or in its name: work it out from the chords,
        // so the band screens can transpose it.
        for (const song of r.songs) {
          if (song.chart.key) continue;
          const key = inferKey(song.chart);
          if (!key) continue;
          song.chart = { ...song.chart, key };
          song.warnings.push(`Key ${key} worked out from the chords`);
        }
      }
    }
  }
  opts.onProgress?.({ done: unique.length, total: unique.length });
  return { results, wholeLibraryText, duplicateFiles, setlists };
}
