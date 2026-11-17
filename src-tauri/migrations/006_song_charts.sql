-- Chord charts for the band (multi-device band screens).
--
-- One chart per song, stored beside it rather than inside `body_text`: the
-- projector and the AI matcher read `body_text` and must never see chord
-- symbols, and a song with no chart behaves exactly as before. The chart is
-- the band's view of the same song — it does not need to agree with the
-- slides line for line (the band screen aligns them by words).
--
-- `chart_json` is the `ChordChart` shape in `src/lib/chords/chart.ts`.
-- `original_key` duplicates `chart_json.key` so the library can list keys
-- without parsing every chart.

CREATE TABLE IF NOT EXISTS song_charts (
  song_id        TEXT PRIMARY KEY REFERENCES songs(id) ON DELETE CASCADE,
  chart_json     TEXT NOT NULL,
  original_key   TEXT,
  -- Which importer produced it ("chordpro", "openlyrics", …), for support.
  source_format  TEXT,
  updated_at     INTEGER NOT NULL
);
