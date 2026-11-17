/**
 * Add hymns from the Open Hymnal Project: search, preview, add.
 *
 * Each hymn is fetched from openhymnal.org only when it's opened, and added
 * only when the operator presses Add — nothing from the Open Hymnal ships in
 * the app. Every hymn shows its copyright status before it can be added:
 *
 *   Public domain            free everywhere (our stricter rule, not just the US)
 *   Public domain in the US  may still be in copyright elsewhere — says why
 *   Free for worship         copyrighted; usable in worship with the notice kept
 *
 * The list starts filtered to "public domain everywhere", the one status that
 * needs no thought; the rest are a tick away, with the reason on each.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Check, ExternalLink, Loader2, Plus, Search, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { addHymnToLibrary, getHymn, searchHymns, type HymnDetail, type HymnSummary } from "@/lib/hymns";
import { listSongs, openLink } from "@/lib/tauri";
import { useToast } from "@/lib/toast";

const STATUS_STYLE: Record<string, string> = {
  public_domain: "border-emerald-500/40 bg-emerald-500/10 text-emerald-400",
  public_domain_us: "border-amber-500/40 bg-amber-500/10 text-amber-400",
  free_for_worship: "border-sky-500/40 bg-sky-500/10 text-sky-400",
  check: "border-border bg-muted text-muted-foreground",
};

export function HymnBrowserDialog({
  onClose,
  onAdded,
}: {
  onClose: () => void;
  /** Called after each hymn is added, so the library can refresh. */
  onAdded: () => void;
}) {
  const toast = useToast();
  const [query, setQuery] = useState("");
  const [everywhereOnly, setEverywhereOnly] = useState(true);
  const [results, setResults] = useState<HymnSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<HymnSummary | null>(null);
  const [detail, setDetail] = useState<HymnDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [adding, setAdding] = useState(false);
  const [added, setAdded] = useState<Set<string>>(new Set());
  const latest = useRef(0);
  // Titles already in the library, to mark what's there. Read whole, not
  // from the library view, whose list is narrowed by its own search box.
  const [libraryTitles, setLibraryTitles] = useState<Set<string>>(new Set());

  useEffect(() => {
    listSongs()
      .then((songs) => setLibraryTitles(new Set(songs.map((s) => s.title.trim().toLowerCase()))))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    const ticket = ++latest.current;
    const timer = setTimeout(async () => {
      const reply = await searchHymns(query);
      if (ticket !== latest.current) return;
      setResults(reply.results);
      setError(reply.error);
    }, 200);
    return () => clearTimeout(timer);
  }, [query]);

  const shown = useMemo(
    () => (results ?? []).filter((h) => !everywhereOnly || h.status === "public_domain"),
    [results, everywhereOnly],
  );
  const hiddenCount = (results?.length ?? 0) - shown.length;

  const open = useCallback(async (hymn: HymnSummary) => {
    setSelected(hymn);
    setDetail(null);
    setDetailError(null);
    setLoadingDetail(true);
    const reply = await getHymn(hymn.hymn_id);
    setLoadingDetail(false);
    setDetail(reply.hymn);
    setDetailError(reply.error);
  }, []);

  const add = useCallback(async () => {
    if (!detail) return;
    setAdding(true);
    try {
      await addHymnToLibrary(detail);
      // No toast: the footer turns into "In your library", right where the
      // operator is looking — a toast on top of it covered the same words.
      setAdded((prev) => new Set(prev).add(detail.hymn_id));
      onAdded();
    } catch (err) {
      toast.error(`Couldn't add it: ${err}`);
    } finally {
      setAdding(false);
    }
  }, [detail, onAdded, toast]);

  const inLibrary = (title: string) => libraryTitles.has(title.trim().toLowerCase());

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6"
      role="dialog"
      aria-modal="true"
      aria-label="Add hymns"
    >
      <div className="flex h-full max-h-[720px] w-full max-w-5xl flex-col rounded-lg border border-border bg-background shadow-2xl">
        <header className="flex items-start justify-between gap-4 border-b border-border px-5 py-3">
          <div>
            <div className="font-semibold">Add hymns</div>
            <div className="text-xs text-muted-foreground">
              Classic hymns from the{" "}
              <button
                type="button"
                onClick={() => void openLink("http://openhymnal.org/").catch(() => undefined)}
                className="underline underline-offset-2"
              >
                Open Hymnal Project
              </button>
              , fetched when you open one. Each shows whether it&rsquo;s free to use.
            </div>
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="text-muted-foreground hover:text-foreground">
            <X className="size-5" />
          </button>
        </header>

        <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
          <div className="flex min-h-0 flex-col border-r border-border">
            <div className="space-y-2 border-b border-border p-3">
              <div className="relative">
                <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  autoFocus
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Title, first line, author or tune…"
                  aria-label="Search hymns"
                  className="h-9 pl-9"
                />
              </div>
              <label className="flex items-center gap-2 text-xs text-muted-foreground">
                <input
                  type="checkbox"
                  checked={everywhereOnly}
                  onChange={(e) => setEverywhereOnly(e.target.checked)}
                  aria-label="Only hymns that are public domain everywhere"
                  className="size-3.5 accent-emerald-500"
                />
                Only hymns that are public domain everywhere
                {everywhereOnly && hiddenCount > 0 && <span>({hiddenCount} more hidden)</span>}
              </label>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto">
              {results === null && !error && (
                <div className="flex items-center gap-2 p-4 text-sm text-muted-foreground">
                  <Loader2 className="size-4 animate-spin" /> Loading the Open Hymnal&rsquo;s index…
                </div>
              )}
              {error && (
                <p role="alert" className="p-4 text-sm text-destructive">
                  {error}
                </p>
              )}
              {results !== null && !error && shown.length === 0 && (
                <p className="p-4 text-sm text-muted-foreground">No hymns match.</p>
              )}
              <ul>
                {shown.map((h) => (
                  <li key={h.hymn_id}>
                    <button
                      type="button"
                      onClick={() => void open(h)}
                      className={`w-full border-b border-border/50 px-3 py-2 text-left hover:bg-muted/50 ${
                        selected?.hymn_id === h.hymn_id ? "bg-muted" : ""
                      }`}
                    >
                      <div className="flex items-center gap-2">
                        <span className="truncate text-sm font-medium">{h.title}</span>
                        {(added.has(h.hymn_id) || inLibrary(h.title)) && (
                          <Check className="size-3.5 shrink-0 text-emerald-500" aria-label="In your library" />
                        )}
                      </div>
                      <div className="truncate text-xs text-muted-foreground">
                        {[h.first_line, h.author, h.tune && `tune: ${h.tune}`].filter(Boolean).join(" · ")}
                      </div>
                      {h.status !== "public_domain" && <StatusBadge status={h.status ?? "check"} label={h.status_label ?? ""} />}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          </div>

          <div className="flex min-h-0 flex-col">
            {!selected && (
              <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
                Choose a hymn to see its words.
              </div>
            )}
            {selected && (
              <>
                <div className="min-h-0 flex-1 overflow-y-auto p-5">
                  <div className="text-lg font-semibold">{selected.title}</div>
                  {loadingDetail && (
                    <div className="mt-4 flex items-center gap-2 text-sm text-muted-foreground">
                      <Loader2 className="size-4 animate-spin" /> Fetching from the Open Hymnal…
                    </div>
                  )}
                  {detailError && (
                    <p role="alert" className="mt-4 text-sm text-destructive">
                      {detailError}
                    </p>
                  )}
                  {detail && (
                    <>
                      <div className="mt-2 space-y-1.5 rounded-md border border-border p-3 text-xs">
                        <StatusBadge status={detail.status ?? "check"} label={detail.status_label ?? ""} />
                        <p>{detail.explanation}</p>
                        {detail.copyright && <p className="text-muted-foreground">{detail.copyright}</p>}
                      </div>
                      <SongText body={detail.body_text} order={detail.slide_order ?? ""} />
                    </>
                  )}
                </div>
                <footer className="flex items-center justify-between gap-3 border-t border-border px-5 py-3">
                  {detail?.source_url ? (
                    <button
                      type="button"
                      onClick={() => void openLink(detail.source_url ?? "").catch(() => undefined)}
                      className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                    >
                      <ExternalLink className="size-3.5" /> View on openhymnal.org
                    </button>
                  ) : (
                    <span />
                  )}
                  {detail && (added.has(detail.hymn_id) || inLibrary(detail.title)) && !adding ? (
                    <span className="flex items-center gap-1.5 text-sm text-emerald-500">
                      <Check className="size-4" /> In your library
                    </span>
                  ) : (
                    <Button type="button" onClick={() => void add()} disabled={!detail || adding}>
                      {adding ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
                      Add to library
                    </Button>
                  )}
                </footer>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function StatusBadge({ status, label }: { status: string; label: string }) {
  return (
    <span
      className={`mt-1 inline-block rounded border px-1.5 py-0.5 text-[11px] font-medium ${
        STATUS_STYLE[status] ?? STATUS_STYLE.check
      }`}
    >
      {label}
    </span>
  );
}

/** The song text as it will be stored: section labels, then lines. */
function SongText({ body, order }: { body: string; order: string }) {
  const sections = body
    .split(/^\[([^\]]+)\]\s*$/m)
    .slice(1)
    .reduce<{ label: string; text: string }[]>((acc, part, i, arr) => {
      if (i % 2 === 0) acc.push({ label: part, text: (arr[i + 1] ?? "").trim() });
      return acc;
    }, []);
  return (
    <div className="mt-4 space-y-4">
      {order && <div className="text-xs text-muted-foreground">Sung as: {order}</div>}
      {sections.map((s, i) => (
        <div key={i}>
          <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{s.label}</div>
          <p className="whitespace-pre-line text-sm leading-relaxed">{s.text}</p>
        </div>
      ))}
    </div>
  );
}
