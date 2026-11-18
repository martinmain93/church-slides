/**
 * Signing in on the website's band page (`/live/`), so a member of an
 * organization can follow its live set without a band link.
 *
 * The page is a public OAuth client of the account server (PKCE, no
 * secret), identified by a client metadata document on the website:
 * `<origin>/oauth/band-page-client.json`. After sign-in it keeps a refresh
 * token in this browser, asks the server which organizations the person is
 * in, and connects to one's live room with a one-use member ticket. Leader
 * controls come with the person's role, decided by the server.
 */

/** The production account server. `?server=` overrides it (local testing). */
export const DEFAULT_SERVER = "https://api.churchslides.app";
const SCOPE = "account offline_access";
const SESSION_KEY = "church-slides.band-member";
const PENDING_KEY = "church-slides.band-member.pending";

interface StoredSession {
  server: string;
  clientId: string;
  refreshToken: string;
}

interface Pending {
  server: string;
  clientId: string;
  state: string;
  verifier: string;
}

export interface Membership {
  orgId: string;
  orgName: string;
  role: string;
}

export interface LiveStatus {
  hostConnected: boolean;
  devices: number;
}

/** The server said no: signed out elsewhere, or no longer a member. */
export class SignedOutError extends Error {}
export class NotMemberError extends Error {}

const CLIENT_KEY = "church-slides.band-member.client";
const redirectUri = () => `${window.location.origin}${window.location.pathname}`;

/**
 * This page's OAuth client id on `server`. On the website it's the client
 * metadata document the website serves; a server that can't read those (or
 * a page not on https, like local testing) registers the page instead, as
 * the desktop app does, and the id is kept for next time.
 */
async function clientId(server: string): Promise<string> {
  const kept = readJson<Record<string, string>>(localStorage, CLIENT_KEY) ?? {};
  if (kept[server]) return kept[server];
  const meta = (await (await fetch(`${server}/.well-known/oauth-authorization-server`)).json()) as {
    client_id_metadata_document_supported?: boolean;
    registration_endpoint?: string;
  };
  if (meta.client_id_metadata_document_supported && window.location.protocol === "https:") {
    return `${window.location.origin}/oauth/band-page-client.json`;
  }
  if (!meta.registration_endpoint) throw new Error("This server can't sign in the band page.");
  const res = await fetch(meta.registration_endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      client_name: "Church Slides band screen",
      redirect_uris: [redirectUri()],
      grant_types: ["authorization_code", "refresh_token"],
      response_types: ["code"],
      token_endpoint_auth_method: "none",
    }),
  });
  if (!res.ok) throw new Error("This server refused to sign in the band page.");
  const id = ((await res.json()) as { client_id: string }).client_id;
  writeJson(localStorage, CLIENT_KEY, { ...kept, [server]: id });
  return id;
}

function readJson<T>(storage: Storage, key: string): T | null {
  try {
    const raw = storage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

function writeJson(storage: Storage, key: string, value: unknown | null): void {
  try {
    if (value === null) storage.removeItem(key);
    else storage.setItem(key, JSON.stringify(value));
  } catch {
    // Private browsing with storage off: sign-in lasts only this page.
  }
}

function base64url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function randomToken(): string {
  return base64url(crypto.getRandomValues(new Uint8Array(32)));
}

async function s256(verifier: string): Promise<string> {
  return base64url(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier))));
}

