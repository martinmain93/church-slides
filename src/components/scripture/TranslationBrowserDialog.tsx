/**
 * Find and download a public-domain Bible translation.
 *
 * The list is eBible.org's public-domain translations — about 110 in 47
 * languages, 34 in English — fetched by the sidecar and cached for a week.
 * Only public domain, so nothing here comes with terms to follow; a church's
 * licensed translation (NIV, ESV…) is "Import a Bible file" instead.
 *
 * Each download is one USFM package (a few MB), built into an offline store
 * on this computer.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Check, Download, Loader2, Search, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { installTranslation, searchTranslationCatalog } from "@/lib/scripture/client";
import type { ScriptureCatalogEntry } from "@/types/generated";

const ALL_LANGUAGES = "";

export function TranslationBrowserDialog({
  onClose,
  onInstalled,
}: {
  onClose: () => void;
  /** Called with the installed translation's id and name after each download. */
  onInstalled: (translation: string, name: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [language, setLanguage] = useState("English");
  const [entries, setEntries] = useState<ScriptureCatalogEntry[] | null>(null);
  const [allLanguages, setAllLanguages] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [installing, setInstalling] = useState<string | null>(null);
  const [installed, setInstalled] = useState<Set<string>>(new Set());
  const [installError, setInstallError] = useState<{ id: string; message: string } | null>(null);
  const latest = useRef(0);

  useEffect(() => {
    const ticket = ++latest.current;
    const timer = setTimeout(async () => {
      const reply = await searchTranslationCatalog(query);
      if (ticket !== latest.current) return;
      setEntries(reply.entries);
      setError(reply.error);
      if (!query.trim() && reply.entries.length) {
        setAllLanguages([...new Set(reply.entries.map((e) => e.language ?? ""))].filter(Boolean));
      }
    }, 200);
    return () => clearTimeout(timer);
  }, [query]);

  const shown = useMemo(
    () => (entries ?? []).filter((e) => language === ALL_LANGUAGES || e.language === language),
    [entries, language],
  );

  const install = useCallback(
    async (entry: ScriptureCatalogEntry) => {
      setInstalling(entry.id);
      setInstallError(null);
      const result = await installTranslation(entry.id);
      setInstalling(null);
      if (result.installed) {
        setInstalled((prev) => new Set(prev).add(entry.id));
        onInstalled(result.translation, entry.title);
      } else {
        setInstallError({ id: entry.id, message: result.error ?? "Couldn't download it." });
      }
    },
    [onInstalled],
  );

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6"
      role="dialog"
      aria-modal="true"
      aria-label="Find a translation"
    >
      <div className="flex h-full max-h-[680px] w-full max-w-2xl flex-col rounded-lg border border-border bg-background shadow-2xl">
        <header className="flex items-start justify-between gap-4 border-b border-border px-5 py-3">
          <div>
            <div className="font-semibold">Find a translation</div>
            <div className="text-xs text-muted-foreground">
              Public-domain Bibles from eBible.org: free to use anywhere, and they work
              offline once downloaded. For a licensed translation like the NIV or ESV, use
              Import a Bible file.
            </div>
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="text-muted-foreground hover:text-foreground">
            <X className="size-5" />
          </button>
        </header>

        <div className="flex gap-2 border-b border-border p-3">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Name or abbreviation…"
              aria-label="Search translations"
              className="h-9 pl-9"
            />
          </div>
          <select
            aria-label="Language"
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
            className="h-9 rounded-md border border-input bg-background px-2 text-sm"
          >
            <option value={ALL_LANGUAGES}>All languages</option>
            {allLanguages.map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </select>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {entries === null && !error && (
            <div className="flex items-center gap-2 p-4 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" /> Loading eBible.org&rsquo;s list…
            </div>
          )}
          {error && (
            <p role="alert" className="p-4 text-sm text-destructive">
              {error}
            </p>
          )}
          {entries !== null && !error && shown.length === 0 && (
            <p className="p-4 text-sm text-muted-foreground">No translations match.</p>
          )}
          <ul className="divide-y divide-border">
            {shown.map((entry) => {
              const done = entry.installed || installed.has(entry.id);
              return (
                <li key={entry.id} className="flex items-center gap-3 px-4 py-2.5">
                  <div className="min-w-0 flex-1">
                    <div className="text-sm">
                      {entry.title}{" "}
                      <span className="text-xs text-muted-foreground">{entry.abbreviation}</span>
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {[language === ALL_LANGUAGES ? entry.language : "", contents(entry)]
                        .filter(Boolean)
                        .join(" · ")}
                    </div>
                    {installError?.id === entry.id && (
                      <div className="text-xs text-destructive">{installError.message}</div>
                    )}
                  </div>
                  {done ? (
                    <span className="flex items-center gap-1 text-xs text-muted-foreground">
                      <Check className="size-3.5" /> Downloaded
                    </span>
                  ) : (
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="h-7 gap-1.5 text-xs"
                      disabled={installing !== null}
                      onClick={() => void install(entry)}
                      aria-label={`Download ${entry.title}`}
                    >
                      {installing === entry.id ? (
                        <Loader2 className="size-3.5 animate-spin" />
                      ) : (
                        <Download className="size-3.5" />
                      )}
                      {installing === entry.id ? "Downloading…" : "Download"}
                    </Button>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      </div>
    </div>
  );
}

/**
 * "Whole Bible + deuterocanon", "New Testament", "Portions". Book counts are
 * eBible's, and a Septuagint counts its Old Testament differently (Brenton is
 * 37 books, complete), so "most of the books" counts as the testament.
 */
export function contents(entry: ScriptureCatalogEntry): string {
  const ot = entry.ot_books ?? 0;
  const nt = entry.nt_books ?? 0;
  const dc = entry.dc_books ?? 0;
  const fullOt = ot >= 35;
  const base =
    fullOt && nt >= 27
      ? "Whole Bible"
      : fullOt
        ? "Old Testament"
        : nt >= 27
          ? ot > 0
            ? "New Testament and parts of the Old"
            : "New Testament"
          : "Portions";
  return dc > 0 ? `${base} + deuterocanon` : base;
}
