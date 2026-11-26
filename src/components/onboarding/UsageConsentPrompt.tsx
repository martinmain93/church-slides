/**
 * Asks, once, what the app may send: error reports (`src-tauri/src/telemetry.rs`)
 * and usage statistics (`src/lib/usage.ts`). On a new install after audio
 * setup, and on an existing one after the update that added each question.
 * Error reports start ticked (an opt-out with notice, which PIPEDA and GDPR
 * allow for low-sensitivity data); usage statistics start unticked. Nothing
 * is sent until the answer is saved. Either can be changed in Settings →
 * Privacy.
 *
 * Held back during a service and while another first-run screen is up, so it
 * never lands in front of the projector or on top of the audio wizard.
 */

import { useEffect, useState } from "react";
import { ShieldCheck } from "lucide-react";

import { Button } from "@/components/ui/button";
import { getSettings, openLink, updateSettings } from "@/lib/tauri";
import { PRIVACY_URL, TERMS_URL, officialBuild, recordAppOpen } from "@/lib/usage";

export function UsageConsentPrompt({
  held,
  onAskingChange,
}: {
  held: boolean;
  /** Whether the question is waiting to be answered, so other first-run screens can wait their turn. */
  onAskingChange?: (asking: boolean) => void;
}) {
  const [asking, setAsking] = useState(false);
  useEffect(() => onAskingChange?.(asking), [asking, onAskingChange]);
  const [saving, setSaving] = useState(false);
  const [reports, setReports] = useState(false);
  const [usage, setUsage] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getSettings()
      .then((s) => {
        if (cancelled) return;
        const usageAnswered = s.share_usage === true || s.share_usage === false;
        if (!officialBuild()) return; // nothing to send to, so nothing to ask
        if (usageAnswered && s.error_reports_asked) return;
        // An earlier answer stands as the starting point; otherwise error
        // reports start ticked and usage statistics unticked.
        setUsage(s.share_usage === true);
        setReports(s.error_reports_asked ? s.send_error_reports : true);
        setAsking(true);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  if (!asking || held) return null;

  async function save() {
    setSaving(true);
    try {
      const s = await getSettings();
      await updateSettings({
        ...s,
        send_error_reports: reports,
        error_reports_asked: true,
        share_usage: usage,
      });
      setAsking(false);
      if (usage)
        window.dispatchEvent(new CustomEvent(USAGE_CONSENT_GIVEN_EVENT));
    } catch {
      setSaving(false); // leave it up; they can answer again
    }
  }

  const link = "underline underline-offset-2 hover:text-foreground";

  return (
    <div
      className="fixed inset-0 z-[55] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="usage-consent-title"
    >
      <div className="w-full max-w-md rounded-lg border border-border bg-card p-5 text-foreground shadow-2xl">
        <h2
          id="usage-consent-title"
          className="flex items-center gap-2 text-base font-semibold"
        >
          <ShieldCheck className="size-4" />
          Help improve Church Slides?
        </h2>
        <p className="mt-2 text-sm text-muted-foreground">
          Your songs, services and audio stay on this computer either way. Choose
          what you&rsquo;re happy to send to the developer:
        </p>
        <div className="mt-4 space-y-3">
          <Choice
            checked={reports}
            onChange={setReports}
            label="Send error reports"
            detail="When something goes wrong: what happened, this computer's model and the app's recent log, which can include song titles and audio device names. Never audio or anything said."
          />
          <Choice
            checked={usage}
            onChange={setUsage}
            label="Share usage statistics"
            detail="How long services run, how many songs they have, and how often the AI changed the slides, plus this install's random number, version and system with update checks. Never song titles, lyrics, names, or anything said."
          />
        </div>
        <p className="mt-4 text-xs text-muted-foreground">
          Change either any time in Settings &rarr; Privacy.{" "}
          <button
            type="button"
            className={link}
            onClick={() => void openLink(PRIVACY_URL).catch(() => undefined)}
          >
            Privacy policy
          </button>
          {" · "}
          <button
            type="button"
            className={link}
            onClick={() => void openLink(TERMS_URL).catch(() => undefined)}
          >
            Terms of use
          </button>
        </p>
        <div className="mt-5 flex justify-end">
          <Button type="button" disabled={saving} onClick={() => void save()}>
            Save
          </Button>
        </div>
      </div>
    </div>
  );
}

function Choice({
  checked,
  onChange,
  label,
  detail,
}: {
  checked: boolean;
  onChange: (on: boolean) => void;
  label: string;
  detail: string;
}) {
  return (
    <label className="flex cursor-pointer items-start gap-3">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 size-4 shrink-0 accent-emerald-500"
      />
      <span>
        <span className="block text-sm font-medium">{label}</span>
        <span className="mt-0.5 block text-xs text-muted-foreground">
          {detail}
        </span>
      </span>
    </label>
  );
}

/** Fired when the user says yes, so today's app-open is counted right away. */
export const USAGE_CONSENT_GIVEN_EVENT = "auto-presenter:usage-consent-given";

/** Records the once-a-day app-open (library size, AI engine state). */
export function useAppOpenUsage(
  props: () => Promise<Record<string, string | number | boolean>>,
) {
  useEffect(() => {
    const run = () => void recordAppOpen(props);
    run();
    window.addEventListener(USAGE_CONSENT_GIVEN_EVENT, run);
    return () => window.removeEventListener(USAGE_CONSENT_GIVEN_EVENT, run);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once per app start
  }, []);
}
