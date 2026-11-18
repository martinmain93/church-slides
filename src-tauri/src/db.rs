//! SQLite layer.
//!
//! The Rust shell owns all writes; the Python sidecar reads via its own
//! aiosqlite handle (added in Phase 4+). Migrations are embedded at compile
//! time and applied idempotently on every launch.

use std::path::{Path, PathBuf};
use std::sync::{Mutex, MutexGuard};

use anyhow::{Context, Result};
use rusqlite::{params, Connection};

const MIGRATIONS: &[(i32, &str)] = &[
    (1, include_str!("../migrations/001_init.sql")),
    (2, include_str!("../migrations/002_settings.sql")),
    (3, include_str!("../migrations/003_slide_order.sql")),
    (
        4,
        include_str!("../migrations/004_setlist_item_override.sql"),
    ),
    (5, include_str!("../migrations/005_song_scriptures.sql")),
    (6, include_str!("../migrations/006_song_charts.sql")),
    (7, include_str!("../migrations/007_song_references.sql")),
    (8, include_str!("../migrations/008_services.sql")),
    (9, include_str!("../migrations/009_service_items.sql")),
    (10, include_str!("../migrations/010_search_credits.sql")),
    (11, include_str!("../migrations/011_setlist_item_look.sql")),
    (12, include_str!("../migrations/012_setlist_item_key.sql")),
];

/// Thread-safe handle to the SQLite database.
///
/// Each library is its own file (docs/roadmap/LIBRARIES_AND_SYNC.md). `conn`
/// is the library that's open; every song, setlist and service query goes
/// there. This computer's own library file (`home_path`) also holds its
/// settings, so settings always go to that file whichever library is open:
/// `home` keeps a connection to it while another library is open, and is
/// `None` while it's the open one (then `conn` is it).
pub struct Db {
    conn: Mutex<Connection>,
    home: Mutex<Option<Connection>>,
    home_path: PathBuf,
    open_path: Mutex<PathBuf>,
}

/// A lock on this computer's own library file, for settings.
pub enum HomeConn<'a> {
    /// Another library is open; this is the home file's own connection.
    Kept(MutexGuard<'a, Option<Connection>>),
    /// The home library is the open one. The `home` guard is held too, so a
    /// switch can't happen underneath.
    Open(
        #[allow(dead_code)] MutexGuard<'a, Option<Connection>>,
        MutexGuard<'a, Connection>,
    ),
}

impl std::ops::Deref for HomeConn<'_> {
    type Target = Connection;
    fn deref(&self) -> &Connection {
        match self {
            HomeConn::Kept(g) => g.as_ref().expect("kept home connection"),
            HomeConn::Open(_, g) => g,
        }
    }
}

impl Db {
    /// Expose the open library's connection for sibling modules (export, …).
    pub fn conn(&self) -> &Mutex<Connection> {
        &self.conn
    }

    /// This computer's own library file, where settings live.
    pub fn home(&self) -> HomeConn<'_> {
        let home = self.home.lock().unwrap();
        if home.is_some() {
            HomeConn::Kept(home)
        } else {
            let open = self.conn.lock().unwrap();
            HomeConn::Open(home, open)
        }
    }

    pub fn home_path(&self) -> &Path {
        &self.home_path
    }

    /// The file of the library that's open.
    pub fn open_path(&self) -> PathBuf {
        self.open_path.lock().unwrap().clone()
    }

    /// Open the library at `path` in place of the one that's open. The home
    /// library is `home_path()`; any other file is created and migrated if
    /// new, and starts empty (no example hymns: its songs come from its
    /// organization).
    pub fn switch_to(&self, path: &Path) -> Result<()> {
        let mut home = self.home.lock().unwrap();
        let mut conn = self.conn.lock().unwrap();
        let mut open = self.open_path.lock().unwrap();
        if *open == path {
            return Ok(());
        }
        if path == self.home_path {
            *conn = home.take().expect("home kept while another library is open");
        } else {
            let other = open_library_file(path)?;
            let previous = std::mem::replace(&mut *conn, other);
            if home.is_none() {
                *home = Some(previous);
            }
        }
        *open = path.to_path_buf();
        Ok(())
    }
}

