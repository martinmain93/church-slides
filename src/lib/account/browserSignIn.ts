/**
 * Signing in to an account server from a web page: the band page (`/live/`)
 * and the web app (`app.churchslides.app`).
 *
 * Each page is a public OAuth client of the server (PKCE, no secret),
 * identified by a client metadata document on its own origin, such as
 * `<origin>/oauth/band-page-client.json`. After sign-in it keeps a refresh
 * token in this browser, and every device keeps its own: signing in on a
 * phone never signs out the slides computer (docs/server-protocol, "Signing
 * in").
 *
 * No React here, and no platform: only `fetch`, `localStorage` and `crypto`.
 */

/** The production account server. `?server=` overrides it (local testing). */
export const DEFAULT_SERVER = "https://api.churchslides.app";
const SCOPE = "account offline_access";

/** Which page is signing in. */
export interface BrowserClient {
  /** Prefix of this page's keys in the browser's storage. */
  storageKey: string;
  /** The name the server shows on its sign-in page and in "signed-in devices". */
  clientName: string;
  /** Path of the client metadata document on this page's origin. */
  clientDocument: string;
  /** Where the sign-in page sends people back: this path on the page's
   * origin, matching the client document's `redirect_uris`. */
  redirectPath: string;
}

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
  /** The server's issuer name, to check the answer came from it (RFC 9207). */
  issuer?: string;
  /** The server says it always sends `iss`: an answer without it is refused. */
  issRequired?: boolean;
}

export interface Membership {
  orgId: string;
  orgName: string;
  role: string;
}

/** The server said no: signed out elsewhere, or no longer a member. */
export class SignedOutError extends Error {}
export class NotMemberError extends Error {}
/** Any other answer from the API that isn't a success. `code` is the
 * protocol's error code ("rate_limited", "not_on_plan", …). */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string | null,
  ) {
    super(message);
  }
}
/** The server couldn't be reached at all (offline, DNS, a dropped connection). */
export class OfflineError extends Error {}

const sessionKey = (c: BrowserClient) => c.storageKey;
const pendingKey = (c: BrowserClient) => `${c.storageKey}.pending`;
const clientKey = (c: BrowserClient) => `${c.storageKey}.client`;
const redirectUri = (c: BrowserClient) => `${window.location.origin}${c.redirectPath}`;

interface ServerMeta {
  issuer?: string;
  /** The server names itself in the sign-in answer (RFC 9207). */
  authorization_response_iss_parameter_supported?: boolean;
  client_id_metadata_document_supported?: boolean;
  registration_endpoint?: string;
  revocation_endpoint?: string;
}

async function serverMeta(server: string): Promise<ServerMeta> {
  return (await (await fetch(`${server}/.well-known/oauth-authorization-server`)).json()) as ServerMeta;
}

/**
 * This page's OAuth client id on `server`. On the website it's the client
 * metadata document the page's origin serves. A server that can't read those
 * (or a page not on https, like local testing) registers the page instead, as
 * the desktop app does, and the id is kept for next time.
 */
async function clientId(client: BrowserClient, server: string, meta: ServerMeta): Promise<string> {
  const kept = readJson<Record<string, string>>(localStorage, clientKey(client)) ?? {};
  if (kept[server]) return kept[server];
  if (meta.client_id_metadata_document_supported && window.location.protocol === "https:") {
    return `${window.location.origin}${client.clientDocument}`;
  }
  if (!meta.registration_endpoint) throw new Error("This server can't sign in this page.");
  const res = await fetch(meta.registration_endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      client_name: client.clientName,
      redirect_uris: [redirectUri(client)],
      grant_types: ["authorization_code", "refresh_token"],
      response_types: ["code"],
      token_endpoint_auth_method: "none",
    }),
  });
  if (!res.ok) throw new Error("This server refused to sign in this page.");
  const id = ((await res.json()) as { client_id: string }).client_id;
  writeJson(localStorage, clientKey(client), { ...kept, [server]: id });
  return id;
}

