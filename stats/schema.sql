-- Church Slides stats (stats/src/worker.ts). Apply with:
--   wrangler d1 execute church-slides-stats --remote --file stats/schema.sql
-- Every statement is idempotent, so re-running it is safe.

-- One row per download-button click on the website.
CREATE TABLE IF NOT EXISTS downloads (
  day      TEXT NOT NULL,   -- YYYY-MM-DD, UTC
  asset    TEXT NOT NULL,   -- which installer
  country  TEXT,            -- two-letter code, from Cloudflare
  os       TEXT,            -- from the browser: mac / windows / other
  referrer TEXT             -- host name of the page that linked here, if any
);
CREATE INDEX IF NOT EXISTS downloads_day ON downloads (day);

-- One row per install, updated by its update checks.
CREATE TABLE IF NOT EXISTS installs (
  id         TEXT PRIMARY KEY,  -- the app's random install id
  first_day  TEXT NOT NULL,
  last_day   TEXT NOT NULL,
  version    TEXT,
  os         TEXT,
  os_version TEXT,
  arch       TEXT,
  country    TEXT
);

-- Which installs opened on which day: active users per day/week/month.
CREATE TABLE IF NOT EXISTS active (
  id      TEXT NOT NULL,
  day     TEXT NOT NULL,
  version TEXT,
  PRIMARY KEY (id, day)
);
CREATE INDEX IF NOT EXISTS active_day ON active (day);

-- Usage statistics, only from installs whose user said yes.
CREATE TABLE IF NOT EXISTS events (
  day        TEXT NOT NULL,
  install_id TEXT NOT NULL,
  name       TEXT NOT NULL,
  version    TEXT,
  country    TEXT,
  props      TEXT              -- JSON object
);
CREATE INDEX IF NOT EXISTS events_day ON events (day);
