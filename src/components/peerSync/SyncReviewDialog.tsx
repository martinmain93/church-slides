/**
 * Review a setlist received from another operator before it's saved. Songs
 * this library already has with the same words are reused silently; songs
 * whose words differ need a choice each (keep mine / use theirs / keep both)
 * before Import is enabled.
 *
 * A whole library is hundreds of songs, so it's reviewed as a summary
 * instead: how many are new, already here, or different, and one choice for
 * all the different ones.
 */

import { useEffect, useMemo, useState } from "react";
import { X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { decisionsFor, normalizeBody, planSetlistSync, type ConflictChoice, type SyncRow } from "@/lib/peerSync/plan";
import { applySetlistBundle, listSongs, type SetlistBundle, type SongRow, type SyncApplySummary } from "@/lib/tauri";
import { cn } from "@/lib/utils";

const CHOICES: { value: ConflictChoice; label: string; hint: string }[] = [
  { value: "mine", label: "Keep mine", hint: "This library's words; the service uses them" },
  { value: "theirs", label: "Use theirs", hint: "Replace this library's song (a backup is kept)" },
  { value: "both", label: "Keep both", hint: "Add theirs as a second copy" },
];

export function SyncReviewDialog({
  bundle,
  fromName,
  onCancel,
  onImported,
}: {
  bundle: SetlistBundle;
  fromName: string;
  onCancel: () => void;
  onImported: (summary: SyncApplySummary) => void;
}) {
  const [library, setLibrary] = useState<SongRow[] | null>(null);
  const [choices, setChoices] = useState<Record<string, ConflictChoice | undefined>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listSongs()
      .then(setLibrary)
      .catch((e) => setError(`Couldn't read the library: ${e instanceof Error ? e.message : e}`));
  }, []);

  const rows = useMemo(() => (library ? planSetlistSync(bundle, library) : []), [bundle, library]);
  const differs = rows.filter((r) => r.kind === "differs");
  const added = rows.filter((r) => r.kind === "new");
  const same = rows.filter((r) => r.kind === "same");
  const [allDiffering, setAllDiffering] = useState<ConflictChoice | undefined>(undefined);
  const wholeLibrary = bundle.library === true;
  const decisions = decisionsFor(
    rows,
    wholeLibrary ? Object.fromEntries(differs.map((r) => [r.song.ref_id, allDiffering])) : choices,
  );
  const undecided = wholeLibrary ? (allDiffering ? 0 : differs.length) : differs.filter((r) => !choices[r.song.ref_id]).length;

  async function doImport() {
    if (!decisions) return;
    setBusy(true);
    setError(null);
    try {
      onImported(await applySetlistBundle(bundle, decisions, fromName));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6"
      role="dialog"
      aria-modal="true"
      aria-label={wholeLibrary ? "Review library" : "Review service"}
    >
      <div className="flex max-h-full w-full max-w-3xl flex-col rounded-lg border border-border bg-background shadow-2xl">
        <header className="flex items-center justify-between border-b border-border px-5 py-3">
          <div>
            <div className="font-semibold">{wholeLibrary ? `${fromName}'s library` : `“${bundle.setlist_name}” from ${fromName}`}</div>
            <div className="text-xs text-muted-foreground">
              {wholeLibrary ? `${bundle.songs.length} song${bundle.songs.length === 1 ? "" : "s"}.` : setSummary(bundle.items)} Nothing is
              saved until you press Import.
            </div>
          </div>
          <button type="button" onClick={onCancel} aria-label="Close" className="text-muted-foreground hover:text-foreground">
            <X className="size-5" />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto" data-testid="sync-review">
          {!library && !error && <div className="p-6 text-sm text-muted-foreground">Checking your library…</div>}
          {wholeLibrary && rows.length > 0 && (
            <LibrarySummary
              added={added.length}
              same={same.length}
              differs={differs}
              choice={allDiffering}
              onChoose={setAllDiffering}
            />
          )}
          {!wholeLibrary && differs.length > 0 && (
            <Section title={`Different words in your library — choose for each (${differs.length})`}>
              {differs.map((r) => (
                <ConflictRow
                  key={r.song.ref_id}
                  row={r}
                  fromName={fromName}
                  choice={choices[r.song.ref_id]}
                  onChoose={(c) => setChoices((m) => ({ ...m, [r.song.ref_id]: c }))}
                />
              ))}
            </Section>
          )}
          {!wholeLibrary && added.length > 0 && (
            <Section title={`New to this library (${added.length})`}>
              {added.map((r) => (
                <SimpleRow key={r.song.ref_id} row={r} note={r.song.chart ? "with chords" : null} />
              ))}
            </Section>
          )}
          {!wholeLibrary && same.length > 0 && (
            <Section title={`Already in your library — yours will be used (${same.length})`}>
              {same.map((r) => (
                <SimpleRow
                  key={r.song.ref_id}
                  row={r}
                  note={r.song.chart ? "adds their chords if yours has none" : null}
                />
              ))}
            </Section>
          )}
        </div>

        <footer className="flex items-center justify-between gap-3 border-t border-border px-5 py-3">
          <div className="text-xs text-destructive">{error}</div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={onCancel}>
              Cancel
            </Button>
            <Button size="sm" onClick={() => void doImport()} disabled={busy || !library || !decisions} data-testid="sync-import">
              {undecided > 0
                ? wholeLibrary
                  ? "Choose for the different songs"
                  : `Choose for ${undecided} more song${undecided === 1 ? "" : "s"}`
                : wholeLibrary
                  ? "Import library"
                  : "Import service"}
            </Button>
          </div>
        </footer>
      </div>
    </div>
  );
}

