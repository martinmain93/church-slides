/**
 * The leader's full-screen pickers: the whole set at a glance, and a search
 * over the entire library for a song that isn't in the set.
 */

import { useEffect, useMemo, useState } from "react";
import { flushSync } from "react-dom";
import { ArrowLeft, Library, X } from "lucide-react";

import type { BandSong } from "@/lib/band/protocol";
import {
  buildLibraryIndex,
  matchingLine,
  recentSongs,
  searchLibrary,
  type LibrarySong,
} from "@/lib/band/librarySearch";
import { fitsNext } from "@/lib/band/fitsNext";
import { displayKey } from "@/lib/chords/chart";
import { KeyBadge } from "@/components/KeyBadge";

/**
 * Tap-to-go with a guard: leaving a song part-way through blanks the screen,
 * so mid-song the first tap only arms the button and the second one goes.
 * An armed button disarms itself after a few seconds.
 */
export function useConfirmTap(midSong: boolean) {
  const [armed, setArmed] = useState<string | null>(null);
  useEffect(() => {
    if (!armed) return;
    const t = setTimeout(() => setArmed(null), 4000);
    return () => clearTimeout(t);
  }, [armed]);
  return {
    armed,
    /** True when the tap should act now. */
    tap(id: string): boolean {
      if (midSong && armed !== id) {
        setArmed(id);
        return false;
      }
      setArmed(null);
      return true;
    },
  };
}

function SheetFrame({ title, onClose, children, footer, leading }: {
  title: React.ReactNode;
  onClose: () => void;
  children: React.ReactNode;
  footer?: React.ReactNode;
  leading?: React.ReactNode;
}) {
  return (
    <div className="fixed inset-0 z-40 flex flex-col bg-background" role="dialog" aria-modal="true">
      <header className="flex items-center gap-2 border-b border-border px-3 py-2">
        {leading}
        <div className="min-w-0 flex-1">{title}</div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="rounded-md p-2 text-muted-foreground hover:text-foreground"
        >
          <X className="size-5" />
        </button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
      {footer}
    </div>
  );
}

// --- the set -------------------------------------------------------------------

export function SetSheet({
  songs,
  setName,
  currentIdx,
  midSong,
  canAddSongs,
  onGo,
  onOpenLibrary,
  onClose,
}: {
  songs: BandSong[];
  setName: string;
  currentIdx: number;
  midSong: boolean;
  canAddSongs: boolean;
  onGo: (itemId: string) => void;
  onOpenLibrary: () => void;
  onClose: () => void;
}) {
  const confirm = useConfirmTap(midSong);
  return (
    <SheetFrame
      title={
        <div>
          <div className="font-semibold">All songs</div>
          <div className="truncate text-xs text-muted-foreground">{setName}</div>
        </div>
      }
      onClose={onClose}
      footer={
        canAddSongs && (
          <div className="border-t border-border p-3">
            <button
              type="button"
              // Must switch screens and focus inside this tap (see
              // `openAndFocus`) or phones won't raise the keyboard.
              onClick={onOpenLibrary}
              className="flex w-full items-center justify-center gap-2 rounded-lg border border-border bg-muted/40 py-3 font-medium"
              data-testid="open-library"
            >
              <Library className="size-5" />
              Full library
            </button>
          </div>
        )
      }
    >
      <ol className="grid grid-cols-1 gap-2 p-3 sm:grid-cols-2 lg:grid-cols-3" data-testid="set-grid">
        {songs.map((s, i) => {
          const current = i === currentIdx;
          const armed = confirm.armed === s.itemId;
          const key = displayKey(s.chart, s.transpose);
          return (
            <li key={s.itemId}>
              <button
                type="button"
                aria-current={current ? "true" : undefined}
                onClick={() => {
                  if (current && !midSong) return onClose();
                  if (!confirm.tap(s.itemId)) return;
                  onGo(s.itemId);
                  onClose();
                }}
                className={[
                  "flex w-full items-center gap-3 rounded-lg border px-3 py-3 text-left",
                  current ? "border-emerald-500 bg-emerald-500/15" : "border-border bg-muted/30",
                  armed ? "border-amber-400 bg-amber-400/20" : "",
                ].join(" ")}
              >
                <span className="w-6 shrink-0 text-right text-sm text-muted-foreground">{i + 1}</span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium">{armed ? `Tap again: go to ${s.title}` : s.title}</span>
                  {s.author && <span className="block truncate text-xs text-muted-foreground">{s.author}</span>}
                </span>
                {key && <KeyBadge songKey={key} />}
              </button>
            </li>
          );
        })}
      </ol>
    </SheetFrame>
  );
}

// --- the library ---------------------------------------------------------------

