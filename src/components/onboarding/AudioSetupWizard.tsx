/**
 * First-launch audio setup wizard.
 *
 * Three-step flow per PHASE_4.md:
 *   1. Choose input source (vocal-aux line-out vs microphone)
 *   2. Pick the actual device and test it: the level meter moves, and a
 *      check confirms sound is coming through
 *   3. Confirm + save (the speech model is picked for the hardware
 *      silently — nobody setting up a church laptop should meet "Whisper")
 *
 * The wizard is intentionally a full-screen overlay rather than a
 * dialog: this is the most important configuration step a non-technical
 * operator will encounter, and burying it in a modal undersells it.
 *
 * Auto-shows on first launch (`setup_completed=false`); also
 * re-launchable from Settings → Audio input → "Run audio setup again".
 */

import { useEffect, useMemo, useState } from "react";
import {
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Headphones,
  Mic,
  RefreshCw,
} from "lucide-react";

import { useAudioDevices } from "@/lib/audioDevices";
import { useAudioLevels } from "@/lib/audioLevels";
import {
  loadAudioInputPrefs,
  saveAudioInputPrefs,
  type AudioInputSource,
} from "@/lib/audioInputPrefs";
import { useModelRecommendation } from "@/lib/modelRecommendation";
import { type AiAvailability, sizeText, useAiAvailability } from "@/lib/aiAvailability";
import { useTranscriptStream } from "@/lib/transcriptStream";
import { Button } from "@/components/ui/button";
import { VuMeter } from "@/components/ui/VuMeter";
import { cn } from "@/lib/utils";

export interface AudioSetupWizardProps {
  onClose: () => void;
}

type WizardStep = 1 | 2 | 3;

const STEP_LABELS = ["Input source", "Pick & test", "Done"] as const;

/** A level above this during the test counts as "heard you". */
const HEARD_DBFS = -45;

export function AudioSetupWizard({ onClose }: AudioSetupWizardProps) {
  const initial = useMemo(() => loadAudioInputPrefs(), []);
  const [step, setStep] = useState<WizardStep>(1);
  const [source, setSource] = useState<AudioInputSource>(
    initial.source === "unknown" ? "vocal_aux" : initial.source,
  );
  const [deviceIndex, setDeviceIndex] = useState<number | null>(
    initial.device_index,
  );
  const [deviceName, setDeviceName] = useState<string | null>(initial.device_name);

  const recommendation = useModelRecommendation();
  const { availability, prepare } = useAiAvailability();

  function finish() {
    const rec =
      recommendation.kind === "ready" ? recommendation.recommendation : null;
    const recommendedBackend = rec?.backend === "mlx" ? "mlx" : "whisper";
    saveAudioInputPrefs({
      ...initial,
      device_index: deviceIndex,
      device_name: deviceName,
      source: source === "unknown" ? "vocal_aux" : source,
      setup_completed: true,
      ...(rec
        ? {
            asr_backend: recommendedBackend,
            asr_model: rec.model_size,
            asr_chosen: true,
          }
        : {}),
    });
    // Fetch the speech model now, at setup, rather than at the start of the
    // first service: it's hundreds of MB, and a fresh install used to say
    // "Ready to go" with none of it here. Saved first, so the download is
    // for the model just chosen. It carries on after setup closes; the home
    // screen shows how far it's got.
    if (availability.kind === "needs_download" || availability.kind === "download_failed") {
      void prepare().catch(() => undefined);
    }
    onClose();
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-background"
      role="dialog"
      aria-modal="true"
      aria-labelledby="wizard-title"
    >
      <div className="flex h-full w-full max-w-4xl flex-col px-8 py-10">
        <header className="space-y-2">
          <div className="flex items-center justify-between">
            <h1 id="wizard-title" className="text-2xl font-semibold">
              Audio setup
            </h1>
            <Button variant="ghost" size="sm" onClick={onClose} aria-label="Close setup">
              Skip
            </Button>
          </div>
          <Stepper current={step} />
        </header>

        <main className="mt-8 flex-1 overflow-y-auto">
          {step === 1 && (
            <Step1 source={source} onChange={setSource} />
          )}
          {step === 2 && (
            <Step2
              source={source}
              deviceIndex={deviceIndex}
              onDeviceChange={(idx, name) => {
                setDeviceIndex(idx);
                setDeviceName(name);
              }}
            />
          )}
          {step === 3 && (
            <Step3
              availability={availability}
              source={source}
              deviceName={deviceName}
              recommendation={recommendation}
            />
          )}
        </main>

        <footer className="mt-6 flex items-center justify-between border-t border-border pt-4">
          <Button
            variant="ghost"
            disabled={step === 1}
            onClick={() => setStep((s) => Math.max(1, s - 1) as WizardStep)}
          >
            <ChevronLeft className="mr-1 size-4" />
            Back
          </Button>
          {step < 3 ? (
            <Button onClick={() => setStep((s) => Math.min(3, s + 1) as WizardStep)}>
              Next
              <ChevronRight className="ml-1 size-4" />
            </Button>
          ) : (
            <Button onClick={finish}>
              Save and finish
              <CheckCircle2 className="ml-1 size-4" />
            </Button>
          )}
        </footer>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------- stepper

function Stepper({ current }: { current: WizardStep }) {
  return (
    <ol className="flex items-center gap-2 text-xs">
      {STEP_LABELS.map((label, i) => {
        const stepNum = (i + 1) as WizardStep;
        const active = stepNum === current;
        const done = stepNum < current;
        return (
          <li
            key={label}
            className={cn(
              "flex items-center gap-2 rounded-full border px-3 py-1",
              active && "border-foreground bg-foreground/10 text-foreground",
              done && "border-emerald-500/40 bg-emerald-500/10 text-emerald-200",
              !active && !done && "border-border text-muted-foreground",
            )}
          >
            <span className="text-[10px] font-semibold">{stepNum}</span>
            {label}
          </li>
        );
      })}
    </ol>
  );
}

// --------------------------------------------------------------------- Step 1

function Step1({
  source,
  onChange,
}: {
  source: AudioInputSource;
  onChange: (next: AudioInputSource) => void;
}) {
  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-medium">Where will the audio come from?</h2>
        <p className="text-sm text-muted-foreground">
          The matcher works on both, but a clean vocal feed is significantly
          easier for the AI to transcribe than a mic in the room.
        </p>
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <SourceCard
          icon={<Headphones className="size-6" />}
          title="Soundboard line-out"
          recommended
          selected={source === "vocal_aux"}
          onClick={() => onChange("vocal_aux")}
        >
          A direct feed from the FOH desk — usually just the lead vocal aux
          channel. Highest transcription accuracy.
        </SourceCard>
        <SourceCard
          icon={<Mic className="size-6" />}
          title="Microphone in the room"
          selected={source === "microphone"}
          onClick={() => onChange("microphone")}
        >
          A USB or built-in mic capturing the full mix. Lower accuracy under
          band noise; use if you can't get a board feed.
        </SourceCard>
      </div>
    </div>
  );
}

function SourceCard({
  icon,
  title,
  selected,
  recommended,
  onClick,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  selected: boolean;
  recommended?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex flex-col items-start gap-3 rounded-xl border bg-card p-4 text-left transition-colors",
        selected
          ? "border-emerald-500 bg-emerald-500/10"
          : "border-border hover:border-foreground/40 hover:bg-accent/40",
      )}
      aria-pressed={selected}
    >
      <div className="flex items-center gap-2">
        <div className="rounded-md bg-muted p-2">{icon}</div>
        <div className="font-medium">{title}</div>
        {recommended && (
          <span className="rounded-full bg-emerald-500/20 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-emerald-200">
            Recommended
          </span>
        )}
      </div>
      <p className="text-sm text-muted-foreground">{children}</p>
    </button>
  );
}

