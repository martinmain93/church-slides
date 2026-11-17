/**
 * Preview of an import before anything is written: every song found in the
 * files, what format it was read as, whether it has chords, and what will
 * happen to it — a new song, chords added to a song already in the library,
 * or skipped. Lead sheets (notation PDFs the engine read) are listed apart:
 * they don't make songs, they attach to one as the score follower's
 * reference.
 *
 * An import from another app, or a playlist or service file, also carries
 * setlists: they become services once the songs are in — the most recent
 * ones by default (`splitRecentServices`), older ones only if asked.
 *
 * Summary first: what will happen, in counts, with one Import button. The
 * per-song table is folded away for big imports, where the defaults are
 * nearly always right; small ones show it open.
 */

import { useMemo, useState } from "react";
import { X } from "lucide-react";

import { UnreadableFiles } from "@/components/library/UnreadableFiles";
import { Button } from "@/components/ui/button";
import { FORMAT_LABELS, type ImportFileResult } from "@/lib/chords/import";
import {
  defaultAction,
  defaultLeadSheetAction,
  planImport,
  planLeadSheets,
  resolveLeadSheetSong,
  resolveSongIds,
  toImportItem,
  toReferenceInput,
  type ImportAction as Action,
  type ImportRow as Row,
  type LeadSheetAction,
  type LeadSheetRow,
} from "@/lib/chords/import/plan";
import { splitRecentServices, type ImportedSetlist } from "@/lib/otherApps";
import {
  addSetlistItem,
  createSetlist,
  importCharts,
  listSetlists,
  saveSongReference,
  type ChartImportItem,
  type SongRow,
} from "@/lib/tauri";

