//! Setlist storage. Every setlist write goes through these helpers; the
//! Python sidecar reads via its own aiosqlite handle in later phases.
//!
//! Items are ordered by an integer `position` column. Reorder is atomic
//! (single transaction), so concurrent inserts can't leave a setlist
//! with duplicated or skipped positions in a way that breaks ordering.
//! New items added concurrently with a reorder simply land at the
//! tail (max position + 1), which is the desired behavior.

use std::sync::Arc;

use anyhow::{anyhow, Context, Result};
use rusqlite::params;
use serde::{Deserialize, Serialize};

use crate::db::{Db, SongRow};

// --- Types ------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SetlistRow {
    pub id: String,
    pub name: String,
    /// The day the service is for, `YYYY-MM-DD` (migration 014), or None.
    #[serde(default)]
    pub service_date: Option<String>,
    pub created_at: i64,
    pub last_used_at: Option<i64>,
    /// Item names in setlist order — song titles, and a passage's reference
    /// or a text item's title — filled by `list_setlists` only, so the home
    /// screen can tell setlists apart at a glance. Empty elsewhere.
    #[serde(default)]
    pub song_titles: Vec<String>,
    /// How many of `song_titles` are songs.
    #[serde(default)]
    pub song_count: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SetlistItemRow {
    pub id: String,
    pub setlist_id: String,
    pub kind: String,
    pub song_id: Option<String>,
    pub position: i64,
    /// JSON array of edited slides scoped to this setlist, or None to fall
    /// back to the library song's body + slide_order. See migration 004.
    #[serde(default)]
    pub slides_override: Option<String>,
    /// JSON describing a scripture or text item (migration 009); None for a
    /// song. The frontend owns its shape — see `src/lib/serviceItems.ts`.
    #[serde(default)]
    pub content: Option<String>,
    /// This item's own display look as JSON (migration 011), or None for the
    /// default style. The frontend owns its shape — `src/lib/displayLook.ts`.
    #[serde(default)]
    pub look: Option<String>,
    /// The key the song is played in for this service (migration 012), or
    /// None for the chart's own key.
    #[serde(default)]
    pub song_key: Option<String>,
}

/// The item kinds besides `song`. Anything else is refused, so a typo can't
/// create an item the frontend has no way to show.
pub const SERVICE_ITEM_KINDS: &[&str] = &["scripture", "text"];

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReadingRow {
    pub id: String,
    pub title: String,
    pub body_text: String,
    pub created_at: i64,
    pub updated_at: i64,
}

#[derive(Debug, Clone, Serialize)]
pub struct SetlistItemWithSong {
    #[serde(flatten)]
    pub item: SetlistItemRow,
    /// Resolved song. None if `song_id` references a deleted song
    /// (shouldn't happen given FK RESTRICT, but defensive).
    pub song: Option<SongRow>,
}

#[derive(Debug, Clone, Serialize)]
pub struct SetlistDetail {
    #[serde(flatten)]
    pub setlist: SetlistRow,
    pub items: Vec<SetlistItemWithSong>,
}

// --- Public API ------------------------------------------------------------

pub fn list_setlists(db: &Arc<Db>) -> Result<Vec<SetlistRow>> {
    let conn = db.conn().lock().unwrap();
    // A setlist not yet presented sorts by when it was made, so a new one is
    // at the top of Home rather than under every service ever run.
    let mut stmt = conn.prepare(
        "SELECT id, name, created_at, last_used_at, service_date \
         FROM setlists \
         ORDER BY COALESCE(last_used_at, created_at) DESC, name COLLATE NOCASE",
    )?;
    let mut rows = stmt
        .query_map([], map_setlist_row)?
        .collect::<Result<Vec<_>, _>>()?;

    // Scripture and text items are named too, so a service is findable on
    // Home by its reading as well as by its songs.
    let mut title_stmt = conn.prepare(
        "SELECT i.setlist_id, s.title, i.content FROM setlist_items i \
         LEFT JOIN songs s ON s.id = i.song_id \
         ORDER BY i.setlist_id, i.position",
    )?;
    let mut titles: std::collections::HashMap<String, Vec<String>> = Default::default();
    let mut songs: std::collections::HashMap<String, usize> = Default::default();
    for row in title_stmt.query_map([], |r| {
        Ok((
            r.get::<_, String>(0)?,
            r.get::<_, Option<String>>(1)?,
            r.get::<_, Option<String>>(2)?,
        ))
    })? {
        let (setlist_id, song_title, content) = row?;
        if song_title.is_some() {
            *songs.entry(setlist_id.clone()).or_default() += 1;
        }
        if let Some(title) = song_title.or_else(|| content.as_deref().and_then(item_label)) {
            titles.entry(setlist_id).or_default().push(title);
        }
    }
    for row in &mut rows {
        row.song_titles = titles.remove(&row.id).unwrap_or_default();
        row.song_count = songs.remove(&row.id).unwrap_or_default();
    }
    Ok(rows)
}