// --------------------------------------------------------------------- Step 2

function Step2({
  source,
  deviceIndex,
  onDeviceChange,
}: {
  source: AudioInputSource;
  deviceIndex: number | null;
  onDeviceChange: (idx: number | null, name: string | null) => void;
}) {
  const [reloadKey, setReloadKey] = useState(0);
  const devices = useAudioDevices(reloadKey);
  const levels = useAudioLevels(50);
  const stream = useTranscriptStream();
  const [hasTestedOnce, setHasTestedOnce] = useState(false);

  const currentValue: string =
    deviceIndex !== null
      ? String(deviceIndex)
      : devices.kind === "ready"
      ? String(devices.devices.find((d) => d.is_default)?.index ?? "default")
      : "default";

  async function runTest() {
    try {
      setHasTestedOnce(true);
      await stream.start({
        backend: "mock",
        deviceIndex: deviceIndex,
      });
    } catch {
      // The transcript stream surfaces errors via stream.error.
    }
  }

  async function stopTest() {
    try {
      await stream.stop();
    } catch {
      // ignore — the wizard cares about UI state, not strict lifecycle
    }
  }

  // Auto-stop the test when leaving step 2.
  useEffect(() => {
    return () => {
      if (stream.session === "running" || stream.session === "starting") {
        stream.stop().catch(() => undefined);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Loud enough to be singing or a band, not just room hiss.
  const [heardSound, setHeardSound] = useState(false);
  useEffect(() => {
    if ((levels.latest?.rms_dbfs ?? -Infinity) > HEARD_DBFS) setHeardSound(true);
  }, [levels.latest]);
  const testRunning =
    stream.session === "running" || stream.session === "starting";

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-medium">Pick the device, then test it</h2>
        <p className="text-sm text-muted-foreground">
          {source === "vocal_aux"
            ? "Choose whichever interface receives the board feed."
            : "Pick the microphone you'll be using. A USB headset works well."}
        </p>
      </div>

      <div className="grid grid-cols-[1fr_auto] items-center gap-x-6">
        <div>
          <div className="text-sm font-medium">Input device</div>
          <div className="mt-0.5 text-xs text-muted-foreground">
            The AI engine will read audio from this device.
          </div>
        </div>
        <div className="flex items-center gap-2">
          {devices.kind === "ready" && devices.devices.length > 0 && (
            <select
              value={currentValue}
              onChange={(e) => {
                const v = e.target.value;
                const idx = v === "default" ? null : Number.parseInt(v, 10);
                const matched =
                  idx !== null && devices.kind === "ready"
                    ? devices.devices.find((d) => d.index === idx)
                    : null;
                onDeviceChange(idx, matched?.name ?? null);
              }}
              className="h-9 rounded-md border border-input bg-background px-3 text-sm"
              aria-label="Audio input device"
            >
              {devices.devices.map((d) => (
                <option key={d.index} value={d.index}>
                  {d.name}
                  {d.is_default ? " (system default)" : ""}
                </option>
              ))}
            </select>
          )}
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

      <div className="space-y-2 rounded-md border border-border bg-muted/20 p-4">
        <div className="flex items-center justify-between">
          <div>
            <div className="text-sm font-medium">Live level</div>
            <div className="text-xs text-muted-foreground">
              Talk or play a song; the bar should jump.
            </div>
          </div>
          {testRunning ? (
            <Button size="sm" variant="ghost" onClick={stopTest}>
              Stop test
            </Button>
          ) : (
            <Button size="sm" onClick={runTest}>
              Test with a song
            </Button>
          )}
        </div>
        <VuMeter
          rmsDbfs={levels.latest?.rms_dbfs ?? null}
          peak={levels.latest?.peak ?? 0}
        />
        <div className="text-[10px] text-muted-foreground">
          {levels.latest
            ? `${levels.latest.rms_dbfs.toFixed(1)} dBFS · peak ${levels.latest.peak.toFixed(2)}`
            : testRunning
            ? "Waiting for audio…"
            : "Click “Test with a song” to start a brief session."}
        </div>
      </div>

      {hasTestedOnce && (
        <div
          className={cn(
            "flex items-center gap-2 rounded-md border p-3 text-sm",
            heardSound
              ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-200"
              : "border-border bg-muted/20 text-muted-foreground",
          )}
          data-testid="wizard-heard"
        >
          {heardSound ? (
            <>
              <CheckCircle2 className="size-4" />
              Sound is coming through — you&rsquo;re set.
            </>
          ) : (
            <>Listening… play some music or sing. If the bar doesn&rsquo;t move, try another device.</>
          )}
        </div>
      )}

      {devices.kind === "error" && (
        <div className="text-xs text-amber-400">
          AI engine offline — launch the desktop app to test audio.
          <span className="block text-muted-foreground">{devices.message}</span>
        </div>
      )}
      {devices.kind === "ready" && devices.devices.length === 0 && (
        <div className="text-xs text-amber-400">
          No input devices detected. Connect a microphone or interface, then
          click rescan.
        </div>
      )}
      {hasTestedOnce && stream.error && (
        <div className="text-xs text-red-300">{stream.error}</div>
      )}
    </div>
  );
}

// --------------------------------------------------------------------- Step 3

/** What happens about the speech model when setup finishes. */
function modelNote(a: AiAvailability): string {
  switch (a.kind) {
    case "needs_download":
    case "download_failed":
      return (
        `The AI's speech model (about ${a.kind === "needs_download" ? sizeText(a.mb) : "2 GB"}) ` +
        "downloads when you finish. It needs the internet this once; the home screen shows how far it's got."
      );
    case "downloading":
      return "The AI's speech model is downloading; the home screen shows how far it's got.";
    case "ready":
      return a.hosted
        ? "Speech is recognised online, so live AI needs the internet during the service."
        : "The speech model is already on this computer, so live AI works offline.";
    case "too_slow":
      return `${a.reason} Presenting by hand works as always.`;
    default:
      return "The speech model is picked for this computer automatically.";
  }
}

function Step3({
  source,
  deviceName,
  recommendation,
  availability,
}: {
  source: AudioInputSource;
  deviceName: string | null;
  recommendation: ReturnType<typeof useModelRecommendation>;
  availability: AiAvailability;
}) {
  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-medium">Ready to go</h2>
        <p className="text-sm text-muted-foreground">
          You can change this any time in Settings → Audio input.
        </p>
      </div>
      <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <SummaryItem
          label="Listening to"
          value={source === "vocal_aux" ? "Sound desk feed" : "Microphone in the room"}
        />
        <SummaryItem label="Device" value={deviceName ?? "This computer's default"} />
      </dl>
      <p className="text-xs text-muted-foreground">
        {recommendation.kind === "error"
          ? "The AI engine isn't running yet. Presenting by hand works without it."
          : modelNote(availability)}
      </p>
    </div>
  );
}

function SummaryItem({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="rounded-md border border-border bg-muted/20 px-3 py-2">
      <div className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className="text-sm">{value}</div>
      {hint && (
        <div className="mt-0.5 text-[11px] text-muted-foreground">{hint}</div>
      )}
    </div>
  );
}