export function readJson<T>(storage: Storage, key: string): T | null {
  try {
    const raw = storage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

export function writeJson(storage: Storage, key: string, value: unknown | null): void {
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

/** Servers the website's pages will talk to: the official one, its staging
 * copy, and, in a development build only, one on this computer. A link
 * naming any other server is ignored, so nobody can point a page (and a
 * member's sign-in) at a server of their own. A self-hosted server serves
 * its own pages. */
const TRUSTED_SERVERS = ["https://api.churchslides.app", "https://church-slides-api-staging.martinmain93.workers.dev"];

/** `url` as a server origin if these pages may use it, else null. */
export function trustedServer(url: string | null | undefined): string | null {
  if (!url) return null;
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  if (parsed.pathname.replace(/\/+$/, "") !== "" || parsed.search || parsed.hash || parsed.username || parsed.password) return null;
  const local =
    import.meta.env.DEV && parsed.protocol === "http:" && (parsed.hostname === "localhost" || parsed.hostname === "127.0.0.1");
  return local || TRUSTED_SERVERS.includes(parsed.origin) ? parsed.origin : null;
}

/** Which account server this page signs in to. */
export function serverFor(client: BrowserClient, loc: Pick<Location, "search"> = window.location): string {
  const fromUrl = trustedServer(new URLSearchParams(loc.search).get("server"));
  if (fromUrl) return fromUrl;
  return trustedServer(readJson<StoredSession>(localStorage, sessionKey(client))?.server) ?? DEFAULT_SERVER;
}

/** Go to the server's sign-in page; it comes back here with a code. */
export async function startSignIn(client: BrowserClient, server = serverFor(client)): Promise<void> {
  const meta = await serverMeta(server);
  const pending: Pending = {
    server,
    clientId: await clientId(client, server, meta),
    state: randomToken(),
    verifier: randomToken(),
    issuer: meta.issuer,
    issRequired: meta.authorization_response_iss_parameter_supported === true,
  };
  writeJson(sessionStorage, pendingKey(client), pending);
  const url = new URL(`${server}/authorize`);
  url.search = new URLSearchParams({
    response_type: "code",
    client_id: pending.clientId,
    redirect_uri: redirectUri(client),
    scope: SCOPE,
    state: pending.state,
    code_challenge: await s256(pending.verifier),
    code_challenge_method: "S256",
    resource: `${server}/v1`,
  }).toString();
  window.location.assign(url.toString());
}

type Make<S> = (server: string, clientId: string, refreshToken: string) => S;

/** The session kept in this browser, if any. */
export function storedSession<S>(client: BrowserClient, make: Make<S>): S | null {
  const s = readJson<StoredSession>(localStorage, sessionKey(client));
  const server = trustedServer(s?.server);
  return s?.clientId && server ? make(server, s.clientId, s.refreshToken) : null;
}

/** Back from the sign-in page (`?code=…&state=…`): trade the code for tokens
 * and tidy the address bar. `null` when this isn't a return. */
export async function finishSignIn<S extends BrowserSession>(
  client: BrowserClient,
  make: Make<S>,
  loc: Location = window.location,
): Promise<S | null> {
  const params = new URLSearchParams(loc.search);
  const code = params.get("code");
  const error = params.get("error");
  if (!code && !error) return null;
  const pending = readJson<Pending>(sessionStorage, pendingKey(client));
  writeJson(sessionStorage, pendingKey(client), null);
  // Keep `?server=` (local testing) so a reload stays on the same server.
  const keep = new URLSearchParams();
  const server = params.get("server");
  if (server) keep.set("server", server);
  const kept = keep.toString(); // not `.size`: Safari before 17 lacks it
  window.history.replaceState(null, "", `${loc.pathname}${kept ? `?${kept}` : ""}${loc.hash}`);
  if (error) return null; // Cancelled on the sign-in page.
  if (!pending || params.get("state") !== pending.state) throw new Error("This sign-in expired. Sign in again.");
  // The answer must come from the server this sign-in started at.
  const iss = params.get("iss");
  if ((iss && pending.issuer && iss !== pending.issuer) || (!iss && pending.issRequired)) {
    throw new Error("This sign-in came from the wrong place. Sign in again.");
  }
  const res = await fetch(`${pending.server}/oauth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      code: code!,
      redirect_uri: redirectUri(client),
      client_id: pending.clientId,
      code_verifier: pending.verifier,
      resource: `${pending.server}/v1`,
    }),
  });
  if (!res.ok) throw new Error("Couldn't finish signing in. Try again.");
  const body = (await res.json()) as TokenBody;
  if (!body.refresh_token) throw new Error("Couldn't finish signing in. Try again.");
  const session = make(pending.server, pending.clientId, body.refresh_token);
  session.keep(body);
  return session;
}

interface TokenBody {
  access_token: string;
  refresh_token?: string;
  expires_in?: number;
}

/** A session that knows how to call the server as the signed-in person. */
export class BrowserSession {
  private access: { token: string; expires: number } | null = null;
  private refreshing: Promise<string> | null = null;

  constructor(
    readonly client: BrowserClient,
    readonly server: string,
    private readonly clientId: string,
    private refreshToken: string,
  ) {}

  /** @internal Store fresh tokens (after sign-in or a refresh). */
  keep(body: TokenBody): void {
    this.access = { token: body.access_token, expires: Date.now() + ((body.expires_in ?? 900) - 30) * 1000 };
    if (body.refresh_token) this.refreshToken = body.refresh_token;
    writeJson(localStorage, sessionKey(this.client), {
      server: this.server,
      clientId: this.clientId,
      refreshToken: this.refreshToken,
    } satisfies StoredSession);
  }

  private async accessToken(): Promise<string> {
    if (this.access && this.access.expires > Date.now()) return this.access.token;
    // One refresh at a time, across every tab: refresh tokens rotate, so a
    // second refresh with the same token signs the person out.
    this.refreshing ??= withLock(`${sessionKey(this.client)}.refresh`, () => this.refresh()).finally(() => {
      this.refreshing = null;
    });
    return this.refreshing;
  }

  private async refresh(): Promise<string> {
    // Another tab may have refreshed already: use the token it kept.
    const stored = readJson<StoredSession>(localStorage, sessionKey(this.client));
    if (stored?.server === this.server && stored.clientId === this.clientId && stored.refreshToken) {
      this.refreshToken = stored.refreshToken;
    }
    const sent = this.refreshToken;
    let res: Response;
    try {
      res = await fetch(`${this.server}/oauth/token`, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          grant_type: "refresh_token",
          refresh_token: sent,
          client_id: this.clientId,
          resource: `${this.server}/v1`,
        }),
      });
    } catch {
      throw new OfflineError("Couldn't reach Church Slides.");
    }
    if (res.status === 400 || res.status === 401) {
      // Forget the sign-in only if no other tab has replaced it meanwhile.
      if (readJson<StoredSession>(localStorage, sessionKey(this.client))?.refreshToken === sent) {
        writeJson(localStorage, sessionKey(this.client), null);
      }
      throw new SignedOutError("You've been signed out. Sign in again.");
    }
    if (!res.ok) throw new OfflineError("Couldn't reach Church Slides.");
    const body = (await res.json()) as TokenBody;
    this.keep(body);
    return body.access_token;
  }

  /** Call `{server}/v1{path}`. */
  async api<T>(method: string, path: string, body?: unknown): Promise<T> {
    const token = await this.accessToken();
    let res: Response;
    try {
      res = await fetch(`${this.server}/v1${path}`, {
        method,
        headers: {
          Authorization: `Bearer ${token}`,
          ...(body === undefined ? {} : { "Content-Type": "application/json" }),
        },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch {
      throw new OfflineError("Couldn't reach Church Slides.");
    }
    if (res.status === 401) {
      this.access = null;
      throw new SignedOutError("You've been signed out. Sign in again.");
    }
    // 404: not a member (the server hides other organizations). 403: a
    // member whose role doesn't allow it, an ApiError like any other.
    if (res.status === 404) throw new NotMemberError("You're no longer in this organization.");
    if (!res.ok) {
      const err = (await res.json().catch(() => null)) as { error?: { code?: string; message?: string } } | null;
      throw new ApiError(err?.error?.message ?? "Couldn't reach Church Slides.", res.status, err?.error?.code ?? null);
    }
    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  }

  async me(): Promise<{ id: string; email: string | null; name: string | null; memberships: Membership[] }> {
    const me = await this.api<{ user: { id: string; email?: string | null; displayName?: string | null }; memberships: Membership[] }>(
      "GET",
      "/me",
    );
    return { id: me.user.id, email: me.user.email ?? null, name: me.user.displayName ?? null, memberships: me.memberships };
  }

  /** Forget this browser's sign-in, and tell the server (best effort). */
  async signOut(): Promise<void> {
    writeJson(localStorage, sessionKey(this.client), null);
    writeJson(localStorage, clientKey(this.client), null);
    try {
      const meta = await serverMeta(this.server);
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

/** Run `f` while holding a lock shared by this site's tabs (Web Locks), or
 * just run it where the browser has none. */
function withLock<T>(name: string, f: () => Promise<T>): Promise<T> {
  const locks = typeof navigator !== "undefined" ? navigator.locks : undefined;
  return locks ? (locks.request(name, f) as Promise<T>) : f();
}

/** A socket address the server handed out must be on the server itself,
 * over TLS (or plain on a development server on this computer). */
export function socketUrlOk(url: string, server: string | undefined): boolean {
  let u: URL;
  try {
    u = new URL(url);
  } catch {
    return false;
  }
  if (!server) return u.protocol === "wss:";
  const s = new URL(server);
  return u.host === s.host && (u.protocol === "wss:" || (u.protocol === "ws:" && s.protocol === "http:"));
}