export function LibrarySheet({
  library,
  current,
  inSet,
  midSong,
  onAdd,
  onGoExisting,
  onBack,
  onClose,
  inputRef,
}: {
  library: LibrarySong[] | null;
  /** The song being played now — "Fits next" suggests what follows it. */
  current?: BandSong;
  /** Song id → the set item already playing it. */
  inSet: Map<string, string>;
  midSong: boolean;
  /** `key`: play it in this key ("fits next" moved it). */
  onAdd: (songId: string, when: "next" | "now", key?: string) => void;
  onGoExisting: (itemId: string) => void;
  onBack: () => void;
  onClose: () => void;
  inputRef: React.RefObject<HTMLInputElement | null>;
}) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const index = useMemo(() => (library ? buildLibraryIndex(library) : null), [library]);
  const hits = useMemo(() => (index && query.trim() ? searchLibrary(index, query) : null), [index, query]);
  const recent = useMemo(() => (index ? recentSongs(index) : []), [index]);
  const fits = useMemo(() => (library ? fitsNext(current, library, inSet) : []), [library, current, inSet]);
  const confirm = useConfirmTap(midSong);

  // Fallback focus for browsers that don't need the gesture (and for tests).
  useEffect(() => {
    inputRef.current?.focus();
  }, [inputRef]);

  const list: { song: LibrarySong; hint: string | null }[] = hits
    ? hits.map((h) => ({ song: h.song, hint: h.via === "lyrics" ? matchingLine(h.song, query) : null }))
    : recent.filter((song) => !fits.some((f) => f.song.id === song.id)).map((song) => ({ song, hint: null }));

  return (
    <SheetFrame
      leading={
        <button type="button" onClick={onBack} aria-label="Back to the set" className="rounded-md p-2 text-muted-foreground">
          <ArrowLeft className="size-5" />
        </button>
      }
      title={
        <input
          ref={inputRef}
          type="search"
          inputMode="search"
          enterKeyHint="search"
          autoComplete="off"
          autoCorrect="off"
          autoCapitalize="none"
          spellCheck={false}
          placeholder="Search titles and lyrics"
          aria-label="Search the library"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(null);
          }}
          className="w-full rounded-md border border-border bg-muted/40 px-3 py-2 text-base"
        />
      }
      onClose={onClose}
    >
      {!index ? (
        <div className="p-6 text-center text-sm text-muted-foreground">Loading the library…</div>
      ) : (
        <>
          {!hits && fits.length > 0 && (
            <>
              <SectionLabel>Fits next</SectionLabel>
              <ul className="divide-y divide-border" data-testid="fits-next">
                {fits.map((f) => (
                  <SongRow
                    key={f.song.id}
                    song={f.song}
                    subtitle={f.reason}
                    songKey={f.key}
                    moveKey={f.semitones !== 0 ? f.key ?? undefined : undefined}
                    existing={undefined}
                    expanded={open === `fit:${f.song.id}`}
                    onToggle={() => setOpen(open === `fit:${f.song.id}` ? null : `fit:${f.song.id}`)}
                    confirm={confirm}
                    onAdd={onAdd}
                    onGoExisting={onGoExisting}
                  />
                ))}
              </ul>
            </>
          )}
          <SectionLabel>
            {hits ? `${hits.length === 60 ? "60+" : hits.length} match${hits.length === 1 ? "" : "es"}` : "Recently played"}
          </SectionLabel>
          {list.length === 0 && (
            <div className="px-4 py-6 text-sm text-muted-foreground">{hits ? "No songs match." : "No songs played yet — start typing."}</div>
          )}
          <ul className="divide-y divide-border" data-testid="library-results">
            {list.map(({ song, hint }) => (
              <SongRow
                key={song.id}
                song={song}
                subtitle={hint ? `“${hint}”` : song.author ?? ""}
                songKey={song.key}
                existing={inSet.get(song.id)}
                expanded={open === song.id}
                onToggle={() => setOpen(open === song.id ? null : song.id)}
                confirm={confirm}
                onAdd={onAdd}
                onGoExisting={onGoExisting}
              />
            ))}
          </ul>
        </>
      )}
    </SheetFrame>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-4 pb-1 pt-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{children}</div>
  );
}

function SongRow({
  song,
  subtitle,
  songKey,
  moveKey,
  existing,
  expanded,
  onToggle,
  confirm,
  onAdd,
  onGoExisting,
}: {
  song: LibrarySong;
  subtitle: string;
  songKey: string | null;
  /** Add it in this key rather than its own. */
  moveKey?: string;
  existing: string | undefined;
  expanded: boolean;
  onToggle: () => void;
  confirm: ReturnType<typeof useConfirmTap>;
  onAdd: (songId: string, when: "next" | "now", key?: string) => void;
  onGoExisting: (itemId: string) => void;
}) {
  return (
    <li>
      <button
        type="button"
        className="flex w-full items-center gap-3 px-4 py-3 text-left"
        onClick={onToggle}
        aria-expanded={expanded}
      >
        <span className="min-w-0 flex-1">
          <span className="block truncate font-medium">{song.title}</span>
          <span className="block truncate text-xs text-muted-foreground">{subtitle}</span>
        </span>
        {existing && <span className="shrink-0 rounded bg-emerald-500/15 px-1.5 text-xs text-emerald-400">in set</span>}
        {songKey && <KeyBadge songKey={songKey} />}
      </button>
      {expanded && (
        <div className="flex gap-2 px-4 pb-3">
          {existing ? (
            <ActionButton
              label={confirm.armed === song.id ? "Tap again: go now" : "Go to it now"}
              onClick={() => confirm.tap(song.id) && onGoExisting(existing)}
            />
          ) : (
            <>
              <ActionButton label="Play next" onClick={() => onAdd(song.id, "next", moveKey)} />
              <ActionButton
                label={confirm.armed === song.id ? "Tap again: go now" : "Go now"}
                primary
                onClick={() => confirm.tap(song.id) && onAdd(song.id, "now", moveKey)}
              />
            </>
          )}
        </div>
      )}
    </li>
  );
}

function ActionButton({ label, onClick, primary }: { label: string; onClick: () => void; primary?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex-1 rounded-lg py-2.5 text-sm font-medium ${
        primary ? "bg-primary text-primary-foreground" : "border border-border bg-muted/40"
      }`}
    >
      {label}
    </button>
  );
}

/**
 * Open the library screen from a tap and focus its search box in the same
 * tap. `flushSync` renders the input before the handler returns, so the
 * `focus()` still counts as user-initiated and phones raise the keyboard.
 */
export function openAndFocus(open: () => void, inputRef: React.RefObject<HTMLInputElement | null>) {
  flushSync(open);
  inputRef.current?.focus();
}

