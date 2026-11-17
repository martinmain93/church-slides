/**
 * "What live AI needs on this computer" — the requirements panel at the top
 * of Settings → "AI that follows the songs".
 *
 * The app works without any of this: the library, presenting by hand and the
 * band screens never need the engine, a model, or internet. This says, in
 * the operator's terms, whether live AI will be there on Sunday and what to
 * do if not — including downloading the speech model now, while there's a
 * connection, rather than discovering at the start of a service that it
 * needs one.
 */

import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Download, Loader2, MinusCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { type AiAvailability, availabilitySummary, useAiAvailability } from "@/lib/aiAvailability";
import { refreshInstructionModels, useInstructionModels } from "@/lib/instructionModels";
import { useMachinePower } from "@/lib/machinePower";
import { useSpokenInstructions } from "@/lib/spokenInstructions";
import { InfoTip } from "@/components/ui/InfoTip";

type Tone = "ok" | "warn" | "off" | "busy";

export function AiRequirements() {
  const { availability, prepare, recheck } = useAiAvailability();
  const power = useMachinePower();
  const [preparing, setPreparing] = useState(false);
  const [prepareError, setPrepareError] = useState<string | null>(null);

  async function download() {
    setPreparing(true);
    setPrepareError(null);
    try {
      await prepare();
    } catch (err) {
      setPrepareError(err instanceof Error ? err.message : String(err));
    } finally {
      setPreparing(false);
    }
  }

  const engine = engineRow(availability);
  const model = modelRow(availability, preparing);
  const canDownload =
    availability.kind === "needs_download" || availability.kind === "download_failed";
  // Off Apple silicon: the machine check's verdict, and a way to run it again
  // (after closing other apps, plugging in, or a hardware change).
  const canRecheck =
    availability.kind === "too_slow" ||
    availability.kind === "check_unavailable" ||
    (availability.kind === "ready" && availability.checked === true);

  return (
    <div className="space-y-2 rounded-md border border-border p-3" data-testid="ai-requirements">
      <div className="flex items-center gap-1.5 text-sm font-medium">
        What live AI needs on this computer
        <InfoTip>
          Presenting by hand, the library and band screens always work without any of this, and
          with no internet.
        </InfoTip>
      </div>
      <Row label="AI engine" tone={engine.tone} text={engine.text} />
      {model && (
        <Row label="Speech model" tone={model.tone} text={prepareError ?? model.text}>
          {canDownload && (
            <Button size="sm" variant="outline" onClick={download} disabled={preparing}>
              {preparing ? (
                <Loader2 className="mr-1.5 size-3.5 animate-spin" />
              ) : (
                <Download className="mr-1.5 size-3.5" />
              )}
              {preparing ? "Downloading…" : "Download now"}
            </Button>
          )}
          {canRecheck && (
            <Button size="sm" variant="outline" onClick={() => void recheck()}>
              Check again
            </Button>
          )}
        </Row>
      )}
      {power &&
        !measured(availability) &&
        availability.kind !== "engine_unavailable" &&
        availability.kind !== "checking" &&
        availability.kind !== "checking_model" && (
        <Row
          label="This computer"
          tone={power.level === "good" ? "ok" : "warn"}
          text={power.summary}
        />
      )}
      {availability.kind === "ready" || availability.kind === "needs_download" ? (
        <SpokenCommandsRow />
      ) : null}
    </div>
  );
}

/** Off Apple silicon the machine check measures this computer running the
 * real work, so the guess from its specs (`useMachinePower`) has nothing to
 * add, and where they disagreed the guess was the one that was wrong. */
function measured(a: AiAvailability): boolean {
  return (
    a.kind === "checking_machine" ||
    a.kind === "too_slow" ||
    a.kind === "check_unavailable" ||
    (a.kind === "ready" && a.checked === true)
  );
}

function engineRow(a: AiAvailability): { tone: Tone; text: string } {
  switch (a.kind) {
    case "checking":
      return { tone: "busy", text: "Checking…" };
    case "engine_unavailable":
      return { tone: "off", text: a.reason };
    default:
      return { tone: "ok", text: "Running on this computer." };
  }
}

function modelRow(a: AiAvailability, preparing: boolean): { tone: Tone; text: string } | null {
  switch (a.kind) {
    case "checking":
    case "engine_unavailable":
      return null;
    case "checking_model":
      return { tone: "busy", text: availabilitySummary(a) };
    case "needs_download":
      return { tone: preparing ? "busy" : "warn", text: availabilitySummary(a) };
    case "downloading":
      return { tone: "busy", text: availabilitySummary(a) };
    case "download_failed":
      return { tone: "warn", text: availabilitySummary(a) };
    case "checking_machine":
      return { tone: "busy", text: availabilitySummary(a) };
    case "too_slow":
    case "check_unavailable":
      return { tone: "warn", text: availabilitySummary(a) };
    case "ready":
      return { tone: a.hosted ? "warn" : "ok", text: availabilitySummary(a) };
  }
}

/** Only shown when spoken commands are on: they have their own model, and a
 * hosted one needs internet all service long. */
function SpokenCommandsRow() {
  const spoken = useSpokenInstructions();
  const models = useInstructionModels();

  useEffect(() => {
    if (spoken.enabled && !models.seen) void refreshInstructionModels().catch(() => undefined);
  }, [spoken.enabled, models.seen]);

  if (!spoken.enabled || !models.seen) return null;
  const active = models.models.find((m) => m.id === models.activeModelId);
  if (!active) return null;

  if (active.model_kind === "hosted") {
    const keyed = active.has_api_key === true;
    return (
      <Row
        label="Spoken commands"
        tone="warn"
        text={
          `Hosted (${active.label}) — needs internet during the service` +
          (keyed ? "." : ", and an API key, which isn't set yet.")
        }
      />
    );
  }
  return (
    <Row
      label="Spoken commands"
      tone={active.installed ? "ok" : "warn"}
      text={
        active.installed
          ? `On this computer (${active.label}) — works offline.`
          : `${active.label} isn't downloaded yet${active.size_mb ? ` (about ${active.size_mb} MB)` : ""} — needs internet once.`
      }
    />
  );
}

function Row({
  label,
  tone,
  text,
  children,
}: {
  label: string;
  tone: Tone;
  text: string;
  children?: React.ReactNode;
}) {
  const Icon = { ok: CheckCircle2, warn: AlertTriangle, off: MinusCircle, busy: Loader2 }[tone];
  const color = {
    ok: "text-emerald-400",
    warn: "text-amber-400",
    off: "text-muted-foreground",
    busy: "text-sky-400 animate-spin",
  }[tone];
  return (
    <div className="flex items-start gap-2 text-xs" data-tone={tone}>
      <Icon className={`mt-0.5 size-3.5 shrink-0 ${color}`} aria-hidden />
      <div className="min-w-0 flex-1">
        <span className="font-medium text-foreground">{label}: </span>
        <span className="text-muted-foreground">{text}</span>
      </div>
      {children}
    </div>
  );
}