pub fn open_library_file(path: &Path) -> Result<Connection> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .with_context(|| format!("creating db parent dir {}", parent.display()))?;
    }
    let mut conn = Connection::open(path)
        .with_context(|| format!("opening sqlite at {}", path.display()))?;
    // busy_timeout: an AI assistant's `--mcp` process may be writing too.
    conn.execute_batch(
        "PRAGMA journal_mode = WAL; PRAGMA foreign_keys = ON; PRAGMA busy_timeout = 5000;",
    )?;
    run_migrations(&mut conn)?;
    Ok(conn)
}

impl Db {
    /// Open (or create) this computer's own library at `path`, run pending
    /// migrations, and seed it with the example hymns if it's empty.
    pub fn open(path: &Path) -> Result<Self> {
        let conn = open_library_file(path)?;
        seed_if_empty(&conn)?;
        Ok(Self {
            conn: Mutex::new(conn),
            home: Mutex::new(None),
            home_path: path.to_path_buf(),
            open_path: Mutex::new(path.to_path_buf()),
        })
    }

    pub fn list_songs(&self) -> Result<Vec<SongRow>> {
        self.list_songs_sorted(SortKey::Alphabetical)
    }

    pub fn list_songs_sorted(&self, sort: SortKey) -> Result<Vec<SongRow>> {
        let conn = self.conn.lock().unwrap();
        let sql = match sort {
            SortKey::Alphabetical => {
                "SELECT s.id, s.title, s.body_text, s.ccli, s.author, s.copyright, \
                        s.default_tempo_wps, s.created_at, s.updated_at, s.slide_order \
                 FROM songs s ORDER BY s.title COLLATE NOCASE"
            }
            SortKey::RecentlyEdited => {
                "SELECT s.id, s.title, s.body_text, s.ccli, s.author, s.copyright, \
                        s.default_tempo_wps, s.created_at, s.updated_at, s.slide_order \
                 FROM songs s ORDER BY s.updated_at DESC, s.title COLLATE NOCASE"
            }
            SortKey::Popular => {
                // Setlists land in Phase 2; until then `usage_count` is 0 for
                // every row and we tie-break alphabetically.
                "SELECT s.id, s.title, s.body_text, s.ccli, s.author, s.copyright, \
                        s.default_tempo_wps, s.created_at, s.updated_at, s.slide_order \
                 FROM songs s \
                 LEFT JOIN ( \
                    SELECT song_id, COUNT(*) AS usage_count \
                    FROM setlist_items WHERE kind = 'song' GROUP BY song_id \
                 ) p ON p.song_id = s.id \
                 ORDER BY COALESCE(p.usage_count, 0) DESC, s.title COLLATE NOCASE"
            }
        };
        let mut stmt = conn.prepare(sql)?;
        let rows = stmt
            .query_map([], |row| Ok(map_song_row(row)?))?
            .collect::<Result<Vec<_>, _>>()?;
        Ok(rows)
    }

