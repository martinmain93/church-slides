-- Library search reads a song's credits too: author, copyright (publisher)
-- and CCLI number, so "Tomlin", "Hillsong" or a SongSelect number find it.
--
-- 001's index used `content='songs'`, which can only index columns `songs`
-- has. Credits are three columns joined, so the index now keeps its own copy
-- of the text (a few hundred KB for a few hundred songs) and the triggers
-- write all three fields.

DROP TRIGGER IF EXISTS songs_ai;
DROP TRIGGER IF EXISTS songs_ad;
DROP TRIGGER IF EXISTS songs_au;
DROP TABLE IF EXISTS songs_fts;

CREATE VIRTUAL TABLE songs_fts USING fts5(
  title,
  body_text,
  credits,
  tokenize='porter unicode61'
);

INSERT INTO songs_fts(rowid, title, body_text, credits)
SELECT rowid, title, body_text,
       COALESCE(author, '') || ' ' || COALESCE(copyright, '') || ' ' || COALESCE(ccli, '')
FROM songs;

CREATE TRIGGER songs_ai AFTER INSERT ON songs BEGIN
  INSERT INTO songs_fts(rowid, title, body_text, credits)
  VALUES (new.rowid, new.title, new.body_text,
          COALESCE(new.author, '') || ' ' || COALESCE(new.copyright, '') || ' ' || COALESCE(new.ccli, ''));
END;

CREATE TRIGGER songs_ad AFTER DELETE ON songs BEGIN
  DELETE FROM songs_fts WHERE rowid = old.rowid;
END;

CREATE TRIGGER songs_au AFTER UPDATE ON songs BEGIN
  DELETE FROM songs_fts WHERE rowid = old.rowid;
  INSERT INTO songs_fts(rowid, title, body_text, credits)
  VALUES (new.rowid, new.title, new.body_text,
          COALESCE(new.author, '') || ' ' || COALESCE(new.copyright, '') || ' ' || COALESCE(new.ccli, ''));
END;
