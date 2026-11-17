/**
 * Offers a newer release when there is one. Checks once at startup and again
 * whenever Settings → "Check for updates" asks; "Update and restart"
 * downloads the new version with a progress bar, installs it over this one
 * and relaunches.
 *
 * Never interrupts a service: while `held` is true (presenting, or the
 * operator view is open) a found update waits until the service ends.
 *
 * The releases come from scripts/release.sh, which publishes a signed
 * latest.json next to the installers; src-tauri/tauri.conf.json names it.
 * The first address there is the stats Worker (`stats/`), which counts the
 * check when it carries this install's id (`updateCheckHeaders`: only with
 * usage statistics on) and answers with GitHub's copy; the second is
 * GitHub itself, for when the Worker can't be reached.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, Download, RotateCw, TriangleAlert } from "lucide-react";
import { check, type Update } from "@tauri-apps/plugin-updater";
import { relaunch } from "@tauri-apps/plugin-process";

import { Button } from "@/components/ui/button";
import { isInTauri } from "@/lib/invoke";
import { updateCheckHeaders } from "@/lib/usage";

/** Fire this on `window` to check now and report the answer either way. */
export const CHECK_FOR_UPDATE_EVENT = "auto-presenter:check-for-update";

/** A computer left on all week still checks (and is counted) once a day. */
const RECHECK_MS = 24 * 60 * 60 * 1000;

type Phase =
  | { kind: "idle" }
  | { kind: "checking" } // a manual check, so say something while it runs
  | { kind: "upToDate" }
  | { kind: "available"; update: Update }
  | { kind: "downloading"; update: Update; received: number; total: number | null }
  | { kind: "installing"; update: Update }
  | { kind: "failed"; message: string; update: Update | null };

