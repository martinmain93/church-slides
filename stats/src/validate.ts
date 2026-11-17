/**
 * What the stats Worker accepts, kept apart from the Worker so it can be
 * tested without one. Everything is checked against a narrow shape: the
 * endpoints are public, so a request is only ever a few short fields, never
 * free text that could carry something personal.
 */

/** Installers the download links may name; anything else is a 404. */
export const ASSETS = new Set([
  "Church-Slides-apple-silicon.dmg",
  "Church-Slides-intel.dmg",
  "Church-Slides-windows-setup.exe",
]);

const REPO_RELEASES = "https://github.com/martinmain93/church-slides-releases/releases";
export const RELEASES = `${REPO_RELEASES}/latest/download`;

/**
 * Where a download link goes: `/dl/<installer>` is the newest release,
 * `/dl/v1.2.3/<installer>` a given one (the Windows button names its release,
 * since the newest may not have a Windows build). Null for anything else.
 */
export function downloadTarget(path: string): { asset: string; url: string } | null {
  const m = /^\/dl\/(?:(v\d{1,3}\.\d{1,3}\.\d{1,3})\/)?([^/]+)$/.exec(path);
  if (!m || !ASSETS.has(m[2])) return null;
  const [, tag, asset] = m;
  return { asset, url: tag ? `${REPO_RELEASES}/download/${tag}/${asset}` : `${RELEASES}/${asset}` };
}

/** The app's install id: a ULID (Crockford base 32, 26 characters). */
const INSTALL_ID = /^[0-9A-HJKMNP-TV-Z]{26}$/;
const SHORT = /^[\w.+ -]{1,40}$/;
const EVENT_NAME = /^[a-z][a-z_]{0,39}$/;
const PROP_KEY = /^[a-z][a-z0-9_]{0,39}$/;

export const MAX_BODY_BYTES = 16 * 1024;
const MAX_EVENTS = 20;
const MAX_PROPS = 20;
const MAX_STRING = 60;

export function utcDay(now: Date): string {
  return now.toISOString().slice(0, 10);
}

export function isInstallId(v: unknown): v is string {
  return typeof v === "string" && INSTALL_ID.test(v);
}

/** A short label (version, OS version, chip), or null. */
export function short(v: unknown): string | null {
  return typeof v === "string" && SHORT.test(v) ? v : null;
}

/** mac / windows / other, from a browser's user agent. */
export function osFromUserAgent(ua: string | null): string {
  if (!ua) return "other";
  if (/Macintosh|Mac OS X/.test(ua)) return "mac";
  if (/Windows/.test(ua)) return "windows";
  return "other";
}

/** Just the host of a referring page: never its path or query. */
export function referrerHost(ref: string | null): string | null {
  if (!ref) return null;
  try {
    return new URL(ref).hostname.slice(0, 100) || null;
  } catch {
    return null;
  }
}

/** Who is checking for an update, from the headers the app sends. */
export interface UpdateCheck {
  id: string;
  version: string | null;
  os: string | null;
  osVersion: string | null;
  arch: string | null;
}

export function updateCheckFrom(headers: Headers): UpdateCheck | null {
  const id = headers.get("x-install-id");
  if (!isInstallId(id)) return null;
  return {
    id,
    version: short(headers.get("x-app-version")),
    os: short(headers.get("x-os")),
    osVersion: short(headers.get("x-os-version")),
    arch: short(headers.get("x-arch")),
  };
}

export type PropValue = string | number | boolean;

export interface UsageEvent {
  name: string;
  props: Record<string, PropValue>;
}

export interface UsageBatch {
  installId: string;
  version: string | null;
  events: UsageEvent[];
}

/**
 * A batch of usage events, or why it was refused. Props are flat and short:
 * numbers, booleans, and strings of at most 60 characters.
 */
export function parseUsageBatch(body: unknown): UsageBatch | string {
  if (typeof body !== "object" || body === null) return "not an object";
  const b = body as Record<string, unknown>;
  if (!isInstallId(b.install_id)) return "bad install_id";
  if (!Array.isArray(b.events) || b.events.length === 0) return "no events";
  if (b.events.length > MAX_EVENTS) return "too many events";
  const events: UsageEvent[] = [];
  for (const raw of b.events) {
    if (typeof raw !== "object" || raw === null) return "bad event";
    const e = raw as Record<string, unknown>;
    if (typeof e.name !== "string" || !EVENT_NAME.test(e.name)) return "bad event name";
    const props: Record<string, PropValue> = {};
    const rawProps = e.props ?? {};
    if (typeof rawProps !== "object" || rawProps === null || Array.isArray(rawProps)) {
      return "bad props";
    }
    const entries = Object.entries(rawProps);
    if (entries.length > MAX_PROPS) return "too many props";
    for (const [k, v] of entries) {
      if (!PROP_KEY.test(k)) return `bad prop key ${k}`;
      if (typeof v === "number") {
        if (!Number.isFinite(v)) return `bad number ${k}`;
        props[k] = v;
      } else if (typeof v === "boolean") {
        props[k] = v;
      } else if (typeof v === "string" && v.length <= MAX_STRING) {
        props[k] = v;
      } else {
        return `bad value ${k}`;
      }
    }
    events.push({ name: e.name, props });
  }
  return { installId: b.install_id, version: short(b.version), events };
}

/** Whether a Basic auth header carries this password (any user name). */
export function passwordMatches(header: string | null, password: string): boolean {
  if (!password || !header?.startsWith("Basic ")) return false;
  let decoded: string;
  try {
    decoded = atob(header.slice(6));
  } catch {
    return false;
  }
  const given = decoded.slice(decoded.indexOf(":") + 1);
  // Constant time, so the response time doesn't leak how much matched.
  if (given.length !== password.length) return false;
  let diff = 0;
  for (let i = 0; i < given.length; i++) diff |= given.charCodeAt(i) ^ password.charCodeAt(i);
  return diff === 0;
}
