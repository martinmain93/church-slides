//! The open library's display look (migration 013): one row, so everyone
//! showing an organization's library projects the same look, and it syncs
//! with the library. The window keeps a copy so the first frame draws in it.

use std::sync::Arc;

use rusqlite::{params, OptionalExtension};
use serde::Serialize;

use crate::db::Db;

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct LibraryLook {
    /// The display look, as JSON.
    pub display: Option<String>,
    /// Scripture's own look, as JSON; none means it wears the display look.
    pub scripture: Option<String>,
}

pub fn get(db: &Db) -> anyhow::Result<Option<LibraryLook>> {
    let conn = db.conn().lock().unwrap();
    Ok(conn
        .query_row("SELECT display, scripture FROM library_look WHERE id = 'default'", [], |r| {
            Ok(LibraryLook { display: r.get(0)?, scripture: r.get(1)? })
        })
        .optional()?)
}

pub fn set(db: &Db, look: &LibraryLook) -> anyhow::Result<()> {
    let conn = db.conn().lock().unwrap();
    // Unchanged is left alone, so it isn't sent anywhere.
    let current: Option<(Option<String>, Option<String>)> = conn
        .query_row("SELECT display, scripture FROM library_look WHERE id = 'default'", [], |r| Ok((r.get(0)?, r.get(1)?)))
        .optional()?;
    if current.as_ref() == Some(&(look.display.clone(), look.scripture.clone())) {
        return Ok(());
    }
    conn.execute(
        "INSERT INTO library_look (id, display, scripture, updated_at) VALUES ('default', ?1, ?2, ?3) \
         ON CONFLICT (id) DO UPDATE SET display = excluded.display, scripture = excluded.scripture, updated_at = excluded.updated_at",
        params![look.display, look.scripture, crate::db::unix_ms()],
    )?;
    Ok(())
}

#[tauri::command]
pub async fn get_library_look(state: tauri::State<'_, Arc<Db>>) -> Result<Option<LibraryLook>, String> {
    get(&state).map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn set_library_look(
    state: tauri::State<'_, Arc<Db>>,
    display: Option<String>,
    scripture: Option<String>,
) -> Result<(), String> {
    for json in [&display, &scripture].into_iter().flatten() {
        if json.len() > 4096 || serde_json::from_str::<serde_json::Value>(json).is_err() {
            return Err("That isn't a look.".into());
        }
    }
    set(&state, &LibraryLook { display, scripture }).map_err(|e| e.to_string())
}
