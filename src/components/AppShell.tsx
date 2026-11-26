import { lazy, Suspense, useEffect, useState } from "react";
import {
  Home as HomeIcon,
  Library as LibraryIcon,
  MessageSquare,
  Settings as SettingsIcon,
} from "lucide-react";

import { DebugPanel } from "@/components/debug/DebugPanel";
import { HomeScreen } from "@/components/home/HomeScreen";
import { LibraryView } from "@/components/library/LibraryView";
import { AudioSetupWizard } from "@/components/onboarding/AudioSetupWizard";
import { BringSongsPrompt } from "@/components/onboarding/BringSongsPrompt";
import { UsageConsentPrompt, useAppOpenUsage } from "@/components/onboarding/UsageConsentPrompt";
import { FeedbackHost, OPEN_FEEDBACK_EVENT } from "@/components/FeedbackDialog";
import { OperatorView } from "@/components/presentation/OperatorView";
import { PresentationView } from "@/components/presentation/PresentationView";
import { SettingsPage } from "@/components/settings/SettingsPage";
import {
  DEFAULT_SETTINGS_PAGE,
  settingsPages,
  type SettingsPageId,
} from "@/components/settings/settingsPages";
import { SetlistBuilder } from "@/components/setlists/SetlistBuilder";
import { OPEN_SETTINGS_EVENT } from "@/components/setlists/AiReadyPill";
import { SidecarStatusBadge } from "@/components/SidecarStatusBadge";
import { IncomingSync } from "@/components/peerSync/IncomingSync";
import { UpdatePrompt } from "@/components/UpdatePrompt";
import { SongEditor } from "@/components/library/SongEditor";
import { useLibraryChanged } from "@/lib/assistantConnect";
import { LibrarySwitcher } from "@/components/LibrarySwitcher";
import { loadLibraryLook } from "@/lib/displayLook";
import { useLibrarySync } from "@/lib/librarySync";
import { useAssistantImport } from "@/lib/assistantImport";
import { loadAudioInputPrefs } from "@/lib/audioInputPrefs";
import { getAiEngineStatus, listSongs, type LibrarySyncStatus } from "@/lib/tauri";
import { fillMissingVerses } from "@/lib/scripture/prepared";
import { monitorCount } from "@/lib/presentation/projection";
import { copiedService, newService, shortServiceDate } from "@/lib/setlistName";
import { ConfirmProvider } from "@/lib/confirm";
import { ToastProvider, useToast } from "@/lib/toast";
import {
  createSetlist,
  duplicateSetlist,
  getSetlist,
  getSettings,
  listSetlists,
  type SetlistDetail,
} from "@/lib/tauri";
import { cn } from "@/lib/utils";
import brandMark from "../../brand/church-slides-mark.svg";

/**
 * The Golden Recording Editor and Model Inspector are developer tools: dev
 * builds only. `import.meta.env.DEV` is false in a release build, so these
 * dynamic imports drop out and neither tool reaches the bundle.
 */
const RecordingEditor = import.meta.env.DEV
  ? lazy(() =>
      import("@/components/recordings/RecordingEditor").then((m) => ({ default: m.RecordingEditor })),
    )
  : null;
const ModelInspector = import.meta.env.DEV
  ? lazy(() =>
      import("@/components/recordings/ModelInspector").then((m) => ({ default: m.ModelInspector })),
    )
  : null;

type View =
  | { kind: "home" }
  | { kind: "library" }
  /** `fromSetlist`: opened from a setlist, so back returns there. */
  | { kind: "editor"; songId: string | null; fromSetlist?: string }
  /** `justCreated`: opened straight from "New setlist", so leaving it empty and
   * unnamed discards it. */
  | { kind: "setlist"; id: string; justCreated?: boolean }
  | { kind: "settings"; page: SettingsPageId; developerOpen?: boolean }
  | { kind: "recordingEditor"; recordingId: string }
  | { kind: "modelInspector"; recordingId: string }
  | {
      kind: "presentation";
      setlist: SetlistDetail;
      initialPosition?: { itemIdx: number; slideIdx: number };
    }
  | {
      kind: "operator";
      setlist: SetlistDetail;
      initialPosition?: { itemIdx: number; slideIdx: number };
    };

type Section = "home" | "library" | "settings";

/**
 * How to run a service. "auto" is what the Start button does: the operator
 * view with the audience screen on the projector when a second display is
 * connected, full-screen presenting on this screen when there isn't one.
 */
export type StartHow = "auto" | "operator" | "single";

export function AppShell() {
  return (
    <ToastProvider>
      <ConfirmProvider>
        <Inner />
      </ConfirmProvider>
    </ToastProvider>
  );
}

