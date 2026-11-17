/**
 * Song files in, from the Import button or dropped on a screen: read them,
 * show the import preview, save. One flow behind the Library, Home and the
 * setlist editor, so a dropped file behaves the same wherever it lands.
 *
 * Whole-library files (a Church Slides backup, a ProPresenter `Export.txt`)
 * replace or merge the entire library, so only the Library takes them — a
 * stray drop on Home or a service never offers to wipe the library.
 */

import { useState, type DragEvent, type ReactNode } from "react";

import { ChartImportDialog } from "@/components/library/ChartImportDialog";
import { isWholeLibraryFile, readImportFiles, type ImportFileResult, type ReadProgress } from "@/lib/chords/import";
import { readLeadSheetWithEngine } from "@/lib/chords/import/leadSheet";
import type { ImportedSetlist } from "@/lib/otherApps";
import { listSongs, type SongRow } from "@/lib/tauri";
import { useToast } from "@/lib/toast";

interface Options {
  /** After saving: the toast message and the songs this import resolved to,
   * new or already in the library, in file order. */
  onImported: (message: string, songIds: string[]) => void;
  /** Only the Library passes this; elsewhere a whole-library file is refused. */
  onWholeLibrary?: (text: string) => void;
  /** The service the songs join (the setlist editor): said in the preview,
   * and a playlist file's own running order isn't made into new services. */
  serviceName?: string;
  /** What the drop overlay says. */
  dropHint: string;
}

export function useSongFileImport({ onImported, onWholeLibrary, serviceName, dropHint }: Options) {
  const toast = useToast();
  const [pending, setPending] = useState<{
    results: ImportFileResult[];
    library: SongRow[];
    duplicateFiles: number;
    setlists: ImportedSetlist[];
  } | null>(null);
  const [progress, setProgress] = useState<ReadProgress | null>(null);
  const [dragging, setDragging] = useState(false);

  async function importFiles(files: File[], fromFolder = false) {
    try {
      setProgress({ done: 0, total: files.length });
      const read = await readImportFiles(files, {
        isWholeLibrary: isWholeLibraryFile,
        onProgress: setProgress,
        songFilesOnly: fromFolder,
        readLeadSheet: readLeadSheetWithEngine,
      });
      setProgress(null);
      if (read.wholeLibraryText !== null) {
        if (onWholeLibrary) onWholeLibrary(read.wholeLibraryText);
        else toast.error("That's a whole-library file — import it from the Library.");
        return;
      }
      if (read.results.length === 0) {
        toast.error("No song files found there.");
        return;
      }
      const library = await listSongs();
      setPending({
        results: read.results,
        library,
        duplicateFiles: read.duplicateFiles,
        setlists: serviceName === undefined ? read.setlists : [],
      });
    } catch (err) {
      setProgress(null);
      toast.error(`Import failed: ${err instanceof Error ? err.message : err}`);
    }
  }

  // Only files from outside the app: dragging a song or slide around inside
  // a screen carries no files and is left to that screen.
  const dropProps = {
    onDragOver: (e: DragEvent) => {
      if (!e.dataTransfer.types.includes("Files")) return;
      e.preventDefault();
      setDragging(true);
    },
    onDragLeave: (e: DragEvent) => {
      // Leaving for a child element fires too; only the zone's own edge counts.
      const next = e.relatedTarget as Node | null;
      if (!next || !(e.currentTarget as Node).contains(next)) setDragging(false);
    },
    onDrop: (e: DragEvent) => {
      setDragging(false);
      if (e.dataTransfer.files.length === 0) return;
      e.preventDefault();
      void importFiles(Array.from(e.dataTransfer.files));
    },
  };

  const ui: ReactNode = (
    <>
      {dragging && (
        <div className="pointer-events-none fixed inset-0 z-40 flex items-center justify-center border-2 border-dashed border-primary bg-background/80 p-8 text-center text-sm">
          {dropHint}
        </div>
      )}
      {progress && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-background/80">
          <div className="w-72 rounded-lg border border-border bg-background p-4 text-sm shadow-xl" role="status">
            <div>
              Reading song files… {progress.done} of {progress.total}
            </div>
            <div className="mt-2 h-1.5 overflow-hidden rounded bg-muted">
              <div
                className="h-full bg-primary transition-[width]"
                style={{ width: `${progress.total ? (progress.done / progress.total) * 100 : 0}%` }}
              />
            </div>
          </div>
        </div>
      )}
      {pending && (
        <ChartImportDialog
          results={pending.results}
          library={pending.library}
          duplicateFiles={pending.duplicateFiles}
          setlists={pending.setlists}
          serviceName={serviceName}
          onClose={() => setPending(null)}
          onImported={(message, songIds) => {
            setPending(null);
            onImported(message, songIds);
          }}
        />
      )}
    </>
  );

  return { importFiles, dropProps, ui };
}
