/**
 * Signing in to the web app (`app.churchslides.app`). Like the band page, it's
 * a public OAuth client identified by a client metadata document on its own
 * origin. A sign-in here is its own session: the slides computer, the band
 * page and every other browser stay signed in.
 */

import {
  BrowserSession,
  finishSignIn,
  serverFor,
  startSignIn,
  storedSession,
  type BrowserClient,
} from "@/lib/account/browserSignIn";

const WEB_APP: BrowserClient = {
  storageKey: "church-slides.web",
  clientName: "Church Slides on the web",
  clientDocument: "/oauth/web-app-client.json",
  redirectPath: "/",
};

const make = (server: string, clientId: string, refreshToken: string) => new WebSession(server, clientId, refreshToken);

export class WebSession extends BrowserSession {
  constructor(server: string, clientId: string, refreshToken: string) {
    super(WEB_APP, server, clientId, refreshToken);
  }

  static stored(): WebSession | null {
    return storedSession(WEB_APP, make);
  }

  static finishSignIn(loc: Location = window.location): Promise<WebSession | null> {
    return finishSignIn(WEB_APP, make, loc);
  }
}

export const webServer = (loc: Pick<Location, "search"> = window.location) => serverFor(WEB_APP, loc);
export const startWebSignIn = (server = webServer()) => startSignIn(WEB_APP, server);

/** What a role may change (docs/server-protocol, "Sync": who may change what). */
export function rolePermissions(role: string): { editSongs: boolean; editServices: boolean } {
  const editSongs = role === "owner" || role === "admin" || role === "operator";
  return { editSongs, editServices: editSongs || role === "set_leader" };
}
