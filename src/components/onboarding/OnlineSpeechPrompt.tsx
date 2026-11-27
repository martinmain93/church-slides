/**
 * Asks, once, whether live AI may listen online (docs/roadmap/HOSTED_SPEECH.md):
 * on a computer without Apple silicon, signed in to an organization whose
 * plan has online speech. Online, the AI follows songs as well as on a Mac;
 * the service's audio goes to the organization's speech server while the AI
 * is on, and nothing is kept. The answer can be changed in Settings → AI.
 *
 * Held back during a service and while another first-run screen is up, so it
 * never lands in front of the projector. Until it's answered, the AI listens
 * on this computer as before.
 */

import { useEffect, useState } from "react";
import { Globe } from "lucide-react";

import { Button } from "@/components/ui/button";
import { loadAudioInputPrefs, resolveLiveAsr } from "@/lib/audioInputPrefs";
import { getSettings, hostedAiStatus, openLink, updateSettings } from "@/lib/tauri";
import { PRIVACY_URL } from "@/lib/usage";

/** The plan's speech arrives with the AI key, within a minute of signing in. */
const CHECK_EVERY_MS = 60_000;

/** Whether to ask now: unanswered, the plan has speech, and this computer
 * would otherwise listen with Whisper on its processor. `done` stops the
 * checking: answered, or a computer that never needs asking. */
export async function shouldAskOnlineSpeech(): Promise<{ ask: boolean; done: boolean }> {
  const s = await getSettings();
  if (s.online_speech === true || s.online_speech === false) return { ask: false, done: true };
  if (!(await hostedAiStatus()).speech) return { ask: false, done: false };
  // Apple silicon always listens on the computer: nothing to ask there.
  const asr = await resolveLiveAsr(loadAudioInputPrefs());
  if (asr.backend !== "whisper") return { ask: false, done: true };
  return { ask: true, done: true };
}

export function OnlineSpeechPrompt({ held }: { held: boolean }) {
  const [asking, setAsking] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const check = async () => {
      const r = await shouldAskOnlineSpeech().catch(() => ({ ask: false, done: false }));
      if (cancelled) return;
      if (r.ask) setAsking(true);
      if (r.done && timer !== undefined) window.clearInterval(timer);
      return r.done;
    };
    void check().then((done) => {
      if (!done && !cancelled) timer = window.setInterval(() => void check(), CHECK_EVERY_MS);
    });
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearInterval(timer);
    };
  }, []);

  if (!asking || held) return null;

  async function answer(online: boolean) {
    setSaving(true);
    try {
      const s = await getSettings();
      await updateSettings({ ...s, online_speech: online });
      setAsking(false);
    } catch {
      setSaving(false); // leave it up; they can answer again
    }
  }

  return (
    <div
      className="fixed inset-0 z-[55] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="online-speech-title"
    >
      <div className="w-full max-w-md rounded-lg border border-border bg-card p-5 text-foreground shadow-2xl">
        <h2 id="online-speech-title" className="flex items-center gap-2 text-base font-semibold">
          <Globe className="size-4" />
          Listen online?
        </h2>
        <p className="mt-2 text-sm text-muted-foreground">
          On this computer, the AI follows songs best online. While the AI is on, the
          service&rsquo;s audio is sent to our speech server to be turned into words. Nothing
          is kept.
        </p>
        <p className="mt-3 text-xs text-muted-foreground">
          Change it any time in Settings &rarr; AI.{" "}
          <button
            type="button"
            className="underline underline-offset-2 hover:text-foreground"
            onClick={() => void openLink(PRIVACY_URL).catch(() => undefined)}
          >
            Privacy policy
          </button>
        </p>
        <div className="mt-5 flex justify-end gap-2">
          <Button type="button" variant="outline" disabled={saving} onClick={() => void answer(false)}>
            Keep it on this computer
          </Button>
          <Button type="button" disabled={saving} onClick={() => void answer(true)}>
            Use online
          </Button>
        </div>
      </div>
    </div>
  );
}