export function ChartImportDialog({
  results,
  library,
  duplicateFiles = 0,
  setlists = [],
  source,
  serviceName,
  onClose,
  onImported,
}: {
  results: ImportFileResult[];
  library: SongRow[];
  /** Identical files (same name and size) that were read only once. */
  duplicateFiles?: number;
  /** Setlists to recreate as services, naming songs by their file. */
  setlists?: ImportedSetlist[];
  /** The app the songs came from, when it wasn't files ("ProPresenter"). */
  source?: string;
  /** Importing into this service: its songs are added to it afterwards,
   * including ones the library already has. */
  serviceName?: string;
  onClose: () => void;
  /** `songIds`: every song the files resolved to — new, chords attached, or
   * already in the library — in file order, without repeats. */
  onImported: (message: string, songIds: string[]) => void;
}) {
  const rows = useMemo<Row[]>(() => planImport(results, library), [results, library]);
  const sheets = useMemo<LeadSheetRow[]>(() => planLeadSheets(results, library, rows), [results, library, rows]);
  const failed = results.filter((r) => r.error);
  // Songs from inside a playlist or service file are named "<file> › <song>".
  const fileCount = new Set(results.map((r) => r.fileName.split(" › ")[0])).size;
  const librarySorted = useMemo(() => [...library].sort((a, b) => a.title.localeCompare(b.title)), [library]);

  const [actions, setActions] = useState<Record<string, Action>>(() =>
    Object.fromEntries(rows.map((r) => [r.key, defaultAction(r)])),
  );
  const [sheetActions, setSheetActions] = useState<Record<string, LeadSheetAction>>(() =>
    Object.fromEntries(sheets.map((r) => [r.key, defaultLeadSheetAction(r)])),
  );
  const services = useMemo(() => splitRecentServices(setlists), [setlists]);
  const [includeOlder, setIncludeOlder] = useState(false);
  // The per-song table, open from the start only for small imports. Built
  // only while open: thousands of rows (each with a select) are slow to
  // build and slower to tear down.
  const [showRows, setShowRows] = useState(() => rows.length + sheets.length <= 10);
  const servicesToAdd = includeOlder ? setlists : services.recent;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const chosen = rows.filter((r) => actions[r.key]?.kind !== "skip");
  // A sheet riding on a song from this import goes nowhere if that song is skipped.
  const sheetAction = (r: LeadSheetRow): LeadSheetAction => {
    const a = sheetActions[r.key] ?? { kind: "skip" };
    return a.kind === "with_import" && actions[a.rowKey]?.kind !== "new" ? { kind: "skip" } : a;
  };
  const chosenSheets = sheets.filter((r) => sheetAction(r).kind !== "skip");
  const total = chosen.length + chosenSheets.length + servicesToAdd.length;
  // Songs the library already has (left as they are) still join the service.
  const alreadyInLibrary =
    serviceName !== undefined ? rows.filter((r) => actions[r.key]?.kind === "skip" && r.match).length : 0;
  // Nothing readable at all: the unreadable-files panel says it all.
  const nothingFound = rows.length === 0 && sheets.length === 0 && setlists.length === 0;

  async function doImport() {
    setBusy(true);
    setError(null);
    try {
      const items: ChartImportItem[] = chosen.map((r) => toImportItem(r, actions[r.key]!, library));
      const summary = items.length ? await importCharts(items) : { songs_created: 0, charts_attached: 0, song_ids: [] };
      const savedIds = new Map(chosen.map((r, i) => [r.key, summary.song_ids[i]!]));
      let sheetsAttached = 0;
      for (const r of chosenSheets) {
        const songId = resolveLeadSheetSong(sheetAction(r), savedIds);
        if (!songId) continue;
        await saveSongReference(toReferenceInput(r, songId));
        sheetsAttached += 1;
      }
      const byFile = resolveSongIds(rows, (r) => actions[r.key]!, savedIds);
      const added = servicesToAdd.length ? await addServices(servicesToAdd, byFile) : 0;
      const sheetSongs = chosenSheets.map((r) => resolveLeadSheetSong(sheetAction(r), savedIds));
      const songIds = [...new Set([...byFile.values(), ...sheetSongs.filter((id): id is string => !!id)])];
      const parts = [];
      if (summary.songs_created) parts.push(`${summary.songs_created} new song${summary.songs_created === 1 ? "" : "s"}`);
      if (summary.charts_attached) parts.push(`chords added to ${summary.charts_attached} existing`);
      if (added) parts.push(`${added} service${added === 1 ? "" : "s"}`);
      if (sheetsAttached) parts.push(`lead sheet${sheetsAttached === 1 ? "" : "s"} added to ${sheetsAttached} song${sheetsAttached === 1 ? "" : "s"}`);
      onImported(`Imported ${parts.join(", ") || "nothing"}.`, songIds);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6" role="dialog" aria-modal="true" aria-label="Import songs">
      <div className="flex max-h-full w-full max-w-4xl flex-col rounded-lg border border-border bg-background shadow-2xl">
        <header className="flex items-center justify-between border-b border-border px-5 py-3">
          <div>
            <div className="font-semibold">Import songs</div>
            {!nothingFound && <div className="text-xs text-muted-foreground">
              Found {rows.length} song{rows.length === 1 ? "" : "s"}
              {sheets.length > 0 && ` and ${sheets.length} lead sheet${sheets.length === 1 ? "" : "s"}`}
              {setlists.length > 0 && ` and ${setlists.length} service${setlists.length === 1 ? "" : "s"}`}
              {source ? ` in ${source}` : ` in ${fileCount} file${fileCount === 1 ? "" : "s"}`}
              . Nothing is saved until you press Import
              {serviceName !== undefined && `; then they're added to ${serviceName.trim() || "this service"}`}.
            </div>}
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="text-muted-foreground hover:text-foreground">
            <X className="size-5" />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {failed.length > 0 && <UnreadableFiles failed={failed} />}
          {duplicateFiles > 0 && (
            <div className="border-b border-border bg-muted/40 px-5 py-2 text-xs">
              {duplicateFiles} identical file copies were read once.
            </div>
          )}
          <ImportSummary
            rows={rows}
            actions={actions}
            sheets={chosenSheets.length}
            services={servicesToAdd.length}
            older={services.older.length}
            includeOlder={includeOlder}
            onIncludeOlder={setIncludeOlder}
          />
          {(rows.length > 0 || sheets.length > 0) && (
          <details open={showRows} onToggle={(e) => setShowRows(e.currentTarget.open)} className="border-t border-border">
            <summary className="cursor-pointer px-5 py-2 text-xs text-muted-foreground hover:text-foreground">
              Review {rows.length} song{rows.length === 1 ? "" : "s"}
              {sheets.length > 0 && ` and ${sheets.length} lead sheet${sheets.length === 1 ? "" : "s"}`}
            </summary>
          {showRows && rows.length > 0 && (
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-background text-left text-xs text-muted-foreground">
              <tr>
                <th className="px-5 py-2 font-medium">Song</th>
                <th className="px-2 py-2 font-medium">Read as</th>
                <th className="px-2 py-2 font-medium">Key</th>
                <th className="px-2 py-2 font-medium">CCLI</th>
                <th className="px-2 py-2 font-medium">Slides</th>
                <th className="px-5 py-2 font-medium">Do</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {rows.map((r) => {
                const action = actions[r.key]!;
                const value = action.kind === "attach" ? `attach:${action.songId}` : action.kind;
                return (
                  <tr key={r.key} data-testid="import-row">
                    <td className="px-5 py-2 align-top">
                      <div className="font-medium">{r.song.title}</div>
                      <div className="text-xs text-muted-foreground">
                        {[r.song.artist, r.fileName].filter(Boolean).join(" · ")}
                      </div>
                      {r.betterCopy && (
                        <div className="text-xs text-muted-foreground">
                          Same song as {r.betterCopy.fileName} — that copy is used
                        </div>
                      )}
                      {[...new Set([...(r.matchNote ? [r.matchNote] : []), ...r.song.warnings])].map((w) => (
                        <div key={w} className="text-xs text-amber-500">
                          {w}
                        </div>
                      ))}
                    </td>
                    <td className="px-2 py-2 align-top text-xs">
                      {FORMAT_LABELS[r.song.format]}
                      <div className={r.hasChords ? "text-emerald-500" : "text-muted-foreground"}>
                        {r.hasChords ? "with chords" : "lyrics only"}
                      </div>
                    </td>
                    <td className="px-2 py-2 align-top">{r.song.chart.key ?? "—"}</td>
                    <td className="px-2 py-2 align-top text-xs">{r.song.ccli ?? "—"}</td>
                    <td className="px-2 py-2 align-top">{r.slideCount}</td>
                    <td className="px-5 py-2 align-top">
                      <select
                        className="w-full rounded-md border border-input bg-background px-2 py-1 text-sm"
                        value={value}
                        onChange={(e) => {
                          const v = e.target.value;
                          setActions((a) => ({
                            ...a,
                            [r.key]: v.startsWith("attach:")
                              ? { kind: "attach", songId: v.slice(7) }
                              : { kind: v as "new" | "skip" },
                          }));
                        }}
                      >
                        <option value="new">Add as a new song</option>
                        {r.match && (
                          <option value={`attach:${r.match.id}`}>
                            Add chords to “{r.match.title}” (keeps its lyrics)
                          </option>
                        )}
                        <option value="skip">Skip</option>
                      </select>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          )}
          {showRows && sheets.length > 0 && (
            <LeadSheetTable
              sheets={sheets}
              chartRows={rows}
              chartActions={actions}
              library={librarySorted}
              actionOf={sheetAction}
              onChange={(key, a) => setSheetActions((s) => ({ ...s, [key]: a }))}
            />
          )}
          </details>
          )}
        </div>

        <footer className="flex items-center justify-between gap-3 border-t border-border px-5 py-3">
          <div className="text-xs text-destructive">{error}</div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={onClose}>
              {nothingFound ? "Close" : "Cancel"}
            </Button>
            {!nothingFound && <Button size="sm" onClick={() => void doImport()} disabled={busy || total + alreadyInLibrary === 0} data-testid="import-confirm">
              {total === 0
                ? `Add ${alreadyInLibrary} song${alreadyInLibrary === 1 ? "" : "s"} to the service`
                : chosenSheets.length === 0 && servicesToAdd.length === 0
                ? `Import ${chosen.length} song${chosen.length === 1 ? "" : "s"}`
                : chosen.length === 0
                  ? `Add ${chosenSheets.length} lead sheet${chosenSheets.length === 1 ? "" : "s"}`
                  : `Import ${total} item${total === 1 ? "" : "s"}`}
            </Button>}
          </div>
        </footer>
      </div>
    </div>
  );
}

/**
 * Lead sheets found in the import. Each attaches to one song as the score
 * follower's reference: the song it matched (CCLI number, then title), a
 * song this import creates, or any library song picked here.
 */
function LeadSheetTable({
  sheets,
  chartRows,
  chartActions,
  library,
  actionOf,
  onChange,
}: {
  sheets: LeadSheetRow[];
  chartRows: Row[];
  chartActions: Record<string, Action>;
  library: SongRow[];
  actionOf: (r: LeadSheetRow) => LeadSheetAction;
  onChange: (key: string, action: LeadSheetAction) => void;
}) {
  const newSongs = chartRows.filter((r) => chartActions[r.key]?.kind === "new");
  return (
    <div className="border-t border-border">
      <div className="px-5 pb-1 pt-3 text-xs text-muted-foreground">
        Lead sheets — the melody and chords of each line. Attached to a song, they let the AI follow the band by the music
        as well as the words. They don&rsquo;t change the song&rsquo;s slides.
      </div>
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-muted-foreground">
          <tr>
            <th className="px-5 py-2 font-medium">Lead sheet</th>
            <th className="px-2 py-2 font-medium">Key</th>
            <th className="px-2 py-2 font-medium">CCLI</th>
            <th className="px-2 py-2 font-medium">Bars</th>
            <th className="px-5 py-2 font-medium">Do</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {sheets.map((r) => {
            const a = actionOf(r);
            const value = a.kind === "attach" ? `attach:${a.songId}` : a.kind === "with_import" ? `with:${a.rowKey}` : "skip";
            return (
              <tr key={r.key} data-testid="lead-sheet-row">
                <td className="px-5 py-2 align-top">
                  <div className="font-medium">{r.sheet.title ?? r.fileName}</div>
                  <div className="text-xs text-muted-foreground">{r.fileName}</div>
                  {r.betterCopy && (
                    <div className="text-xs text-muted-foreground">Same song as {r.betterCopy.fileName} — that sheet is used</div>
                  )}
                  {!r.match && !r.importRow && (
                    <div className="text-xs text-amber-500">No song in the library matches — pick one, or import the song first</div>
                  )}
                </td>
                <td className="px-2 py-2 align-top">{r.sheet.key ?? "—"}</td>
                <td className="px-2 py-2 align-top text-xs">{r.sheet.ccli ?? "—"}</td>
                <td className="px-2 py-2 align-top">
                  {r.sheet.barCount}
                  {r.sheet.problemBars > 0 && (
                    <div className="text-xs text-amber-500" title="Bars whose notes didn't add up; the rest of the sheet is still used">
                      {r.sheet.problemBars} unread
                    </div>
                  )}
                </td>
                <td className="px-5 py-2 align-top">
                  <select
                    className="w-full rounded-md border border-input bg-background px-2 py-1 text-sm"
                    value={value}
                    onChange={(e) => {
                      const v = e.target.value;
                      onChange(
                        r.key,
                        v.startsWith("attach:")
                          ? { kind: "attach", songId: v.slice(7) }
                          : v.startsWith("with:")
                            ? { kind: "with_import", rowKey: v.slice(5) }
                            : { kind: "skip" },
                      );
                    }}
                  >
                    {r.match && <option value={`attach:${r.match.id}`}>Add to “{r.match.title}”</option>}
                    {newSongs.map((n) => (
                      <option key={n.key} value={`with:${n.key}`}>
                        Add to the new song “{n.song.title}”
                      </option>
                    ))}
                    <option value="skip">Skip</option>
                    <optgroup label="Add to another song…">
                      {library
                        .filter((s) => s.id !== r.match?.id)
                        .map((s) => (
                          <option key={s.id} value={`attach:${s.id}`}>
                            {s.title}
                          </option>
                        ))}
                    </optgroup>
                  </select>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** What the import will do, in counts — the whole decision for most imports. */
function ImportSummary({
  rows,
  actions,
  sheets,
  services,
  older,
  includeOlder,
  onIncludeOlder,
}: {
  rows: Row[];
  actions: Record<string, Action>;
  sheets: number;
  services: number;
  older: number;
  includeOlder: boolean;
  onIncludeOlder: (on: boolean) => void;
}) {
  let added = 0;
  let chords = 0;
  let have = 0;
  let copies = 0;
  for (const r of rows) {
    const a = actions[r.key]?.kind;
    if (a === "new") added += 1;
    else if (a === "attach") chords += 1;
    else if (r.betterCopy) copies += 1;
    else if (r.match) have += 1;
  }
  const n = (count: number, one: string, many = `${one}s`) => `${count} ${count === 1 ? one : many}`;
  const lines = [
    added > 0 && n(added, "new song"),
    chords > 0 && `Chords added to ${n(chords, "song")} you already have`,
    have > 0 && `${n(have, "song")} already in your library — left as they are`,
    copies > 0 && `${n(copies, "repeat copy", "repeat copies")} skipped`,
    sheets > 0 && n(sheets, "lead sheet"),
    services > 0 && n(services, includeOlder || older === 0 ? "service" : "recent service"),
  ].filter((l): l is string => typeof l === "string");
  if (lines.length === 0 && older === 0) return null;
  return (
    <div className="space-y-1 px-5 py-4 text-sm" data-testid="import-summary">
      {lines.map((l) => (
        <div key={l}>{l}</div>
      ))}
      {older > 0 && (
        <label className="flex items-center gap-2 pt-1 text-xs text-muted-foreground">
          <input type="checkbox" checked={includeOlder} onChange={(e) => onIncludeOlder(e.target.checked)} />
          Also bring {n(older, "older service")}
        </label>
      )}
    </div>
  );
}

/**
 * Recreate another app's setlists as services, in running order. One that's
 * already here by name (an import run twice) is left alone. Returns how many
 * were added.
 */
async function addServices(setlists: ImportedSetlist[], songIds: Map<string, string>): Promise<number> {
  const existing = new Set((await listSetlists()).map((s) => s.name.trim().toLowerCase()));
  let added = 0;
  for (const s of setlists) {
    const ids = s.songs.map((f) => songIds.get(f)).filter((id): id is string => id !== undefined);
    if (ids.length === 0 || existing.has(s.name.trim().toLowerCase())) continue;
    const id = await createSetlist(s.name);
    for (const songId of ids) await addSetlistItem(id, songId);
    existing.add(s.name.trim().toLowerCase());
    added += 1;
  }
  return added;
}
