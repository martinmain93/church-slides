/**
 * What an import of song files will do, before anything is written: match
 * each song found against the library, pick the best copy when several files
 * hold the same song, and turn a decision into the item `import_charts`
 * saves.
 *
 * Shared by the import dialog (a person reviews and adjusts the actions) and
 * the AI-assistant import (the defaults are applied as they stand), so both
 * make the same calls about duplicates.
 */

import { chartHasChords } from "@/lib/chords/chart";
import type { ImportFileResult, ImportedChartSong, ImportedLeadSheet } from "@/lib/chords/import/types";
import { relabelFromChart } from "@/lib/chords/import/relabel";
import { chartToSong } from "@/lib/chords/import/toSong";
import { parseSong } from "@/lib/parseSong";
import type { ChartImportItem, NewSongReference, SongRow } from "@/lib/tauri";

export type ImportAction = { kind: "new" } | { kind: "attach"; songId: string } | { kind: "skip" };

export interface ImportRow {
  key: string;
  fileName: string;
  song: ImportedChartSong;
  hasChords: boolean;
  slideCount: number;
  match: SongRow | null;
  /** Why `match` isn't the obvious one, when the CCLI number and title disagreed. */
  matchNote: string | null;
  /** Another row in this import is the same song and a better copy of it. */
  betterCopy: ImportRow | null;
}

