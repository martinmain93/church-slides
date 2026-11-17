-- References for the score follower: what a song sounds like, to follow it by.
--
-- A reference is what the follower compares live audio against — the melody,
-- chords and word rhythm of each slide's line, and the order the arrangement
-- runs in. Two kinds, one table:
--
--   lead_sheet  a notation PDF read by the sidecar (auto_presenter/leadsheet);
--               `data_json` is the parsed score. One per song: importing a
--               new sheet replaces the old one.
--   recording   (later) a past service of the song with the operator's slide
--               changes as ground truth — the "auto-golden". Many per song.
--
-- Like `song_charts`, kept beside the song rather than in `body_text`: the
-- projector and the word matcher never see it, and a song without one
-- behaves exactly as before. `data_json` is opaque here; its shape belongs to
-- the sidecar and carries its own reader version. `summary_json` is the
-- small part the library shows (bars, key, sections) without loading the
-- reference itself.

CREATE TABLE IF NOT EXISTS song_references (
  id            TEXT PRIMARY KEY,
  song_id       TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
  kind          TEXT NOT NULL,
  -- The file it came from (lead sheet) or the recording id (recording).
  source_name   TEXT,
  data_json     TEXT NOT NULL,
  summary_json  TEXT NOT NULL DEFAULT '{}',
  created_at    INTEGER NOT NULL,
  updated_at    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS song_references_song ON song_references(song_id);
