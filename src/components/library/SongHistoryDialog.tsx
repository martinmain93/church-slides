import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { songRevision, songRevisions, type SongRevision } from "@/lib/tauri";

/**
 * Earlier words of a song in an organization's library: kept by the server
 * whenever two computers changed the words and one replaced the other
 * (docs/server-protocol, "Sync"). "Bring back" puts them in the editor, where
 * they're saved like any edit.
 */
export function SongHistoryDialog({
  songId,
  onRestore,
  onClose,
}: {
  songId: string;
  onRestore: (words: string) => void;
  onClose: () => void;
}) {
  const [list, setList] = useState<SongRevision[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<SongRevision | null>(null);

  useEffect(() => {
    let live = true;
    songRevisions(songId).then(
      (r) => live && setList(r.filter((x) => x.field === "body_text")),
      (e) => live && setError(e instanceof Error ? e.message : String(e)),
    );
    return () => {
      live = false;
    };
  }, [songId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function pick(r: SongRevision) {
    setError(null);
    try {
      setPicked(await songRevision(songId, r.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="song-history-title"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="flex max-h-full w-full max-w-2xl flex-col gap-3 rounded-lg border border-border bg-card p-5 text-foreground shadow-2xl">
        <h2 id="song-history-title" className="text-base font-semibold">
          Earlier words
        </h2>
        {error && <p className="text-sm text-destructive">{error}</p>}
        {list === null && !error && <p className="text-sm text-muted-foreground">Loading…</p>}
        {list?.length === 0 && (
          <p className="text-sm text-muted-foreground">
            None kept. Words are kept here when two computers change this song&rsquo;s words at once.
          </p>
        )}
        {list && list.length > 0 && (
          <div className="flex min-h-0 gap-3">
            <ul className="w-48 shrink-0 space-y-1 overflow-y-auto text-sm">
              {list.map((r) => (
                <li key={r.id}>
                  <button
                    type="button"
                    onClick={() => void pick(r)}
                    className={`w-full rounded-md px-2 py-1.5 text-left hover:bg-accent/40 ${picked?.id === r.id ? "bg-accent/60" : ""}`}
                  >
                    <div>{new Date(r.replacedAt).toLocaleString()}</div>
                    {r.replacedBy && <div className="text-xs text-muted-foreground">replaced by {r.replacedBy}</div>}
                  </button>
                </li>
              ))}
            </ul>
            <pre
              data-testid="song-history-words"
              className="max-h-96 min-h-40 flex-1 overflow-y-auto whitespace-pre-wrap rounded-md border border-border bg-background p-3 font-sans text-sm"
            >
              {picked?.value ?? <span className="text-muted-foreground">Pick one to see its words.</span>}
            </pre>
          </div>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
          <Button
            disabled={!picked?.value}
            onClick={() => {
              if (picked?.value) onRestore(picked.value);
              onClose();
            }}
          >
            Bring back
          </Button>
        </div>
      </div>
    </div>
  );
}
