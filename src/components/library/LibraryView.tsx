import { useEffect, useMemo, useState } from "react";
import { AppWindow, BookOpen, ChevronDown, Download, FileText, FolderOpen, Plus, Search, Trash2, Type, Upload } from "lucide-react";

import {
  deleteReading,
  exportLibrary,
  importLibrary,
  listChartMeta,
  listSongReferences,
  listReadings,
  listSongsSorted,
  type ChartMetaRow,
  type ImportMode,
  type ReadingRow,
  type SongRow,
  type SortKey,
} from "@/lib/tauri";
import { downloadText, pickFiles, timestampedFilename } from "@/lib/files";
import { parseSong } from "@/lib/parseSong";
import {
  looksLikeProPresenterExport,
  parseProPresenterExport,
  toLibraryExport,
} from "@/lib/propresenterImport";
import { IMPORT_ACCEPT } from "@/lib/chords/import";
import { detectOtherApps, type DetectedApp } from "@/lib/otherApps";
import { BringSongsDialog } from "@/components/library/BringSongsDialog";
import { useSongFileImport } from "@/components/library/useSongFileImport";
import { HymnBrowserDialog } from "@/components/library/HymnBrowserDialog";
import { KeyBadge } from "@/components/KeyBadge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Menu, MenuItem, MenuSeparator } from "@/components/ui/Menu";
import { useConfirm } from "@/lib/confirm";
import { useToast } from "@/lib/toast";
import { cn } from "@/lib/utils";
import { withoutPart } from "@/lib/smartSearch/parseQuery";
import { useSmartSearch } from "@/lib/smartSearch/useSmartSearch";
import { SearchUnderstood } from "@/components/library/SearchUnderstood";

interface Props {
  onOpenSong: (id: string | null) => void;
  reloadKey?: number;
  onAfterImport: () => void;
}

const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  { value: "alphabetical", label: "A–Z" },
  { value: "recently_edited", label: "Recently edited" },
  { value: "popular", label: "Popular" },
];

