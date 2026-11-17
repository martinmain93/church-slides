/**
 * Song-file imports asked for by an AI assistant (the `import_song_files`
 * MCP tool). The request arrives from `src-tauri/src/assistant_link.rs`; this
 * reads the files through the same importers and duplicate rules as the
 * Library screen, applies the default choices a person would see in the
 * import dialog, and reports back per song.
 */

import { useEffect, useRef } from "react";

import { FORMAT_LABELS, isWholeLibraryFile, readImportFiles } from "@/lib/chords/import";
import { readLeadSheetWithEngine, type ReadLeadSheet } from "@/lib/chords/import/leadSheet";
import {
  defaultAction,
  defaultLeadSheetAction,
  planImport,
  planLeadSheets,
  resolveLeadSheetSong,
  toImportItem,
  toReferenceInput,
  type ImportRow,
} from "@/lib/chords/import/plan";
import { invoke, isInTauri } from "@/lib/invoke";
import { importCharts, listSongs, saveSongReference, type ChartImportSummary, type SongRow } from "@/lib/tauri";

export const ASSISTANT_IMPORT_EVENT = "assistant-import";

export interface AssistantImportRequest {
  id: string;
  files: { path: string; name: string }[];
  dry_run: boolean;
  /** File path → library song id: add that file's chords to this song, whatever it matched. */
  attach_to?: Record<string, string>;
}

export interface ImportedSongReport {
  file: string;
  title: string;
  read_as: string;
  has_chords: boolean;
  key: string | null;
  ccli: string | null;
  slides: number;
  outcome: "new_song" | "chords_added_to_existing" | "skipped";
  /** The song in the library afterwards (new, or the one chords went to). */
  song_id: string | null;
  existing_song: { id: string; title: string } | null;
  reason: string | null;
  warnings: string[];
}

/** A lead sheet (notation PDF) found in the files: a reference, not a song. */
export interface ImportedLeadSheetReport {
  file: string;
  title: string | null;
  key: string | null;
  ccli: string | null;
  bars: number;
  unreadable_bars: number;
  outcome: "attached" | "skipped";
  /** The song it was attached to (or, in a dry run, would be). */
  song: { id: string | null; title: string } | null;
  reason: string | null;
}

export interface AssistantImportReport {
  dry_run: boolean;
  summary: string;
  songs: ImportedSongReport[];
  lead_sheets: ImportedLeadSheetReport[];
  unreadable: { file: string; error: string }[];
  identical_copies_ignored: number;
}

export interface ImportDeps {
  readFile: (path: string) => Promise<ArrayBuffer>;
  listSongs: () => Promise<SongRow[]>;
  importCharts: typeof importCharts;
  /** Lead-sheet detection and saving; without them PDFs are read as charts only. */
  readLeadSheet?: ReadLeadSheet;
  saveSongReference?: typeof saveSongReference;
}