pub fn get_setlist(db: &Arc<Db>, id: &str) -> Result<Option<SetlistDetail>> {
    let conn = db.conn().lock().unwrap();

    let setlist = match conn
        .prepare("SELECT id, name, created_at, last_used_at, service_date FROM setlists WHERE id = ?1")?
        .query_row(params![id], map_setlist_row)
    {
        Ok(s) => s,
        Err(rusqlite::Error::QueryReturnedNoRows) => return Ok(None),
        Err(e) => return Err(e.into()),
    };

    let mut item_stmt = conn.prepare(
        "SELECT i.id, i.setlist_id, i.kind, i.song_id, i.position, \
                s.id, s.title, s.body_text, s.ccli, s.author, s.copyright, \
                s.default_tempo_wps, s.created_at, s.updated_at, s.slide_order, \
                i.slides_override, i.content, i.look, i.song_key \
         FROM setlist_items i \
         LEFT JOIN songs s ON s.id = i.song_id \
         WHERE i.setlist_id = ?1 \
         ORDER BY i.position",
    )?;

    let items = item_stmt
        .query_map(params![id], |row| {
            let item = SetlistItemRow {
                id: row.get(0)?,
                setlist_id: row.get(1)?,
                kind: row.get(2)?,
                song_id: row.get(3)?,
                position: row.get(4)?,
                slides_override: row.get(15)?,
                content: row.get(16)?,
                look: row.get(17)?,
                song_key: row.get(18)?,
            };
            let song_id: Option<String> = row.get(5)?;
            let song = match song_id {
                Some(_) => Some(SongRow {
                    id: row.get(5)?,
                    title: row.get(6)?,
                    body_text: row.get(7)?,
                    ccli: row.get(8)?,
                    author: row.get(9)?,
                    copyright: row.get(10)?,
                    default_tempo_wps: row.get(11)?,
                    created_at: row.get(12)?,
                    updated_at: row.get(13)?,
                    slide_order: row.get(14)?,
                }),
                None => None,
            };
            Ok(SetlistItemWithSong { item, song })
        })?
        .collect::<Result<Vec<_>, _>>()?;

    Ok(Some(SetlistDetail { setlist, items }))
}

pub fn create_setlist(db: &Arc<Db>, name: &str, service_date: Option<&str>) -> Result<String> {
    let service_date = checked_date(service_date)?;
    let id = format!(
        "setlist_{}",
        ulid::Ulid::new().to_string().to_lowercase()
    );
    let now = unix_ms();
    let conn = db.conn().lock().unwrap();
    conn.execute(
        "INSERT INTO setlists (id, name, created_at, service_date) VALUES (?1, ?2, ?3, ?4)",
        params![id, name, now, service_date],
    )?;
    Ok(id)
}

/// A service date as stored: `YYYY-MM-DD`, or None. Anything else is refused.
fn checked_date(date: Option<&str>) -> Result<Option<&str>> {
    match date {
        None | Some("") => Ok(None),
        Some(d) if d.len() == 10 && d.bytes().enumerate().all(|(i, b)| if i == 4 || i == 7 { b == b'-' } else { b.is_ascii_digit() }) => Ok(Some(d)),
        Some(d) => Err(anyhow!("not a date: {d}")),
    }
}

pub fn update_setlist_date(db: &Arc<Db>, id: &str, service_date: Option<&str>) -> Result<()> {
    let service_date = checked_date(service_date)?;
    let conn = db.conn().lock().unwrap();
    let n = conn.execute(
        "UPDATE setlists SET service_date = ?2 WHERE id = ?1",
        params![id, service_date],
    )?;
    if n == 0 {
        return Err(anyhow!("no setlist with id {id}"));
    }
    Ok(())
}

pub fn update_setlist_name(db: &Arc<Db>, id: &str, name: &str) -> Result<()> {
    let conn = db.conn().lock().unwrap();
    let n = conn.execute(
        "UPDATE setlists SET name = ?2 WHERE id = ?1",
        params![id, name],
    )?;
    if n == 0 {
        return Err(anyhow!("no setlist with id {id}"));
    }
    Ok(())
}

pub fn delete_setlist(db: &Arc<Db>, id: &str) -> Result<()> {
    let conn = db.conn().lock().unwrap();
    conn.execute("DELETE FROM setlists WHERE id = ?1", params![id])?;
    Ok(())
}

pub fn mark_setlist_used(db: &Arc<Db>, id: &str) -> Result<()> {
    let now = unix_ms();
    let conn = db.conn().lock().unwrap();
    conn.execute(
        "UPDATE setlists SET last_used_at = ?2 WHERE id = ?1",
        params![id, now],
    )?;
    crate::services::record_service_start(&conn, id, now)?;
    Ok(())
}

pub fn add_setlist_item(db: &Arc<Db>, setlist_id: &str, song_id: &str) -> Result<String> {
    // Verify both rows exist before inserting (clearer error than FK).
    let conn = db.conn().lock().unwrap();
    conn.query_row(
        "SELECT 1 FROM setlists WHERE id = ?1",
        params![setlist_id],
        |_| Ok(()),
    )
    .map_err(|_| anyhow!("no setlist with id {setlist_id}"))?;
    conn.query_row(
        "SELECT 1 FROM songs WHERE id = ?1",
        params![song_id],
        |_| Ok(()),
    )
    .map_err(|_| anyhow!("no song with id {song_id}"))?;

    let next_pos: i64 = conn
        .query_row(
            "SELECT COALESCE(MAX(position), -1) + 1 \
             FROM setlist_items WHERE setlist_id = ?1",
            params![setlist_id],
            |row| row.get(0),
        )
        .unwrap_or(0);
    let item_id = format!(
        "item_{}",
        ulid::Ulid::new().to_string().to_lowercase()
    );
    conn.execute(
        "INSERT INTO setlist_items (id, setlist_id, kind, song_id, position) \
         VALUES (?1, ?2, 'song', ?3, ?4)",
        params![item_id, setlist_id, song_id, next_pos],
    )?;
    crate::services::record_song_added(&conn, setlist_id, song_id, unix_ms())?;
    Ok(item_id)
}

