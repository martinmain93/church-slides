-- Auto Presenter — initial schema
-- See Plan/PHASE_0.md for design rationale.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS songs (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  body_text TEXT NOT NULL,
  ccli TEXT,
  author TEXT,
  copyright TEXT,
  default_tempo_wps REAL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS songs_fts USING fts5(
  title,
  body_text,
  content='songs',
  content_rowid='rowid',
  tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS songs_ai AFTER INSERT ON songs BEGIN
  INSERT INTO songs_fts(rowid, title, body_text)
  VALUES (new.rowid, new.title, new.body_text);
END;

CREATE TRIGGER IF NOT EXISTS songs_ad AFTER DELETE ON songs BEGIN
  INSERT INTO songs_fts(songs_fts, rowid, title, body_text)
  VALUES ('delete', old.rowid, old.title, old.body_text);
END;

CREATE TRIGGER IF NOT EXISTS songs_au AFTER UPDATE ON songs BEGIN
  INSERT INTO songs_fts(songs_fts, rowid, title, body_text)
  VALUES ('delete', old.rowid, old.title, old.body_text);
  INSERT INTO songs_fts(rowid, title, body_text)
  VALUES (new.rowid, new.title, new.body_text);
END;

CREATE TABLE IF NOT EXISTS setlists (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  last_used_at INTEGER
);

CREATE TABLE IF NOT EXISTS setlist_items (
  id TEXT PRIMARY KEY,
  setlist_id TEXT NOT NULL REFERENCES setlists(id) ON DELETE CASCADE,
  kind TEXT NOT NULL DEFAULT 'song',
  song_id TEXT REFERENCES songs(id) ON DELETE RESTRICT,
  position INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS setlist_items_setlist
  ON setlist_items(setlist_id, position);
