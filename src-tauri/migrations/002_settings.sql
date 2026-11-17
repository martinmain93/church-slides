-- Per-app key/value settings store. We keep it simple — one row per key,
-- value is a JSON-encoded string. Typed read/write happens in db::settings.

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);
