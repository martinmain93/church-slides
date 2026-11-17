/**
 * Usage statistics, for installs whose user said yes (Settings → Privacy,
 * asked once by `UsageConsentPrompt`). A few counts per service and per day:
 * how long, how many songs, how often the AI moved the slides versus a hand.
 * Never song titles, lyrics, setlist names or anything typed or said. The
 * same choice covers the install count sent with update checks
 * (`updateCheckHeaders`).
 *
 * Events wait in a small queue in this webview's storage and go to the stats
 * Worker (`stats/`) in a batch, so a service in a hall with no Wi-Fi is sent
 * the next time the app is online. Dev builds send nothing.
 *
 * The stats Worker also answers the updater (`tauri.conf.json`) and the
 * website's download buttons (`site/`); the privacy page describes all of it.
 */

import { getAppIdentity, getSettings, type AppIdentity } from "@/lib/tauri";
import { isInTauri } from "@/lib/invoke";

/** The stats Worker, baked in at build time from `VITE_CHURCH_SLIDES_STATS_URL`
 * (set by `scripts/release.sh` from `release.local.env`, and by the Windows
 * workflow). Anyone's own build from source has none: it sends nothing and
 * never asks about statistics or error reports. */
export function statsUrl(): string {
  return import.meta.env.VITE_CHURCH_SLIDES_STATS_URL ?? "";
}

/** An official release, which can send error reports and usage statistics
 * if the user agrees. The release script sets the Sentry DSN and the stats
 * address together, so one says for both. */
export function officialBuild(): boolean {
  return statsUrl() !== "";
}

export const PRIVACY_URL = "https://church-slides.netlify.app/privacy.html";

export const TERMS_URL = "https://church-slides.netlify.app/terms.html";
/** Every file format the song import reads, and how to export from other apps. */
export const FORMATS_URL = "https://church-slides.netlify.app/formats.html";

/** Where to ask for data to be seen or deleted (`open_link` allows only this
 * address). */
export const PRIVACY_EMAIL = "martinmain93@gmail.com";

const QUEUE_KEY = "church-slides:usage-queue";
const LAST_OPEN_KEY = "church-slides:usage-last-open";
/** Events kept while offline, at most; the oldest go first. */
const MAX_QUEUED = 60;
/** What the Worker accepts per request. */
const BATCH = 20;

export type UsageProps = Record<string, string | number | boolean>;

interface QueuedEvent {
  name: string;
  props: UsageProps;
}

function readQueue(): QueuedEvent[] {
  try {
    const raw = localStorage.getItem(QUEUE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? (parsed as QueuedEvent[]) : [];
  } catch {
    return [];
  }
}

function writeQueue(q: QueuedEvent[]) {
  try {
    if (q.length === 0) localStorage.removeItem(QUEUE_KEY);
    else localStorage.setItem(QUEUE_KEY, JSON.stringify(q.slice(-MAX_QUEUED)));
  } catch {
    // Storage unavailable: the event is lost, which is fine for statistics.
  }
}

function canSend(): boolean {
  return isInTauri() && !import.meta.env.DEV && officialBuild();
}

async function sharing(): Promise<boolean> {
  try {
    return (await getSettings()).share_usage === true;
  } catch {
    return false;
  }
}

/** Queue an event and try to send what's waiting. A no-op unless the user
 * agreed to share usage statistics. */
export async function recordUsage(name: string, props: UsageProps = {}): Promise<void> {
  if (!canSend() || !(await sharing())) return;
  writeQueue([...readQueue(), { name, props }]);
  await flushUsage();
}

let identity: Promise<AppIdentity> | null = null;
let flushing = false;

/** Send what's queued. Failures keep it for next time. */
export async function flushUsage(): Promise<void> {
  if (flushing || !canSend()) return;
  flushing = true;
  try {
    if (!(await sharing())) {
      writeQueue([]); // said no (or changed their mind): drop what's waiting
      return;
    }
    identity ??= getAppIdentity();
    const who = await identity;
    for (;;) {
      const queue = readQueue();
      if (queue.length === 0) return;
      const batch = queue.slice(0, BATCH);
      const res = await fetch(`${statsUrl()}/v1/events`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ install_id: who.install_id, version: who.version, events: batch }),
      });
      // A refusal (400) won't get better by retrying: drop it. Anything else
      // (offline, the Worker down) keeps it.
      if (!res.ok && res.status !== 400) return;
      writeQueue(readQueue().slice(batch.length));
    }
  } catch {
    // Offline: try again next time.
  } finally {
    flushing = false;
  }
}

/** Once a day per install: the library's size and whether the AI can run.
 * Also sends anything left from a service that ended offline. */
export async function recordAppOpen(props: () => Promise<UsageProps>): Promise<void> {
  if (!canSend() || !(await sharing())) return;
  const today = new Date().toISOString().slice(0, 10);
  let last: string | null = null;
  try {
    last = localStorage.getItem(LAST_OPEN_KEY);
  } catch {
    // no storage: count every open, which is close enough
  }
  if (last === today) {
    await flushUsage();
    return;
  }
  try {
    localStorage.setItem(LAST_OPEN_KEY, today);
  } catch {
    // as above
  }
  await recordUsage("app_open", await props());
}

/** Headers for the update check, which the stats Worker counts: only for
 * installs sharing usage statistics. Otherwise none, and it isn't counted. */
export async function updateCheckHeaders(): Promise<Record<string, string> | undefined> {
  try {
    if (!officialBuild() || !(await sharing())) return undefined;
    identity ??= getAppIdentity();
    const who = await identity;
    return {
      "X-Install-Id": who.install_id,
      "X-App-Version": who.version,
      "X-OS": who.os,
      "X-OS-Version": who.os_version ?? "",
      "X-Arch": who.arch,
    };
  } catch {
    return undefined;
  }
}

/**
 * One service's counts, for the `service` event: kept as the service runs
 * and sent when it ends. Only numbers and which screen layout was used.
 */
export class ServiceTally {
  private readonly startedAt: number;
  private changes = { ai: 0, manual: 0, voice: 0 };
  private songs = new Set<number>();
  private aiMs = 0;
  private aiSince: number | null = null;
  private continuous = false;

  constructor(
    private readonly screen: "operator" | "single",
    now = Date.now(),
  ) {
    this.startedAt = now;
  }

  slideShown(itemIdx: number, source: "ai" | "manual" | "voice" | null) {
    this.songs.add(itemIdx);
    if (source) this.changes[source] += 1;
  }

  aiMode(on: boolean, now = Date.now()) {
    if (on && this.aiSince === null) this.aiSince = now;
    if (!on && this.aiSince !== null) {
      this.aiMs += now - this.aiSince;
      this.aiSince = null;
    }
  }

  displayContinuous() {
    this.continuous = true;
  }

  /** The event's props, or null for a service too short to count (opened by
   * mistake, or a quick look at the slides). */
  finish(now = Date.now()): UsageProps | null {
    this.aiMode(false, now);
    const minutes = Math.round((now - this.startedAt) / 60_000);
    if (minutes < 2) return null;
    return {
      screen: this.screen,
      minutes,
      songs: this.songs.size,
      ai_minutes: Math.round(this.aiMs / 60_000),
      by_ai: this.changes.ai,
      by_hand: this.changes.manual,
      by_voice: this.changes.voice,
      continuous: this.continuous,
    };
  }
}