/// Add a scripture or text item, after `after_item_id` when given (the item
/// the operator has selected) and at the end otherwise.
pub fn add_service_item(
    db: &Arc<Db>,
    setlist_id: &str,
    kind: &str,
    content: &str,
    slides_override: Option<&str>,
    after_item_id: Option<&str>,
) -> Result<String> {
    if !SERVICE_ITEM_KINDS.contains(&kind) {
        return Err(anyhow!("unknown item kind {kind:?}"));
    }
    serde_json::from_str::<serde_json::Value>(content).context("item content is not JSON")?;
    let mut conn = db.conn().lock().unwrap();
    let tx = conn.transaction()?;
    tx.query_row("SELECT 1 FROM setlists WHERE id = ?1", params![setlist_id], |_| Ok(()))
        .map_err(|_| anyhow!("no setlist with id {setlist_id}"))?;

    let after: Option<i64> = match after_item_id {
        Some(after) => Some(
            tx.query_row(
                "SELECT position FROM setlist_items WHERE id = ?1 AND setlist_id = ?2",
                params![after, setlist_id],
                |row| row.get(0),
            )
            .map_err(|_| anyhow!("no item with id {after} in setlist {setlist_id}"))?,
        ),
        None => None,
    };
    let position = match after {
        Some(pos) => {
            tx.execute(
                "UPDATE setlist_items SET position = position + 1 \
                 WHERE setlist_id = ?1 AND position > ?2",
                params![setlist_id, pos],
            )?;
            pos + 1
        }
        None => tx.query_row(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM setlist_items WHERE setlist_id = ?1",
            params![setlist_id],
            |row| row.get(0),
        )?,
    };
    let item_id = format!("item_{}", ulid::Ulid::new().to_string().to_lowercase());
    tx.execute(
        "INSERT INTO setlist_items (id, setlist_id, kind, song_id, position, slides_override, content) \
         VALUES (?1, ?2, ?3, NULL, ?4, ?5, ?6)",
        params![item_id, setlist_id, kind, position, slides_override, content],
    )?;
    tx.commit()?;
    Ok(item_id)
}

/// Replace a scripture or text item's content ("Use my own text", a renamed
/// text item). Songs have no content and are refused.
pub fn set_setlist_item_content(
    db: &Arc<Db>,
    setlist_id: &str,
    item_id: &str,
    content: &str,
) -> Result<()> {
    serde_json::from_str::<serde_json::Value>(content).context("item content is not JSON")?;
    let conn = db.conn().lock().unwrap();
    let n = conn.execute(
        "UPDATE setlist_items SET content = ?3 \
         WHERE id = ?1 AND setlist_id = ?2 AND kind != 'song'",
        params![item_id, setlist_id, content],
    )?;
    if n == 0 {
        return Err(anyhow!("no scripture or text item {item_id} in setlist {setlist_id}"));
    }
    Ok(())
}

/// "Start from last service": a new setlist holding a copy of another's
/// items — songs, readings and every per-service edit — in the same order.
pub fn duplicate_setlist(db: &Arc<Db>, source_id: &str, name: &str, service_date: Option<&str>) -> Result<String> {
    let service_date = checked_date(service_date)?;
    let mut conn = db.conn().lock().unwrap();
    let tx = conn.transaction()?;
    tx.query_row("SELECT 1 FROM setlists WHERE id = ?1", params![source_id], |_| Ok(()))
        .map_err(|_| anyhow!("no setlist with id {source_id}"))?;
    let id = format!("setlist_{}", ulid::Ulid::new().to_string().to_lowercase());
    tx.execute(
        "INSERT INTO setlists (id, name, created_at, service_date) VALUES (?1, ?2, ?3, ?4)",
        params![id, name, unix_ms(), service_date],
    )?;
    type CopiedItem = (String, Option<String>, i64, Option<String>, Option<String>, Option<String>, Option<String>);
    let items: Vec<CopiedItem> = tx
        .prepare(
            "SELECT kind, song_id, position, slides_override, content, look, song_key FROM setlist_items \
             WHERE setlist_id = ?1 ORDER BY position",
        )?
        .query_map(params![source_id], |r| {
            Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?, r.get(5)?, r.get(6)?))
        })?
        .collect::<Result<_, _>>()?;
    for (kind, song_id, position, slides_override, content, look, song_key) in items {
        tx.execute(
            "INSERT INTO setlist_items (id, setlist_id, kind, song_id, position, slides_override, content, look, song_key) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            params![
                format!("item_{}", ulid::Ulid::new().to_string().to_lowercase()),
                id,
                kind,
                song_id,
                position,
                slides_override,
                content,
                look,
                song_key
            ],
        )?;
    }
    tx.commit()?;
    Ok(id)
}

// --- Readings ---------------------------------------------------------------

