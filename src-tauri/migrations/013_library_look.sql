-- The library's display look (docs/roadmap/LIBRARIES_AND_SYNC.md): one row,
-- id 'default', so every computer showing this library projects the same
-- look. `display` and `scripture` are the looks as JSON; no scripture look
-- means scripture wears the display look. Which screen it appears on stays a
-- per-computer setting.
CREATE TABLE IF NOT EXISTS library_look (
  id         TEXT PRIMARY KEY,
  display    TEXT,
  scripture  TEXT,
  updated_at INTEGER NOT NULL
);