    pub fn get_song(&self, id: &str) -> Result<Option<SongRow>> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT id, title, body_text, ccli, author, copyright, default_tempo_wps, \
                    created_at, updated_at, slide_order \
             FROM songs WHERE id = ?1",
        )?;
        let mut rows = stmt.query(params![id])?;
        match rows.next()? {
            Some(row) => Ok(Some(map_song_row(row)?)),
            None => Ok(None),
        }
    }

    pub fn search_songs(&self, query: &str) -> Result<Vec<SongRow>> {
        let trimmed = query.trim();
        if trimmed.is_empty() {
            return self.list_songs();
        }
        let escaped = sanitize_fts5(trimmed);
        if escaped.is_empty() {
            // Query was all punctuation / special chars — there's nothing
            // meaningful to look up. Return empty so the UI shows "no
            // results" rather than every song.
            return Ok(Vec::new());
        }
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT s.id, s.title, s.body_text, s.ccli, s.author, s.copyright, \
                    s.default_tempo_wps, s.created_at, s.updated_at, s.slide_order \
             FROM songs s \
             JOIN songs_fts ON songs_fts.rowid = s.rowid \
             WHERE songs_fts MATCH ?1 \
             ORDER BY bm25(songs_fts, 10.0, 1.0, 5.0)",
        )?;
        let mut rows = stmt
            .query_map(params![escaped], |row| Ok(map_song_row(row)?))?
            .collect::<Result<Vec<_>, _>>()?;
        // A song whose title is what was typed comes first, before songs
        // that only mention the words in their lyrics: relevance alone put
        // "King Of My Heart" third for "king of my", behind songs whose
        // lyrics repeat those words. Stable, so relevance orders each tier.
        let terms = search_words(trimmed);
        rows.sort_by_key(|row| title_tier(&row.title, &terms));
        Ok(rows)
    }

    pub fn create_song(&self, input: NewSong) -> Result<String> {
        let id = format!("song_{}", ulid::Ulid::new().to_string().to_lowercase());
        let now = unix_ms();
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT INTO songs (id, title, body_text, ccli, author, copyright, \
                                default_tempo_wps, slide_order, created_at, updated_at) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?9)",
            params![
                id,
                input.title,
                input.body_text,
                input.ccli,
                input.author,
                input.copyright,
                input.default_tempo_wps,
                input.slide_order,
                now,
            ],
        )?;
        Ok(id)
    }

    pub fn update_song(&self, id: &str, input: NewSong) -> Result<()> {
        let now = unix_ms();
        let conn = self.conn.lock().unwrap();
        let updated = conn.execute(
            "UPDATE songs \
             SET title = ?2, body_text = ?3, ccli = ?4, author = ?5, \
                 copyright = ?6, default_tempo_wps = ?7, slide_order = ?8, \
                 updated_at = ?9 \
             WHERE id = ?1",
            params![
                id,
                input.title,
                input.body_text,
                input.ccli,
                input.author,
                input.copyright,
                input.default_tempo_wps,
                input.slide_order,
                now,
            ],
        )?;
        if updated == 0 {
            anyhow::bail!("no song with id {id}");
        }
        Ok(())
    }

    pub fn delete_song(&self, id: &str) -> Result<()> {
        // The schema declares
        //     setlist_items.song_id REFERENCES songs(id) ON DELETE RESTRICT
        // so a plain DELETE would fail with `FOREIGN KEY constraint failed`
        // for any song the operator has dropped into a setlist. That's not
        // useful behavior — operators delete songs they're done with and
        // expect them gone everywhere. Drop the setlist items pointing at
        // the song first, then the song itself, in a single transaction so
        // a failure halfway through doesn't leave dangling rows.
        let mut conn = self.conn.lock().unwrap();
        let tx = conn.transaction()?;
        tx.execute("DELETE FROM setlist_items WHERE song_id = ?1", params![id])?;
        tx.execute("DELETE FROM songs WHERE id = ?1", params![id])?;
        tx.commit()?;
        Ok(())
    }

    /// Run an arbitrary SELECT, returning rows as JSON objects. Test-only:
    /// the tests use it to look inside the schema (the FTS index); nothing in
    /// the app runs SQL it was handed.
    #[cfg(test)]
    pub fn query_json(&self, sql: &str) -> Result<Vec<serde_json::Value>> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(sql)?;
        let column_count = stmt.column_count();
        let columns: Vec<String> = (0..column_count)
            .map(|i| stmt.column_name(i).unwrap_or("?").to_string())
            .collect();

        let rows = stmt.query_map([], |row| {
            let mut obj = serde_json::Map::new();
            for (i, name) in columns.iter().enumerate() {
                let value: rusqlite::types::Value = row.get(i)?;
                obj.insert(name.clone(), sqlite_value_to_json(value));
            }
            Ok(serde_json::Value::Object(obj))
        })?;

        rows.collect::<Result<Vec<_>, _>>().map_err(Into::into)
    }
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct SongRow {
    pub id: String,
    pub title: String,
    pub body_text: String,
    pub ccli: Option<String>,
    pub author: Option<String>,
    pub copyright: Option<String>,
    pub default_tempo_wps: Option<f64>,
    /// Compact section-reference spec for the default play order
    /// (e.g. "V1, C, V2, C"). `#[serde(default)]` keeps older export
    /// blobs (without this field) importable.
    #[serde(default)]
    pub slide_order: Option<String>,
    pub created_at: i64,
    pub updated_at: i64,
}