pub fn list_readings(db: &Arc<Db>) -> Result<Vec<ReadingRow>> {
    let conn = db.conn().lock().unwrap();
    let mut stmt = conn.prepare(
        "SELECT id, title, body_text, created_at, updated_at FROM readings \
         ORDER BY title COLLATE NOCASE",
    )?;
    let rows = stmt
        .query_map([], |r| {
            Ok(ReadingRow {
                id: r.get(0)?,
                title: r.get(1)?,
                body_text: r.get(2)?,
                created_at: r.get(3)?,
                updated_at: r.get(4)?,
            })
        })?
        .collect::<Result<Vec<_>, _>>()?;
    Ok(rows)
}

/// Save a reading to the library. A reading with the same title (ignoring
/// case) is updated rather than duplicated, so "Save to library" twice on the
/// Apostles' Creed keeps one Apostles' Creed.
pub fn save_reading(db: &Arc<Db>, title: &str, body_text: &str) -> Result<String> {
    let title = title.trim();
    if title.is_empty() {
        return Err(anyhow!("a reading needs a title"));
    }
    let now = unix_ms();
    let conn = db.conn().lock().unwrap();
    let existing: Option<String> = conn
        .query_row(
            "SELECT id FROM readings WHERE title = ?1 COLLATE NOCASE",
            params![title],
            |r| r.get(0),
        )
        .ok();
    if let Some(id) = existing {
        conn.execute(
            "UPDATE readings SET title = ?2, body_text = ?3, updated_at = ?4 WHERE id = ?1",
            params![id, title, body_text, now],
        )?;
        return Ok(id);
    }
    let id = format!("reading_{}", ulid::Ulid::new().to_string().to_lowercase());
    conn.execute(
        "INSERT INTO readings (id, title, body_text, created_at, updated_at) \
         VALUES (?1, ?2, ?3, ?4, ?4)",
        params![id, title, body_text, now],
    )?;
    Ok(id)
}

pub fn delete_reading(db: &Arc<Db>, id: &str) -> Result<()> {
    let conn = db.conn().lock().unwrap();
    conn.execute("DELETE FROM readings WHERE id = ?1", params![id])?;
    Ok(())
}

pub fn remove_setlist_item(
    db: &Arc<Db>,
    setlist_id: &str,
    item_id: &str,
) -> Result<()> {
    let conn = db.conn().lock().unwrap();
    let n = conn.execute(
        "DELETE FROM setlist_items WHERE id = ?1 AND setlist_id = ?2",
        params![item_id, setlist_id],
    )?;
    if n == 0 {
        return Err(anyhow!(
            "no item with id {item_id} in setlist {setlist_id}"
        ));
    }
    Ok(())
}

/// Set (or clear, with `None`) an item's own display look for this setlist.
/// `look` is the JSON the frontend stores (`displayLook.ts`); it must parse.
pub fn set_setlist_item_look(
    db: &Arc<Db>,
    setlist_id: &str,
    item_id: &str,
    look: Option<&str>,
) -> Result<()> {
    if let Some(json) = look {
        serde_json::from_str::<serde_json::Value>(json).context("item look is not JSON")?;
    }
    let conn = db.conn().lock().unwrap();
    let n = conn.execute(
        "UPDATE setlist_items SET look = ?3 WHERE id = ?1 AND setlist_id = ?2",
        params![item_id, setlist_id, look],
    )?;
    if n == 0 {
        return Err(anyhow!("no item with id {item_id} in setlist {setlist_id}"));
    }
    Ok(())
}

/// Set (or clear, with `None`) the key a song is played in for this setlist:
/// a major or minor key name such as "Bb" or "F#m".
pub fn set_setlist_item_key(
    db: &Arc<Db>,
    setlist_id: &str,
    item_id: &str,
    key: Option<&str>,
) -> Result<()> {
    let key = key.map(str::trim).filter(|k| !k.is_empty());
    if let Some(k) = key {
        if !is_key_name(k) {
            return Err(anyhow!("\"{k}\" isn't a key — use a name like G, Bb, F# or Em"));
        }
    }
    let conn = db.conn().lock().unwrap();
    let n = conn.execute(
        "UPDATE setlist_items SET song_key = ?3 WHERE id = ?1 AND setlist_id = ?2 AND kind = 'song'",
        params![item_id, setlist_id, key],
    )?;
    if n == 0 {
        return Err(anyhow!("no song item with id {item_id} in setlist {setlist_id}"));
    }
    Ok(())
}

/// "C", "Bb", "F#", "Ebm", "C#m": a key name the band screens can transpose to.
pub fn is_key_name(k: &str) -> bool {
    let mut chars = k.chars();
    let Some(root) = chars.next() else { return false };
    if !('A'..='G').contains(&root) {
        return false;
    }
    matches!(chars.as_str(), "" | "#" | "b" | "m" | "#m" | "bm")
}

/// Set (or clear, with `None`) the per-setlist slide override for an item.
/// The caller passes a JSON array of edited slides; `None` reverts the item
/// to the library song's body + slide_order.
pub fn set_setlist_item_slides(
    db: &Arc<Db>,
    setlist_id: &str,
    item_id: &str,
    slides_override: Option<&str>,
) -> Result<()> {
    let conn = db.conn().lock().unwrap();
    let n = conn.execute(
        "UPDATE setlist_items SET slides_override = ?3 \
         WHERE id = ?1 AND setlist_id = ?2",
        params![item_id, setlist_id, slides_override],
    )?;
    if n == 0 {
        return Err(anyhow!(
            "no item with id {item_id} in setlist {setlist_id}"
        ));
    }
    Ok(())
}

