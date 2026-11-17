import { useEffect, useState } from "react";
import {
  Activity,
  ChevronDown,
  Download,
  Mic,
  Pencil,
  RefreshCw,
  Square,
  Star,
  Trash2,
} from "lucide-react";

import {
  getAppIdentity,
  getSettings,
  listSongs,
  openLink,
  updateSettings,
  type AppSettings,
} from "@/lib/tauri";
import { PRIVACY_EMAIL, PRIVACY_URL, TERMS_URL, officialBuild } from "@/lib/usage";
import { useAudioDevices } from "@/lib/audioDevices";
import { useAudioLevels } from "@/lib/audioLevels";
import { useTranscriptStream } from "@/lib/transcriptStream";
import {
  loadAudioInputPrefs,
  saveAudioInputPrefs,
  type AudioInputPrefs,
} from "@/lib/audioInputPrefs";
import {
  deleteRecording,
  exportRecording,
  useRecordingsList,
} from "@/lib/recordingClient";
import { Input } from "@/components/ui/input";
import { AiRequirements } from "@/components/settings/AiRequirements";
import { GroqKeyField, SpeechSettings } from "@/components/settings/AISettings";
import { InstructionModelPicker } from "@/components/debug/InstructionModelPicker";
import { DisplayLookControls } from "@/components/presentation/DisplayLookDialog";
import { AssistantConnect } from "@/components/settings/AssistantConnect";
import { DiagnosticsExport } from "@/components/settings/DiagnosticsExport";
import { OpenSourceLicencesLink } from "@/components/settings/OpenSourceLicences";
import { ScriptureSettingsPanel } from "@/components/scripture/ScriptureSettingsPanel";
import { settingsPageInfo, type SettingsPageId } from "@/components/settings/settingsPages";
import { InfoTip } from "@/components/ui/InfoTip";
import { FEATURES } from "@/lib/features";
import { setAiEnabled, useAiEnabled } from "@/lib/aiPrefs";
import { refreshInstructionModels, useInstructionModels } from "@/lib/instructionModels";
import { CHECK_FOR_UPDATE_EVENT } from "@/components/UpdatePrompt";
import { isInTauri } from "@/lib/invoke";
import { getVersion } from "@tauri-apps/api/app";
import { Button } from "@/components/ui/button";
import { ModelDownloadProgress } from "@/components/ui/ModelDownloadProgress";
import { VuMeter } from "@/components/ui/VuMeter";
import { useConfirm } from "@/lib/confirm";
import { useToast } from "@/lib/toast";
import type { RecordingSummary } from "@/types/generated";

