//! Score-follower references (table `song_references`, migration 007).
//!
//! A reference is what the follower compares live audio against: today a
//! lead sheet read by the sidecar, later a recorded service with its
//! operator's slide changes. Like charts, the payload is opaque JSON here —
//! its shape belongs to the sidecar, which reads it back. This layer stores
//! it, keeps one lead sheet per song (a new import replaces the old one), and
//! lists the small summaries the library shows without loading the payloads.

use anyhow::Result;
use rusqlite::params;

use crate::db::Db;

/// Kinds of which a song keeps only one: saving another replaces it.
const ONE_PER_SONG: &[&str] = &["lead_sheet"];

#[derive(Debug, Clone, serde::Deserialize)]
pub struct NewReference {
    pub song_id: String,
    pub kind: String,
    pub source_name: Option<String>,
    pub data_json: String,
    /// Small display facts (bars, key, sections…); `{}` when there are none.
    #[serde(default)]
    pub summary_json: Option<String>,
}

/// Everything about a reference except its payload.
#[derive(Debug, Clone, serde::Serialize)]
pub struct ReferenceSummary {
    pub id: String,
    pub song_id: String,
    pub kind: String,
    pub source_name: Option<String>,
    pub summary_json: String,
    pub created_at: i64,
    pub updated_at: i64,
}

fn unix_ms() -> i64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
}

pub fn save(db: &Db, r: NewReference) -> Result<ReferenceSummary> {
    if r.kind.trim().is_empty() {
        anyhow::bail!("a reference needs a kind");
    }
    let summary_json = r.summary_json.unwrap_or_else(|| "{}".into());
    serde_json::from_str::<serde_json::Value>(&r.data_json)
        .map_err(|e| anyhow::anyhow!("reference data is not JSON: {e}"))?;
    serde_json::from_str::<serde_json::Value>(&summary_json)
        .map_err(|e| anyhow::anyhow!("reference summary is not JSON: {e}"))?;

    let mut conn = db.conn().lock().unwrap();
    let tx = conn.transaction()?;
    let exists: i64 =
        tx.query_row("SELECT COUNT(*) FROM songs WHERE id = ?1", params![r.song_id], |row| row.get(0))?;
    if exists == 0 {
        anyhow::bail!("no song with id {}", r.song_id);
    }
    let now = unix_ms();
    // A replaced lead sheet keeps its creation time: it is the same song's
    // sheet, read again or swapped for a better one.
    let mut created_at = now;
    if ONE_PER_SONG.contains(&r.kind.as_str()) {
        let previous: Option<i64> = tx
            .query_row(
                "SELECT MIN(created_at) FROM song_references WHERE song_id = ?1 AND kind = ?2",
                params![r.song_id, r.kind],
                |row| row.get(0),
            )
            .unwrap_or(None);
        if let Some(p) = previous {
            created_at = p;
        }
        tx.execute(
            "DELETE FROM song_references WHERE song_id = ?1 AND kind = ?2",
            params![r.song_id, r.kind],
        )?;
    }
    let id = format!("ref_{}", ulid::Ulid::new().to_string().to_lowercase());
    tx.execute(
        "INSERT INTO song_references (id, song_id, kind, source_name, data_json, summary_json, created_at, updated_at) \
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
        params![id, r.song_id, r.kind, r.source_name, r.data_json, summary_json, created_at, now],
    )?;
    tx.commit()?;
    Ok(ReferenceSummary {
        id,
        song_id: r.song_id,
        kind: r.kind,
        source_name: r.source_name,
        summary_json,
        created_at,
        updated_at: now,
    })
}

/// Summaries for one song, or for the whole library when `song_id` is None.
pub fn list(db: &Db, song_id: Option<&str>) -> Result<Vec<ReferenceSummary>> {
    let conn = db.conn().lock().unwrap();
    let mut stmt = conn.prepare(
        "SELECT id, song_id, kind, source_name, summary_json, created_at, updated_at \
         FROM song_references WHERE ?1 IS NULL OR song_id = ?1 ORDER BY song_id, created_at",
    )?;
    let rows = stmt
        .query_map(params![song_id], |row| {
            Ok(ReferenceSummary {
                id: row.get(0)?,
                song_id: row.get(1)?,
                kind: row.get(2)?,
                source_name: row.get(3)?,
                summary_json: row.get(4)?,
                created_at: row.get(5)?,
                updated_at: row.get(6)?,
            })
        })?
        .collect::<Result<Vec<_>, _>>()?;
    Ok(rows)
}