export function LibraryView({ onOpenSong, reloadKey = 0, onAfterImport }: Props) {
  const [query, setQuery] = useState("");
  // Readings (a creed, a prayer) saved from a service. The switch between
  // them and the songs only appears once there is one.
  const [readings, setReadings] = useState<ReadingRow[]>([]);
  const [showing, setShowing] = useState<"songs" | "readings">("songs");
  useEffect(() => {
    listReadings()
      .then(setReadings)
      .catch(() => undefined);
  }, [reloadKey]);
  const showReadings = showing === "readings" && readings.length > 0;
  const q = query.trim().toLowerCase();
  const visibleReadings = readings.filter(
    (r) => q === "" || r.title.toLowerCase().includes(q) || r.body_text.toLowerCase().includes(q),
  );

  async function handleDeleteReading(r: ReadingRow) {
    const ok = await confirm({
      title: `Delete "${r.title}"?`,
      message: "It goes from the library. Services that already use it keep their copy.",
      confirmLabel: "Delete",
      variant: "destructive",
    });
    if (!ok) return;
    try {
      await deleteReading(r.id);
      setReadings((all) => all.filter((x) => x.id !== r.id));
    } catch (err) {
      toast.error(`Delete failed: ${err instanceof Error ? err.message : err}`);
    }
  }
  const [sort, setSort] = useState<SortKey>("alphabetical");
  const [songs, setSongs] = useState<SongRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();
  const confirm = useConfirm();
  const { importFiles, dropProps, ui: importUi } = useSongFileImport({
    onImported: (message) => {
      toast.success(message);
      onAfterImport();
    },
    onWholeLibrary: (text) => void importWholeLibrary(text),
    dropHint:
      "Drop song files to import — PDF chord sheets, ChordPro, OnSong, OpenLyrics, OpenSong, SongSelect, plain text, or ProPresenter and OpenLP files",
  });
  const [addingHymns, setAddingHymns] = useState(false);
  // Other presentation apps on this computer, offered at the top of Import.
  const [otherApps, setOtherApps] = useState<DetectedApp[]>([]);
  const [bringingFrom, setBringingFrom] = useState<DetectedApp | null>(null);
  useEffect(() => {
    detectOtherApps().then(setOtherApps).catch(() => undefined);
  }, []);
  // Song id → the chart's key and whether it has chords, for songs with one.
  const [charts, setCharts] = useState<Record<string, ChartMetaRow>>({});
  useEffect(() => {
    listChartMeta().then(setCharts).catch(() => setCharts({}));
  }, [reloadKey]);
  // Songs with a lead sheet: the score follower can follow them by the music.
  const [leadSheets, setLeadSheets] = useState<Set<string>>(new Set());
  useEffect(() => {
    listSongReferences()
      .then((refs) => setLeadSheets(new Set(refs.filter((r) => r.kind === "lead_sheet").map((r) => r.song_id))))
      .catch(() => setLeadSheets(new Set()));
  }, [reloadKey]);

  // An empty box lists the library in the chosen order; anything typed goes
  // through the one search (titles, lyrics, topics, filters).
  const searching = query.trim() !== "";
  useEffect(() => {
    if (searching) return;
    let cancelled = false;
    setError(null);
    listSongsSorted(sort)
      .then((rows) => {
        if (!cancelled) setSongs(rows);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
        setSongs([]);
      });
    return () => {
      cancelled = true;
    };
  }, [searching, sort, reloadKey]);
  const { result: found, error: searchError } = useSmartSearch(query, reloadKey);
  const shown = searching ? (found?.songs ?? null) : songs;

  async function handleExport() {
    try {
      const json = await exportLibrary();
      downloadText(timestampedFilename("church-slides-library", "json"), json);
      toast.success("Library exported.");
    } catch (err) {
      toast.error(`Export failed: ${err instanceof Error ? err.message : err}`);
    }
  }

  async function handleImport() {
    const files = await pickFiles(IMPORT_ACCEPT);
    if (files.length > 0) await importFiles(files);
  }

  async function handleImportFolder() {
    const files = await pickFiles(IMPORT_ACCEPT, true, true);
    if (files.length > 0) await importFiles(files, true);
  }

  async function importWholeLibrary(text: string) {
    try {
      // Auto-detect: ProPresenter `.txt` exports start with `Title:`
      // (possibly BOM-prefixed); Church Slides library JSON starts
      // with `{`. We convert ProPresenter exports to the same library
      // JSON shape and hand off to the existing `import_library`
      // Tauri command.
      let jsonToImport: string;
      let detected: "library" | "propresenter";
      if (looksLikeProPresenterExport(text)) {
        const report = parseProPresenterExport(text);
        if (report.songs.length === 0) {
          toast.error(
            "No songs found in this ProPresenter export. Is the file empty?",
          );
          return;
        }
        const skippedSuffix = report.skipped_empty
          ? `, ${report.skipped_empty} placeholder${
              report.skipped_empty === 1 ? "" : "s"
            } skipped`
          : "";
        const renamedSuffix = report.duplicate_titles_renamed.length
          ? `, ${report.duplicate_titles_renamed.length} duplicate title${
              report.duplicate_titles_renamed.length === 1 ? "" : "s"
            } renamed`
          : "";
        const proceed = await confirm({
          title: "Detected ProPresenter export",
          message:
            `Import ${report.songs.length} song(s)${skippedSuffix}${renamedSuffix}?\n\n` +
            "ProPresenter exports don't include section labels — we'll guess [chorus]/[verse] from repeated stanzas. You can re-label after import.",
          confirmLabel: "Import",
        });
        if (!proceed) return;
        jsonToImport = JSON.stringify(toLibraryExport(report));
        detected = "propresenter";
      } else {
        jsonToImport = text;
        detected = "library";
      }

      const replace = await confirm({
        title: "Replace your existing library?",
        message: "Replace wipes the current library; Merge adds or updates rows. Merge is usually what you want.",
        confirmLabel: "Replace",
        cancelLabel: "Merge",
        variant: "destructive",
      });
      const mode: ImportMode = replace ? "replace" : "merge";
      const summary = await importLibrary(jsonToImport, mode);
      const prefix = detected === "propresenter" ? "ProPresenter import: " : "";
      toast.success(
        `${prefix}imported ${summary.songs_imported} new song(s), updated ${summary.songs_replaced}.`,
      );
      onAfterImport();
    } catch (err) {
      toast.error(`Import failed: ${err instanceof Error ? err.message : err}`);
    }
  }

  return (
    <div className="relative flex h-full flex-col" {...dropProps}>
      {importUi}
      {addingHymns && (
        <HymnBrowserDialog
          onClose={() => setAddingHymns(false)}
          onAdded={onAfterImport}
        />
      )}
      {bringingFrom && (
        <BringSongsDialog
          apps={otherApps}
          start={bringingFrom}
          onClose={() => setBringingFrom(null)}
          onImported={(message) => {
            setBringingFrom(null);
            toast.success(message);
            onAfterImport();
          }}
        />
      )}
      <header className="flex flex-wrap items-center gap-3 border-b border-border bg-background px-6 py-3">
        <div className="relative flex-1 min-w-[180px] max-w-md">
          <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={showReadings ? "Search readings…" : "Search songs, lyrics or topics…"}
            className="h-9 pl-9"
          />
        </div>
        {readings.length > 0 && (
          <div className="flex rounded-md border border-input p-0.5 text-xs" role="tablist">
            {(["songs", "readings"] as const).map((id) => (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={showing === id}
                onClick={() => setShowing(id)}
                className={cn(
                  "rounded px-2.5 py-1 font-medium",
                  showing === id ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:text-foreground",
                )}
              >
                {id === "songs" ? "Songs" : "Readings"}
              </button>
            ))}
          </div>
        )}
        {!showReadings && (
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value as SortKey)}
            disabled={query.trim() !== ""}
            aria-label="Sort songs"
            className="h-9 rounded-md border border-input bg-background px-3 text-sm disabled:opacity-50"
            title={query.trim() !== "" ? "Clear search to change sort" : undefined}
          >
            {SORT_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        )}
        <Menu
          align="right"
          panelClassName="w-80"
          trigger={(toggle, open) => (
            <Button
              variant="outline"
              size="sm"
              onClick={toggle}
              aria-haspopup="menu"
              aria-expanded={open}
              title="Bring songs in from files — or drop them anywhere here"
            >
              <Upload className="size-4" />
              Import
              <ChevronDown className="size-3.5 opacity-60" />
            </Button>
          )}
        >
          {otherApps.map((app) => (
            <MenuItem
              key={app.id}
              icon={<AppWindow />}
              onSelect={() => setBringingFrom(app)}
              hint={`Its songs and services on this computer — ${app.name} itself isn't changed`}
            >
              From {app.name}
            </MenuItem>
          ))}
          {otherApps.length > 0 && <MenuSeparator />}
          <MenuItem
            icon={<FileText />}
            onSelect={handleImport}
            hint="PDF chord sheets, ChordPro, OnSong, OpenLyrics, OpenSong, SongSelect, text, a library backup, ProPresenter songs and playlists, or OpenLP services"
          >
            Song files…
          </MenuItem>
          <MenuItem
            icon={<FolderOpen />}
            onSelect={handleImportFolder}
            hint="Every song file in a folder and its subfolders"
          >
            A whole folder…
          </MenuItem>
          <MenuItem
            icon={<BookOpen />}
            onSelect={() => setAddingHymns(true)}
            hint="Classic hymns from the Open Hymnal Project, each marked with whether it's free to use"
          >
            Hymns from the Open Hymnal…
          </MenuItem>
        </Menu>
        <Button onClick={() => onOpenSong(null)} size="sm">
          <Plus className="size-4" />
          New song
        </Button>
        <Menu label="More options">
          <MenuItem
            icon={<Download />}
            onSelect={handleExport}
            hint="Every song in one file — a backup, or to move to another computer"
          >
            Export library…
          </MenuItem>
        </Menu>
      </header>

      {(searching ? searchError : error) && (
        <div className="border-b border-destructive/40 bg-destructive/10 px-6 py-2 text-xs text-destructive">
          {searching ? searchError : error}
        </div>
      )}
      {searching && found && <SearchUnderstood result={found} onRemove={(part) => setQuery(withoutPart(query, part))} />}

      <div className="flex-1 overflow-y-auto">
        {showReadings && (
          <>
            <p className="border-b border-border px-6 py-2 text-xs text-muted-foreground">
              Saved from a service with "Save to library". Add one to a service from its Songs search.
            </p>
            <ul className="divide-y divide-border" data-testid="library-readings">
              {visibleReadings.map((r) => (
                <li key={r.id} className="group flex items-center gap-4 px-6 py-3">
                  <Type className="size-4 shrink-0 text-violet-400" aria-hidden />
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-medium">{r.title}</div>
                    <div className="truncate text-xs text-muted-foreground">{firstLine(r.body_text)}</div>
                  </div>
                  <button
                    type="button"
                    onClick={() => void handleDeleteReading(r)}
                    title="Delete from the library"
                    aria-label={`Delete ${r.title}`}
                    className="rounded p-1 text-muted-foreground opacity-0 transition-opacity hover:text-destructive focus-visible:opacity-100 group-hover:opacity-100"
                  >
                    <Trash2 className="size-4" />
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
        {!showReadings && shown === null && (
          <div className="px-6 py-8 text-sm text-muted-foreground">Loading library…</div>
        )}
        {!showReadings && shown?.length === 0 && (
          <EmptyState
            onOpenSong={onOpenSong}
            onImport={handleImport}
            onAddHymns={() => setAddingHymns(true)}
            querying={searching}
          />
        )}
        {!showReadings && shown && shown.length > 0 && (
          <ul className="divide-y divide-border">
            {shown.map((song) => (
              <SongRowItem
                key={song.id}
                song={song}
                chartKey={charts[song.id]?.key ?? null}
                hasChords={charts[song.id]?.has_chords ?? false}
                hasLeadSheet={leadSheets.has(song.id)}
                matched={searching ? found?.reasons[song.id] : undefined}
                onOpen={() => onOpenSong(song.id)}
              />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function SongRowItem({
  song,
  chartKey,
  hasChords,
  hasLeadSheet = false,
  matched,
  onOpen,
}: {
  song: SongRow;
  /** The chart's written key, if it has one. */
  chartKey: string | null;
  hasChords: boolean;
  hasLeadSheet?: boolean;
  /** The passage a topic search matched, shown instead of the first line. */
  matched?: string;
  onOpen: () => void;
}) {
  const preview = useMemo(() => matched ?? firstLine(song.body_text), [matched, song.body_text]);
  const slideCount = useMemo(() => parseSong(song.body_text).slides.length, [song.body_text]);

  return (
    <li>
      <button
        type="button"
        onClick={onOpen}
        className="flex w-full items-center gap-4 px-6 py-3 text-left transition-colors hover:bg-accent/40 focus-visible:bg-accent/40 focus-visible:outline-none"
      >
        <div className="min-w-0 flex-1">
          <div className="truncate font-medium">{song.title || "Untitled"}</div>
          {preview && (
            <div
              className={`truncate text-xs text-muted-foreground${matched ? " italic" : ""}`}
              title={matched ? "Why this song came up" : undefined}
            >
              {preview}
            </div>
          )}
        </div>
        {hasChords && (
          <span
            className="rounded bg-sky-500/15 px-1.5 py-0.5 text-[11px] font-medium text-sky-400"
            title="Has a chord chart for band screens"
          >
            chords
          </span>
        )}
        {chartKey && (
          <KeyBadge songKey={chartKey} className="size-6 text-[11px]" />
        )}
        {hasLeadSheet && (
          <span
            className="rounded bg-violet-500/15 px-1.5 py-0.5 text-[11px] font-medium text-violet-400"
            title="Has a lead sheet: the AI can follow this song by its melody and chords"
          >
            lead sheet
          </span>
        )}
        <div className="text-xs text-muted-foreground">
          {slideCount} {slideCount === 1 ? "slide" : "slides"}
        </div>
      </button>
    </li>
  );
}

function firstLine(body: string): string {
  for (const line of body.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    if (trimmed.startsWith("[") && trimmed.endsWith("]")) continue;
    return trimmed;
  }
  return "";
}

function EmptyState({
  onOpenSong,
  onImport,
  onAddHymns,
  querying,
}: {
  onOpenSong: (id: string | null) => void;
  onImport: () => void;
  onAddHymns: () => void;
  querying: boolean;
}) {
  if (querying) {
    return (
      <div className="px-6 py-12 text-center text-sm text-muted-foreground">
        No matching songs.
      </div>
    );
  }
  return (
    <div className="flex flex-col items-center gap-4 px-6 py-16 text-center">
      <div className="text-sm text-muted-foreground">Your library is empty.</div>
      <div className="flex gap-3">
        <Button onClick={() => onOpenSong(null)} size="sm">
          <Plus className="size-4" />
          Create your first song
        </Button>
        <Button onClick={onImport} variant="outline" size="sm">
          <Upload className="size-4" />
          Import library
        </Button>
        <Button onClick={onAddHymns} variant="outline" size="sm">
          <BookOpen className="size-4" />
          Add hymns
        </Button>
      </div>
    </div>
  );
}