function Inner() {
  const toast = useToast();
  // The organization library's change notices, for as long as the app runs.
  const librarySync = useLibrarySync();
  const [view, setView] = useState<View>({ kind: "home" });
  const [reloadKey, setReloadKey] = useState(0);
  const [recordServices, setRecordServices] = useState(true);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [consentAsking, setConsentAsking] = useState(false);

  const bumpReload = () => setReloadKey((k) => k + 1);

  // Usage statistics, if the user agreed: once a day, the library's size and
  // whether the AI engine came up (after it's done starting).
  useAppOpenUsage(async () => {
    let engine = await getAiEngineStatus().catch(() => null);
    for (let i = 0; i < 60 && engine?.state === "starting"; i++) {
      await new Promise((r) => window.setTimeout(r, 1000));
      engine = await getAiEngineStatus().catch(() => null);
    }
    const songs = await listSongs().then((l) => l.length).catch(() => -1);
    return { songs, ai: engine?.state ?? "unknown" };
  });

  // An AI assistant edited the library through the MCP server.
  useLibraryChanged(bumpReload);
  // The display look belongs to the open library.
  useEffect(() => void loadLibraryLook(), []);
  useLibraryChanged(() => void loadLibraryLook());
  useAssistantImport((summary) => {
    toast.success(`AI assistant — ${summary}`);
    bumpReload();
  });

  // First launch opens audio setup — but only once the AI engine is actually
  // up. The wizard exists to set up live AI's input; on a machine or build
  // without the engine it's a dead end in front of an app that otherwise
  // works fine by hand. (Settings can still open it deliberately.)
  useEffect(() => {
    if (loadAudioInputPrefs().setup_completed) return;
    let cancelled = false;
    let timer: number | undefined;
    const check = () => {
      getAiEngineStatus()
        .then((engine) => {
          if (cancelled) return;
          if (engine.state === "ready") setWizardOpen(true);
          else if (engine.state === "starting") timer = window.setTimeout(check, 1000);
        })
        .catch(() => undefined);
    };
    check();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, []);

  useEffect(() => {
    const handler = () => setWizardOpen(true);
    window.addEventListener("auto-presenter:open-audio-wizard", handler);
    return () =>
      window.removeEventListener("auto-presenter:open-audio-wizard", handler);
  }, []);

  // "AI Not Ready" beside "Start service" sends the operator here to finish
  // setting the AI up; other buttons name the page (`detail.page`).
  useEffect(() => {
    const handler = (event: Event) => {
      const page = (event as CustomEvent<{ page?: SettingsPageId } | undefined>).detail?.page;
      setView({ kind: "settings", page: page ?? "ai" });
    };
    window.addEventListener(OPEN_SETTINGS_EVENT, handler);
    return () => window.removeEventListener(OPEN_SETTINGS_EVENT, handler);
  }, []);

  // Track the record-services preference across the app so PresentationView
  // can opt-in/out per service. Falls back to true (the default) if settings
  // aren't reachable — early dev users naturally accumulate eval material.
  useEffect(() => {
    let cancelled = false;
    getSettings()
      .then((s) => {
        if (!cancelled) setRecordServices(s.record_services);
      })
      .catch(() => {
        // browser dev mode or settings not yet persisted — leave default
      });
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  function activeSection(): Section {
    if (view.kind === "home" || view.kind === "setlist") return "home";
    if (view.kind === "editor" && view.fromSetlist) return "home";
    if (
      view.kind === "settings" ||
      view.kind === "recordingEditor" ||
      view.kind === "modelInspector"
    )
      return "settings";
    return "library";
  }

  async function onCopySetlist(sourceId: string) {
    try {
      const all = await listSetlists();
      const source = all.find((s) => s.id === sourceId);
      const { name, date } = source ? copiedService(source, all) : newService(all);
      const id = await duplicateSetlist(sourceId, name, date);
      setView({ kind: "setlist", id });
      toast.success(`Copied. This is the new service for ${shortServiceDate(date)}.`);
    } catch (err) {
      toast.error(`Copy failed: ${err instanceof Error ? err.message : err}`);
    }
  }

  async function onCreateSetlist() {
    try {
      const { name, date } = newService(await listSetlists());
      const id = await createSetlist(name, date);
      setView({ kind: "setlist", id, justCreated: true });
    } catch (err) {
      toast.error(`Create failed: ${err instanceof Error ? err.message : err}`);
    }
  }

  async function startService(
    setlistId: string,
    how: StartHow = "auto",
    initialPosition?: { itemIdx: number; slideIdx: number },
  ) {
    try {
      let detail = await getSetlist(setlistId);
      if (!detail) {
        toast.error("Setlist not found.");
        return;
      }
      // A passage an assistant added has only its reference until a Bible
      // fills it in — usually when the setlist is opened, but a service can
      // start straight from Home.
      if (await fillMissingVerses(detail).catch(() => false)) {
        detail = (await getSetlist(setlistId)) ?? detail;
      }
      if (detail.items.length === 0) {
        toast.error("Add at least one song before starting a service.");
        return;
      }
      // Clamp the resume position to the current setlist shape — songs may
      // have been added/removed since the last session was saved.
      let safe: { itemIdx: number; slideIdx: number } | undefined;
      if (initialPosition) {
        const itemIdx = Math.max(
          0,
          Math.min(initialPosition.itemIdx, detail.items.length - 1),
        );
        safe = { itemIdx, slideIdx: Math.max(0, initialPosition.slideIdx) };
      }
      const kind =
        how === "operator"
          ? "operator"
          : how === "single"
            ? "presentation"
            : (await monitorCount()) > 1
              ? "operator"
              : "presentation";
      setView({ kind, setlist: detail, initialPosition: safe });
    } catch (err) {
      toast.error(`Start failed: ${err instanceof Error ? err.message : err}`);
    }
  }

  // A service ends back on its setlist — where the next one is prepared.
  const exitService = (setlistId: string) => {
    setView({ kind: "setlist", id: setlistId });
    bumpReload();
  };

  return (
    <>
      {view.kind === "presentation" ? (
        <PresentationView
          // `key` forces a fresh mount (and a fresh state machine) when the
          // setlist changes. Cleaner than reset-on-prop-change inside the hook.
          key={view.setlist.id}
          setlist={view.setlist}
          initialPosition={view.initialPosition}
          recordingEnabled={recordServices}
          onExit={() => exitService(view.setlist.id)}
        />
      ) : view.kind === "operator" ? (
        <OperatorView
          key={view.setlist.id}
          setlist={view.setlist}
          initialPosition={view.initialPosition}
          recordingEnabled={recordServices}
          onExit={() => exitService(view.setlist.id)}
        />
      ) : view.kind === "setlist" ? (
        <SetlistBuilder
          key={view.id}
          setlistId={view.id}
          justCreated={view.justCreated ?? false}
          reloadKey={reloadKey}
          onClose={() => {
            setView({ kind: "home" });
            bumpReload();
          }}
          onStartService={(id, how) => void startService(id, how)}
          onEditSong={(songId) => setView({ kind: "editor", songId, fromSetlist: view.id })}
        />
      ) : (
        <div className="flex h-full">
          <Sidebar
            active={activeSection()}
            onSelectHome={() => {
              setView({ kind: "home" });
              bumpReload();
            }}
            onSelectLibrary={() => {
              setView({ kind: "library" });
              bumpReload();
            }}
            settingsPage={view.kind === "settings" ? view.page : view.kind === "recordingEditor" || view.kind === "modelInspector" ? "other" : null}
            onSelectSettings={(page) => setView({ kind: "settings", page })}
            librarySync={librarySync}
          />
          <main className="flex-1 overflow-hidden bg-background">
            {view.kind === "home" && (
              <HomeScreen
                reloadKey={reloadKey}
                onCreateSetlist={onCreateSetlist}
                onCopySetlist={(id) => void onCopySetlist(id)}
                onOpenSetlist={(id) => setView({ kind: "setlist", id })}
                onContinueService={(setlistId, jumpTo) =>
                  void startService(setlistId, "auto", jumpTo)
                }
                onStartService={(setlistId) => void startService(setlistId)}
                onOpenLibrary={() => setView({ kind: "library" })}
              />
            )}
            {view.kind === "library" && (
              <LibraryView
                onOpenSong={(id) => setView({ kind: "editor", songId: id })}
                reloadKey={reloadKey}
                onAfterImport={bumpReload}
              />
            )}
            {view.kind === "editor" && (
              <SongEditor
                songId={view.songId}
                backLabel={view.fromSetlist ? "setlist" : "Library"}
                onClose={() => {
                  setView(view.fromSetlist ? { kind: "setlist", id: view.fromSetlist } : { kind: "library" });
                  bumpReload();
                }}
              />
            )}
            {view.kind === "settings" && (
              <SettingsPage
                page={view.page}
                developerOpen={view.developerOpen}
                onEditRecording={
                  RecordingEditor
                    ? (id) => setView({ kind: "recordingEditor", recordingId: id })
                    : undefined
                }
                onInspectRecording={
                  ModelInspector
                    ? (id) => setView({ kind: "modelInspector", recordingId: id })
                    : undefined
                }
              />
            )}
            {view.kind === "recordingEditor" && RecordingEditor && (
              <Suspense fallback={null}>
                <RecordingEditor
                  key={view.recordingId}
                  recordingId={view.recordingId}
                  onBack={() => setView({ kind: "settings", page: "other", developerOpen: true })}
                />
              </Suspense>
            )}
            {view.kind === "modelInspector" && ModelInspector && (
              <Suspense fallback={null}>
                <ModelInspector
                  key={view.recordingId}
                  recordingId={view.recordingId}
                  onBack={() => setView({ kind: "settings", page: "other", developerOpen: true })}
                />
              </Suspense>
            )}
          </main>
        </div>
      )}
      <DebugPanel />
      {/* Another operator sending a setlist, or asking to copy one. */}
      <IncomingSync
        onImported={(m) => {
          toast.success(m);
          bumpReload();
        }}
        onError={(m) => toast.error(m)}
      />
      {wizardOpen && (
        <AudioSetupWizard onClose={() => setWizardOpen(false)} />
      )}
      {/* A newer release, offered at startup — never in the middle of a service. */}
      <UpdatePrompt held={view.kind === "presentation" || view.kind === "operator"} />
      <UsageConsentPrompt
        held={wizardOpen || view.kind === "presentation" || view.kind === "operator"}
        onAskingChange={setConsentAsking}
      />
      <BringSongsPrompt
        held={wizardOpen || consentAsking || view.kind === "presentation" || view.kind === "operator"}
        onImported={(message) => {
          toast.success(message);
          bumpReload();
        }}
      />
      <FeedbackHost />
    </>
  );
}

function Sidebar({
  active,
  settingsPage,
  onSelectHome,
  onSelectLibrary,
  onSelectSettings,
  librarySync,
}: {
  active: Section;
  /** The open settings sub-page, or null when settings aren't open. */
  settingsPage: SettingsPageId | null;
  onSelectHome: () => void;
  onSelectLibrary: () => void;
  onSelectSettings: (page: SettingsPageId) => void;
  librarySync: LibrarySyncStatus;
}) {
  return (
    <aside className="flex w-56 flex-col overflow-y-auto border-r border-border bg-muted/30">
      <div className="flex items-center justify-between px-4 py-4">
        <span className="flex items-center gap-2">
          <img src={brandMark} alt="" className="h-6 w-auto" />
          <span className="font-brand text-base font-semibold tracking-tight">Church Slides</span>
        </span>
        <SidecarStatusBadge />
      </div>
      <LibrarySwitcher onSwitched={onSelectHome} sync={librarySync} />
      <nav className="flex flex-1 flex-col gap-1 px-2">
        <SidebarLink
          icon={<HomeIcon className="size-4" />}
          label="Home"
          active={active === "home"}
          onClick={onSelectHome}
        />
        <SidebarLink
          icon={<LibraryIcon className="size-4" />}
          label="Library"
          active={active === "library"}
          onClick={onSelectLibrary}
        />
      </nav>
      {/* Settings' sub-pages open beneath it, pushing it (and feedback) up. */}
      <nav className="flex flex-col gap-1 px-2 pb-3">
        <SidebarLink
          icon={<MessageSquare className="size-4" />}
          label="Send feedback"
          active={false}
          onClick={() => window.dispatchEvent(new CustomEvent(OPEN_FEEDBACK_EVENT))}
        />
        <SidebarLink
          icon={<SettingsIcon className="size-4" />}
          label="Settings"
          active={active === "settings" && settingsPage === null}
          expanded={settingsPage !== null}
          onClick={() => onSelectSettings(settingsPage ?? DEFAULT_SETTINGS_PAGE)}
        />
        {settingsPage !== null && (
          <ul aria-label="Settings pages" className="flex flex-col gap-0.5">
            {settingsPages().map((page) => (
              <li key={page.id}>
                <button
                  type="button"
                  onClick={() => onSelectSettings(page.id)}
                  aria-current={page.id === settingsPage ? "page" : undefined}
                  className={cn(
                    "w-full rounded-md py-1.5 pl-9 pr-3 text-left text-sm transition-colors",
                    page.id === settingsPage
                      ? "bg-accent text-accent-foreground"
                      : "text-muted-foreground hover:bg-accent/40 hover:text-foreground",
                  )}
                >
                  {page.label}
                </button>
              </li>
            ))}
          </ul>
        )}
      </nav>
    </aside>
  );
}

function SidebarLink({
  icon,
  label,
  active,
  expanded,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  active: boolean;
  /** Has sub-items showing beneath it. */
  expanded?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-expanded={expanded}
      className={cn(
        "flex items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors",
        active
          ? "bg-accent text-accent-foreground"
          : expanded
            ? "text-foreground hover:bg-accent/40"
            : "text-muted-foreground hover:bg-accent/40 hover:text-foreground",
      )}
    >
      {icon}
      {label}
    </button>
  );
}