/** Title as people type it differently: case, punctuation, "(Live)". */
export function normalizeTitle(t: string): string {
  return t
    .toLowerCase()
    .replace(/\(.*?\)|\[.*?\]/g, " ")
    .replace(/[‘’']/g, "")
    .replace(/(\d)[,.](?=\d{3}\b)/g, "$1") // "10,000" = "10000"
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim();
}

/** How good a copy of a song is, to pick one when a folder holds several. */
function copyScore(r: ImportRow): number {
  const chords = r.song.chart.sections.reduce((n, s) => n + s.stanzas.flat().reduce((m, l) => m + l.chords.length, 0), 0);
  return (r.hasChords ? 1000 : 0) + (r.song.ccli ? 100 : 0) + (r.song.chart.key ? 10 : 0) + Math.min(chords, 9);
}

export function planImport(results: ImportFileResult[], library: SongRow[]): ImportRow[] {
  // Match library songs by CCLI number first (exact, whatever the title
  // says), then by title.
  const byTitle = new Map<string, SongRow>();
  const byCcli = new Map<string, SongRow>();
  for (const s of library) {
    byTitle.set(normalizeTitle(s.title), s);
    if (s.ccli?.trim()) byCcli.set(s.ccli.trim(), s);
  }
  const all: ImportRow[] = results.flatMap((r, fi) =>
    r.songs.map((song, i) => ({
      key: `${fi}:${r.fileName}#${i}`,
      fileName: r.fileName,
      song,
      hasChords: chartHasChords(song.chart),
      slideCount: parseSong(chartToSong(song).body_text).slides.length,
      ...matchSong(song, byCcli, byTitle),
      betterCopy: null,
    })),
  );
  // The same song from several files (a sheet in two keys, or saved into
  // every week's folder): keep the best copy, skip the rest by default.
  const bySong = new Map<string, ImportRow[]>();
  for (const r of all) {
    const k = r.song.ccli ? `ccli:${r.song.ccli}` : `title:${normalizeTitle(r.song.title)}`;
    bySong.set(k, [...(bySong.get(k) ?? []), r]);
  }
  for (const group of bySong.values()) {
    if (group.length < 2) continue;
    const best = [...group].sort((a, b) => copyScore(b) - copyScore(a))[0]!;
    for (const r of group) if (r !== best) r.betterCopy = best;
  }
  return all;
}

/** Do two titles name the same song ("Holy Holy Holy" / "Holy, Holy, Holy (Nicaea)")? */
function sameTitle(a: string, b: string): boolean {
  const x = normalizeTitle(a);
  const y = normalizeTitle(b);
  return x === y || x.startsWith(`${y} `) || y.startsWith(`${x} `);
}

/**
 * The library song a file is: by CCLI number (exact, whatever the title
 * says), then by title. A CCLI number is sometimes wrong — on the sheet or in
 * the library — so when it points at a song with an unrelated title while
 * another library song has this exact title, the title wins.
 */
function matchSong(
  song: ImportedChartSong,
  byCcli: Map<string, SongRow>,
  byTitle: Map<string, SongRow>,
): { match: SongRow | null; matchNote: string | null } {
  const byNumber = song.ccli ? byCcli.get(song.ccli) : undefined;
  const byName = byTitle.get(normalizeTitle(song.title));
  if (byNumber && byName && byNumber !== byName && !sameTitle(byNumber.title, song.title)) {
    return {
      match: byName,
      matchNote: `CCLI ${song.ccli} belongs to “${byNumber.title}” in the library; matched by title instead`,
    };
  }
  return { match: byNumber ?? byName ?? null, matchNote: null };
}

export function defaultAction(row: ImportRow): ImportAction {
  if (row.betterCopy) return { kind: "skip" };
  if (row.match) return row.hasChords ? { kind: "attach", songId: row.match.id } : { kind: "skip" };
  return { kind: "new" };
}

/** The save request for one row. `action` must not be "skip". */
export function toImportItem(row: ImportRow, action: ImportAction, library?: SongRow[]): ChartImportItem {
  const { body_text, slide_order } = chartToSong(row.song);
  return {
    existing_song_id: action.kind === "attach" ? action.songId : null,
    song:
      action.kind === "new"
        ? {
            title: row.song.title,
            body_text,
            slide_order,
            ccli: row.song.ccli,
            author: row.song.artist,
            copyright: row.song.copyright,
            default_tempo_wps: null,
          }
        : null,
    chart_json: JSON.stringify(row.song.chart),
    original_key: row.song.chart.key,
    source_format: row.song.format,
    // Adding chords to a library song also fills in the licensing facts
    // it's missing (never overwrites ones it has).
    fill_metadata:
      action.kind === "attach"
        ? {
            // A number that belongs to another library song isn't this one's.
            ccli: row.matchNote ? null : row.song.ccli,
            author: row.song.artist,
            copyright: row.song.copyright,
          }
        : null,
    relabel: action.kind === "attach" ? relabelFor(row, action.songId, library) : null,
  };
}

/** Section names from the chart for a library song whose labels say nothing. */
function relabelFor(row: ImportRow, songId: string, library: SongRow[] | undefined): ChartImportItem["relabel"] {
  const song = library?.find((s) => s.id === songId) ?? (row.match?.id === songId ? row.match : undefined);
  if (!song) return null;
  const next = relabelFromChart(song, row.song);
  return next && { ...next, previous_body: song.body_text };
}

/**
 * The library song each file's song ended up as, after an import: the one it
 * created or added chords to, the library song it was skipped in favour of,
 * or the better copy's. For setlists brought across with the songs.
 */
export function resolveSongIds(
  rows: ImportRow[],
  actionOf: (r: ImportRow) => ImportAction,
  savedIds: Map<string, string>,
): Map<string, string> {
  const resolve = (r: ImportRow, depth = 0): string | null => {
    const saved = savedIds.get(r.key);
    if (saved) return saved;
    const a = actionOf(r);
    if (a.kind === "attach") return a.songId;
    if (r.match) return r.match.id;
    return r.betterCopy && depth < 8 ? resolve(r.betterCopy, depth + 1) : null;
  };
  const out = new Map<string, string>();
  for (const r of rows) {
    const id = resolve(r);
    if (id && !out.has(r.fileName)) out.set(r.fileName, id);
  }
  return out;
}

// --- Lead sheets ---------------------------------------------------------------

/**
 * Attach to a library song, to a song this same import creates (a chart and
 * its lead sheet dropped in together), or skip. A lead sheet never creates a
 * song by itself: it has no slides, only the music of the song's lines.
 */
export type LeadSheetAction =
  | { kind: "attach"; songId: string }
  | { kind: "with_import"; rowKey: string }
  | { kind: "skip" };

export interface LeadSheetRow {
  key: string;
  fileName: string;
  sheet: ImportedLeadSheet;
  /** The library song it belongs to, by CCLI number, then title. */
  match: SongRow | null;
  /** A song in this import it belongs to, when the library has none. */
  importRow: ImportRow | null;
  /** Another sheet in this import is the same song and reads better. */
  betterCopy: LeadSheetRow | null;
}

export function planLeadSheets(results: ImportFileResult[], library: SongRow[], chartRows: ImportRow[]): LeadSheetRow[] {
  const byTitle = new Map<string, SongRow>();
  const byCcli = new Map<string, SongRow>();
  for (const s of library) {
    byTitle.set(normalizeTitle(s.title), s);
    if (s.ccli?.trim()) byCcli.set(s.ccli.trim(), s);
  }
  const titleOf = (r: ImportFileResult) => r.leadSheet?.title || fromFile(r.fileName);
  const rows: LeadSheetRow[] = results.flatMap((r, fi) => {
    const sheet = r.leadSheet;
    if (!sheet) return [];
    const title = normalizeTitle(titleOf(r));
    const match = (sheet.ccli ? byCcli.get(sheet.ccli) : undefined) ?? byTitle.get(title) ?? null;
    const importRow = match
      ? null
      : chartRows.find((c) => (sheet.ccli && c.song.ccli === sheet.ccli) || normalizeTitle(c.song.title) === title) ?? null;
    return [{ key: `${fi}:${r.fileName}#sheet`, fileName: r.fileName, sheet, match, importRow, betterCopy: null }];
  });
  // Two sheets for one song (say, two keys): keep the one that reads best.
  const bySong = new Map<string, LeadSheetRow[]>();
  for (const r of rows) {
    const k = r.match ? `song:${r.match.id}` : r.importRow ? `row:${r.importRow.key}` : `file:${r.key}`;
    bySong.set(k, [...(bySong.get(k) ?? []), r]);
  }
  for (const group of bySong.values()) {
    if (group.length < 2) continue;
    const best = [...group].sort((a, b) => a.sheet.problemBars - b.sheet.problemBars || b.sheet.barCount - a.sheet.barCount)[0]!;
    for (const r of group) if (r !== best) r.betterCopy = best;
  }
  return rows;
}

/** "What A Beautiful Name-lead-D.pdf" -> "What A Beautiful Name". */
function fromFile(name: string): string {
  return name.replace(/\.pdf$/i, "").replace(/-lead-[^-]*$/i, "").trim();
}

/**
 * Default: attach to the song it matched. With the import's own chart rows,
 * follow that row — if the chart is skipped, there is no song to attach to.
 */
export function defaultLeadSheetAction(row: LeadSheetRow, chartActions?: (r: ImportRow) => ImportAction): LeadSheetAction {
  if (row.betterCopy) return { kind: "skip" };
  if (row.match) return { kind: "attach", songId: row.match.id };
  if (row.importRow) {
    const a = chartActions ? chartActions(row.importRow) : defaultAction(row.importRow);
    if (a.kind === "new") return { kind: "with_import", rowKey: row.importRow.key };
    if (a.kind === "attach") return { kind: "attach", songId: a.songId };
  }
  return { kind: "skip" };
}

/** The display facts kept beside the reference (the library lists these). */
export function leadSheetSummary(sheet: ImportedLeadSheet): Record<string, unknown> {
  return {
    title: sheet.title,
    key: sheet.key,
    ccli: sheet.ccli,
    bars: sheet.barCount,
    problem_bars: sheet.problemBars,
    sections: sheet.sections,
    reader_version: sheet.readerVersion,
  };
}

export function toReferenceInput(row: LeadSheetRow, songId: string): NewSongReference {
  return {
    song_id: songId,
    kind: "lead_sheet",
    source_name: row.fileName,
    data_json: row.sheet.scoreJson,
    summary_json: JSON.stringify(leadSheetSummary(row.sheet)),
  };
}

/**
 * The song each lead sheet ends up on, once the charts are saved:
 * `savedIds` maps a chart row's key to the song id `import_charts` returned.
 */
export function resolveLeadSheetSong(action: LeadSheetAction, savedIds: Map<string, string>): string | null {
  if (action.kind === "attach") return action.songId;
  if (action.kind === "with_import") return savedIds.get(action.rowKey) ?? null;
  return null;
}
