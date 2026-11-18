import { useCallback, useEffect, useState } from "react";

import { useLibraryChanged } from "@/lib/assistantConnect";
import { listLibraries, openLibrary, type LibraryInfo } from "@/lib/tauri";
import { useToast } from "@/lib/toast";

/**
 * Which library Home and Library show: this computer's own, or an
 * organization's. Only there when this computer has more than one, so most
 * people never see it.
 */
export function LibrarySwitcher({ onSwitched }: { onSwitched: () => void }) {
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

  if (libraries.length < 2) return null;
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
    </div>
  );
}
