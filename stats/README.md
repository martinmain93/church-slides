# Church Slides stats Worker

Counts downloads and installs, and receives opt-in usage statistics. It runs on
the developer's own Cloudflare account (free plan), unlike the band relay
(`relay/`), which each church deploys into its own. `src/worker.ts` describes
the endpoints; `site/privacy.html` describes them for users. Change both together.

- **Readout:** https://church-slides-stats.martinmain93.workers.dev/stats (any user name, the `STATS_PASSWORD`)
- **Raw data:** `wrangler d1 execute church-slides-stats --remote --command "SELECT …"`

## First-time setup

```sh
cd stats
wrangler login                                   # once, in a browser
wrangler d1 create church-slides-stats           # paste the database_id into wrangler.toml
wrangler d1 execute church-slides-stats --remote --file schema.sql
wrangler secret put STATS_PASSWORD               # the /stats password
wrangler deploy
```

The deployed address must match the one in `src/lib/usage.ts` (`STATS_URL`),
`src-tauri/tauri.conf.json` (first updater endpoint), `scripts/changelog.py`
(`DOWNLOADS`) and `site/index.html` (download buttons).

## Locally

```sh
cd stats
wrangler d1 execute church-slides-stats --local --file schema.sql
wrangler dev --local --var STATS_PASSWORD:pw     # http://localhost:8787/stats, password "pw"
```

Tests for the request checks are in `test/` and run with the app's `pnpm test`.

## Deleting an install's data

When someone sends their install number (Settings → Privacy):

```sh
wrangler d1 execute church-slides-stats --remote --command \
  "DELETE FROM installs WHERE id='<ID>'; DELETE FROM active WHERE id='<ID>'; DELETE FROM events WHERE install_id='<ID>';"
```

Their error reports and feedback are in Sentry: search `user.id:<ID>`.
