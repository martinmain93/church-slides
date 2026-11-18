import { useCallback, useEffect, useState } from "react";

import { useLibraryChanged } from "@/lib/assistantConnect";
import { syncNote } from "@/lib/librarySync";
import { listLibraries, openLibrary, type LibraryInfo, type LibrarySyncStatus } from "@/lib/tauri";
import { useToast } from "@/lib/toast";

/**
 * Which library Home and Library show: this computer's own, or an
 * organization's. Only there when this computer has more than one, so most
 * people never see it. Under it, an organization library's sync status, only
 * when something's waiting or wrong.
 */
export function LibrarySwitcher({ onSwitched, sync }: { onSwitched: () => void; sync?: LibrarySyncStatus }) {
  const toast = useToast();
  const [libraries, setLibraries] = useState<LibraryInfo[]>([]);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    listLibraries()
      .then(setLibraries)
      .catch(() => setLibraries([]));
  }, []);
  useEffect(load, [load]);
  useLibraryChanged(load);
  // Libraries can come and go outside the app (a folder removed, another
  // computer's sync): check again whenever the window comes back.
  useEffect(() => {
    window.addEventListener("focus", load);
    return () => window.removeEventListener("focus", load);
  }, [load]);

  const note = sync ? syncNote(sync) : null;
  const noteLine = note && (
    <p
      data-testid="library-sync-note"
      className={`mt-1 px-1 text-xs ${note.tone === "warn" ? "text-amber-600 dark:text-amber-400" : "text-muted-foreground"}`}
    >
      {note.text}
    </p>
  );
  if (libraries.length < 2) return noteLine ? <div className="px-3 pb-2">{noteLine}</div> : null;
  const open = libraries.find((l) => l.open);

  return (
    <div className="px-3 pb-2">
      <select
        aria-label="Library"
        value={open?.id ?? ""}
        disabled={busy}
        onChange={async (e) => {
          setBusy(true);
          try {
            await openLibrary(e.target.value);
            load();
            onSwitched();
          } catch (err) {
            toast.error(`Couldn't open that library: ${err instanceof Error ? err.message : err}`);
          } finally {
            setBusy(false);
          }
        }}
        className="h-8 w-full truncate rounded-md border border-input bg-background px-2 text-sm font-medium"
        data-testid="library-switcher"
      >
        {libraries.map((l) => (
          <option key={l.id} value={l.id}>
            {l.name}
          </option>
        ))}
      </select>
      {noteLine}
    </div>
  );
}