/** Which account server this page signs in to. */
export function memberServer(loc: Pick<Location, "search"> = window.location): string {
  const fromUrl = new URLSearchParams(loc.search).get("server");
  if (fromUrl && /^https?:\/\//.test(fromUrl)) return fromUrl.replace(/\/+$/, "");
  return readJson<StoredSession>(localStorage, SESSION_KEY)?.server ?? DEFAULT_SERVER;
}

/** Go to the server's sign-in page; it comes back here with a code. */
export async function startMemberSignIn(server = memberServer()): Promise<void> {
  const pending: Pending = { server, clientId: await clientId(server), state: randomToken(), verifier: randomToken() };
  writeJson(sessionStorage, PENDING_KEY, pending);
  const url = new URL(`${server}/authorize`);
  url.search = new URLSearchParams({
    response_type: "code",
    client_id: pending.clientId,
    redirect_uri: redirectUri(),
    scope: SCOPE,
    state: pending.state,
    code_challenge: await s256(pending.verifier),
    code_challenge_method: "S256",
    resource: `${server}/v1`,
  }).toString();
  window.location.assign(url.toString());
}

/** A session that knows how to call the server as the signed-in person. */
export class MemberSession {
  private access: { token: string; expires: number } | null = null;
  private refreshing: Promise<string> | null = null;

  constructor(
    readonly server: string,
    private readonly clientId: string,
    private refreshToken: string,
  ) {}

  /** The session kept in this browser, if any. */
  static stored(): MemberSession | null {
    const s = readJson<StoredSession>(localStorage, SESSION_KEY);
    return s?.clientId ? new MemberSession(s.server, s.clientId, s.refreshToken) : null;
  }

  /** Back from the sign-in page (`?code=…&state=…`): trade the code for
   * tokens and tidy the address bar. `null` when this isn't a return. */
  static async finishSignIn(loc: Location = window.location): Promise<MemberSession | null> {
    const params = new URLSearchParams(loc.search);
    const code = params.get("code");
    const error = params.get("error");
    if (!code && !error) return null;
    const pending = readJson<Pending>(sessionStorage, PENDING_KEY);
    writeJson(sessionStorage, PENDING_KEY, null);
    window.history.replaceState(null, "", loc.pathname);
    if (error) return null; // Cancelled on the sign-in page.
    if (!pending || params.get("state") !== pending.state) throw new Error("This sign-in expired. Sign in again.");
    const res = await fetch(`${pending.server}/oauth/token`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        grant_type: "authorization_code",
        code: code!,
        redirect_uri: redirectUri(),
        client_id: pending.clientId,
        code_verifier: pending.verifier,
        resource: `${pending.server}/v1`,
      }),
    });
    if (!res.ok) throw new Error("Couldn't finish signing in. Try again.");
    const body = (await res.json()) as { access_token: string; refresh_token?: string; expires_in?: number };
    if (!body.refresh_token) throw new Error("Couldn't finish signing in. Try again.");
    const session = new MemberSession(pending.server, pending.clientId, body.refresh_token);
    session.keep(body);
    return session;
  }

  private keep(body: { access_token: string; refresh_token?: string; expires_in?: number }): void {
    this.access = { token: body.access_token, expires: Date.now() + ((body.expires_in ?? 900) - 30) * 1000 };
    if (body.refresh_token) this.refreshToken = body.refresh_token;
    writeJson(localStorage, SESSION_KEY, {
      server: this.server,
      clientId: this.clientId,
      refreshToken: this.refreshToken,
    } satisfies StoredSession);
  }

  private async accessToken(): Promise<string> {
    if (this.access && this.access.expires > Date.now()) return this.access.token;
    // One refresh at a time: refresh tokens rotate, so a second would fail.
    this.refreshing ??= (async () => {
      try {
        const res = await fetch(`${this.server}/oauth/token`, {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: new URLSearchParams({
            grant_type: "refresh_token",
            refresh_token: this.refreshToken,
            client_id: this.clientId,
            resource: `${this.server}/v1`,
          }),
        });
        if (res.status === 400 || res.status === 401) {
          writeJson(localStorage, SESSION_KEY, null);
          throw new SignedOutError("You've been signed out. Sign in again.");
        }
        if (!res.ok) throw new Error("Couldn't reach Church Slides.");
        const body = (await res.json()) as { access_token: string; refresh_token?: string; expires_in?: number };
        this.keep(body);
        return body.access_token;
      } finally {
        this.refreshing = null;
      }
    })();
    return this.refreshing;
  }

  private async api<T>(method: string, path: string): Promise<T> {
    const res = await fetch(`${this.server}/v1${path}`, {
      method,
      headers: { Authorization: `Bearer ${await this.accessToken()}` },
    });
    if (res.status === 401) {
      this.access = null;
      throw new SignedOutError("You've been signed out. Sign in again.");
    }
    if (res.status === 404 || res.status === 403) throw new NotMemberError("You're no longer in this organization.");
    if (!res.ok) throw new Error("Couldn't reach Church Slides.");
    return (await res.json()) as T;
  }

  async memberships(): Promise<{ email: string | null; memberships: Membership[] }> {
    const me = await this.api<{ user: { email?: string | null }; memberships: Membership[] }>("GET", "/me");
    return { email: me.user.email ?? null, memberships: me.memberships };
  }

  liveStatus(orgId: string): Promise<LiveStatus> {
    return this.api("GET", `/orgs/${encodeURIComponent(orgId)}/live`);
  }

  /** A fresh, one-use address for this person's socket to the live room. */
  async liveSocketUrl(orgId: string): Promise<string> {
    return (await this.api<{ url: string }>("POST", `/orgs/${encodeURIComponent(orgId)}/live/member-ticket`)).url;
  }

  /** Forget this browser's sign-in, and tell the server (best effort). */
  async signOut(): Promise<void> {
    writeJson(localStorage, SESSION_KEY, null);
    try {
      const meta = (await (await fetch(`${this.server}/.well-known/oauth-authorization-server`)).json()) as {
        revocation_endpoint?: string;
      };
      if (!meta.revocation_endpoint) return;
      await fetch(meta.revocation_endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({ token: this.refreshToken, client_id: this.clientId }),
      });
    } catch {
      // Offline: the token still expires by itself.
    }
  }
}

/** Which organization to follow: the one with a service running (if only
 * one is), else the only one, else the person picks. */
export function pickOrganization(
  memberships: Membership[],
  live: Record<string, boolean>,
  remembered: string | null,
): Membership | null {
  const running = memberships.filter((m) => live[m.orgId]);
  if (running.length === 1) return running[0]!;
  if (memberships.length === 1) return memberships[0]!;
  if (running.length === 0 && remembered) return memberships.find((m) => m.orgId === remembered) ?? null;
  return null;
}
