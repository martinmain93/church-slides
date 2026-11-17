/**
 * Inline status bar for Whisper model loads.
 *
 * Renders only while a download/load is in flight or recently
 * completed — fades out after a brief grace period so the section
 * doesn't permanently take up vertical space. Failed loads stay
 * visible until the user starts a new session.
 */

import { useEffect, useState } from "react";
import { AlertCircle, CheckCircle2, Download } from "lucide-react";

import { useModelDownloadProgress } from "@/lib/modelDownload";
import { cn } from "@/lib/utils";

const HIDE_AFTER_COMPLETE_MS = 4000;

export function ModelDownloadProgress() {
  const { latest } = useModelDownloadProgress();
  const [hidden, setHidden] = useState(false);

  // Auto-hide successful completions after a short delay so the UI
  // doesn't leave a stale "completed" bar around forever.
  useEffect(() => {
    if (!latest) return;
    if (latest.state !== "completed") {
      setHidden(false);
      return;
    }
    setHidden(false);
    const timer = window.setTimeout(() => setHidden(true), HIDE_AFTER_COMPLETE_MS);
    return () => window.clearTimeout(timer);
  }, [latest]);

  if (!latest || hidden) return null;

  const fraction = Math.max(0, Math.min(1, latest.fraction));
  const failed = latest.state === "failed";
  const completed = latest.state === "completed";

  return (
    <div
      className={cn(
        "rounded-md border px-3 py-2 text-xs",
        failed
          ? "border-red-500/40 bg-red-500/10 text-red-200"
          : completed
          ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-200"
          : "border-sky-500/30 bg-sky-500/10 text-sky-100",
      )}
      role="status"
      aria-live="polite"
    >
      <div className="flex items-center gap-2">
        {failed ? (
          <AlertCircle className="size-3.5" aria-hidden />
        ) : completed ? (
          <CheckCircle2 className="size-3.5" aria-hidden />
        ) : (
          <Download className="size-3.5" aria-hidden />
        )}
        <span className="font-medium">
          {failed
            ? `Model load failed (${latest.model_size})`
            : completed
            ? `Model ready: ${latest.model_size}`
            : `Loading model: ${latest.model_size}`}
        </span>
      </div>
      {latest.detail && (
        <div className="mt-0.5 text-[11px] opacity-80">{latest.detail}</div>
      )}
      {!failed && !completed && (
        <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-zinc-800">
          <div
            className="h-full bg-sky-400 transition-[width] duration-200"
            style={{ width: `${fraction * 100}%` }}
          />
        </div>
      )}
    </div>
  );
}