export async function runAssistantImport(
  req: AssistantImportRequest,
  deps: ImportDeps,
): Promise<AssistantImportReport> {
  const files: File[] = [];
  const unreadable: { file: string; error: string }[] = [];
  for (const f of req.files) {
    try {
      files.push(new File([await deps.readFile(f.path)], f.name));
    } catch (err) {
      unreadable.push({ file: f.name, error: err instanceof Error ? err.message : String(err) });
    }
  }

  const read = await readImportFiles(files, { isWholeLibrary: isWholeLibraryFile, readLeadSheet: deps.readLeadSheet });
  if (read.wholeLibraryText !== null) {
    // Whole-library imports ask merge-or-replace; that's a person's call.
    unreadable.push({
      file: files[0]?.name ?? "",
      error: "A whole-library export (ProPresenter Export.txt or a Church Slides backup) — import it from the Library screen",
    });
  }
  for (const r of read.results) if (r.error) unreadable.push({ file: r.fileName, error: r.error });

  const library = await deps.listSongs();
  const rows = planImport(read.results, library);
  const actions = rows.map(defaultAction);
  // The assistant named the song a file belongs to: that beats any match.
  const pathOf = new Map(req.files.map((f) => [f.name, f.path]));
  for (const [i, r] of rows.entries()) {
    const songId = req.attach_to?.[pathOf.get(r.fileName) ?? ""];
    if (!songId) continue;
    const song = library.find((s) => s.id === songId);
    if (!song) throw new Error(`attach_to: no song with id ${songId} (for ${r.fileName})`);
    r.match = song;
    r.matchNote = null;
    r.betterCopy = null;
    actions[i] = { kind: "attach", songId };
  }
  const sheets = planLeadSheets(read.results, library, rows);
  const sheetActions = sheets.map((r) => defaultLeadSheetAction(r));

  let summary: ChartImportSummary | null = null;
  const chosen = rows.flatMap((_, i) => (actions[i]!.kind === "skip" ? [] : [i]));
  const items = new Map(chosen.map((i) => [i, toImportItem(rows[i]!, actions[i]!, library)]));
  if (!req.dry_run && chosen.length > 0) {
    summary = await deps.importCharts([...items.values()]);
  }
  const savedId = new Map(chosen.map((rowIdx, k) => [rowIdx, summary?.song_ids[k] ?? null]));
  const savedByKey = new Map(chosen.flatMap((rowIdx, k) => (summary?.song_ids[k] ? [[rows[rowIdx]!.key, summary.song_ids[k]!]] : [])));

  const lead_sheets: ImportedLeadSheetReport[] = [];
  for (const [i, r] of sheets.entries()) {
    const action = sheetActions[i]!;
    const target =
      action.kind === "attach"
        ? { id: action.songId, title: library.find((s) => s.id === action.songId)?.title ?? "" }
        : action.kind === "with_import"
          ? { id: savedByKey.get(action.rowKey) ?? null, title: r.importRow?.song.title ?? "" }
          : null;
    let outcome: ImportedLeadSheetReport["outcome"] = target ? "attached" : "skipped";
    if (target && !req.dry_run) {
      const songId = resolveLeadSheetSong(action, savedByKey);
      if (songId && deps.saveSongReference) await deps.saveSongReference(toReferenceInput(r, songId));
      else outcome = "skipped";
    }
    lead_sheets.push({
      file: r.fileName,
      title: r.sheet.title,
      key: r.sheet.key,
      ccli: r.sheet.ccli,
      bars: r.sheet.barCount,
      unreadable_bars: r.sheet.problemBars,
      outcome,
      song: target,
      reason: r.betterCopy
        ? `Same song as ${r.betterCopy.fileName}, which reads better`
        : !target
          ? "No song in the library matches its title or CCLI number — import the song first, then the sheet"
          : null,
    });
  }

  const songs = rows.map((r, i): ImportedSongReport => {
    const action = actions[i]!;
    return {
      file: r.fileName,
      title: r.song.title,
      read_as: FORMAT_LABELS[r.song.format],
      has_chords: r.hasChords,
      key: r.song.chart.key,
      ccli: r.song.ccli,
      slides: r.slideCount,
      outcome: action.kind === "new" ? "new_song" : action.kind === "attach" ? "chords_added_to_existing" : "skipped",
      song_id: action.kind === "attach" ? action.songId : (savedId.get(i) ?? null),
      existing_song: r.match ? { id: r.match.id, title: r.match.title } : null,
      reason: skipReason(r, action.kind, Boolean(items.get(i)?.relabel)),
      warnings: [...(r.matchNote ? [r.matchNote] : []), ...r.song.warnings],
    };
  });

  const count = (o: ImportedSongReport["outcome"]) => songs.filter((s) => s.outcome === o).length;
  const parts = [
    `${count("new_song")} new song(s)`,
    `chords added to ${count("chords_added_to_existing")} existing`,
    `${count("skipped")} skipped`,
  ];
  const sheetsAttached = lead_sheets.filter((s) => s.outcome === "attached").length;
  if (lead_sheets.length) parts.push(`${sheetsAttached} of ${lead_sheets.length} lead sheet(s) attached to songs`);
  if (unreadable.length) parts.push(`${unreadable.length} file(s) couldn't be read`);
  return {
    dry_run: req.dry_run,
    summary: `${req.dry_run ? "Would import" : "Imported"}: ${parts.join(", ")}.`,
    songs,
    lead_sheets,
    unreadable,
    identical_copies_ignored: read.duplicateFiles,
  };
}

function skipReason(r: ImportRow, kind: string, relabeled: boolean): string | null {
  if (kind === "attach") {
    return `Already in the library as “${r.match?.title}”; its lyrics were kept` +
      (relabeled ? ", with its sections named from this chart" : "");
  }
  if (kind !== "skip") return null;
  if (r.betterCopy) return `Same song as ${r.betterCopy.fileName}, which is the better copy`;
  if (r.match) return `Already in the library as “${r.match.title}”, and this file has no chords to add`;
  return null;
}

/**
 * Serve assistant import requests while the main window is open.
 * `onImported` runs after anything was saved, with a one-line summary.
 */
export function useAssistantImport(onImported: (summary: string) => void): void {
  const latest = useRef(onImported);
  latest.current = onImported;

  useEffect(() => {
    if (!isInTauri()) return;
    let unlisten: (() => void) | undefined;
    let cancelled = false;
    const depsFor = (id: string): ImportDeps => ({
      readFile: (path) => invoke<ArrayBuffer>("assistant_read_file", { id, path }),
      listSongs,
      importCharts,
      readLeadSheet: readLeadSheetWithEngine,
      saveSongReference,
    });

    void import("@tauri-apps/api/event").then(async ({ listen }) => {
      const off = await listen<AssistantImportRequest>(ASSISTANT_IMPORT_EVENT, async ({ payload }) => {
        try {
          const report = await runAssistantImport(payload, depsFor(payload.id));
          await invoke("assistant_import_done", { id: payload.id, ok: true, result: report, error: null });
          if (
            !payload.dry_run &&
            (report.songs.some((s) => s.outcome !== "skipped") || report.lead_sheets.some((s) => s.outcome === "attached"))
          ) {
            latest.current(report.summary);
          }
        } catch (err) {
          await invoke("assistant_import_done", {
            id: payload.id,
            ok: false,
            result: null,
            error: err instanceof Error ? err.message : String(err),
          }).catch(() => undefined);
        }
      });
      if (cancelled) {
        off();
        return;
      }
      unlisten = off;
      await invoke("assistant_link_ready").catch(() => undefined);
    });
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, []);
}
