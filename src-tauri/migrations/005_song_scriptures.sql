-- Scriptures attached to songs (Bible integration).
--
-- Anchored by *words*, not by slide id. `docs/SONG_FORMAT.md` is explicit that
-- slide ids are `sha1(song_id : order : lyrics)` and "an in-memory artifact,
-- not a database key" — inserting one slide reshuffles every id after it, so a
-- scripture pinned to a slide id would detach the first time someone fixed a
-- typo earlier in the song. `anchor_lyric` survives edits, repeats,
-- per-setlist slide overrides, and the leading-blank-slide offset.
--
-- Nothing else in the schema changes: no column is added to `songs`, so a
-- library with the feature switched off is byte-identical to one that never
-- had it. See Plan/BIBLE_INTEGRATION.md §2.

CREATE TABLE IF NOT EXISTS song_scriptures (
  id            TEXT PRIMARY KEY,
  song_id       TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
  -- Canonical display form, e.g. "Psalm 103:1–5". The sidecar's
  -- `scripture.references` parser is the authority on what may go here.
  reference     TEXT NOT NULL,
  -- NULL means "whatever translation the service is set to".
  translation   TEXT,
  -- "chorus:1", narrowing where anchor_lyric is looked for. NULL = anywhere.
  section_key   TEXT,
  -- The line this passage sits behind, verbatim. NULL = the whole section
  -- (or the whole song, when section_key is NULL too).
  anchor_lyric  TEXT,
  -- 1 = show on every repeat of the matching slide, 0 = only the first.
  every_repeat  INTEGER NOT NULL DEFAULT 1,
  position      INTEGER NOT NULL,
  -- 'manual' | 'suggested'. Suggested rows came from the AI and were approved
  -- by a human; keeping the provenance lets the editor show it.
  source        TEXT NOT NULL DEFAULT 'manual',
  -- 'quotes' | 'alludes' | 'thematic', or NULL for manually added rows.
  strength      TEXT,
  note          TEXT
);

CREATE INDEX IF NOT EXISTS song_scriptures_song
  ON song_scriptures(song_id, position);
