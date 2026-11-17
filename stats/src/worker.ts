/**
 * Church Slides stats: how many people download the app, from where, and how
 * many installs are in use — on the developer's own Cloudflare account
 * (unlike the band relay, which lives in each church's).
 *
 *   GET  /dl/[v1.2.3/]<file>    the website's download buttons: counted, then
 *                               redirected to the file on GitHub
 *   GET  /update/latest.json    the app's update check: counted when it
 *                               carries an install id (only from installs
 *                               sharing usage statistics), answered with
 *                               GitHub's copy
 *   POST /v1/events             usage statistics, from installs whose user
 *                               said yes to sharing them
 *   GET  /stats                 the readout (Basic auth, STATS_PASSWORD)
 *
 * No IP address is stored: the country comes from Cloudflare (`request.cf`),
 * and only that is kept. A daily cron removes rows older than two years.
 * The privacy page (site/privacy.html) describes exactly this; change both
 * together.
 */

import {
  MAX_BODY_BYTES,
  downloadTarget,
  RELEASES,
  osFromUserAgent,
  parseUsageBatch,
  passwordMatches,
  referrerHost,
  updateCheckFrom,
  utcDay,
} from "./validate";
import { renderStats } from "./stats";

export interface Env {
  DB: D1Database;
  /** `wrangler secret put STATS_PASSWORD`. Unset, /stats stays locked. */
  STATS_PASSWORD?: string;
}

const KEEP_DAYS = 730;

const CORS = {
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "POST, OPTIONS",
  "access-control-allow-headers": "content-type",
};

function country(request: Request): string | null {
  const c = (request as Request & { cf?: { country?: string } }).cf?.country;
  return typeof c === "string" && /^[A-Z0-9]{2}$/.test(c) ? c : null;
}

/** Record without holding up the redirect; a failed write loses one count. */
function inBackground(ctx: ExecutionContext, work: Promise<unknown>) {
  ctx.waitUntil(work.catch((err) => console.error("stats write failed", err)));
}

async function handleDownload(request: Request, env: Env, ctx: ExecutionContext, path: string) {
  const target = downloadTarget(path);
  if (!target) return new Response("Not found", { status: 404 });
  inBackground(
    ctx,
    env.DB.prepare("INSERT INTO downloads (day, asset, country, os, referrer) VALUES (?, ?, ?, ?, ?)")
      .bind(
        utcDay(new Date()),
        target.asset,
        country(request),
        osFromUserAgent(request.headers.get("user-agent")),
        referrerHost(request.headers.get("referer")),
      )
      .run(),
  );
  return Response.redirect(target.url, 302);
}

async function handleUpdateCheck(request: Request, env: Env, ctx: ExecutionContext) {
  const check = updateCheckFrom(request.headers);
  if (check) {
    const day = utcDay(new Date());
    const where = country(request);
    inBackground(
      ctx,
      env.DB.batch([
        env.DB.prepare(
          `INSERT INTO installs (id, first_day, last_day, version, os, os_version, arch, country)
           VALUES (?1, ?2, ?2, ?3, ?4, ?5, ?6, ?7)
           ON CONFLICT (id) DO UPDATE SET last_day = ?2, version = ?3, os = ?4,
             os_version = ?5, arch = ?6, country = COALESCE(?7, country)`,
        ).bind(check.id, day, check.version, check.os, check.osVersion, check.arch, where),
        env.DB.prepare(
          "INSERT INTO active (id, day, version) VALUES (?, ?, ?) ON CONFLICT DO UPDATE SET version = excluded.version",
        ).bind(check.id, day, check.version),
      ]),
    );
  }
  // Fetched here, not redirected: a redirect would have the updater repeat
  // the install-id headers to GitHub. Failing, the app falls back to GitHub's
  // copy itself (the second endpoint in tauri.conf.json).
  const upstream = await fetch(`${RELEASES}/latest.json`, { cf: { cacheTtl: 300, cacheEverything: true } });
  if (!upstream.ok) return new Response("Upstream unavailable", { status: 502 });
  return new Response(upstream.body, {
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
}

async function handleEvents(request: Request, env: Env) {
  const length = Number(request.headers.get("content-length") ?? "0");
  if (length > MAX_BODY_BYTES) return new Response("Too large", { status: 413, headers: CORS });
  const text = await request.text();
  if (text.length > MAX_BODY_BYTES) return new Response("Too large", { status: 413, headers: CORS });
  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch {
    return new Response("Bad JSON", { status: 400, headers: CORS });
  }
  const batch = parseUsageBatch(body);
  if (typeof batch === "string") return new Response(batch, { status: 400, headers: CORS });
  const day = utcDay(new Date());
  const where = country(request);
  await env.DB.batch(
    batch.events.map((e) =>
      env.DB.prepare(
        "INSERT INTO events (day, install_id, name, version, country, props) VALUES (?, ?, ?, ?, ?, ?)",
      ).bind(day, batch.installId, e.name, batch.version, where, JSON.stringify(e.props)),
    ),
  );
  return new Response(null, { status: 204, headers: CORS });
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname;
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });

    if (request.method === "GET" && path.startsWith("/dl/")) {
      return handleDownload(request, env, ctx, path);
    }
    if (request.method === "GET" && path === "/update/latest.json") {
      return handleUpdateCheck(request, env, ctx);
    }
    if (request.method === "POST" && path === "/v1/events") {
      return handleEvents(request, env);
    }
    if (request.method === "GET" && path === "/stats") {
      if (!passwordMatches(request.headers.get("authorization"), env.STATS_PASSWORD ?? "")) {
        return new Response("Password needed", {
          status: 401,
          headers: { "www-authenticate": 'Basic realm="Church Slides stats"' },
        });
      }
      const html = await renderStats(env.DB, utcDay(new Date()));
      return new Response(html, {
        headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
      });
    }
    return new Response("Not found", { status: 404 });
  },

  async scheduled(_event: ScheduledController, env: Env): Promise<void> {
    const cutoff = utcDay(new Date(Date.now() - KEEP_DAYS * 86_400_000));
    await env.DB.batch([
      env.DB.prepare("DELETE FROM downloads WHERE day < ?").bind(cutoff),
      env.DB.prepare("DELETE FROM active WHERE day < ?").bind(cutoff),
      env.DB.prepare("DELETE FROM events WHERE day < ?").bind(cutoff),
      env.DB.prepare("DELETE FROM installs WHERE last_day < ?").bind(cutoff),
    ]);
  },
};
