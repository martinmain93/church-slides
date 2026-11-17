/**
 * The operator's "put a verse up now" box.
 *
 * One field takes either a reference ("jer 29:11", "1 cor 13:4-7") or words
 * from the passage ("plans to prosper you"). A reference that resolves is the
 * first result; text matches follow. Enter shows the first result full screen,
 * which makes the common case — someone calls out a reference — type-and-go.
 *
 * Whatever is up is shown at the top with a "Take down" button, whichever way
 * it got there: typed here, or asked for out loud.
 *
 * The translation picker beside the box is for this service only: it starts
 * at the default from Settings, and a speaker naming one ("read it from the
 * King James") moves it too.
 *
 * Before the service (`onAdd` given) the same box prepares instead: Enter
 * adds the passage to the service, and pasting the pastor's message ("Sermon:
 * John 3:1-21, also Rom 5:8") offers every passage in it at once. One box,
 * whose action follows Prep vs Live (Plan/SERVICE_ITEMS.md).
 */

import { useCallback, useEffect, useState } from "react";
import { BookOpen, Loader2, Plus, Search, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { OPEN_SETTINGS_EVENT } from "@/components/setlists/AiReadyPill";
import {
  RECOMMENDED_TRANSLATION,
  findScriptureReferences,
  passageContexts,
  installTranslation,
  listTranslations,
  resolvePassages,
  searchScripture,
} from "@/lib/scripture/client";
import {
  clearLiveScripture,
  setSessionTranslation,
  showLiveScripture,
  useActiveTranslation,
  useLiveScripture,
  type LiveScriptureDisplay,
} from "@/lib/scripture/liveScripture";
import { withScriptureDefaults } from "@/lib/scripture/settings";
import { getSettings, updateSettings } from "@/lib/tauri";
import { cn } from "@/lib/utils";
import type { FoundReferences } from "@/lib/scripture/client";
import { tidyTyped, versesOf, wholeChapterReference } from "@/lib/scripture/typedReference";
import type { ScripturePassage, ScriptureTranslationInfo } from "@/types/generated";

const SEARCH_DEBOUNCE_MS = 200;

export function LiveScripturePanel({
  translation: defaultTranslation,
  onAdd,
}: {
  /** The default from Settings; this service may have picked another. */
  translation: string;
  /** Prep: add passages to the service instead of showing them. */
  onAdd?: (passages: ScripturePassage[]) => void | Promise<void>;
}) {
  const translation = useActiveTranslation(defaultTranslation);
  const live = useLiveScripture();
  const [installedList, setInstalledList] = useState<ScriptureTranslationInfo[]>([]);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<ScripturePassage[]>([]);
  const [searching, setSearching] = useState(false);
  // null while unknown: say nothing until the catalog has answered.
  const [installed, setInstalled] = useState<boolean | null>(null);
  const [installing, setInstalling] = useState(false);
  const [installError, setInstallError] = useState<string | null>(null);
  // The engine didn't answer: don't claim nothing is installed.
  const [engineError, setEngineError] = useState<string | null>(null);
  // Passages read out of a pasted message (prep only), and what it named
  // that the Bible doesn't hold.
  const [fromMessage, setFromMessage] = useState<FoundReferences | null>(null);
  const [reading, setReading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void listTranslations().then((catalog) => {
      if (cancelled) return;
      setEngineError(catalog.error);
      if (catalog.error) {
        setInstalled(null);
        return;
      }
      const id = translation || catalog.defaultTranslation;
      setInstalled(catalog.translations.some((t) => t.id === id && t.installed));
      setInstalledList(catalog.translations.filter((t) => t.installed));
    });
    return () => {
      cancelled = true;
    };
  }, [translation]);

  useEffect(() => {
    const q = query.trim();
    if (q === "") {
      setFromMessage(null);
      setResults([]);
      setSearching(false);
      return;
    }
    let cancelled = false;
    setSearching(true);
    const timer = setTimeout(() => {
      void (async () => {
        // A book and a chapter ("John 3") is that chapter: offered whole
        // before the service (to add), and verse by verse to pick from —
        // not a text search for the words "John" and "3".
        const chapter = wholeChapterReference(q);
        if (chapter) {
          const whole = (await resolvePassages([chapter], translation)).byReference.get(chapter);
          if (cancelled) return;
          if (whole) {
            setResults([...(onAdd ? [whole] : []), ...versesOf(whole)]);
            setSearching(false);
            return;
          }
        }
        const typed = tidyTyped(q);
        const [resolved, found] = await Promise.all([
          resolvePassages([typed], translation),
          searchScripture(q, translation, 8),
        ]);
        if (cancelled) return;
        // A reference that resolves is what was meant; text matches for its
        // words ("John", "16") would only be noise under it.
        const exact = resolved.byReference.get(typed);
        // One verse comes first, as asked; the passage around it follows, for
        // when the reading is really the rest of the paragraph or the story.
        const around =
          exact && exact.verses.length === 1 ? await passageContexts(exact.reference, translation) : [];
        if (cancelled) return;
        setResults(exact ? [exact, ...around] : found);
        setSearching(false);
      })();
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [query, translation]);

  // Nothing installed: download the recommended Bible by name, make it the
  // default, and use it straight away.
  const install = useCallback(async () => {
    setInstalling(true);
    setInstallError(null);
    const result = await installTranslation(RECOMMENDED_TRANSLATION.id);
    setInstalling(false);
    if (!result.installed) {
      setInstallError(result.error ?? "Couldn't download it.");
      return;
    }
    await makeDefaultTranslation(result.translation);
    setSessionTranslation(result.translation);
  }, []);

  const show = (passage: ScripturePassage, display: LiveScriptureDisplay) =>
    showLiveScripture(passage, display, "operator");

  /** Read every passage out of a message — on paste, or Enter on text that
   * isn't one reference. Asks a model when one is set up, so it isn't run on
   * every keystroke. */
  const readMessage = async (text: string) => {
    setReading(true);
    const found = await findScriptureReferences(text, translation);
    setReading(false);
    setFromMessage(found);
    return found;
  };

  const add = async (passages: ScripturePassage[]) => {
    if (!onAdd || passages.length === 0) return;
    await onAdd(passages);
    setQuery("");
    setFromMessage(null);
  };

  const onEnter = async () => {
    if (!onAdd) {
      if (results[0]) show(results[0], "full");
      return;
    }
    if (fromMessage && fromMessage.passages.length > 0) return add(fromMessage.passages);
    const q = wholeChapterReference(query) ?? tidyTyped(query);
    const exact = (await resolvePassages([q], translation)).byReference.get(q);
    if (exact) return add([exact]);
    const found = await readMessage(query);
    if (found.passages.length === 1) return add(found.passages);
    if (found.passages.length === 0 && results[0]) return add([results[0]]);
  };

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {live && (
        <div
          data-testid="live-scripture-banner"
          className="border-b border-border bg-sky-500/10 px-3 py-2.5"
        >
          <div className="flex items-start gap-2">
            <BookOpen className="mt-0.5 size-4 shrink-0 text-sky-400" />
            <div className="min-w-0 flex-1">
              <div className="text-sm font-semibold">{live.passage.reference}</div>
              <div className="text-[11px] text-muted-foreground">
                On screen · {live.display === "full" ? "full screen" : "beside lyrics"}
                {live.source === "voice" && " · asked for out loud"}
              </div>
            </div>
          </div>
          <div className="mt-2 flex items-center gap-1.5 pl-6">
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              onClick={() => show(live.passage, live.display === "full" ? "beside" : "full")}
            >
              {live.display === "full" ? "Beside lyrics" : "Full screen"}
            </Button>
            <Button
              size="sm"
              variant="secondary"
              className="h-7 text-xs"
              onClick={clearLiveScripture}
            >
              <X className="mr-1 size-3.5" />
              Take down
            </Button>
          </div>
        </div>
      )}

      <div className="flex items-center gap-2 border-b border-border px-3 py-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              // Typing past a read message means it's no longer the message.
              setFromMessage(null);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") void onEnter();
            }}
            onPaste={(e) => {
              if (!onAdd) return;
              const pasted = e.clipboardData.getData("text");
              // A message, not a reference: read it whole, line breaks and all
              // (the box itself keeps one line).
              if (/\n/.test(pasted.trim()) || pasted.trim().split(/\s+/).length > 4) {
                void readMessage(pasted);
              }
            }}
            placeholder={onAdd ? "Reference, words, or the pastor's message…" : "Reference or words…"}
            className="h-9 pl-9"
            disabled={installed === false}
            data-testid="live-scripture-input"
          />
        </div>
        {installedList.length > 1 && (
          <select
            aria-label="Translation"
            value={translation || installedList[0]?.id || ""}
            onChange={(e) =>
              setSessionTranslation(e.target.value === defaultTranslation ? null : e.target.value)
            }
            className="h-9 max-w-[7rem] rounded-md border border-input bg-background px-2 text-xs"
            title="Translation for this service. The default is set in Settings."
          >
            {installedList.map((t) => (
              <option key={t.id} value={t.id} title={t.name}>
                {t.abbreviation || t.id.toUpperCase()}
                {t.id === defaultTranslation ? " (default)" : ""}
              </option>
            ))}
          </select>
        )}
      </div>

      <div className="flex-1 overflow-y-auto px-2 py-2">
        {installed === false ? (
          <div className="space-y-2 px-3 py-3 text-xs text-muted-foreground">
            <p>
              No Bible is downloaded yet. The {RECOMMENDED_TRANSLATION.name} is free to use; it
              downloads once, then works offline.
            </p>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" onClick={() => void install()} disabled={installing}>
                {installing && <Loader2 className="mr-1.5 size-3.5 animate-spin" />}
                {installing ? "Downloading…" : `Download ${RECOMMENDED_TRANSLATION.abbreviation}`}
              </Button>
              {/* Prep only: leaving for Settings mid-service would close the operator view. */}
              {onAdd && (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={installing}
                  onClick={() =>
                    window.dispatchEvent(new CustomEvent(OPEN_SETTINGS_EVENT, { detail: { page: "scripture" } }))
                  }
                >
                  See all translations
                </Button>
              )}
            </div>
            {installError && <p className="text-destructive">{installError}</p>}
          </div>
        ) : installed === null && engineError ? (
          <div className="px-3 py-3 text-xs text-muted-foreground">
            The Bible text isn't available right now: the app's engine isn't answering.
          </div>
        ) : query.trim() === "" ? (
          <div className="px-3 py-3 text-xs text-muted-foreground">
            Type a reference like <span className="font-medium">jer 29:11</span> or{" "}
            <span className="font-medium">1 cor 13:4-7</span>, or words from the
            passage.{" "}
            {onAdd
              ? "Or paste the pastor's message to add every passage in it. Enter adds to the service."
              : "Enter puts the first result on screen."}
          </div>
        ) : reading ? (
          <div className="flex items-center gap-2 px-3 py-3 text-xs text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" />
            Reading the message…
          </div>
        ) : fromMessage && fromMessage.passages.length > 1 ? (
          <MessagePassages found={fromMessage} onAdd={(p) => void add(p)} />
        ) : results.length === 0 && !searching && !fromMessage?.passages.length ? (
          <div className="px-3 py-3 text-xs text-muted-foreground">No matches.</div>
        ) : null}
        {!reading && !(fromMessage && fromMessage.passages.length > 1) && (
          <ul className="space-y-1">
            {(fromMessage?.passages.length === 1 ? fromMessage.passages : results).map((passage, i) => (
              <PassageResult
                key={passage.reference}
                passage={passage}
                first={i === 0}
                live={live?.passage.reference === passage.reference}
                onShow={(display) => show(passage, display)}
                onAdd={onAdd ? () => void add([passage]) : undefined}
              />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

/** Every passage a pasted message names, to add in one go. */
function MessagePassages({
  found,
  onAdd,
}: {
  found: FoundReferences;
  onAdd: (passages: ScripturePassage[]) => void;
}) {
  return (
    <div className="space-y-2 px-1 py-1" data-testid="scripture-from-message">
      <div className="flex items-center justify-between gap-2 px-2">
        <span className="text-xs text-muted-foreground">
          {found.passages.length} passages in this message
        </span>
        <Button size="sm" className="h-7 gap-1 text-xs" onClick={() => onAdd(found.passages)}>
          <Plus className="size-3.5" />
          Add all
        </Button>
      </div>
      <ul className="space-y-1">
        {found.passages.map((p) => (
          <li key={p.reference} className="group rounded-md px-3 py-2 hover:bg-accent/40">
            <div className="flex items-baseline gap-2">
              <span className="text-sm font-semibold">{p.reference}</span>
              <button
                type="button"
                onClick={() => onAdd([p])}
                className="ml-auto text-[11px] text-muted-foreground opacity-0 hover:text-foreground focus-visible:opacity-100 group-hover:opacity-100"
              >
                Add just this
              </button>
            </div>
            <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">{p.text}</p>
          </li>
        ))}
      </ul>
      {found.unresolved.length > 0 && (
        <p className="px-2 text-[11px] text-amber-400">
          Not found in this Bible: {found.unresolved.join(", ")}
        </p>
      )}
    </div>
  );
}

function PassageResult({
  passage,
  first,
  live,
  onShow,
  onAdd,
}: {
  passage: ScripturePassage;
  first: boolean;
  live: boolean;
  onShow: (display: LiveScriptureDisplay) => void;
  /** Prep: one "Add to service" in place of the show buttons. */
  onAdd?: () => void;
}) {
  return (
    <li
      data-testid="live-scripture-result"
      className={cn(
        "group rounded-md px-3 py-2 hover:bg-accent/40",
        live && "bg-sky-500/10",
      )}
    >
      <div className="flex items-baseline gap-2">
        <span className="text-sm font-semibold">{passage.reference}</span>
        {passage.heading && (
          <span className="min-w-0 truncate text-xs text-muted-foreground">{passage.heading}</span>
        )}
        {first && (
          <span className="text-[10px] text-muted-foreground">Enter</span>
        )}
      </div>
      <p className="mt-0.5 line-clamp-3 text-xs text-muted-foreground">{passage.text}</p>
      <div className="mt-1.5 flex items-center gap-1.5">
        {onAdd ? (
          <Button size="sm" variant="secondary" className="h-7 gap-1 text-xs" onClick={onAdd}>
            <Plus className="size-3.5" />
            Add to service
          </Button>
        ) : (
          <>
            <Button size="sm" variant="secondary" className="h-7 text-xs" onClick={() => onShow("full")}>
              Full screen
            </Button>
            <Button size="sm" variant="outline" className="h-7 text-xs" onClick={() => onShow("beside")}>
              Beside lyrics
            </Button>
          </>
        )}
      </div>
    </li>
  );
}

/** Make `id` the default translation in Settings. */
async function makeDefaultTranslation(id: string): Promise<void> {
  try {
    const app = await getSettings();
    await updateSettings({ ...app, scripture: { ...withScriptureDefaults(app.scripture), translation: id } });
  } catch {
    // This service still uses it (setSessionTranslation); only the default didn't stick.
  }
}
