-- Service items: prepared scripture and one-off text in a setlist.
-- See Plan/SERVICE_ITEMS.md.
--
-- `setlist_items.kind` has existed since 001 ('song' only until now). Two
-- more kinds use it, both with song_id NULL:
--   'scripture'  content = {"reference","translation","verses":[{ref,text}],
--                           "credit"?, "own_text"?}
--   'text'       content = {"title"}; its slides live in `slides_override`,
--                exactly like a song edited for one setlist.
-- `content` is NULL for songs.

ALTER TABLE setlist_items ADD COLUMN content TEXT;

-- Readings saved to the library (a creed, a prayer, a liturgy): text items
-- worth reusing. A table of their own rather than rows in `songs`, so nothing
-- that reads songs — the AI's library index, CCLI facts, charts, the MCP song
-- tools — ever mistakes one for a song. The library search lists both.
CREATE TABLE IF NOT EXISTS readings (
  id          TEXT PRIMARY KEY,
  title       TEXT NOT NULL,
  -- Same format as a song body (docs/SONG_FORMAT.md): blank line = new slide.
  body_text   TEXT NOT NULL,
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL
);