#[derive(Debug, Clone, Copy, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SortKey {
    Alphabetical,
    RecentlyEdited,
    Popular,
}

impl Default for SortKey {
    fn default() -> Self {
        Self::Alphabetical
    }
}

#[derive(Debug, Clone, serde::Deserialize)]
pub struct NewSong {
    pub title: String,
    pub body_text: String,
    pub ccli: Option<String>,
    pub author: Option<String>,
    pub copyright: Option<String>,
    pub default_tempo_wps: Option<f64>,
    #[serde(default)]
    pub slide_order: Option<String>,
}

fn map_song_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<SongRow> {
    Ok(SongRow {
        id: row.get(0)?,
        title: row.get(1)?,
        body_text: row.get(2)?,
        ccli: row.get(3)?,
        author: row.get(4)?,
        copyright: row.get(5)?,
        default_tempo_wps: row.get(6)?,
        created_at: row.get(7)?,
        updated_at: row.get(8)?,
        slide_order: row.get(9)?,
    })
}

/// Turn a user-typed search box query into an FTS5 MATCH expression.
///
/// We do **prefix matching per term** so the library list responds
/// as the operator types. Typing "agn" should reveal "Agnus Dei"
/// (matches `agn*` against the stemmed token `agnu`), not just wait
/// until the operator has typed enough characters to match a full
/// stem. The previous behavior wrapped the input in double quotes,
/// producing an FTS5 phrase query that only matches full indexed
/// tokens — which under the porter tokenizer was needlessly strict.
///
/// Each term is:
///   1. Split on punctuation the way the unicode61 tokenizer splits the
///      lyrics, so `10,000` is the two tokens `10` and `000` it was
///      indexed as (stripped to `10000` it matched nothing). Apostrophes
///      are dropped rather than split on: `father's` -> `fathers`, which
///      the porter stemmer still matches, where `s*` would match any word
///      starting with s. This also drops FTS5 syntax like `"`, `(`, `:`,
///      `-` and `*`.
///   2. Suffixed with `*` for prefix matching (FTS5 syntax requires
///      the asterisk to touch the token — `agn*`, not `agn *`).
///   3. Joined with the next term using a space — FTS5's implicit
///      AND across terms.
///
/// Empty terms (or terms that collapsed to nothing after stripping)
/// are skipped. If the whole query collapses to nothing, returns an
/// empty string — `search_songs` short-circuits on that case so we
/// don't hand an empty MATCH expression to SQLite (which errors).
/// Lowercase alphanumeric words, as FTS5's unicode61 tokenizer sees them.
fn search_words(text: &str) -> Vec<String> {
    text.split(|c: char| !c.is_alphanumeric())
        .filter(|w| !w.is_empty())
        .map(|w| w.to_lowercase())
        .collect()
}

/// How well a title matches the query: 0 exactly, 1 it starts with it,
/// 2 every query word starts a title word, 3 not by its title (the lyrics).
fn title_tier(title: &str, terms: &[String]) -> u8 {
    let title_words = search_words(title);
    let joined = title_words.join(" ");
    let wanted = terms.join(" ");
    if !wanted.is_empty() && joined == wanted {
        0
    } else if !wanted.is_empty() && joined.starts_with(&wanted) {
        1
    } else if !terms.is_empty()
        && terms
            .iter()
            .all(|t| title_words.iter().any(|w| w.starts_with(t.as_str())))
    {
        2
    } else {
        3
    }
}

fn sanitize_fts5(query: &str) -> String {
    let mut out = String::new();
    let terms = query
        .split(|c: char| !c.is_alphanumeric() && !matches!(c, '\'' | '’'))
        .map(|raw| raw.chars().filter(|c| c.is_alphanumeric()).collect::<String>());
    for cleaned in terms {
        if cleaned.is_empty() {
            continue;
        }
        if !out.is_empty() {
            out.push(' ');
        }
        out.push_str(&cleaned);
        out.push('*');
    }
    out
}