/** A whole library at a glance, and one choice for every song whose words differ. */
function LibrarySummary({
  added,
  same,
  differs,
  choice,
  onChoose,
}: {
  added: number;
  same: number;
  differs: Extract<SyncRow, { kind: "differs" }>[];
  choice: ConflictChoice | undefined;
  onChoose: (c: ConflictChoice) => void;
}) {
  const n = (k: number) => `${k} song${k === 1 ? "" : "s"}`;
  return (
    <div className="flex flex-col gap-4 px-5 py-4 text-sm" data-testid="sync-library-summary">
      <ul className="flex flex-col gap-1">
        {added > 0 && (
          <li>
            <span className="font-medium">{n(added)}</span> new to this library — added, with their chords
          </li>
        )}
        {same > 0 && (
          <li>
            <span className="font-medium">{n(same)}</span> already here with the same words — yours are kept, and get their chords
            if yours have none
          </li>
        )}
        {differs.length > 0 && (
          <li>
            <span className="font-medium">{n(differs.length)}</span> here with different words
          </li>
        )}
      </ul>
      {differs.length > 0 && (
        <div>
          <div className="mb-2 font-medium">For the {n(differs.length)} with different words:</div>
          <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="What to do with songs whose words differ">
            {CHOICES.map((c) => (
              <button
                key={c.value}
                type="button"
                role="radio"
                aria-checked={choice === c.value}
                title={c.hint}
                onClick={() => onChoose(c.value)}
                className={cn(
                  "rounded-md border px-3 py-1 text-xs",
                  choice === c.value ? "border-primary bg-primary text-primary-foreground" : "border-border hover:bg-muted",
                )}
              >
                {c.label}
              </button>
            ))}
          </div>
          <details className="mt-2 text-xs text-muted-foreground">
            <summary className="cursor-pointer">Which songs</summary>
            <ul className="mt-1 columns-2">
              {differs.map((r) => (
                <li key={r.song.ref_id}>{r.song.title}</li>
              ))}
            </ul>
          </details>
        </div>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="bg-muted/40 px-5 py-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{title}</h3>
      <ul className="divide-y divide-border">{children}</ul>
    </section>
  );
}

function SimpleRow({ row, note }: { row: SyncRow; note: string | null }) {
  return (
    <li className="flex items-baseline gap-3 px-5 py-2 text-sm" data-testid="sync-row">
      <span className="font-medium">{row.song.title}</span>
      {row.song.author && <span className="truncate text-xs text-muted-foreground">{row.song.author}</span>}
      {note && <span className="ml-auto shrink-0 text-xs text-muted-foreground">{note}</span>}
    </li>
  );
}

function ConflictRow({
  row,
  fromName,
  choice,
  onChoose,
}: {
  row: Extract<SyncRow, { kind: "differs" }>;
  fromName: string;
  choice: ConflictChoice | undefined;
  onChoose: (c: ConflictChoice) => void;
}) {
  const [compare, setCompare] = useState(false);
  return (
    <li className="px-5 py-3 text-sm" data-testid="sync-conflict">
      <div className="flex items-baseline gap-3">
        <span className="font-medium">{row.song.title}</span>
        {row.match.title !== row.song.title && (
          <span className="text-xs text-muted-foreground">yours: “{row.match.title}”</span>
        )}
        <button type="button" className="ml-auto text-xs text-muted-foreground underline" onClick={() => setCompare((v) => !v)}>
          {compare ? "Hide words" : "Compare words"}
        </button>
      </div>
      <div className="mt-2 flex flex-wrap gap-2" role="radiogroup" aria-label={`What to do with ${row.song.title}`}>
        {CHOICES.map((c) => (
          <button
            key={c.value}
            type="button"
            role="radio"
            aria-checked={choice === c.value}
            title={c.hint}
            onClick={() => onChoose(c.value)}
            className={cn(
              "rounded-md border px-3 py-1 text-xs",
              choice === c.value ? "border-primary bg-primary text-primary-foreground" : "border-border hover:bg-muted",
            )}
          >
            {c.label}
          </button>
        ))}
      </div>
      {compare && (
        <div className="mt-2 grid grid-cols-2 gap-2">
          <Words label="Yours" text={row.match.body_text} other={row.song.body_text} />
          <Words label={fromName} text={row.song.body_text} other={row.match.body_text} />
        </div>
      )}
    </li>
  );
}

/** One side of the comparison, with the lines the other side doesn't have highlighted. */
function Words({ label, text, other }: { label: string; text: string; other: string }) {
  const theirs = useMemo(() => new Set(normalizeBody(other).split("\n")), [other]);
  const lines = normalizeBody(text).split("\n");
  const changed = lines.filter((l) => l && !theirs.has(l)).length;
  return (
    <div className="min-w-0">
      <div className="mb-1 text-xs font-medium text-muted-foreground">
        {label}
        {changed > 0 && ` — ${changed} line${changed === 1 ? "" : "s"} not in the other`}
      </div>
      <div className="max-h-64 overflow-auto rounded border border-border bg-muted/30 p-2 text-xs">
        {lines.map((l, i) => (
          <div
            key={i}
            data-changed={l && !theirs.has(l) ? "true" : undefined}
            className={cn("min-h-[1em] whitespace-pre-wrap", l && !theirs.has(l) && "rounded-sm bg-amber-400/25")}
          >
            {l}
          </div>
        ))}
      </div>
    </div>
  );
}

/** "5 songs in the set." — and the readings, when it has any. */
function setSummary(items: { kind?: string }[]): string {
  const songs = items.filter((i) => (i.kind ?? "song") === "song").length;
  const others = items.length - songs;
  const n = (k: number, one: string) => `${k} ${one}${k === 1 ? "" : "s"}`;
  return others > 0
    ? `${n(songs, "song")} and ${n(others, "reading")} in the set.`
    : `${n(songs, "song")} in the set.`;
}
