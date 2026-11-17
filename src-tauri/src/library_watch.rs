//! Tell the UI when *another process* changed the library — today that's an
//! AI assistant working through the `--mcp` server.
//!
//! SQLite's `data_version` pragma changes whenever a different connection
//! commits to the file, and never for this connection's own writes. So the
//! app's own edits (which the UI already knows about) don't echo, and the
//! watcher needs no knowledge of who the other writer is.

use std::sync::Arc;
use std::time::Duration;

use tauri::Emitter;

use crate::db::Db;

pub const LIBRARY_CHANGED_EVENT: &str = "library-changed";

const POLL: Duration = Duration::from_millis(1000);

pub fn spawn(app: tauri::AppHandle, db: Arc<Db>) {
    tauri::async_runtime::spawn(async move {
        let mut last = data_version(&db);
        loop {
            tokio::time::sleep(POLL).await;
            let now = data_version(&db);
            if now.is_some() && now != last {
                last = now;
                let _ = app.emit(LIBRARY_CHANGED_EVENT, ());
            }
        }
    });
}

fn data_version(db: &Db) -> Option<i64> {
    let conn = db.conn().lock().ok()?;
    conn.query_row("PRAGMA data_version", [], |r| r.get(0)).ok()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db::NewSong;

    #[test]
    fn data_version_moves_only_for_other_connections() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("t.db");
        let app = Db::open(&path).unwrap();
        let assistant = Db::open(&path).unwrap();
        let song = |t: &str| NewSong {
            title: t.into(),
            body_text: "[v]\nx".into(),
            ccli: None,
            author: None,
            copyright: None,
            default_tempo_wps: None,
            slide_order: None,
        };

        let v0 = data_version(&app);
        app.create_song(song("mine")).unwrap();
        assert_eq!(data_version(&app), v0, "own writes don't echo");
        assistant.create_song(song("theirs")).unwrap();
        assert_ne!(data_version(&app), v0, "another process's write is seen");
    }
}
