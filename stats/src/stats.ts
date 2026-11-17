/**
 * The /stats page: a handful of tables over the last 30 days, rendered on the
 * server so it needs no script or build. Add a query here when a question
 * comes up; the raw rows are in D1 for anything one-off
 * (`wrangler d1 execute church-slides-stats --remote --command "…"`).
 */

type Row = Record<string, string | number | null>;

function daysBefore(day: string, n: number): string {
  return new Date(Date.parse(`${day}T00:00:00Z`) - n * 86_400_000).toISOString().slice(0, 10);
}

function esc(v: unknown): string {
  return String(v ?? "—").replace(/[&<>"]/g, (c) => `&#${c.charCodeAt(0)};`);
}

function table(title: string, rows: Row[], note = ""): string {
  if (rows.length === 0) return `<section><h2>${esc(title)}</h2><p class="empty">Nothing yet.</p></section>`;
  const cols = Object.keys(rows[0]);
  const head = cols.map((c) => `<th>${esc(c)}</th>`).join("");
  const body = rows
    .map((r) => `<tr>${cols.map((c) => `<td>${esc(r[c])}</td>`).join("")}</tr>`)
    .join("");
  return `<section><h2>${esc(title)}</h2>${note ? `<p class="note">${esc(note)}</p>` : ""}<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></section>`;
}

async function all(db: D1Database, sql: string, ...args: unknown[]): Promise<Row[]> {
  const res = await db.prepare(sql).bind(...args).all<Row>();
  return res.results ?? [];
}

export async function renderStats(db: D1Database, today: string): Promise<string> {
  const d30 = daysBefore(today, 29);
  const d7 = daysBefore(today, 6);

  const [headline] = await all(
    db,
    `SELECT
       (SELECT COUNT(*) FROM downloads WHERE day >= ?1) AS downloads_30d,
       (SELECT COUNT(*) FROM installs) AS installs_ever,
       (SELECT COUNT(DISTINCT id) FROM active WHERE day = ?3) AS active_today,
       (SELECT COUNT(DISTINCT id) FROM active WHERE day >= ?2) AS active_7d,
       (SELECT COUNT(DISTINCT id) FROM active WHERE day >= ?1) AS active_30d,
       (SELECT COUNT(DISTINCT install_id) FROM events WHERE day >= ?1) AS sharing_usage_30d`,
    d30,
    d7,
    today,
  );

  const sections = await Promise.all([
    all(db, "SELECT asset, COUNT(*) AS downloads FROM downloads WHERE day >= ? GROUP BY asset ORDER BY downloads DESC", d30).then(
      (r) => table("Downloads by installer (30 days)", r),
    ),
    all(db, "SELECT COALESCE(country, '?') AS country, COUNT(*) AS downloads FROM downloads WHERE day >= ? GROUP BY country ORDER BY downloads DESC LIMIT 25", d30).then(
      (r) => table("Downloads by country (30 days)", r),
    ),
    all(db, "SELECT day, COUNT(*) AS downloads FROM downloads WHERE day >= ? GROUP BY day ORDER BY day DESC", d30).then(
      (r) => table("Downloads by day", r),
    ),
    all(db, "SELECT COALESCE(referrer, '(direct)') AS referrer, COUNT(*) AS downloads FROM downloads WHERE day >= ? GROUP BY referrer ORDER BY downloads DESC LIMIT 15", d30).then(
      (r) => table("Where downloads came from (30 days)", r),
    ),
    all(db, "SELECT day, COUNT(*) AS installs_open FROM active WHERE day >= ? GROUP BY day ORDER BY day DESC", d30).then(
      (r) => table("Installs opened per day", r, "Counted from update checks, so only installs sharing usage statistics are here. Downloads count everyone."),
    ),
    all(db, "SELECT COALESCE(version, '?') AS version, COUNT(*) AS installs FROM installs WHERE last_day >= ? GROUP BY version ORDER BY version DESC", d30).then(
      (r) => table("Versions in use (seen in 30 days, sharing installs only)", r),
    ),
    all(db, "SELECT COALESCE(os, '?') AS os, COALESCE(os_version, '?') AS os_version, COALESCE(arch, '?') AS chip, COUNT(*) AS installs FROM installs WHERE last_day >= ? GROUP BY os, os_version, arch ORDER BY installs DESC", d30).then(
      (r) => table("Systems in use (seen in 30 days, sharing installs only)", r),
    ),
    all(db, "SELECT COALESCE(country, '?') AS country, COUNT(*) AS installs FROM installs WHERE last_day >= ? GROUP BY country ORDER BY installs DESC LIMIT 25", d30).then(
      (r) => table("Installs by country (seen in 30 days, sharing installs only)", r),
    ),
    all(
      db,
      `SELECT json_extract(props, '$.screen') AS screen, COUNT(*) AS services,
         COUNT(DISTINCT install_id) AS installs,
         ROUND(AVG(json_extract(props, '$.minutes')), 0) AS avg_minutes,
         ROUND(100.0 * SUM(json_extract(props, '$.ai_minutes') > 0) / COUNT(*), 0) AS pct_with_ai,
         SUM(json_extract(props, '$.by_ai')) AS changes_by_ai,
         SUM(json_extract(props, '$.by_hand')) AS changes_by_hand,
         SUM(json_extract(props, '$.continuous')) AS used_scroll
       FROM events WHERE name = 'service' AND day >= ? GROUP BY screen`,
      d30,
    ).then((r) => table("Services (30 days, sharing installs only)", r)),
    all(
      db,
      `SELECT json_extract(props, '$.ai') AS ai_engine, COUNT(DISTINCT install_id) AS installs,
         ROUND(AVG(json_extract(props, '$.songs')), 0) AS avg_library_songs
       FROM events WHERE name = 'app_open' AND day >= ? GROUP BY ai_engine ORDER BY installs DESC`,
      d30,
    ).then((r) => table("App opens (30 days, sharing installs only)", r)),
    all(db, "SELECT name, COUNT(*) AS events, COUNT(DISTINCT install_id) AS installs FROM events WHERE day >= ? GROUP BY name ORDER BY events DESC", d30).then(
      (r) => table("All usage events (30 days)", r),
    ),
  ]);

  const tiles = Object.entries(headline ?? {})
    .map(([k, v]) => `<div class="tile"><div class="n">${esc(v)}</div><div class="k">${esc(k.replace(/_/g, " "))}</div></div>`)
    .join("");

  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Church Slides stats</title>
<style>
  :root { color-scheme: light dark; --bg: #fafaf9; --fg: #1c1917; --muted: #78716c; --line: #e7e5e4; --tile: #fff; }
  @media (prefers-color-scheme: dark) { :root { --bg: #0c0a09; --fg: #f5f5f4; --muted: #a8a29e; --line: #292524; --tile: #1c1917; } }
  body { margin: 0; padding: 24px 16px 48px; background: var(--bg); color: var(--fg); font: 14px/1.45 system-ui, sans-serif; }
  main { max-width: 960px; margin: 0 auto; }
  h1 { font-size: 22px; margin: 0 0 4px; } h2 { font-size: 15px; margin: 28px 0 8px; }
  .sub, .note, .empty { color: var(--muted); } .note { margin: -4px 0 8px; font-size: 12px; }
  .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 8px; margin-top: 16px; }
  .tile { background: var(--tile); border: 1px solid var(--line); border-radius: 8px; padding: 12px; }
  .n { font-size: 24px; font-weight: 600; font-variant-numeric: tabular-nums; } .k { color: var(--muted); font-size: 12px; }
  section { overflow-x: auto; }
  table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
  th, td { text-align: left; padding: 6px 10px 6px 0; border-bottom: 1px solid var(--line); white-space: nowrap; }
  th { color: var(--muted); font-weight: 500; font-size: 12px; }
</style></head>
<body><main>
<h1>Church Slides stats</h1>
<div class="sub">As of ${esc(today)} (UTC). Kept for two years.</div>
<div class="tiles">${tiles}</div>
${sections.join("\n")}
</main></body></html>`;
}
