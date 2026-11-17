//! The service log: which songs each service held, newest first.
//!
//! Nobody writes to it by hand. A service start (`mark_setlist_used`) records
//! the setlist's songs as they are at that moment, and a song added during
//! the service (`add_setlist_item`) joins it. Library search reads it to
//! answer "haven't played in the last 3 services" and "what did we play last
//! Sunday" — see migration 008 for why `setlists.last_used_at` can't.

use std::sync::Arc;

use anyhow::Result;
use rusqlite::{params, Connection, OptionalExtension};
use serde::Serialize;

use crate::db::Db;

/// Starting the same setlist again within this window (a restart, a second
/// window, the app reopened mid-service) is the same service, not a new one.
pub const SERVICE_MERGE_MS: i64 = 12 * 60 * 60 * 1000;

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct ServiceRow {
    pub id: String,
    pub setlist_id: String,
    pub started_at: i64,
    pub song_ids: Vec<String>,
}

/// The service this setlist is in now, if it started within the merge window.
fn current_service(conn: &Connection, setlist_id: &str, now: i64) -> Result<Option<String>> {
    Ok(conn
        .query_row(
            "SELECT id FROM services WHERE setlist_id = ?1 AND started_at > ?2 \
             ORDER BY started_at DESC LIMIT 1",
            params![setlist_id, now - SERVICE_MERGE_MS],
            |row| row.get(0),
        )
        .optional()?)
}

/// A service start: open a service (or rejoin the one in progress) and record
/// every song the setlist holds now.
pub fn record_service_start(conn: &Connection, setlist_id: &str, now: i64) -> Result<String> {
    let id = match current_service(conn, setlist_id, now)? {
        Some(id) => id,
        None => {
            let id = format!("service_{}", ulid::Ulid::new().to_string().to_lowercase());
            conn.execute(
                "INSERT INTO services (id, setlist_id, started_at) VALUES (?1, ?2, ?3)",
                params![id, setlist_id, now],
            )?;
            id
        }
    };
    conn.execute(
        "INSERT OR IGNORE INTO service_songs (service_id, song_id) \
         SELECT ?1, song_id FROM setlist_items \
         WHERE setlist_id = ?2 AND song_id IS NOT NULL",
        params![id, setlist_id],
    )?;
    Ok(id)
}

/// A song added to a setlist joins the service it's being presented in, if
/// any. Adding songs while planning next week's service records nothing.
pub fn record_song_added(conn: &Connection, setlist_id: &str, song_id: &str, now: i64) -> Result<()> {
    if let Some(id) = current_service(conn, setlist_id, now)? {
        conn.execute(
            "INSERT OR IGNORE INTO service_songs (service_id, song_id) VALUES (?1, ?2)",
            params![id, song_id],
        )?;
    }
    Ok(())
}

/// The most recent `limit` services, newest first, with their songs.
pub fn list_recent_services(db: &Arc<Db>, limit: u32) -> Result<Vec<ServiceRow>> {
    let conn = db.conn().lock().unwrap();
    let mut stmt = conn.prepare(
        "SELECT id, setlist_id, started_at FROM services ORDER BY started_at DESC LIMIT ?1",
    )?;
    let mut rows = stmt
        .query_map(params![limit], |row| {
            Ok(ServiceRow {
                id: row.get(0)?,
                setlist_id: row.get(1)?,
                started_at: row.get(2)?,
                song_ids: Vec::new(),
            })
        })?
        .collect::<Result<Vec<_>, _>>()?;
    let mut songs = conn.prepare("SELECT song_id FROM service_songs WHERE service_id = ?1")?;
    for row in &mut rows {
        row.song_ids = songs
            .query_map(params![row.id], |r| r.get(0))?
            .collect::<Result<Vec<String>, _>>()?;
    }
    Ok(rows)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db::NewSong;
    use crate::setlists;

    fn fresh() -> (tempfile::TempDir, Arc<Db>) {
        let dir = tempfile::tempdir().unwrap();
        let db = Arc::new(Db::open(&dir.path().join("t.db")).unwrap());
        (dir, db)
    }

    fn song(db: &Arc<Db>, title: &str) -> String {
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
    fn a_service_start_records_the_songs_it_held() {
        let (_d, db) = fresh();
        let a = song(&db, "Alpha");
        let b = song(&db, "Beta");
        let sl = setlists::create_setlist(&db, "Sunday").unwrap();
        setlists::add_setlist_item(&db, &sl, &a).unwrap();
        setlists::add_setlist_item(&db, &sl, &b).unwrap();
        setlists::mark_setlist_used(&db, &sl).unwrap();

        let services = list_recent_services(&db, 10).unwrap();
        assert_eq!(services.len(), 1);
        let mut got = services[0].song_ids.clone();
        got.sort();
        let mut want = vec![a, b];
        want.sort();
        assert_eq!(got, want);
    }

    #[test]
    fn restarting_and_adding_mid_service_stay_one_service() {
        let (_d, db) = fresh();
        let a = song(&db, "Alpha");
        let c = song(&db, "Gamma");
        let sl = setlists::create_setlist(&db, "Sunday").unwrap();
        setlists::add_setlist_item(&db, &sl, &a).unwrap();
        setlists::mark_setlist_used(&db, &sl).unwrap();
        setlists::mark_setlist_used(&db, &sl).unwrap();
        setlists::add_setlist_item(&db, &sl, &c).unwrap();

        let services = list_recent_services(&db, 10).unwrap();
        assert_eq!(services.len(), 1);
        assert!(services[0].song_ids.contains(&c));
    }

    #[test]
    fn planning_a_setlist_records_nothing() {
        let (_d, db) = fresh();
        let a = song(&db, "Alpha");
        let sl = setlists::create_setlist(&db, "Next week").unwrap();
        setlists::add_setlist_item(&db, &sl, &a).unwrap();
        assert!(list_recent_services(&db, 10).unwrap().is_empty());
    }

    #[test]
    fn services_more_than_the_window_apart_are_separate_and_newest_first() {
        let (_d, db) = fresh();
        let a = song(&db, "Alpha");
        let sl = setlists::create_setlist(&db, "Sunday").unwrap();
        setlists::add_setlist_item(&db, &sl, &a).unwrap();
        {
            let conn = db.conn().lock().unwrap();
            record_service_start(&conn, &sl, 1_000).unwrap();
            record_service_start(&conn, &sl, 1_000 + SERVICE_MERGE_MS + 1).unwrap();
        }
        let services = list_recent_services(&db, 10).unwrap();
        assert_eq!(services.len(), 2);
        assert!(services[0].started_at > services[1].started_at);
    }

    #[test]
    fn a_deleted_setlist_keeps_its_history() {
        let (_d, db) = fresh();
        let a = song(&db, "Alpha");
        let sl = setlists::create_setlist(&db, "Sunday").unwrap();
        setlists::add_setlist_item(&db, &sl, &a).unwrap();
        setlists::mark_setlist_used(&db, &sl).unwrap();
        setlists::delete_setlist(&db, &sl).unwrap();
        assert_eq!(list_recent_services(&db, 10).unwrap()[0].song_ids, vec![a]);
    }
}