/// The payload of one reference, for whoever follows the song.
#[allow(dead_code)] // read by the follower once it loads references; tested below
pub fn data(db: &Db, id: &str) -> Result<Option<String>> {
    let conn = db.conn().lock().unwrap();
    let mut stmt = conn.prepare("SELECT data_json FROM song_references WHERE id = ?1")?;
    let mut rows = stmt.query(params![id])?;
    Ok(match rows.next()? {
        Some(row) => Some(row.get(0)?),
        None => None,
    })
}

pub fn delete(db: &Db, id: &str) -> Result<()> {
    let conn = db.conn().lock().unwrap();
    conn.execute("DELETE FROM song_references WHERE id = ?1", params![id])?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fresh() -> (tempfile::TempDir, Db) {
        let dir = tempfile::tempdir().unwrap();
        let db = Db::open(&dir.path().join("t.db")).unwrap();
        (dir, db)
    }

    fn sheet(song_id: &str, data: &str) -> NewReference {
        NewReference {
            song_id: song_id.into(),
            kind: "lead_sheet".into(),
            source_name: Some("Song-lead-G.pdf".into()),
            data_json: data.into(),
            summary_json: Some("{\"bars\":34}".into()),
        }
    }

    #[test]
    fn a_new_lead_sheet_replaces_the_old_one() {
        let (_d, db) = fresh();
        let song = db.list_songs().unwrap()[0].id.clone();
        let first = save(&db, sheet(&song, "{\"v\":1}")).unwrap();
        let second = save(&db, sheet(&song, "{\"v\":2}")).unwrap();
        let all = list(&db, Some(&song)).unwrap();
        assert_eq!(all.len(), 1);
        assert_eq!(all[0].id, second.id);
        assert_eq!(second.created_at, first.created_at);
        assert_eq!(data(&db, &second.id).unwrap().as_deref(), Some("{\"v\":2}"));
        assert!(data(&db, &first.id).unwrap().is_none());
    }

    #[test]
    fn recordings_accumulate() {
        let (_d, db) = fresh();
        let song = db.list_songs().unwrap()[0].id.clone();
        for i in 0..2 {
            save(
                &db,
                NewReference {
                    song_id: song.clone(),
                    kind: "recording".into(),
                    source_name: Some(format!("rec_{i}")),
                    data_json: "{}".into(),
                    summary_json: None,
                },
            )
            .unwrap();
        }
        save(&db, sheet(&song, "{}")).unwrap();
        assert_eq!(list(&db, Some(&song)).unwrap().len(), 3);
        assert_eq!(list(&db, None).unwrap().len(), 3);
    }

    #[test]
    fn deleting_a_song_deletes_its_references() {
        let (_d, db) = fresh();
        let song = db.list_songs().unwrap()[0].id.clone();
        save(&db, sheet(&song, "{}")).unwrap();
        db.delete_song(&song).unwrap();
        assert!(list(&db, None).unwrap().is_empty());
    }

    #[test]
    fn rejects_unknown_songs_and_bad_json() {
        let (_d, db) = fresh();
        assert!(save(&db, sheet("song_nope", "{}")).is_err());
        let song = db.list_songs().unwrap()[0].id.clone();
        assert!(save(&db, sheet(&song, "not json")).is_err());
        assert!(list(&db, None).unwrap().is_empty());
    }

    #[test]
    fn delete_removes_one_reference() {
        let (_d, db) = fresh();
        let song = db.list_songs().unwrap()[0].id.clone();
        let r = save(&db, sheet(&song, "{}")).unwrap();
        delete(&db, &r.id).unwrap();
        assert!(list(&db, Some(&song)).unwrap().is_empty());
    }
}
