import { useEffect, useMemo, useState } from "react";
import { ChevronDown, Copy, Library as LibraryIcon, Play, Plus, Search, Trash2 } from "lucide-react";

import { deleteSetlist, listSetlists, listSongs, type SetlistRow } from "@/lib/tauri";
import {
  clearLastSession,
  loadLastSession,
  type LastSession,
} from "@/lib/presentation/persistence";
import { SpeechModelStatus } from "@/components/home/SpeechModelStatus";
import { useSongFileImport } from "@/components/library/useSongFileImport";
import { PeerSyncButton } from "@/components/peerSync/PeerSyncButton";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useConfirm } from "@/lib/confirm";
import { useToast } from "@/lib/toast";

const RECENT_LIMIT = 6;

interface Props {
  reloadKey?: number;
  onCreateSetlist: () => void;
  /** A new setlist starting as a copy of this one ("Start from last service"). */
  onCopySetlist: (id: string) => void;
  onOpenSetlist: (id: string) => void;
  onContinueService: (
    setlistId: string,
    jumpTo: { itemIdx: number; slideIdx: number },
  ) => void;
  onOpenLibrary: () => void;
  /** Start a setlist straight from its row. */
  onStartService: (setlistId: string) => void;
}

export function HomeScreen({
  reloadKey = 0,
  onCreateSetlist,
  onCopySetlist,
  onOpenSetlist,
  onContinueService,
  onOpenLibrary,
  onStartService,
}: Props) {
  const toast = useToast();
  const confirm = useConfirm();
  const [setlists, setSetlists] = useState<SetlistRow[] | null>(null);
  const [songCount, setSongCount] = useState<number | null>(null);
  const [lastSession, setLastSession] = useState<LastSession | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [showAll, setShowAll] = useState(false);
  // A setlist got from another computer: this screen's own write, which the
  // library watcher doesn't echo.
  const [synced, setSynced] = useState(0);
  // The most recent setlist with anything in it: the weekly order of worship
  // to start the next one from.
  const lastService = setlists?.find((s) => (s.song_titles?.length ?? 0) > 0) ?? null;
  // Song files dropped anywhere on Home go into the library.
  const { dropProps, ui: importUi } = useSongFileImport({
    onImported: (message) => {
      toast.success(message);
      setSynced((n) => n + 1);
    },
    dropHint: "Drop song files to add them to the library",
  });

  useEffect(() => {
    let cancelled = false;
    setLastSession(loadLastSession());
    Promise.all([listSetlists(), listSongs()])
      .then(([sl, songs]) => {
        if (cancelled) return;
        setSetlists(sl);
        setSongCount(songs.length);
      })
      .catch((err) => {
        if (cancelled) return;
        toast.error(`Could not load home: ${err instanceof Error ? err.message : err}`);
      });
    return () => {
      cancelled = true;
    };
  }, [toast, reloadKey, synced]);

  const lastSessionSetlist = lastSession
    ? setlists?.find((s) => s.id === lastSession.setlistId)
    : null;

  const filteredSetlists = useMemo(() => {
    if (!setlists) return null;
    const q = searchQuery.trim().toLowerCase();
    if (!q) return showAll ? setlists : setlists.slice(0, RECENT_LIMIT);
    return setlists.filter((sl) => {
      const nameMatch = (sl.name || "").toLowerCase().includes(q);
      const songMatch = (sl.song_titles ?? []).some((t) => t.toLowerCase().includes(q));
      return nameMatch || songMatch;
    });
  }, [setlists, searchQuery, showAll]);

  async function handleDeleteSetlist(sl: SetlistRow) {
    const titles = sl.song_titles ?? [];
    if (titles.length > 0) {
      const ok = await confirm({
        title: `Delete "${sl.name || "this setlist"}"?`,
        message: "This can't be undone. (The songs themselves stay in your library.)",
        confirmLabel: "Delete",
        variant: "destructive",
      });
      if (!ok) return;
    }
    try {
      await deleteSetlist(sl.id);
      setSetlists((prev) => (prev ? prev.filter((item) => item.id !== sl.id) : prev));
      if (lastSession?.setlistId === sl.id) {
        clearLastSession();
        setLastSession(null);
      }
    } catch (err) {
      toast.error(`Delete failed: ${err instanceof Error ? err.message : err}`);
    }
  }

  return (
    <div className="flex h-full flex-col overflow-y-auto" {...dropProps}>
      {importUi}
      <header className="border-b border-border bg-background px-6 py-4">
        <h1 className="text-base font-semibold">Home</h1>
      </header>
      <div className="mx-auto w-full max-w-3xl space-y-8 px-6 py-8">
        <SpeechModelStatus />
        {lastSession && lastSessionSetlist && (
          <ContinueLastService
            session={lastSession}
            name={lastSessionSetlist.name}
            onContinue={() =>
              onContinueService(lastSession.setlistId, {
                itemIdx: lastSession.itemIdx,
                slideIdx: lastSession.slideIdx,
              })
            }
            onDismiss={() => {
              clearLastSession();
              setLastSession(null);
            }}
          />
        )}
        <Section
          title="Recent setlists"
          action={
            <div className="flex items-center gap-2">
              {/* Always there while this computer has no setlists — that's
                  when one is most likely coming from another computer. */}
              {setlists !== null && (
                <PeerSyncButton
                  onMessage={(m, kind) => (kind === "error" ? toast.error(m) : toast.success(m))}
                  onImported={() => setSynced((n) => n + 1)}
                  hideWhenAlone={setlists.length > 0}
                  align="right"
                />
              )}
              {lastService && (
                <Button
                  onClick={() => onCopySetlist(lastService.id)}
                  size="sm"
                  variant="ghost"
                  title={`A new setlist with everything in "${lastService.name}" — songs, readings and edits — to change from there`}
                >
                  <Copy className="size-4" />
                  Start from last service
                </Button>
              )}
              <Button onClick={onCreateSetlist} size="sm">
                <Plus className="size-4" />
                New setlist
              </Button>
            </div>
          }
        >
          {setlists === null && <Loading />}
          {setlists?.length === 0 && (
            <EmptySetlists onCreate={onCreateSetlist} />
          )}
          {setlists && setlists.length > 0 && (
            <div className="space-y-2">
              {setlists.length > 3 && (
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder="Search setlists by name or song…"
                    aria-label="Search setlists"
                    className="h-9 pl-9"
                  />
                </div>
              )}
              {filteredSetlists && filteredSetlists.length === 0 ? (
                <div className="rounded-lg border border-border bg-card px-4 py-6 text-center text-xs text-muted-foreground">
                  No setlists match &ldquo;{searchQuery}&rdquo;.
                </div>
              ) : (
                <ul className="divide-y divide-border rounded-lg border border-border bg-card">
                  {(filteredSetlists ?? []).map((sl) => {
                    const titles = sl.song_titles ?? [];
                    return (
                      <li key={sl.id} className="group flex items-center gap-2 pr-3">
                        <button
                          type="button"
                          onClick={() => onOpenSetlist(sl.id)}
                          className="flex min-w-0 flex-1 items-center gap-4 px-4 py-3 text-left transition-colors hover:bg-accent/40 focus-visible:bg-accent/40 focus-visible:outline-none"
                        >
                          <div className="min-w-0 flex-1">
                            <div className="flex items-baseline gap-2">
                              <span className="truncate font-medium">{sl.name || "Untitled"}</span>
                              <span className="shrink-0 text-xs text-muted-foreground">
                                {sl.last_used_at
                                  ? `used ${formatRelative(sl.last_used_at)}`
                                  : `created ${formatRelative(sl.created_at)}`}
                              </span>
                            </div>
                            <div className="truncate text-xs text-muted-foreground">
                              {titles.length === 0
                                ? "No songs yet"
                                : `${countLabel(sl.song_count ?? titles.length, titles.length)} · ${titles.join(" · ")}`}
                            </div>
                          </div>
                        </button>
                        <button
                          type="button"
                          onClick={() => void handleDeleteSetlist(sl)}
                          title={`Delete ${sl.name || "setlist"}`}
                          aria-label={`Delete ${sl.name || "setlist"}`}
                          className="rounded p-1.5 text-muted-foreground opacity-0 transition-opacity hover:bg-destructive/10 hover:text-destructive focus-visible:opacity-100 group-hover:opacity-100"
                        >
                          <Trash2 className="size-3.5" />
                        </button>
                        {titles.length > 0 && (
                          <Button
                            size="sm"
                            variant="outline"
                            className="shrink-0 gap-1"
                            onClick={() => onStartService(sl.id)}
                            title="Start this service"
                          >
                            <Play className="size-3.5" />
                            Start
                          </Button>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
              {searchQuery.trim() === "" && setlists.length > RECENT_LIMIT && (
                <button
                  type="button"
                  onClick={() => setShowAll((v) => !v)}
                  className="flex w-full items-center justify-center gap-1 rounded-md py-1.5 text-xs text-muted-foreground transition-colors hover:bg-accent/40 hover:text-foreground"
                >
                  <ChevronDown className={`size-3.5 transition-transform ${showAll ? "rotate-180" : ""}`} />
                  {showAll ? "Show fewer" : `Show all ${setlists.length} setlists`}
                </button>
              )}
            </div>
          )}
        </Section>

        <Section title="Library">
          <button
            type="button"
            onClick={onOpenLibrary}
            className="flex w-full items-center gap-4 rounded-lg border border-border bg-card px-4 py-4 text-left transition-colors hover:bg-accent/40 focus-visible:bg-accent/40 focus-visible:outline-none"
          >
            <LibraryIcon className="size-5 text-muted-foreground" />
            <div className="flex-1">
              <div className="text-sm font-medium">Open library</div>
              <div className="text-xs text-muted-foreground">
                {songCount === null
                  ? "Loading…"
                  : `${songCount} song${songCount === 1 ? "" : "s"} in your library`}
              </div>
            </div>
          </button>
        </Section>
      </div>
    </div>
  );
}

function Section({
  title,
  action,
  children,
}: {
  title: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          {title}
        </h2>
        {action}
      </div>
      {children}
    </section>
  );
}

function Loading() {
  return <div className="rounded-lg bg-card px-4 py-8 text-center text-sm text-muted-foreground">Loading…</div>;
}

function EmptySetlists({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-border bg-card/50 px-6 py-10 text-center">
      <div className="text-sm text-muted-foreground">
        No setlists yet. Build one from your library.
      </div>
      <Button onClick={onCreate} size="sm">
        <Plus className="size-4" />
        New setlist
      </Button>
    </div>
  );
}

function ContinueLastService({
  session,
  name,
  onContinue,
  onDismiss,
}: {
  session: LastSession;
  name: string;
  onContinue: () => void;
  onDismiss: () => void;
}) {
  return (
    <section className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-4">
      <div className="flex items-center gap-4">
        <Play className="size-5 text-emerald-400" />
        <div className="flex-1">
          <div className="text-sm font-medium">Continue last service?</div>
          <div className="text-xs text-muted-foreground">
            {name} · saved {formatRelative(session.savedAt)}
          </div>
        </div>
        <Button size="sm" onClick={onContinue}>
          Continue
        </Button>
        <Button size="sm" variant="ghost" onClick={onDismiss}>
          Dismiss
        </Button>
      </div>
    </section>
  );
}

function formatRelative(unixMs: number): string {
  const diff = Date.now() - unixMs;
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return "just now";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} min ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} hr ago`;
  const day = Math.floor(hr / 24);
  if (day < 7) return `${day} day${day === 1 ? "" : "s"} ago`;
  const date = new Date(unixMs);
  const sameYear = date.getFullYear() === new Date().getFullYear();
  return date.toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    ...(sameYear ? {} : { year: "numeric" }),
  });
}

/** "5 songs", or "4 songs + 2 more" when the service has passages or text. */
function countLabel(songs: number, all: number): string {
  const label = `${songs} song${songs === 1 ? "" : "s"}`;
  return all > songs ? `${label} + ${all - songs} more` : label;
}
