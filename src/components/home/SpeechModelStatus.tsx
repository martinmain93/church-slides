/**
 * The speech model, on the home screen, until it's on this computer.
 *
 * A fresh install has no speech model, and nothing used to say so before a
 * service: setup ended on "Ready to go", and the download only began when a
 * service started, so on a slow connection the AI sat waiting while the band
 * played. This puts it where the operator starts — downloading (with how
 * far), or a button to download it, or why it couldn't — and says nothing
 * once the model is here, or when there's no engine to need one.
 */

import { useState } from "react";
import { AlertTriangle, Download, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { availabilitySummary, sizeText, useAiAvailability } from "@/lib/aiAvailability";

export function SpeechModelStatus() {
  const { availability: a, prepare, recheck } = useAiAvailability();
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function download() {
    setStarting(true);
    setError(null);
    try {
      await prepare();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setStarting(false);
    }
  }

  if (a.kind === "downloading" || (starting && a.kind === "needs_download")) {
    const pct =
      a.kind === "downloading" && a.mbDone !== null && a.mb > 0
        ? Math.min(99, Math.round((100 * a.mbDone) / a.mb))
        : null;
    return (
      <section
        className="space-y-2 rounded-lg border border-sky-500/30 bg-sky-500/10 p-4"
        data-testid="speech-model-status"
        data-state="downloading"
      >
        <div className="flex items-center gap-3">
          <Loader2 className="size-4 shrink-0 animate-spin text-sky-300" aria-hidden />
          <div className="flex-1 text-sm">
            {a.kind === "downloading" ? availabilitySummary(a) : "Starting the speech model download…"}
          </div>
          {pct !== null && <div className="text-sm tabular-nums text-sky-200">{pct}%</div>}
        </div>
        <div className="h-1 overflow-hidden rounded-full bg-sky-950">
          <div
            className="h-full bg-sky-400 transition-[width] duration-300 ease-linear"
            style={{ width: `${pct ?? 0}%` }}
          />
        </div>
        <div className="text-xs text-muted-foreground">
          Live AI is ready when it finishes. Everything else works in the meantime.
        </div>
      </section>
    );
  }

  if (a.kind === "checking_machine") {
    return (
      <section
        className="flex items-center gap-3 rounded-lg border border-sky-500/30 bg-sky-500/10 p-4"
        data-testid="speech-model-status"
        data-state="checking"
      >
        <Loader2 className="size-4 shrink-0 animate-spin text-sky-300" aria-hidden />
        <div className="flex-1 text-sm">{availabilitySummary(a)}</div>
      </section>
    );
  }

  if (a.kind === "too_slow") {
    return (
      <section
        className="flex items-start gap-4 rounded-lg border border-border bg-card p-4"
        data-testid="speech-model-status"
        data-state="too_slow"
      >
        <AlertTriangle className="mt-0.5 size-5 shrink-0 text-muted-foreground" aria-hidden />
        <div className="flex-1 space-y-0.5">
          <div className="text-sm font-medium">Live AI isn't available on this computer</div>
          <div className="text-xs text-muted-foreground">{availabilitySummary(a)}</div>
        </div>
      </section>
    );
  }

  if (a.kind === "check_unavailable") {
    return (
      <section
        className="flex items-center gap-4 rounded-lg border border-amber-500/30 bg-amber-500/10 p-4"
        data-testid="speech-model-status"
        data-state="check_unavailable"
      >
        <AlertTriangle className="size-5 shrink-0 text-amber-300" aria-hidden />
        <div className="flex-1 space-y-0.5">
          <div className="text-sm font-medium">Live AI needs a quick check of this computer</div>
          <div className="text-xs text-muted-foreground">{availabilitySummary(a)}</div>
        </div>
        <Button size="sm" onClick={() => void recheck()}>
          Check again
        </Button>
      </section>
    );
  }

  if (a.kind === "needs_download" || a.kind === "download_failed") {
    const failed = a.kind === "download_failed" || error !== null;
    return (
      <section
        className="flex items-center gap-4 rounded-lg border border-amber-500/30 bg-amber-500/10 p-4"
        data-testid="speech-model-status"
        data-state={failed ? "failed" : "needed"}
      >
        <AlertTriangle className="size-5 shrink-0 text-amber-300" aria-hidden />
        <div className="flex-1 space-y-0.5">
          <div className="text-sm font-medium">
            {failed ? "The speech model didn't download" : "The speech model isn't on this computer yet"}
          </div>
          <div className="text-xs text-muted-foreground">
            {error ?? availabilitySummary(a)}
          </div>
        </div>
        <Button size="sm" onClick={download}>
          <Download className="size-4" />
          {failed || a.kind !== "needs_download" ? "Try again" : `Download (${sizeText(a.mb)})`}
        </Button>
      </section>
    );
  }

  return null;
}