fn run_migrations(conn: &mut Connection) -> Result<()> {
    conn.execute_batch("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);")?;

    let current: i32 = conn
        .query_row(
            "SELECT COALESCE(MAX(version), 0) FROM schema_version",
            [],
            |row| row.get(0),
        )
        .unwrap_or(0);

    for (version, sql) in MIGRATIONS {
        if *version <= current {
            continue;
        }
        let tx = conn.transaction()?;
        tx.execute_batch(sql)
            .with_context(|| format!("applying migration {version}"))?;
        tx.execute(
            "INSERT INTO schema_version (version) VALUES (?1)",
            params![version],
        )?;
        tx.commit()?;
        log::info!("applied migration {version}");
    }
    Ok(())
}

/// A brand-new library starts with four complete example hymns, so there is
/// something to build a first setlist from. See `seed_hymns` for what they
/// are and why they're safe to ship.
fn seed_if_empty(conn: &Connection) -> Result<()> {
    let count: i64 = conn.query_row("SELECT COUNT(*) FROM songs", [], |row| row.get(0))?;
    if count > 0 {
        return Ok(());
    }
    let now = unix_ms();
    for hymn in crate::seed_hymns::SEED_HYMNS {
        conn.execute(
            "INSERT INTO songs (id, title, body_text, author, copyright, slide_order, \
             created_at, updated_at) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?7)",
            params![
                hymn.id,
                hymn.title,
                hymn.body,
                hymn.author,
                hymn.copyright,
                (!hymn.slide_order.is_empty()).then_some(hymn.slide_order),
                now,
            ],
        )?;
    }
    log::info!("seeded {} example hymns", crate::seed_hymns::SEED_HYMNS.len());
    Ok(())
}

fn unix_ms() -> i64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
}

#[cfg(test)]
fn sqlite_value_to_json(v: rusqlite::types::Value) -> serde_json::Value {
    use rusqlite::types::Value;
    match v {
        Value::Null => serde_json::Value::Null,
        Value::Integer(i) => serde_json::Value::from(i),
        Value::Real(f) => serde_json::Value::from(f),
        Value::Text(s) => serde_json::Value::String(s),
        Value::Blob(_) => serde_json::Value::Null, // not exposed
    }
}