/// Atomically rewrite item positions in `item_ids` order. Items not in the
/// list (e.g. concurrently added) keep their existing position and end up
/// at the tail by virtue of being numbered after the last position assigned
/// here (max(item_ids.len() - 1, existing position)).
pub fn reorder_setlist(
    db: &Arc<Db>,
    setlist_id: &str,
    item_ids: &[String],
) -> Result<()> {
    let mut conn = db.conn().lock().unwrap();
    let tx = conn.transaction()?;

    // Verify each item belongs to this setlist before mutating anything.
    for id in item_ids {
        let count: i64 = tx
            .query_row(
                "SELECT COUNT(*) FROM setlist_items WHERE id = ?1 AND setlist_id = ?2",
                params![id, setlist_id],
                |row| row.get(0),
            )
            .unwrap_or(0);
        if count == 0 {
            return Err(anyhow!(
                "item {id} does not belong to setlist {setlist_id}"
            ));
        }
    }

    for (i, id) in item_ids.iter().enumerate() {
        tx.execute(
            "UPDATE setlist_items SET position = ?1 WHERE id = ?2",
            params![i as i64, id],
        )?;
    }

    tx.commit().context("committing reorder")?;
    Ok(())
}

// --- Helpers ----------------------------------------------------------------

/// A scripture or text item's name: its reference, or its title.
pub fn item_label(content: &str) -> Option<String> {
    let v: serde_json::Value = serde_json::from_str(content).ok()?;
    ["reference", "title"]
        .iter()
        .find_map(|k| v.get(k).and_then(|t| t.as_str()))
        .map(str::trim)
        .filter(|t| !t.is_empty())
        .map(str::to_string)
}