export function UpdatePrompt({ held }: { held: boolean }) {
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const busy = useRef(false);
  const phaseRef = useRef(phase);
  phaseRef.current = phase;

  const runCheck = useCallback(async (manual: boolean) => {
    if (busy.current) return;
    busy.current = true;
    if (manual) setPhase({ kind: "checking" });
    // An unprompted check (startup, or the daily one) never disturbs an offer
    // that's already up or a download under way.
    const show = (next: Phase) => {
      if (manual || phaseRef.current.kind === "idle") setPhase(next);
    };
    try {
      const update = await check({ headers: await updateCheckHeaders() });
      if (update) show({ kind: "available", update });
      else show(manual ? { kind: "upToDate" } : { kind: "idle" });
    } catch (err) {
      // Offline at startup is normal (a church hall with no Wi-Fi): only a
      // check someone asked for reports a failure.
      console.warn("update check failed", err);
      show(manual ? { kind: "failed", message: errorText(err), update: null } : { kind: "idle" });
    } finally {
      busy.current = false;
    }
  }, []);

  // Dev builds would "update" themselves into the released app.
  const enabled = isInTauri() && !import.meta.env.DEV;

  useEffect(() => {
    if (!enabled) return;
    void runCheck(false);
    const id = window.setInterval(() => void runCheck(false), RECHECK_MS);
    return () => window.clearInterval(id);
  }, [enabled, runCheck]);

  useEffect(() => {
    const onRequest = () => {
      if (enabled) void runCheck(true);
      else setPhase({ kind: "failed", message: "Updates are only available in the installed app.", update: null });
    };
    window.addEventListener(CHECK_FOR_UPDATE_EVENT, onRequest);
    return () => window.removeEventListener(CHECK_FOR_UPDATE_EVENT, onRequest);
  }, [enabled, runCheck]);

  async function install(update: Update) {
    setPhase({ kind: "downloading", update, received: 0, total: null });
    let received = 0;
    let total: number | null = null;
    let shownAt = 0;
    try {
      // `headers: {}`: the check's install id is for the stats Worker, and
      // the updater would otherwise repeat it to GitHub with the download.
      await update.downloadAndInstall((event) => {
        if (event.event === "Started") {
          total = event.data.contentLength ?? null;
          setPhase({ kind: "downloading", update, received: 0, total });
        } else if (event.event === "Progress") {
          received += event.data.chunkLength;
          // A progress event comes per network chunk, hundreds a second: each
          // one re-rendered the dialog and restarted the bar's animation, so
          // the bar trailed the text. About ten updates a second is smooth.
          const now = performance.now();
          if (now - shownAt >= PROGRESS_EVERY_MS || (total !== null && received >= total)) {
            shownAt = now;
            setPhase({ kind: "downloading", update, received, total });
          }
        } else {
          setPhase({ kind: "installing", update });
        }
      }, { headers: {} });
      setPhase({ kind: "installing", update });
      await relaunch();
    } catch (err) {
      setPhase({ kind: "failed", message: errorText(err), update });
    }
  }

  const close = () => setPhase({ kind: "idle" });

  if (phase.kind === "idle") return null;
  // Hold an unprompted offer back until the service is over; a download
  // already under way carries on.
  if (held && phase.kind === "available") return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-6"
      role="dialog"
      aria-modal="true"
      aria-labelledby="update-title"
      data-testid="update-prompt"
    >
      <div className="w-full max-w-md rounded-lg border border-border bg-background p-5 shadow-2xl">
        {phase.kind === "checking" && (
          <>
            <Title icon={<RotateCw className="size-5 animate-spin" />}>Checking for updates…</Title>
          </>
        )}

        {phase.kind === "upToDate" && (
          <>
            <Title icon={<CheckCircle2 className="size-5 text-emerald-400" />}>You have the latest version</Title>
            <div className="mt-4 flex justify-end">
              <Button size="sm" onClick={close}>OK</Button>
            </div>
          </>
        )}

        {phase.kind === "available" && (
          <>
            <Title icon={<Download className="size-5" />}>Church Slides {phase.update.version} is available</Title>
            <p className="mt-2 text-sm text-muted-foreground">
              You have {phase.update.currentVersion}. Updating takes a minute or two, and the app restarts when it's done.
            </p>
            <Notes text={phase.update.body} />
            <div className="mt-4 flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={close}>
                Later
              </Button>
              <Button size="sm" onClick={() => void install(phase.update)} data-testid="update-install">
                Update and restart
              </Button>
            </div>
          </>
        )}

        {(phase.kind === "downloading" || phase.kind === "installing") && (
          <>
            <Title icon={<Download className="size-5" />}>
              {phase.kind === "downloading" ? `Downloading ${phase.update.version}…` : "Installing… the app will restart"}
            </Title>
            <Progress
              fraction={
                phase.kind === "installing"
                  ? 1
                  : phase.total
                    ? phase.received / phase.total
                    : null
              }
            />
            <p className="mt-2 text-xs text-muted-foreground" aria-live="polite">
              {phase.kind === "installing"
                ? "Almost done."
                : phase.total
                  ? `${megabytes(phase.received)} of ${megabytes(phase.total)}`
                  : megabytes(phase.received)}
            </p>
          </>
        )}

        {phase.kind === "failed" && (
          <>
            <Title icon={<TriangleAlert className="size-5 text-amber-400" />}>
              {phase.update ? "The update didn't finish" : "Couldn't check for updates"}
            </Title>
            <p className="mt-2 text-sm text-muted-foreground">
              {phase.message} This version keeps working as it is.
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={close}>
                Close
              </Button>
              {phase.update && (
                <Button size="sm" onClick={() => void install(phase.update!)}>
                  Try again
                </Button>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function Title({ icon, children }: { icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <div id="update-title" className="flex items-center gap-2 font-semibold">
      {icon}
      {children}
    </div>
  );
}

/** The release's changelog entry: one bullet per line. */
function Notes({ text }: { text?: string }) {
  const lines = (text ?? "")
    .split("\n")
    .map((l) => l.replace(/^\s*[-*]\s*/, "").trim())
    .filter(Boolean);
  if (lines.length === 0) return null;
  return (
    <ul className="mt-3 max-h-48 list-disc space-y-1 overflow-y-auto pl-5 text-sm">
      {lines.map((l, i) => (
        <li key={i}>{l}</li>
      ))}
    </ul>
  );
}

/** How often the download's progress is shown (ms). */
const PROGRESS_EVERY_MS = 100;

/** `fraction` null = size unknown, so the bar just shows activity. The
 *  activity bar and the progress bar are separate elements: as one, the
 *  bar's width animated from the activity bar's third down to the first few
 *  percent once the size was known, and looked like it went backwards. */
function Progress({ fraction }: { fraction: number | null }) {
  return (
    <div
      className="mt-4 h-2 overflow-hidden rounded-full bg-muted"
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={fraction === null ? undefined : Math.round(fraction * 100)}
    >
      {fraction === null ? (
        <div key="activity" className="h-full w-1/3 animate-pulse rounded-full bg-primary" />
      ) : (
        <div
          key="progress"
          data-testid="update-progress-bar"
          className="h-full rounded-full bg-primary transition-[width] duration-100 ease-linear"
          style={{ width: `${Math.min(100, Math.max(0, fraction * 100))}%` }}
        />
      )}
    </div>
  );
}

function megabytes(bytes: number): string {
  return `${(bytes / 1_000_000).toFixed(bytes < 10_000_000 ? 1 : 0)} MB`;
}

function errorText(err: unknown): string {
  const text = err instanceof Error ? err.message : String(err);
  return /network|connect|dns|timed? ?out|offline/i.test(text)
    ? "There's no internet connection."
    : `${text.replace(/\.$/, "")}.`;
}
