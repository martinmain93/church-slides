-- A log of services: when a setlist was presented and which songs it held
-- then. `setlists.last_used_at` alone can't answer "haven't played in the last
-- 3 services" — a setlist is reused and edited, and its songs today aren't
-- the songs it held three Sundays ago.
--
-- Written by `setlists::mark_setlist_used` (every service start) and
-- `add_setlist_item` (a song added mid-service). A start within
-- SERVICE_MERGE_MS of the last one for the same setlist is the same service.

CREATE TABLE IF NOT EXISTS services (
  id TEXT PRIMARY KEY,
  -- No foreign key: a deleted setlist's services are still history.
  setlist_id TEXT NOT NULL,
  started_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS services_started ON services(started_at);
CREATE INDEX IF NOT EXISTS services_setlist ON services(setlist_id, started_at);

CREATE TABLE IF NOT EXISTS service_songs (
  service_id TEXT NOT NULL REFERENCES services(id) ON DELETE CASCADE,
  -- No foreign key either: a song deleted later was still played.
  song_id TEXT NOT NULL,
  PRIMARY KEY (service_id, song_id)
);

CREATE INDEX IF NOT EXISTS service_songs_song ON service_songs(song_id);
