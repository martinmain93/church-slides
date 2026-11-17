/**
 * Church Slides online relay: band screens over the internet, for when the
 * church's Wi-Fi won't let devices reach the slides computer (guest networks
 * that isolate devices, separate networks for the stage, a phone on 4G).
 *
 * Deployed by the desktop app into the *church's own* Cloudflare account
 * (see src-tauri/src/cloud.rs), so it runs on their free plan and nobody
 * else's credentials. One Worker; one Durable Object per service ("room"),
 * which exists only while devices are connected and sleeps between events.
 *
 * The protocol is the local band server's (src-tauri/src/band.rs) unchanged:
 * the slides computer publishes `setlist` and `position` snapshots, the room
 * keeps the latest of each and hands them to every device the moment it
 * connects; leader commands go back to the slides computer.
 *
 *   GET /r/<room>/band?k=<key>             the band page
 *   GET /r/<room>/leader?k=<key>&pin=…     the same page, leader controls
 *   GET /r/<room>/ws?k=<key>               a device's socket
 *   GET /r/<room>/host?hk=<host key>&pin=… the slides computer's socket
 *
 * `<key>` is HMAC(HOST_KEY, room): the slides computer can mint it, a device
 * gets it from the QR code, and the Worker checks it *before* touching the
 * Durable Object — a guessed or stale link costs nothing.
 */

import { BAND_CSS, BAND_JS } from "./assets";

export interface Env {
  ROOMS: DurableObjectNamespace;
  /** Shared with the slides computer at deploy time. Never sent to devices. */
  HOST_KEY: string;
}

const ROOM_RE = /^\/r\/([a-z0-9]{8,32})\/(band|leader|ws|host)$/;

export async function roomKey(hostKey: string, room: string): Promise<string> {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(hostKey), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = new Uint8Array(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(`room:${room}`)));
  return [...sig.slice(0, 16)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** Constant-time string comparison. */
function same(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function page(): Response {
  const html = `<!doctype html>
<html lang="en" class="dark">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
<meta name="theme-color" content="#09090b" />
<title>Band screen</title>
<link rel="stylesheet" href="/assets/band.css" />
</head>
<body>
<div id="root"></div>
<script type="module" src="/assets/band.js"></script>
</body>
</html>`;
  return new Response(html, { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" } });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/assets/band.js") {
      return new Response(BAND_JS, { headers: { "content-type": "text/javascript; charset=utf-8", "cache-control": "no-cache" } });
    }
    if (url.pathname === "/assets/band.css") {
      return new Response(BAND_CSS, { headers: { "content-type": "text/css; charset=utf-8", "cache-control": "no-cache" } });
    }
    if (url.pathname === "/health") return new Response("ok");

    const m = ROOM_RE.exec(url.pathname);
    if (!m) return new Response("Not found", { status: 404 });
    const [, room, what] = m as unknown as [string, string, string];

    if (what === "host") {
      if (!same(url.searchParams.get("hk") ?? "", env.HOST_KEY)) return new Response("Forbidden", { status: 403 });
    } else {
      if (!same(url.searchParams.get("k") ?? "", await roomKey(env.HOST_KEY, room))) {
        return new Response(
          what === "ws" ? "Forbidden" : "This band screen link has expired or is wrong. Scan the QR code on the slides computer again.",
          { status: 403, headers: { "content-type": "text/plain; charset=utf-8" } },
        );
      }
      if (what === "band" || what === "leader") return page();
    }

    if (request.headers.get("Upgrade")?.toLowerCase() !== "websocket") {
      return new Response("Expected a WebSocket", { status: 426 });
    }
    return env.ROOMS.get(env.ROOMS.idFromName(room)).fetch(request);
  },
} satisfies ExportedHandler<Env>;

export { Room } from "./room";