fn map_setlist_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<SetlistRow> {
    Ok(SetlistRow {
        id: row.get(0)?,
        name: row.get(1)?,
        created_at: row.get(2)?,
        last_used_at: row.get(3)?,
        service_date: row.get(4)?,
        song_titles: Vec::new(),
        song_count: 0,
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

    fn make_song(db: &Arc<Db>, title: &str) -> String {
        db.create_song(NewSong {
            title: title.into(),
            body_text: format!("[v]\n{title}"),
            ccli: None,
            author: None,
            copyright: None,
            default_tempo_wps: None,
            slide_order: None,
        })
        .unwrap()
    }

    #[test]
    fn a_service_keeps_its_date_and_refuses_a_malformed_one() {
        let (_d, db) = fresh();
        let id = create_setlist(&db, "Sunday 29 Nov", Some("2026-11-29")).unwrap();
        assert_eq!(get_setlist(&db, &id).unwrap().unwrap().setlist.service_date.as_deref(), Some("2026-11-29"));
        update_setlist_date(&db, &id, Some("2026-12-06")).unwrap();
        let copy = duplicate_setlist(&db, &id, "Sunday 13 Dec", Some("2026-12-13")).unwrap();
        assert_eq!(list_setlists(&db).unwrap().iter().find(|s| s.id == id).unwrap().service_date.as_deref(), Some("2026-12-06"));
        assert_eq!(get_setlist(&db, &copy).unwrap().unwrap().setlist.service_date.as_deref(), Some("2026-12-13"));
        assert!(update_setlist_date(&db, &id, Some("6 Dec")).is_err());
        update_setlist_date(&db, &id, None).unwrap();
        assert_eq!(get_setlist(&db, &id).unwrap().unwrap().setlist.service_date, None);
    }

    #[test]
    fn create_then_list() {
        let (_d, db) = fresh();
        let id = create_setlist(&db, "Sunday morning", None).unwrap();
        let all = list_setlists(&db).unwrap();
        assert_eq!(all.len(), 1);
        assert_eq!(all[0].id, id);
        assert_eq!(all[0].name, "Sunday morning");
    }

    #[test]
    fn new_setlist_lists_above_one_presented_before_it_was_made() {
        let (_d, db) = fresh();
        let week = 7 * 24 * 3600 * 1000;
        let old = create_setlist(&db, "Last week", None).unwrap();
        {
            let conn = db.conn().lock().unwrap();
            conn.execute(
                "UPDATE setlists SET created_at = created_at - ?2, last_used_at = created_at - ?2 + 1 WHERE id = ?1",
                params![old, 2 * week],
            )
            .unwrap();
        }
        let new = create_setlist(&db, "This Sunday", None).unwrap();
        let all = list_setlists(&db).unwrap();
        assert_eq!(all[0].id, new);
        assert_eq!(all[1].id, old);
    }

    #[test]
    fn list_carries_song_titles_in_setlist_order() {
        let (_d, db) = fresh();
        let song_a = make_song(&db, "Alpha");
        let song_b = make_song(&db, "Bravo");
        let full = create_setlist(&db, "Full", None).unwrap();
        let empty = create_setlist(&db, "Empty", None).unwrap();
        let a = add_setlist_item(&db, &full, &song_a).unwrap();
        let b = add_setlist_item(&db, &full, &song_b).unwrap();
        reorder_setlist(&db, &full, &[b, a]).unwrap();

        let all = list_setlists(&db).unwrap();
        let titles = |id: &str| all.iter().find(|s| s.id == id).unwrap().song_titles.clone();
        assert_eq!(titles(&full), vec!["Bravo", "Alpha"]);
        assert!(titles(&empty).is_empty());
    }

    #[test]
    fn get_returns_items_in_position_order() {
        let (_d, db) = fresh();
        let song_a = make_song(&db, "Alpha");
        let song_b = make_song(&db, "Bravo");
        let song_c = make_song(&db, "Charlie");

        let setlist_id = create_setlist(&db, "Test", None).unwrap();
        let item_a = add_setlist_item(&db, &setlist_id, &song_a).unwrap();
        let item_b = add_setlist_item(&db, &setlist_id, &song_b).unwrap();
        let item_c = add_setlist_item(&db, &setlist_id, &song_c).unwrap();

        let detail = get_setlist(&db, &setlist_id).unwrap().unwrap();
        assert_eq!(detail.items.len(), 3);
        assert_eq!(detail.items[0].item.id, item_a);
        assert_eq!(detail.items[1].item.id, item_b);
        assert_eq!(detail.items[2].item.id, item_c);
        assert_eq!(detail.items[0].song.as_ref().unwrap().title, "Alpha");
    }

    #[test]
    fn add_item_uses_incrementing_position() {
        let (_d, db) = fresh();
        let song = make_song(&db, "Song");
        let setlist_id = create_setlist(&db, "Test", None).unwrap();

        for i in 0..5 {
            add_setlist_item(&db, &setlist_id, &song).unwrap();
            let detail = get_setlist(&db, &setlist_id).unwrap().unwrap();
            assert_eq!(detail.items.len(), i + 1);
            for (idx, it) in detail.items.iter().enumerate() {
                assert_eq!(it.item.position, idx as i64);
            }
        }
    }

    #[test]
    fn reorder_swaps_positions() {
        let (_d, db) = fresh();
        let song_a = make_song(&db, "Alpha");
        let song_b = make_song(&db, "Bravo");
        let song_c = make_song(&db, "Charlie");
        let setlist_id = create_setlist(&db, "Test", None).unwrap();
        let a = add_setlist_item(&db, &setlist_id, &song_a).unwrap();
        let b = add_setlist_item(&db, &setlist_id, &song_b).unwrap();
        let c = add_setlist_item(&db, &setlist_id, &song_c).unwrap();

        reorder_setlist(&db, &setlist_id, &[c.clone(), a.clone(), b.clone()]).unwrap();

        let detail = get_setlist(&db, &setlist_id).unwrap().unwrap();
        let titles: Vec<&str> = detail
            .items
            .iter()
            .map(|i| i.song.as_ref().unwrap().title.as_str())
            .collect();
        assert_eq!(titles, vec!["Charlie", "Alpha", "Bravo"]);
    }

    #[test]
    fn reorder_rejects_foreign_items() {
        let (_d, db) = fresh();
        let song = make_song(&db, "Song");
        let sl1 = create_setlist(&db, "One", None).unwrap();
        let sl2 = create_setlist(&db, "Two", None).unwrap();
        let item_in_sl1 = add_setlist_item(&db, &sl1, &song).unwrap();

        // Try to reorder sl2 with an item from sl1.
        let err = reorder_setlist(&db, &sl2, &[item_in_sl1]).unwrap_err();
        assert!(err.to_string().contains("does not belong"));
    }

    #[test]
    fn delete_setlist_cascades_items() {
        let (_d, db) = fresh();
        let song = make_song(&db, "Song");
        let setlist_id = create_setlist(&db, "Test", None).unwrap();
        add_setlist_item(&db, &setlist_id, &song).unwrap();
        add_setlist_item(&db, &setlist_id, &song).unwrap();

        delete_setlist(&db, &setlist_id).unwrap();

        let conn = db.conn().lock().unwrap();
        let count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM setlist_items WHERE setlist_id = ?1",
                params![setlist_id],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(count, 0);
    }

    #[test]
    fn remove_item_keeps_others() {
        let (_d, db) = fresh();
        let song = make_song(&db, "Song");
        let setlist_id = create_setlist(&db, "Test", None).unwrap();
        let a = add_setlist_item(&db, &setlist_id, &song).unwrap();
        let b = add_setlist_item(&db, &setlist_id, &song).unwrap();
        let c = add_setlist_item(&db, &setlist_id, &song).unwrap();

        remove_setlist_item(&db, &setlist_id, &b).unwrap();

        let detail = get_setlist(&db, &setlist_id).unwrap().unwrap();
        let ids: Vec<&str> = detail.items.iter().map(|i| i.item.id.as_str()).collect();
        assert_eq!(ids, vec![a.as_str(), c.as_str()]);
    }

    #[test]
    fn an_item_keeps_its_own_look_and_a_copy_keeps_it_too() {
        let (_d, db) = fresh();
        let song = make_song(&db, "Song");
        let setlist_id = create_setlist(&db, "Test", None).unwrap();
        let item = add_setlist_item(&db, &setlist_id, &song).unwrap();
        let detail = get_setlist(&db, &setlist_id).unwrap().unwrap();
        assert_eq!(detail.items[0].item.look, None);

        let look = r#"{"theme":"light","font":"georgia","size":"l","weight":"regular"}"#;
        set_setlist_item_look(&db, &setlist_id, &item, Some(look)).unwrap();
        let copy = duplicate_setlist(&db, &setlist_id, "Copy", None).unwrap();
        let copied = get_setlist(&db, &copy).unwrap().unwrap();
        assert_eq!(copied.items[0].item.look.as_deref(), Some(look));

        set_setlist_item_look(&db, &setlist_id, &item, None).unwrap();
        let detail = get_setlist(&db, &setlist_id).unwrap().unwrap();
        assert_eq!(detail.items[0].item.look, None);
        assert!(set_setlist_item_look(&db, &setlist_id, &item, Some("not json")).is_err());
        assert!(set_setlist_item_look(&db, &setlist_id, "nope", None).is_err());
    }

    #[test]
    fn an_item_keeps_its_own_key_and_a_copy_keeps_it_too() {
        let (_d, db) = fresh();
        let song = make_song(&db, "Song");
        let setlist_id = create_setlist(&db, "Test", None).unwrap();
        let item = add_setlist_item(&db, &setlist_id, &song).unwrap();
        set_setlist_item_key(&db, &setlist_id, &item, Some("Bb")).unwrap();
        let copy = duplicate_setlist(&db, &setlist_id, "Copy", None).unwrap();
        assert_eq!(get_setlist(&db, &copy).unwrap().unwrap().items[0].item.song_key.as_deref(), Some("Bb"));

        set_setlist_item_key(&db, &setlist_id, &item, None).unwrap();
        assert_eq!(get_setlist(&db, &setlist_id).unwrap().unwrap().items[0].item.song_key, None);
        assert!(set_setlist_item_key(&db, &setlist_id, &item, Some("H")).is_err());
        assert!(set_setlist_item_key(&db, &setlist_id, &item, Some("Bbb")).is_err());
        assert!(set_setlist_item_key(&db, &setlist_id, "nope", Some("G")).is_err());
        for ok in ["C", "F#", "Ebm", "C#m"] {
            assert!(is_key_name(ok), "{ok}");
        }
    }

    #[test]
    fn set_and_clear_slides_override() {
        let (_d, db) = fresh();
        let song = make_song(&db, "Song");
        let setlist_id = create_setlist(&db, "Test", None).unwrap();
        let item = add_setlist_item(&db, &setlist_id, &song).unwrap();

        // Default: no override.
        let detail = get_setlist(&db, &setlist_id).unwrap().unwrap();
        assert_eq!(detail.items[0].item.slides_override, None);

        // Set one.
        let json = r#"[{"section":{"type":"chorus","number":1},"lyrics":"Hi"}]"#;
        set_setlist_item_slides(&db, &setlist_id, &item, Some(json)).unwrap();
        let detail = get_setlist(&db, &setlist_id).unwrap().unwrap();
        assert_eq!(detail.items[0].item.slides_override.as_deref(), Some(json));

        // Clear it.
        set_setlist_item_slides(&db, &setlist_id, &item, None).unwrap();
        let detail = get_setlist(&db, &setlist_id).unwrap().unwrap();
        assert_eq!(detail.items[0].item.slides_override, None);
    }

    #[test]
    fn set_slides_override_rejects_foreign_item() {
        let (_d, db) = fresh();
        let song = make_song(&db, "Song");
        let sl1 = create_setlist(&db, "One", None).unwrap();
        let sl2 = create_setlist(&db, "Two", None).unwrap();
        let item = add_setlist_item(&db, &sl1, &song).unwrap();
        let err = set_setlist_item_slides(&db, &sl2, &item, Some("[]")).unwrap_err();
        assert!(err.to_string().contains("no item"));
    }

    #[test]
    fn service_item_lands_after_the_selected_item() {
        let (_d, db) = fresh();
        let song = make_song(&db, "Song");
        let sl = create_setlist(&db, "Test", None).unwrap();
        let a = add_setlist_item(&db, &sl, &song).unwrap();
        let b = add_setlist_item(&db, &sl, &song).unwrap();
        let reading = add_service_item(
            &db, &sl, "scripture", r#"{"reference":"John 3:16"}"#, None, Some(&a),
        )
        .unwrap();
        let end = add_service_item(&db, &sl, "text", r#"{"title":"Welcome"}"#, Some("[]"), None)
            .unwrap();

        let detail = get_setlist(&db, &sl).unwrap().unwrap();
        let ids: Vec<&str> = detail.items.iter().map(|i| i.item.id.as_str()).collect();
        assert_eq!(ids, vec![a.as_str(), reading.as_str(), b.as_str(), end.as_str()]);
        let positions: Vec<i64> = detail.items.iter().map(|i| i.item.position).collect();
        assert_eq!(positions, vec![0, 1, 2, 3]);
        let r = &detail.items[1];
        assert_eq!(r.item.kind, "scripture");
        assert!(r.song.is_none());
        assert_eq!(r.item.content.as_deref(), Some(r#"{"reference":"John 3:16"}"#));
        assert_eq!(detail.items[3].item.slides_override.as_deref(), Some("[]"));
    }

    #[test]
    fn service_items_refuse_bad_kinds_and_content() {
        let (_d, db) = fresh();
        let sl = create_setlist(&db, "Test", None).unwrap();
        assert!(add_service_item(&db, &sl, "song", "{}", None, None).is_err());
        assert!(add_service_item(&db, &sl, "video", "{}", None, None).is_err());
        assert!(add_service_item(&db, &sl, "text", "not json", None, None).is_err());
    }

    #[test]
    fn content_updates_only_service_items() {
        let (_d, db) = fresh();
        let song = make_song(&db, "Song");
        let sl = create_setlist(&db, "Test", None).unwrap();
        let s = add_setlist_item(&db, &sl, &song).unwrap();
        let t = add_service_item(&db, &sl, "text", r#"{"title":"A"}"#, None, None).unwrap();
        set_setlist_item_content(&db, &sl, &t, r#"{"title":"B"}"#).unwrap();
        assert!(set_setlist_item_content(&db, &sl, &s, r#"{"title":"B"}"#).is_err());
        let detail = get_setlist(&db, &sl).unwrap().unwrap();
        assert_eq!(detail.items[1].item.content.as_deref(), Some(r#"{"title":"B"}"#));
    }

    #[test]
    fn list_names_readings_alongside_songs() {
        let (_d, db) = fresh();
        let song = make_song(&db, "Alpha");
        let sl = create_setlist(&db, "Sunday", None).unwrap();
        add_setlist_item(&db, &sl, &song).unwrap();
        add_service_item(&db, &sl, "scripture", r#"{"reference":"Psalm 23"}"#, None, None).unwrap();
        add_service_item(&db, &sl, "text", r#"{"title":"Announcements"}"#, None, None).unwrap();
        let all = list_setlists(&db).unwrap();
        assert_eq!(all[0].song_titles, vec!["Alpha", "Psalm 23", "Announcements"]);
        assert_eq!(all[0].song_count, 1);
    }

    #[test]
    fn duplicate_copies_items_and_edits_in_order() {
        let (_d, db) = fresh();
        let song = make_song(&db, "Alpha");
        let sl = create_setlist(&db, "Last week", None).unwrap();
        let a = add_setlist_item(&db, &sl, &song).unwrap();
        set_setlist_item_slides(&db, &sl, &a, Some("[]")).unwrap();
        add_service_item(&db, &sl, "text", r#"{"title":"Creed"}"#, Some("[1]"), None).unwrap();

        let copy = duplicate_setlist(&db, &sl, "This week", None).unwrap();
        let src = get_setlist(&db, &sl).unwrap().unwrap();
        let dst = get_setlist(&db, &copy).unwrap().unwrap();
        assert_eq!(dst.setlist.name, "This week");
        assert_eq!(dst.items.len(), 2);
        for (s, d) in src.items.iter().zip(&dst.items) {
            assert_ne!(s.item.id, d.item.id);
            assert_eq!(s.item.kind, d.item.kind);
            assert_eq!(s.item.song_id, d.item.song_id);
            assert_eq!(s.item.slides_override, d.item.slides_override);
            assert_eq!(s.item.content, d.item.content);
        }
    }

    #[test]
    fn saving_a_reading_twice_keeps_one() {
        let (_d, db) = fresh();
        let a = save_reading(&db, "Apostles' Creed", "I believe").unwrap();
        let b = save_reading(&db, "apostles' creed", "I believe in God").unwrap();
        assert_eq!(a, b);
        let all = list_readings(&db).unwrap();
        assert_eq!(all.len(), 1);
        assert_eq!(all[0].body_text, "I believe in God");
        assert!(save_reading(&db, "  ", "x").is_err());
        delete_reading(&db, &a).unwrap();
        assert!(list_readings(&db).unwrap().is_empty());
    }

    #[test]
    fn mark_used_bumps_recency_sort() {
        let (_d, db) = fresh();
        let older = create_setlist(&db, "Older", None).unwrap();
        std::thread::sleep(std::time::Duration::from_millis(5));
        let newer = create_setlist(&db, "Newer", None).unwrap();

        // By default newer (most recently created) sorts first.
        let by_default = list_setlists(&db).unwrap();
        assert_eq!(by_default[0].id, newer);
        assert_eq!(by_default[1].id, older);

        // Mark the older one used; it should jump to the top.
        std::thread::sleep(std::time::Duration::from_millis(5));
        mark_setlist_used(&db, &older).unwrap();
        let after = list_setlists(&db).unwrap();
        assert_eq!(after[0].id, older);
        assert_eq!(after[1].id, newer);
    }

    #[test]
    fn phase_1_carry_over_popular_sort_with_real_setlists() {
        // Phase 1 deferred verification of `SortKey::Popular` ranking
        // until setlists existed. With this commit they do, so verify
        // popularity ordering using only the real Setlist API.
        let (_d, db) = fresh();
        let song_a = make_song(&db, "Alpha");
        let song_b = make_song(&db, "Bravo");
        let song_c = make_song(&db, "Charlie");

        let sl1 = create_setlist(&db, "Sunday", None).unwrap();
        let sl2 = create_setlist(&db, "Wednesday", None).unwrap();
        let sl3 = create_setlist(&db, "Special", None).unwrap();

        // Alpha appears in 3 setlists; Bravo in 1; Charlie in 0.
        add_setlist_item(&db, &sl1, &song_a).unwrap();
        add_setlist_item(&db, &sl2, &song_a).unwrap();
        add_setlist_item(&db, &sl3, &song_a).unwrap();
        add_setlist_item(&db, &sl1, &song_b).unwrap();
        let _ = song_c; // unused but must exist

        use crate::db::SortKey;
        let titles: Vec<String> = db
            .list_songs_sorted(SortKey::Popular)
            .unwrap()
            .into_iter()
            .map(|s| s.title)
            .collect();

        assert_eq!(titles[0], "Alpha");
        assert_eq!(titles[1], "Bravo");
        // The example hymns and Charlie tie at zero — alphabetical fallback.
        assert_eq!(titles[2], "Abide With Me");
        assert_eq!(titles[3], "Amazing Grace");
    }
}