/// Resolve the database path for the running app.
pub fn default_db_path<P: AsRef<Path>>(app_data_dir: P) -> PathBuf {
    app_data_dir.as_ref().join("library.db")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_db() -> (tempfile::TempDir, Db) {
        let dir = tempfile::tempdir().expect("tempdir");
        let path = dir.path().join("test.db");
        let db = Db::open(&path).expect("open");
        (dir, db)
    }

    #[test]
    fn migration_is_idempotent() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("test.db");
        let _ = Db::open(&path).unwrap();
        let _ = Db::open(&path).unwrap(); // open again — must not crash
    }

    #[test]
    fn seed_inserts_the_example_hymns() {
        let (_dir, db) = temp_db();
        let songs = db.list_songs().unwrap();
        assert_eq!(songs.len(), 4);
        let grace = db.get_song("song_amazing_grace").unwrap().unwrap();
        assert_eq!(grace.title, "Amazing Grace");
        assert!(grace.copyright.unwrap().ends_with("Public domain."));
        assert_eq!(grace.body_text.matches("[verse").count(), 7);
        let well = db.get_song("song_it_is_well").unwrap().unwrap();
        assert_eq!(well.slide_order.as_deref(), Some("V1, C, V2, C, V3, C, V4, C, V5, C"));
    }

    #[test]
    fn fts5_finds_seed_song_by_lyric() {
        let (_dir, db) = temp_db();
        let rows = db
            .query_json(
                "SELECT s.id FROM songs s \
                 JOIN songs_fts ON songs_fts.rowid = s.rowid \
                 WHERE songs_fts MATCH 'wretch'",
            )
            .unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].get("id").unwrap(), "song_amazing_grace");
    }

    #[test]
    fn fts5_trigger_on_update() {
        let (_dir, db) = temp_db();
        {
            let conn = db.conn.lock().unwrap();
            conn.execute(
                "UPDATE songs SET body_text = 'something completely different' \
                 WHERE id = 'song_amazing_grace'",
                [],
            )
            .unwrap();
        }
        let rows_old = db
            .query_json("SELECT 1 FROM songs_fts WHERE songs_fts MATCH 'wretch'")
            .unwrap();
        assert!(rows_old.is_empty(), "old FTS row should be gone");
        let rows_new = db
            .query_json("SELECT 1 FROM songs_fts WHERE songs_fts MATCH 'completely'")
            .unwrap();
        assert_eq!(rows_new.len(), 1);
    }

    fn make_song(title: &str, body: &str) -> NewSong {
        NewSong {
            title: title.into(),
            body_text: body.into(),
            ccli: None,
            author: None,
            copyright: None,
            default_tempo_wps: None,
            slide_order: None,
        }
    }

    #[test]
    fn create_get_update_delete_roundtrip() {
        let (_dir, db) = temp_db();
        let id = db
            .create_song(make_song("Test Song", "[v]\nHello world"))
            .unwrap();
        assert!(id.starts_with("song_"));

        let got = db.get_song(&id).unwrap().expect("song exists");
        assert_eq!(got.title, "Test Song");
        assert_eq!(got.body_text, "[v]\nHello world");

        db.update_song(&id, make_song("Renamed", "[v]\nNew lyrics"))
            .unwrap();
        let updated = db.get_song(&id).unwrap().expect("still exists");
        assert_eq!(updated.title, "Renamed");
        assert_eq!(updated.body_text, "[v]\nNew lyrics");
        assert!(updated.updated_at >= updated.created_at);

        db.delete_song(&id).unwrap();
        assert!(db.get_song(&id).unwrap().is_none());
    }

    #[test]
    fn delete_song_cascades_setlist_items() {
        let (_dir, db) = temp_db();
        let song_id = db
            .create_song(make_song("To Delete", "[v]\nLyrics"))
            .unwrap();
        // Attach the song to a setlist — without the cascade this is what
        // would block deletion via the ON DELETE RESTRICT constraint.
        let conn = db.conn().lock().unwrap();
        conn.execute(
            "INSERT INTO setlists (id, name, created_at) VALUES ('sl_test', 'Sunday', 0)",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO setlist_items (id, setlist_id, kind, song_id, position) \
             VALUES ('it1', 'sl_test', 'song', ?1, 0)",
            params![song_id],
        )
        .unwrap();
        drop(conn);

        db.delete_song(&song_id).expect("delete should cascade");
        assert!(db.get_song(&song_id).unwrap().is_none());

        // Setlist still exists, but the orphan item is gone.
        let conn = db.conn().lock().unwrap();
        let n: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM setlist_items WHERE song_id = ?1",
                params![song_id],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(n, 0);
        let setlists: i64 = conn
            .query_row("SELECT COUNT(*) FROM setlists", [], |row| row.get(0))
            .unwrap();
        assert_eq!(setlists, 1);
    }

    #[test]
    fn search_finds_by_title_and_content() {
        let (_dir, db) = temp_db();
        // Seed song already has "Amazing grace" + "wretch"
        let by_title = db.search_songs("amazing").unwrap();
        assert_eq!(by_title.len(), 1);
        let by_content = db.search_songs("wretch").unwrap();
        assert_eq!(by_content.len(), 1);
        let nothing = db.search_songs("xyzzy").unwrap();
        assert!(nothing.is_empty());
    }

    #[test]
    fn search_reads_credits_author_publisher_and_ccli() {
        let (_dir, db) = temp_db();
        let id = db
            .create_song(NewSong {
                title: "Good Good Father".into(),
                body_text: "Oh I've heard a thousand stories".into(),
                ccli: Some("7036612".into()),
                author: Some("Pat Barrett, Anthony Brown".into()),
                copyright: Some("2014 Capitol CMG Paragon".into()),
                default_tempo_wps: None,
                slide_order: None,
            })
            .unwrap();
        let ids = |q: &str| db.search_songs(q).unwrap().into_iter().map(|s| s.id).collect::<Vec<_>>();
        assert_eq!(ids("barrett"), vec![id.clone()]);
        assert_eq!(ids("capitol"), vec![id.clone()]);
        assert_eq!(ids("7036612"), vec![id.clone()]);
        // An edit to the credits reaches the index.
        let mut song = db.get_song(&id).unwrap().unwrap();
        song.author = Some("Someone Else".into());
        db.update_song(&id, NewSong {
            title: song.title,
            body_text: song.body_text,
            ccli: song.ccli,
            author: song.author,
            copyright: song.copyright,
            default_tempo_wps: None,
            slide_order: None,
        })
        .unwrap();
        assert!(ids("barrett").is_empty());
        assert_eq!(ids("someone"), vec![id]);
    }

    #[test]
    fn search_splits_numbers_and_hyphens_like_the_index() {
        let (_dir, db) = temp_db();
        db.create_song(make_song("10,000 Reasons (Bless The Lord)", "Bless the Lord O my soul"))
            .unwrap();
        db.create_song(make_song("Ever-lasting", "Father's love never fails"))
            .unwrap();
        assert_eq!(db.search_songs("10,000 reasons").unwrap().len(), 1);
        assert_eq!(db.search_songs("ever-lasting").unwrap().len(), 1);
        assert_eq!(db.search_songs("father's love").unwrap().len(), 1);
        assert_eq!(sanitize_fts5("10,000 reasons"), "10* 000* reasons*");
        assert_eq!(sanitize_fts5("father’s"), "fathers*");
    }

    #[test]
    fn search_puts_a_title_match_before_lyric_matches() {
        let (_dir, db) = temp_db();
        db.create_song(make_song(
            "Heaven",
            "king of my heart king of my heart my king my king of my soul",
        ))
        .unwrap();
        db.create_song(make_song("King Of My Heart", "A quiet song at morning"))
            .unwrap();
        db.create_song(make_song("Kingdom Come", "of my my my"))
            .unwrap();
        let titles = |q: &str| -> Vec<String> {
            db.search_songs(q)
                .unwrap()
                .into_iter()
                .map(|s| s.title)
                .collect()
        };
        assert_eq!(titles("king of my")[0], "King Of My Heart");
        assert_eq!(titles("King Of My Heart")[0], "King Of My Heart");
        // a title prefix of a word counts too ("kingd" -> "Kingdom Come")
        assert_eq!(titles("kingd")[0], "Kingdom Come");
        // lyric-only matches still come back, after the title matches
        assert!(titles("king of my").contains(&"Heaven".to_string()));
    }

    #[test]
    fn search_empty_query_returns_all() {
        let (_dir, db) = temp_db();
        let rows = db.search_songs("   ").unwrap();
        assert_eq!(rows.len(), crate::seed_hymns::SEED_HYMNS.len());
    }

    #[test]
    fn search_handles_quotes_and_punctuation() {
        let (_dir, db) = temp_db();
        // Should not crash on shell-style quoting input
        let _ = db.search_songs("\"amazing\"").unwrap();
        let _ = db.search_songs("don't").unwrap();
        let _ = db.search_songs("god,").unwrap();
    }

    #[test]
    fn search_does_prefix_matching_for_typed_input() {
        // The reported bug: typing "amaz" should return "Amazing Grace"
        // (the seed song), not wait until the operator types "amazi"
        // (or whatever stem the porter tokenizer settles on).
        let (_dir, db) = temp_db();
        let from_short = db.search_songs("amaz").unwrap();
        assert_eq!(from_short.len(), 1, "prefix `amaz` should match `amazing`");
        // And the partial-prefix match cascades — single-letter still
        // matches anything that starts with that letter (FTS5 prefix
        // tokens accept arbitrary lengths).
        let from_one = db.search_songs("a").unwrap();
        assert!(!from_one.is_empty());
    }

    #[test]
    fn search_returns_empty_for_punctuation_only_query() {
        let (_dir, db) = temp_db();
        // No alphanumerics → nothing meaningful to look up. The seed
        // song must not appear; the previous behaviour (phrase-search
        // over `","`) silently returned everything.
        let result = db.search_songs(",,,").unwrap();
        assert!(result.is_empty());
    }

    #[test]
    fn search_terms_combine_with_implicit_and() {
        let (_dir, db) = temp_db();
        // Both terms appear in the seed song's body → matches.
        let both = db.search_songs("amazing wretch").unwrap();
        assert_eq!(both.len(), 1);
        // Second term doesn't appear → no match (AND semantics).
        let missing = db.search_songs("amazing xyzzy").unwrap();
        assert!(missing.is_empty());
    }

    #[test]
    fn update_unknown_song_errors() {
        let (_dir, db) = temp_db();
        let err = db.update_song("missing_id", make_song("X", "Y"));
        assert!(err.is_err());
    }

    #[test]
    fn sort_alphabetical_default() {
        let (_dir, db) = temp_db();
        db.create_song(make_song("Bee", "B")).unwrap();
        db.create_song(make_song("Aardvark", "A")).unwrap();
        let titles: Vec<String> = db
            .list_songs_sorted(SortKey::Alphabetical)
            .unwrap()
            .into_iter()
            .map(|s| s.title)
            .collect();
        assert_eq!(
            titles,
            vec![
                "Aardvark",
                "Abide With Me",
                "Amazing Grace",
                "Bee",
                "Holy, Holy, Holy",
                "It Is Well With My Soul"
            ]
        );
    }

    #[test]
    fn sort_recent_uses_updated_at() {
        let (_dir, db) = temp_db();
        let id_a = db.create_song(make_song("Alpha", "a")).unwrap();
        std::thread::sleep(std::time::Duration::from_millis(10));
        let id_b = db.create_song(make_song("Beta", "b")).unwrap();
        std::thread::sleep(std::time::Duration::from_millis(10));
        db.update_song(&id_a, make_song("Alpha", "a-edited"))
            .unwrap();

        let order: Vec<String> = db
            .list_songs_sorted(SortKey::RecentlyEdited)
            .unwrap()
            .into_iter()
            .map(|s| s.id)
            .collect();
        assert_eq!(order[0], id_a);
        assert_eq!(order[1], id_b);
    }

    #[test]
    fn sort_popular_falls_back_alphabetical_with_no_setlists() {
        let (_dir, db) = temp_db();
        db.create_song(make_song("Bravo", "b")).unwrap();
        db.create_song(make_song("Alpha", "a")).unwrap();

        let pop: Vec<String> = db
            .list_songs_sorted(SortKey::Popular)
            .unwrap()
            .into_iter()
            .map(|s| s.title)
            .collect();
        let alpha: Vec<String> = db
            .list_songs_sorted(SortKey::Alphabetical)
            .unwrap()
            .into_iter()
            .map(|s| s.title)
            .collect();
        assert_eq!(pop, alpha);
    }

    #[test]
    fn sort_popular_ranks_by_setlist_usage() {
        let (_dir, db) = temp_db();
        let id_a = db.create_song(make_song("Alpha", "a")).unwrap();
        let id_b = db.create_song(make_song("Bravo", "b")).unwrap();
        let _id_c = db.create_song(make_song("Charlie", "c")).unwrap();

        let conn = db.conn.lock().unwrap();
        conn.execute(
            "INSERT INTO setlists (id, name, created_at) VALUES ('sl1', 'Test', 0)",
            [],
        )
        .unwrap();
        for (i, sid) in [&id_a, &id_a, &id_a, &id_b].iter().enumerate() {
            conn.execute(
                "INSERT INTO setlist_items (id, setlist_id, song_id, position) \
                 VALUES (?1, 'sl1', ?2, ?3)",
                params![format!("item{i}"), sid, i as i64],
            )
            .unwrap();
        }
        drop(conn);

        let titles: Vec<String> = db
            .list_songs_sorted(SortKey::Popular)
            .unwrap()
            .into_iter()
            .map(|s| s.title)
            .collect();
        assert_eq!(titles[0], "Alpha");
        assert_eq!(titles[1], "Bravo");
        // Unused songs (the example hymns, Charlie) tie at zero: alphabetical.
        assert_eq!(titles[2], "Abide With Me");
        assert_eq!(titles[3], "Amazing Grace");
        assert_eq!(titles[4], "Charlie");
    }
}