export function SettingsPage({
  page,
  developerOpen = false,
  onEditRecording,
  onInspectRecording,
}: {
  /** Which sub-page to show; the sidebar lists them under "Settings". */
  page: SettingsPageId;
  /** On "Other": open the developer tools, e.g. on returning from a recording. */
  developerOpen?: boolean;
  /** Open the Golden Recording Editor for a recording id. */
  onEditRecording?: (recordingId: string) => void;
  /** Open the Model Inspector for a recording id. */
  onInspectRecording?: (recordingId: string) => void;
}) {
  const toast = useToast();
  const [settings, setSettings] = useState<AppSettings | null>(null);

  useEffect(() => {
    let cancelled = false;
    getSettings()
      .then((s) => {
        if (!cancelled) setSettings(s);
      })
      .catch((err) => {
        if (cancelled) return;
        toast.error(`Could not load settings: ${err instanceof Error ? err.message : err}`);
      });
    return () => {
      cancelled = true;
    };
  }, [toast]);

  // Every setting here applies the moment it's changed — no Save button.
  async function saveSettings(patch: Partial<AppSettings>) {
    if (!settings) return;
    const previous = settings;
    setSettings({ ...settings, ...patch });
    try {
      setSettings(await updateSettings({ ...settings, ...patch }));
    } catch (err) {
      setSettings(previous);
      toast.error(`Couldn't save: ${err instanceof Error ? err.message : err}`);
    }
  }

  const info = settingsPageInfo(page);

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-border bg-background px-6 py-3">
        <h1 className="text-base font-semibold">{info.label}</h1>
        <p className="text-xs text-muted-foreground">{info.description}</p>
      </header>
      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="mx-auto max-w-2xl space-y-4">
          {!settings ? (
            <div className="text-sm text-muted-foreground">Loading settings…</div>
          ) : (
            <>
              {page === "audio" && (
                <Section>
                  <AudioInputPicker />
                  <div className="flex justify-start">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() =>
                        window.dispatchEvent(new CustomEvent("auto-presenter:open-audio-wizard"))
                      }
                    >
                      Run audio setup again
                    </Button>
                  </div>
                </Section>
              )}

              {page === "ai" && (
                <>
                  <Section>
                    <AiEnabledField />
                    <AiRequirements />
                    <ModelDownloadProgress />
                  </Section>
                  <Section>
                    <SpeechSettings />
                  </Section>
                  {FEATURES.spokenInstructions && (
                    <Section>
                      <div className="space-y-1.5">
                        <div className="flex items-center gap-1.5 text-sm font-medium">
                          Spoken requests
                          <InfoTip>
                            While the AI is on, the worship leader can say &ldquo;go to the
                            chorus&rdquo; or &ldquo;let&rsquo;s sing Way Maker&rdquo; and the
                            slides follow. It uses Groq&rsquo;s online model, so it needs the
                            internet during the service and a free Groq key: the same key as
                            Groq&rsquo;s online speech above.
                          </InfoTip>
                        </div>
                        <SpokenRequestsToggle
                          value={settings.spoken_requests ?? null}
                          onChange={(on) => void saveSettings({ spoken_requests: on })}
                        />
                        <GroqKeyField />
                        {/* Which model, local or hosted: for developers. */}
                        {import.meta.env.DEV && (
                          <details className="mt-2 rounded-md border border-border bg-muted/20 text-xs">
                            <summary className="cursor-pointer select-none px-3 py-2 font-medium text-muted-foreground hover:text-foreground">
                              Developer: choose spoken-request model (local / hosted)
                            </summary>
                            <div className="border-t border-border px-3 pt-2">
                              <InstructionModelPicker />
                            </div>
                          </details>
                        )}
                      </div>
                    </Section>
                  )}
                  <Section title="AI assistants">
                    <AssistantConnect />
                  </Section>
                </>
              )}

              {page === "display" && (
                <Section>
                  <DisplayLookControls />
                </Section>
              )}

              {page === "scripture" && FEATURES.liveScripture && (
                <Section>
                  <ScriptureSettingsPanel />
                </Section>
              )}

              {page === "other" && (
                <>
                  <Section title="Privacy">
                    {/* Only official builds have somewhere to send reports to. */}
                    {officialBuild() && (
                      <>
                        <Field
                          label="Send error reports"
                          hint="A technical report when something goes wrong. Never audio."
                          info={
                            <>
                              What went wrong, this computer&rsquo;s model and the app&rsquo;s recent
                              log, which can include song titles and audio device names. Never audio,
                              or anything said into the microphone.
                            </>
                          }
                        >
                          <Toggle
                            value={settings.send_error_reports}
                            onChange={(on) =>
                              void saveSettings({ send_error_reports: on, error_reports_asked: true })
                            }
                            ariaLabel="Send error reports"
                          />
                        </Field>
                        {settings.send_error_reports && (
                          <ReportContactField
                            value={settings.report_contact ?? ""}
                            onSave={(contact) => void saveSettings({ report_contact: contact })}
                          />
                        )}
                        <Field
                          label="Share usage statistics"
                          hint="Counts and timings. Never titles, lyrics or anything said."
                          info={
                            <>
                              How long services run, how many songs they have, and how often the AI
                              changed the slides compared with someone by hand — plus this
                              install&rsquo;s random number, version, and the computer&rsquo;s system
                              version and chip with each update check.
                            </>
                          }
                        >
                          <Toggle
                            value={settings.share_usage === true}
                            onChange={(on) => void saveSettings({ share_usage: on })}
                            ariaLabel="Share usage statistics"
                          />
                        </Field>
                      </>
                    )}
                    <PrivacyFooter />
                    <div className="mt-3 text-xs text-muted-foreground">
                      <OpenSourceLicencesLink />
                    </div>
                  </Section>

                  <Section title="Updates">
                    <div className="flex items-center justify-between gap-4">
                    <span className="text-sm">
                      <AppVersion />
                    </span>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => window.dispatchEvent(new CustomEvent(CHECK_FOR_UPDATE_EVENT))}
                    >
                      Check for updates
                    </Button>
                  </div>
                  </Section>

                  {/* Closed by default: almost nobody needs these, and the
                      recordings list is long. */}
                  <details
                    open={developerOpen}
                    className="group rounded-lg border border-border bg-card"
                  >
                    <summary className="flex cursor-pointer select-none list-none items-center justify-between px-5 py-4 text-sm font-semibold [&::-webkit-details-marker]:hidden">
                      Developer tools
                      <ChevronDown className="size-4 text-muted-foreground transition-transform group-open:rotate-180" />
                    </summary>
                    <div className="space-y-4 border-t border-border px-5 py-4">
                      <DiagnosticsExport />
                      <p className="text-xs text-muted-foreground">
                        The live debug panel opens with ⌘D.
                      </p>
                      <RecordingSetting settings={settings} onSave={saveSettings} />
                      <RecordingsList onEdit={onEditRecording} onInspect={onInspectRecording} />
                    </div>
                  </details>
                </>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function AppVersion() {
  const [version, setVersion] = useState<string | null>(null);
  useEffect(() => {
    if (isInTauri()) void getVersion().then(setVersion, () => {});
  }, []);
  return <>Church Slides {version ?? ""}</>;
}

/** One group of settings on a page, as a card, with an optional heading. */
function Section({ title, children }: { title?: string; children: React.ReactNode }) {
  return (
    <section className="space-y-4 rounded-lg border border-border bg-card px-5 py-4">
      {title && <h2 className="text-sm font-semibold">{title}</h2>}
      {children}
    </section>
  );
}

/**
 * Service recording, for tuning the AI: off in release builds until someone
 * turns it on here and agrees to the notice — a recording is the whole room,
 * prayer requests and visitors included. Dev builds record by default.
 */
function RecordingSetting({
  settings,
  onSave,
}: {
  settings: AppSettings;
  onSave: (patch: Partial<AppSettings>) => Promise<void>;
}) {
  const confirm = useConfirm();
  const keepDays = settings.recording_keep_days ?? 30;

  async function toggle(on: boolean) {
    if (!on) {
      await onSave({ record_services: false });
      return;
    }
    if (!settings.recording_acknowledged) {
      const ok = await confirm({
        title: "Record services?",
        message:
          "Each service's audio, a transcript of what the AI heard, and when the slides changed will be saved on this computer. Nothing is sent anywhere.\n\n" +
          "A recording picks up everything the microphone hears, including prayers, announcements, visitors and children. Your church is responsible for telling the people present, for example on the service sheet or a notice at the door, and for following the recording and privacy laws where you are.\n\n" +
          "The AI works the same with this off.",
        confirmLabel: "Record services",
        cancelLabel: "Cancel",
      });
      if (!ok) return;
    }
    await onSave({ record_services: true, recording_acknowledged: true });
  }

  return (
    <div className="space-y-3">
      <Field
        label="Record each service"
        hint="Saves audio and slide changes on this computer, to tune the AI."
        info={
          <>
            Each service&rsquo;s audio, a transcript of what the AI heard, and when each slide
            changed. Nothing is sent anywhere. The operator screen shows Recording while it&rsquo;s
            on.
          </>
        }
      >
        <Toggle
          value={settings.record_services}
          onChange={(on) => void toggle(on)}
          ariaLabel="Record each service"
        />
      </Field>
      <Field
        label="Delete recordings after"
        hint="Approved recordings are kept."
      >
        <select
          aria-label="Delete recordings after"
          value={keepDays}
          onChange={(e) => void onSave({ recording_keep_days: Number(e.target.value) })}
          className="h-8 rounded-md border border-border bg-background px-2 text-sm"
        >
          {[7, 30, 90, 365].map((d) => (
            <option key={d} value={d}>
              {d === 365 ? "1 year" : `${d} days`}
            </option>
          ))}
          <option value={0}>Never</option>
        </select>
      </Field>
    </div>
  );
}

/** The install number (to quote when asking for your data to be deleted), a
 * one-click request to see or delete it, and the privacy policy and terms. */
function PrivacyFooter() {
  const [installId, setInstallId] = useState<string | null>(null);
  useEffect(() => {
    getAppIdentity()
      .then((who) => setInstallId(who.install_id))
      .catch(() => undefined);
  }, []);
  const link = "underline underline-offset-2 hover:text-foreground";
  const askToDelete = () => {
    const subject = encodeURIComponent("Delete my Church Slides data");
    const body = encodeURIComponent(
      `Please delete the data you hold for this install.\n\nInstall number: ${installId ?? "(see Settings → Other)"}\n`,
    );
    void openLink(`mailto:${PRIVACY_EMAIL}?subject=${subject}&body=${body}`).catch(() => undefined);
  };
  return (
    <div className="space-y-2 text-xs text-muted-foreground">
      {installId && (
        <div>
          This install&rsquo;s number: <span className="select-all font-mono">{installId}</span>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <button type="button" className={link} onClick={askToDelete}>
          Ask for my data to be deleted
        </button>
        <button
          type="button"
          className={link}
          onClick={() => void openLink(PRIVACY_URL).catch(() => undefined)}
        >
          Privacy policy
        </button>
        <button
          type="button"
          className={link}
          onClick={() => void openLink(TERMS_URL).catch(() => undefined)}
        >
          Terms of use
        </button>
      </div>
    </div>
  );
}

/** Saved when the field loses focus, not per keystroke. */

function ReportContactField({ value, onSave }: { value: string; onSave: (v: string) => void }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  return (
    <div className="space-y-1">
      <label htmlFor="report-contact" className="flex items-center gap-1.5 text-sm font-medium">
        Contact for follow-up <span className="font-normal text-muted-foreground">(optional)</span>
        <InfoTip>
          So the developers can ask about a problem. Leave it empty and reports carry only this
          install&rsquo;s random number.
        </InfoTip>
      </label>
      <div className="text-xs text-muted-foreground">An email or your church&rsquo;s name.</div>
      <Input
        id="report-contact"
        value={draft}
        placeholder="e.g. tech@yourchurch.org"
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => draft.trim() !== value && onSave(draft.trim())}
        className="max-w-sm"
      />
    </div>
  );
}

/** The same switch as the pill beside "Start service". */
function AiEnabledField() {
  const enabled = useAiEnabled();
  return (
    <Field
      label="Use the AI in services"
      hint="Services start with it on."
      info="The AI listens to the singing and changes the slides for you. The AI button during a service switches it off for that service."
    >
      <Toggle value={enabled} onChange={setAiEnabled} ariaLabel="Use the AI in services" />
    </Field>
  );
}

/**
 * A setting: label, a one-line hint, the control on the right. Anything that
 * takes more than a line to explain goes in `info`, behind an (i).
 */
function Field({
  label,
  hint,
  info,
  children,
}: {
  label: string;
  hint?: string;
  info?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="grid grid-cols-[1fr_auto] items-center gap-x-6 gap-y-1">
      <div>
        <div className="flex items-center gap-1.5 text-sm font-medium">
          {label}
          {info && <InfoTip label={`About ${label.toLowerCase()}`}>{info}</InfoTip>}
        </div>
        {hint && <div className="mt-0.5 text-xs text-muted-foreground">{hint}</div>}
      </div>
      <div>{children}</div>
    </div>
  );
}

function AudioInputPicker() {
  const [reloadKey, setReloadKey] = useState(0);
  const state = useAudioDevices(reloadKey);
  const [prefs, setPrefs] = useState<AudioInputPrefs>(() => loadAudioInputPrefs());
  const levels = useAudioLevels(50);
  const stream = useTranscriptStream();
  const testing = stream.session === "running" || stream.session === "starting";

  // Stop the test session if the user navigates away from Settings.
  useEffect(() => {
    return () => {
      stream.stop().catch(() => undefined);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function toggleTest() {
    try {
      if (testing) await stream.stop();
      // Mock backend: we only need captured audio frames for the level
      // meter, not transcription — no model download, instant start.
      else await stream.start({ backend: "mock" });
    } catch {
      // surfaced via stream.error
    }
  }

  function updatePrefs(next: AudioInputPrefs) {
    setPrefs(next);
    saveAudioInputPrefs(next);
  }

  function handleSelectDevice(value: string) {
    const idx = value === "default" ? null : Number.parseInt(value, 10);
    const resolvedIdx = Number.isFinite(idx) ? (idx as number | null) : null;
    // Pin the device by name too — names survive across reboots / USB
    // re-plug events that would otherwise shift sounddevice indices.
    const matched =
      resolvedIdx !== null && state.kind === "ready"
        ? state.devices.find((d) => d.index === resolvedIdx)
        : null;
    updatePrefs({
      ...prefs,
      device_index: resolvedIdx,
      device_name: matched?.name ?? null,
    });
  }

  // Default to the OS-default device when no preference is saved yet.
  const selectedValue: string =
    prefs.device_index !== null
      ? String(prefs.device_index)
      : state.kind === "ready"
      ? String(state.devices.find((d) => d.is_default)?.index ?? "default")
      : "default";

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-[1fr_auto] items-center gap-x-6">
        <div>
          <div className="flex items-center gap-1.5 text-sm font-medium">
            Input device
            <InfoTip>
              A feed from the sound desk works best: the AI hears the singing clearly, without the
              room&rsquo;s noise.
            </InfoTip>
          </div>
          <div className="mt-0.5 text-xs text-muted-foreground">Microphone or line-in.</div>
        </div>
        <div className="flex items-center gap-2">
          {state.kind === "ready" && state.devices.length > 0 && (
            <select
              value={selectedValue}
              onChange={(e) => handleSelectDevice(e.target.value)}
              className="h-9 rounded-md border border-input bg-background px-3 text-sm"
              aria-label="Audio input device"
            >
              {state.devices.map((d) => (
                <option key={d.index} value={d.index}>
                  {d.name}
                  {d.is_default ? " (system default)" : ""}
                </option>
              ))}
            </select>
          )}
          <Button
            type="button"
            variant={testing ? "destructive" : "outline"}
            size="sm"
            onClick={toggleTest}
            disabled={!testing && !levels.connected}
            title="Start a brief session so the level meter shows live input"
          >
            {testing ? <Square className="size-3.5" /> : <Mic className="size-3.5" />}
            {testing ? "Stop test" : "Test input"}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={() => setReloadKey((k) => k + 1)}
            title="Rescan devices"
            aria-label="Rescan devices"
          >
            <RefreshCw className="size-4" />
          </Button>
        </div>
      </div>
      <div className="space-y-1">
        <VuMeter
          rmsDbfs={levels.latest?.rms_dbfs ?? null}
          peak={levels.latest?.peak ?? 0}
        />
        <div className="text-[10px] text-muted-foreground">
          {testing && levels.latest
            ? `${levels.latest.rms_dbfs.toFixed(1)} dBFS · peak ${levels.latest.peak.toFixed(2)}`
            : testing
            ? "Listening… speak or play audio into the selected device."
            : "Test input to check it's picking up sound."}
        </div>
      </div>
      {state.kind === "loading" && (
        <div className="text-xs text-muted-foreground">Looking for devices…</div>
      )}
      {state.kind === "error" && (
        <div className="text-xs text-amber-400">
          AI engine offline — launch the desktop app to configure audio.
          <span className="block text-muted-foreground">{state.message}</span>
        </div>
      )}
      {state.kind === "ready" && state.devices.length === 0 && (
        <div className="text-xs text-amber-400">
          No input devices detected. Connect a microphone or audio interface,
          then click rescan.
        </div>
      )}
    </div>
  );
}

function RecordingsList({
  onEdit,
  onInspect,
}: {
  onEdit?: (id: string) => void;
  onInspect?: (id: string) => void;
}) {
  const toast = useToast();
  const confirm = useConfirm();
  const { state, refresh } = useRecordingsList();
  const [busyId, setBusyId] = useState<string | null>(null);
  /**
   * song_id -> title for the whole list. One `listSongs` beats a `getSong`
   * per recording — the library is a few hundred rows and every row in the
   * list needs a lookup.
   */
  const [songTitles, setSongTitles] = useState<Record<string, string>>({});

  useEffect(() => {
    let cancelled = false;
    listSongs()
      .then((songs) => {
        if (cancelled) return;
        const map: Record<string, string> = {};
        for (const s of songs) map[s.id] = s.title;
        setSongTitles(map);
      })
      .catch(() => {
        // Library unavailable (browser dev) — rows fall back to song ids.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function onDelete(id: string) {
    const ok = await confirm({
      title: "Delete this recording?",
      message: "The audio file and ground-truth events will be removed from disk. This can't be undone.",
      confirmLabel: "Delete",
      variant: "destructive",
    });
    if (!ok) return;
    setBusyId(id);
    try {
      const deleted = await deleteRecording(id);
      if (deleted) {
        toast.success("Recording deleted.");
        refresh();
      } else {
        toast.error("Recording not found.");
      }
    } catch (err) {
      toast.error(`Delete failed: ${err instanceof Error ? err.message : err}`);
    } finally {
      setBusyId(null);
    }
  }

  async function onExport(id: string) {
    setBusyId(id);
    try {
      const reply = await exportRecording(id);
      if (reply.error) {
        toast.error(`Export failed: ${reply.error}`);
      } else if (reply.zipPath) {
        toast.success(`Exported to ${reply.zipPath}`);
      }
    } catch (err) {
      toast.error(`Export failed: ${err instanceof Error ? err.message : err}`);
    } finally {
      setBusyId(null);
    }
  }

  if (state.kind === "loading") {
    return (
      <div className="rounded-md border border-border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
        Loading recordings…
      </div>
    );
  }
  if (state.kind === "error") {
    return (
      <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-400">
        AI engine offline — recordings will be listed once the desktop app is running.
      </div>
    );
  }
  if (state.recordings.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-border bg-muted/20 px-3 py-3 text-center text-xs text-muted-foreground">
        No recordings yet. Run a service with recording enabled to capture audio + ground truth.
      </div>
    );
  }
  return (
    <ul className="divide-y divide-border rounded-md border border-border bg-muted/20">
      {state.recordings.map((rec) => (
        <RecordingRow
          key={rec.id}
          recording={rec}
          songTitles={songTitles}
          onInspect={onInspect ? () => onInspect(rec.id) : undefined}
          busy={busyId === rec.id}
          onEdit={onEdit ? () => onEdit(rec.id) : undefined}
          onExport={() => onExport(rec.id)}
          onDelete={() => onDelete(rec.id)}
        />
      ))}
    </ul>
  );
}

function RecordingRow({
  recording,
  songTitles,
  busy,
  onEdit,
  onInspect,
  onExport,
  onDelete,
}: {
  recording: RecordingSummary;
  songTitles: Record<string, string>;
  busy: boolean;
  onEdit?: () => void;
  onInspect?: () => void;
  onExport: () => void;
  onDelete: () => void;
}) {
  const songIds = recording.song_ids ?? [];
  // Unresolved ids still show something rather than a blank row.
  const titles = songIds.map((id) => songTitles[id] ?? id);
  const approved = recording.golden_approved ?? false;

  return (
    <li
      className="flex items-center justify-between gap-3 px-3 py-2 text-xs"
      title={recording.id}
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          {approved && (
            <Star
              className="size-3.5 shrink-0 fill-amber-400 text-amber-400"
              aria-label="Approved golden recording"
            />
          )}
          <span className="truncate font-medium text-foreground">
            {titles.length > 0
              ? titles.join(" · ")
              : new Date(recording.started_at_ms).toLocaleString()}
          </span>
        </div>
        <div className="text-muted-foreground">
          {titles.length > 0 && (
            <>
              {new Date(recording.started_at_ms).toLocaleString()}
              {" · "}
            </>
          )}
          {formatDuration(recording.duration_seconds)}
          {" · "}
          {recording.ground_truth_count} slide change
          {recording.ground_truth_count === 1 ? "" : "s"}
          {/* The star already says "approved"; only the un-signed-off state
              needs spelling out. */}
          {!approved && recording.has_golden ? " · draft" : ""}
        </div>
      </div>
      {onEdit && (
        <Button
          type="button"
          variant="ghost"
          size="icon"
          disabled={busy}
          onClick={onEdit}
          aria-label="Edit golden recording"
          title="Edit transitions (golden editor)"
        >
          <Pencil className="size-3.5" />
        </Button>
      )}
      {onInspect && (
        <Button
          type="button"
          variant="ghost"
          size="icon"
          disabled={busy}
          onClick={onInspect}
          aria-label="Inspect model"
          title="Inspect what the matcher predicted"
        >
          <Activity className="size-3.5" />
        </Button>
      )}
      <Button
        type="button"
        variant="ghost"
        size="icon"
        disabled={busy}
        onClick={onExport}
        aria-label="Export recording"
        title="Export as zip"
      >
        <Download className="size-3.5" />
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        disabled={busy}
        onClick={onDelete}
        aria-label="Delete recording"
        title="Delete recording"
      >
        <Trash2 className="size-3.5" />
      </Button>
    </li>
  );
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const minutes = Math.floor(seconds / 60);
  const remaining = Math.round(seconds % 60);
  return `${minutes} min ${remaining} s`;
}

/** Act on spoken requests. Unset, the engine decides at each service: on when
 * the chosen model is hosted and its key is saved — shown here the same way. */
function SpokenRequestsToggle({
  value,
  onChange,
}: {
  value: boolean | null;
  onChange: (on: boolean) => void;
}) {
  const models = useInstructionModels();
  useEffect(() => {
    if (!models.seen) void refreshInstructionModels().catch(() => undefined);
  }, [models.seen]);
  const active = models.models.find((m) => m.id === models.activeModelId);
  const keyed = active?.model_kind === "hosted" && active.has_api_key === true;
  const on = value ?? keyed;
  return (
    <Field
      label="Act on spoken requests"
      hint={
        value === null
          ? keyed
            ? "On, because a Groq key is saved."
            : "Turns on by itself once a Groq key is saved below."
          : "“Go to the chorus”, “let’s sing Way Maker”, said out loud during a service."
      }
    >
      <Toggle value={on} onChange={onChange} ariaLabel="Act on spoken requests" />
    </Field>
  );
}

function Toggle({
  value,
  onChange,
  ariaLabel,
}: {
  value: boolean;
  onChange: (v: boolean) => void;
  ariaLabel: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={value}
      aria-label={ariaLabel}
      onClick={() => onChange(!value)}
      className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
        value ? "bg-emerald-500" : "bg-zinc-700"
      }`}
    >
      <span
        className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
          value ? "translate-x-6" : "translate-x-1"
        }`}
      />
    </button>
  );
}
