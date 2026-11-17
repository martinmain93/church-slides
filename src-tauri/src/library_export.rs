//! JSON library export/import. The exchange format is documented in
//! `docs/EXPORT_FORMAT.md` and is forwards-compatible: importers ignore
//! unknown fields and reject blobs whose `schema_version` is greater
//! than `CURRENT_SCHEMA_VERSION`.
//!
//! Imports are atomic at the song level: each row goes in via
//! `INSERT OR REPLACE`, so re-importing an exported file is idempotent.

use std::sync::Arc;

use anyhow::{anyhow, Context, Result};
use rusqlite::params;
use serde::{Deserialize, Serialize};

use crate::db::{Db, SongRow};

pub const CURRENT_SCHEMA_VERSION: u32 = 1;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LibraryExport {
    pub schema_version: u32,
    pub exported_at: i64, // unix ms
    pub songs: Vec<SongRow>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ImportMode {
    /// Add or update by ID; existing songs not in the import remain.
    Merge,
    /// Wipe the songs table first, then insert the import. Setlists
    /// referencing missing songs would fail (RESTRICT) — Phase 2 work.
    Replace,
}

#[derive(Debug, Clone, Serialize)]
pub struct ImportSummary {
    pub schema_version: u32,
    pub songs_imported: usize,
    pub songs_replaced: usize,
}

pub fn export_library(db: &Arc<Db>) -> Result<LibraryExport> {
    let songs = db.list_songs()?;
    Ok(LibraryExport {
        schema_version: CURRENT_SCHEMA_VERSION,
        exported_at: unix_ms(),
        songs,
    })
}

pub fn export_library_json(db: &Arc<Db>) -> Result<String> {
    let blob = export_library(db)?;
    serde_json::to_string_pretty(&blob).context("serializing library export")
}

pub fn import_library_json(db: &Arc<Db>, json: &str, mode: ImportMode) -> Result<ImportSummary> {
    let parsed: LibraryExport = serde_json::from_str(json).context("parsing library import")?;
    if parsed.schema_version > CURRENT_SCHEMA_VERSION {
        return Err(anyhow!(
            "library was exported with schema_version={} but this build supports up to {}",
            parsed.schema_version,
            CURRENT_SCHEMA_VERSION
        ));
    }

    let mut conn = db.conn().lock().unwrap();
    let tx = conn.transaction()?;

    if matches!(mode, ImportMode::Replace) {
        tx.execute("DELETE FROM songs", [])?;
    }

    let mut imported = 0usize;
    let mut replaced = 0usize;

    for song in &parsed.songs {
        let existed: i64 = tx
            .query_row(
                "SELECT COUNT(*) FROM songs WHERE id = ?1",
                params![song.id],
                |row| row.get(0),
            )
            .unwrap_or(0);

        tx.execute(
            "INSERT INTO songs (id, title, body_text, ccli, author, copyright, \
                                default_tempo_wps, slide_order, created_at, updated_at) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10) \
             ON CONFLICT(id) DO UPDATE SET \
                title = excluded.title, \
                body_text = excluded.body_text, \
                ccli = excluded.ccli, \
                author = excluded.author, \
                copyright = excluded.copyright, \
                default_tempo_wps = excluded.default_tempo_wps, \
                slide_order = excluded.slide_order, \
                updated_at = excluded.updated_at",
            params![
                song.id,
                song.title,
                song.body_text,
                song.ccli,
                song.author,
                song.copyright,
                song.default_tempo_wps,
                song.slide_order,
                song.created_at,
                song.updated_at,
            ],
        )?;

        if existed > 0 {
            replaced += 1;
        } else {
            imported += 1;
        }
    }

    tx.commit()?;

    Ok(ImportSummary {
        schema_version: parsed.schema_version,
        songs_imported: imported,
        songs_replaced: replaced,
    })
}

fn unix_ms() -> i64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use super::*;
    use crate::db::{Db, NewSong};

    fn fresh() -> (tempfile::TempDir, Arc<Db>) {
        let dir = tempfile::tempdir().unwrap();
        let db = Arc::new(Db::open(&dir.path().join("t.db")).unwrap());
        (dir, db)
    }

    #[test]
    fn export_includes_seed_song() {
        let (_d, db) = fresh();
        let blob = export_library(&db).unwrap();
        assert_eq!(blob.schema_version, CURRENT_SCHEMA_VERSION);
        assert_eq!(blob.songs.len(), crate::seed_hymns::SEED_HYMNS.len());
        assert!(blob.songs.iter().any(|s| s.title == "Amazing Grace"));
    }

    #[test]
    fn export_then_import_replace_preserves_everything() {
        let (_d, db) = fresh();
        db.create_song(NewSong {
            title: "Custom".into(),
            body_text: "[v]\nLine".into(),
            ccli: Some("12345".into()),
            author: Some("Me".into()),
            copyright: None,
            default_tempo_wps: Some(1.8),
            slide_order: Some("V1, C, V1".into()),
        })
        .unwrap();

        let json = export_library_json(&db).unwrap();
        let summary = import_library_json(&db, &json, ImportMode::Replace).unwrap();
        assert_eq!(summary.schema_version, CURRENT_SCHEMA_VERSION);
        assert_eq!(summary.songs_imported + summary.songs_replaced, crate::seed_hymns::SEED_HYMNS.len() + 1);

        let after = db.list_songs().unwrap();
        assert_eq!(after.len(), crate::seed_hymns::SEED_HYMNS.len() + 1);
        let custom = after.iter().find(|s| s.title == "Custom").unwrap();
        assert_eq!(custom.ccli.as_deref(), Some("12345"));
        assert_eq!(custom.author.as_deref(), Some("Me"));
        assert_eq!(custom.default_tempo_wps, Some(1.8));
        assert_eq!(custom.slide_order.as_deref(), Some("V1, C, V1"));
    }

    #[test]
    fn import_merge_updates_existing_rows() {
        let (_d, db) = fresh();
        let blob = export_library_json(&db).unwrap();
        let conn = db.conn().lock().unwrap();
        conn.execute(
            "UPDATE songs SET body_text = 'replaced' WHERE id = 'song_amazing_grace'",
            [],
        )
        .unwrap();
        drop(conn);

        let summary = import_library_json(&db, &blob, ImportMode::Merge).unwrap();
        assert_eq!(summary.songs_replaced, crate::seed_hymns::SEED_HYMNS.len());
        assert_eq!(summary.songs_imported, 0);

        let restored = db.get_song("song_amazing_grace").unwrap().unwrap();
        assert!(restored.body_text.contains("Amazing grace"));
    }

    #[test]
    fn import_rejects_future_schema() {
        let (_d, db) = fresh();
        let blob = r#"{"schema_version":999,"exported_at":0,"songs":[]}"#;
        let err = import_library_json(&db, blob, ImportMode::Merge).unwrap_err();
        assert!(err.to_string().contains("schema_version"));
    }

    #[test]
    fn import_rejects_garbage_json() {
        let (_d, db) = fresh();
        let err = import_library_json(&db, "not json", ImportMode::Merge).unwrap_err();
        assert!(err.to_string().contains("parsing"));
    }

    #[test]
    fn fts5_repopulates_after_import() {
        let (_d, db) = fresh();
        let json = export_library_json(&db).unwrap();
        import_library_json(&db, &json, ImportMode::Replace).unwrap();
        let hits = db.search_songs("wretch").unwrap();
        assert_eq!(hits.len(), 1);
    }
}
