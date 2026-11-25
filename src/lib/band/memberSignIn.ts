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

import {
  BrowserSession,
  finishSignIn,
  serverFor,
  socketUrlOk,
  startSignIn,
  storedSession,
  type BrowserClient,
  type Membership,
} from "@/lib/account/browserSignIn";

export {
  DEFAULT_SERVER,
  NotMemberError,
  SignedOutError,
  trustedServer,
  type Membership,
} from "@/lib/account/browserSignIn";

const BAND_PAGE: BrowserClient = {
  storageKey: "church-slides.band-member",
  clientName: "Church Slides band screen",
  clientDocument: "/oauth/band-page-client.json",
  redirectPath: "/live/",
};

export interface LiveStatus {
  hostConnected: boolean;
  devices: number;
}

/** Which account server this page signs in to. */
export function memberServer(loc: Pick<Location, "search"> = window.location): string {
  return serverFor(BAND_PAGE, loc);
}

/** Go to the server's sign-in page; it comes back here with a code. */
export function startMemberSignIn(server = memberServer()): Promise<void> {
  return startSignIn(BAND_PAGE, server);
}

const make = (server: string, clientId: string, refreshToken: string) =>
  new MemberSession(server, clientId, refreshToken);

/** A session that knows how to call the server as the signed-in person. */
export class MemberSession extends BrowserSession {
  constructor(server: string, clientId: string, refreshToken: string) {
    super(BAND_PAGE, server, clientId, refreshToken);
  }

  /** The session kept in this browser, if any. */
  static stored(): MemberSession | null {
    return storedSession(BAND_PAGE, make);
  }

  /** Back from the sign-in page: trade the code for tokens. `null` when this isn't a return. */
  static finishSignIn(loc: Location = window.location): Promise<MemberSession | null> {
    return finishSignIn(BAND_PAGE, make, loc);
  }

  async memberships(): Promise<{ email: string | null; memberships: Membership[] }> {
    const me = await this.me();
    return { email: me.email, memberships: me.memberships };
  }

  liveStatus(orgId: string): Promise<LiveStatus> {
    return this.api("GET", `/orgs/${encodeURIComponent(orgId)}/live`);
  }

  /** A fresh, one-use address for this person's socket to the live room. */
  async liveSocketUrl(orgId: string): Promise<string> {
    const { url } = await this.api<{ url: string }>("POST", `/orgs/${encodeURIComponent(orgId)}/live/member-ticket`);
    if (!socketUrlOk(url, this.server)) throw new Error("The server sent a live address that isn't its own.");
    return url;
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
