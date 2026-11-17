/**
 * "Bring your songs": import the library and services of another
 * presentation app on this computer, through the same preview as song files.
 *
 * From the first-run prompt it says plainly what will be read before reading
 * anything (macOS asks too, for apps that keep songs in Documents). From the
 * Library's Import menu, choosing "From ProPresenter" already said so, and
 * reading starts at once (`start`).
 */

import { useEffect, useRef, useState } from "react";
import { Loader2, X } from "lucide-react";

import { ChartImportDialog } from "@/components/library/ChartImportDialog";
import { Button } from "@/components/ui/button";
import { readOtherApp, type DetectedApp, type OtherAppImport } from "@/lib/otherApps";
import { listSongs, type SongRow } from "@/lib/tauri";

type Step =
  | { kind: "choose" }
  | { kind: "reading"; app: DetectedApp }
  | { kind: "preview"; app: DetectedApp; data: OtherAppImport; library: SongRow[] };

export function BringSongsDialog({
  apps,
  start,
  onClose,
  onImported,
}: {
  /** The apps found on this computer (`detectOtherApps`). */
  apps: DetectedApp[];
  /** Read this one straight away, without the explaining screen. */
  start?: DetectedApp;
  onClose: () => void;
  onImported: (message: string) => void;
}) {
  const [chosen, setChosen] = useState<DetectedApp | null>(start ?? apps[0] ?? null);
  const [step, setStep] = useState<Step>(start ? { kind: "reading", app: start } : { kind: "choose" });
  const [error, setError] = useState<string | null>(null);

  const started = useRef(false);
  useEffect(() => {
    if (!start || started.current) return;
    started.current = true;
    void read(start);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, on open
  }, []);

  async function read(app: DetectedApp) {
    setError(null);
    setStep({ kind: "reading", app });
    try {
      const [data, library] = await Promise.all([readOtherApp(app.id), listSongs()]);
      if (data.results.every((r) => r.songs.length === 0)) {
        setError(`No songs found in ${app.name}.`);
        setStep({ kind: "choose" });
        return;
      }
      setStep({ kind: "preview", app, data, library });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setStep({ kind: "choose" });
    }
  }

  if (step.kind === "preview") {
    return (
      <ChartImportDialog
        results={step.data.results}
        setlists={step.data.setlists}
        source={step.app.name}
        library={step.library}
        onClose={onClose}
        onImported={onImported}
      />
    );
  }

  const reading = step.kind === "reading";
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label="Bring your songs"
    >
      <div className="w-full max-w-md rounded-lg border border-border bg-card p-5 text-foreground shadow-2xl">
        <div className="flex items-start justify-between gap-3">
          <h2 className="text-base font-semibold">Bring your songs</h2>
          <button type="button" onClick={onClose} aria-label="Close" className="text-muted-foreground hover:text-foreground">
            <X className="size-4" />
          </button>
        </div>

        {start && reading ? (
          <p className="mt-3 flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> Reading songs and services from {start.name}…
          </p>
        ) : (
          <>
            <p className="mt-2 text-sm text-muted-foreground">
              Copy songs and services from {apps.length === 1 ? apps[0]!.name : "another app"} into Church Slides.
            </p>
            {apps.length > 1 && (
              <div className="mt-4 grid gap-2" role="radiogroup" aria-label="App">
                {apps.map((a) => (
                  <label key={a.id} className="flex cursor-pointer items-center gap-2 rounded-md border border-border px-3 py-2 text-sm">
                    <input type="radio" name="other-app" checked={chosen?.id === a.id} onChange={() => setChosen(a)} />
                    {a.name}
                  </label>
                ))}
              </div>
            )}
            <p className="mt-4 text-xs text-muted-foreground">
              Church Slides only reads {chosen?.name}&rsquo;s songs and services on this computer — nothing in it
              is changed, nothing is uploaded, and you&rsquo;ll see everything before it&rsquo;s saved.
            </p>
          </>
        )}

        {error && <p className="mt-3 text-sm text-destructive">{error}</p>}

        <div className="mt-5 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={onClose}>
            {start ? "Cancel" : "Not now"}
          </Button>
          {!(start && reading) && (
            <Button size="sm" disabled={!chosen || reading} onClick={() => chosen && void read(chosen)}>
              {reading ? (
                <>
                  <Loader2 className="size-4 animate-spin" /> Reading {step.app.name}…
                </>
              ) : (
                `Import from ${chosen?.name ?? ""}`
              )}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
