//! Which libraries this computer has, and which one is open.
//!
//! Each library is its own SQLite file (docs/roadmap/LIBRARIES_AND_SYNC.md):
//!
//! ```text
//! <app data>/library.db                 this computer's own library + settings
//! <app data>/orgs/<org id>/library.db   one per organization
//! ```
//!
//! The open library is remembered in this computer's settings as the path
//! of its file relative to the app's folder (`""` for its own library), so
//! the engine and the AI-assistant (`--mcp`) process find the same file
//! without asking the app.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use anyhow::{bail, Context, Result};
use rusqlite::{params, Connection, OpenFlags, OptionalExtension};
use serde::Serialize;
use tauri::Emitter;

use crate::db::{Db, HomeConn};

/// Settings key (in the home file) naming the open library's file.
pub const OPEN_KEY: &str = "open_library";
/// Settings key (in an organization's file) holding its display name.
const NAME_KEY: &str = "library_name";
/// The id of this computer's own library.
pub const HOME_ID: &str = "this-computer";

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct LibraryInfo {
    pub id: String,
    pub name: String,
    /// "computer" or "organization".
    pub kind: &'static str,
    pub open: bool,
}

/// Where an organization's library lives. Ids come from the server; anything
/// outside a plain id alphabet is refused rather than turned into a path.
pub fn org_path(home_path: &Path, org_id: &str) -> Result<PathBuf> {
    if org_id.is_empty()
        || org_id == HOME_ID
        || !org_id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
    {
        bail!("not a library id: {org_id:?}");
    }
    let dir = home_path.parent().context("library has no folder")?;
    Ok(dir.join("orgs").join(org_id).join("library.db"))
}

fn relative(home_path: &Path, path: &Path) -> String {
    let dir = home_path.parent().unwrap_or(Path::new(""));
    if path == home_path {
        return String::new();
    }
    path.strip_prefix(dir)
        .map(|p| p.to_string_lossy().replace('\\', "/"))
        .unwrap_or_default()
}

/// The file of the library that was open last, read straight from the home
/// file. Falls back to the home library when nothing's recorded or the file
/// has gone.
pub fn remembered_path(home_path: &Path) -> PathBuf {
    let read = || -> rusqlite::Result<Option<String>> {
        let conn = Connection::open_with_flags(
            home_path,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )?;
        conn.busy_timeout(std::time::Duration::from_secs(1))?;
        conn.query_row("SELECT value FROM settings WHERE key = ?1", params![OPEN_KEY], |r| {
            r.get::<_, String>(0)
        })
        .optional()
    };
    let rel = read()
        .ok()
        .flatten()
        .and_then(|raw| serde_json::from_str::<String>(&raw).ok())
        .unwrap_or_default();
    resolve(home_path, &rel)
}

fn resolve(home_path: &Path, rel: &str) -> PathBuf {
    // Only ever `orgs/<id>/library.db`: anything else (absolute, `..`, a
    // drive letter) opens this computer's own library.
    let parts: Vec<&str> = rel.split('/').collect();
    let well_formed = matches!(parts.as_slice(), ["orgs", id, "library.db"] if org_path(home_path, id).is_ok());
    if !well_formed {
        return home_path.to_path_buf();
    }
    let path = home_path.parent().unwrap_or(Path::new("")).join(rel);
    if path.exists() {
        path
    } else {
        home_path.to_path_buf()
    }
}

fn remember(home: &HomeConn<'_>, rel: &str) -> Result<()> {
    home.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?1, ?2, ?3) \
         ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        params![OPEN_KEY, serde_json::to_string(rel)?, unix_ms()],
    )?;
    Ok(())
}

fn org_name(path: &Path) -> Option<String> {
    let conn = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY).ok()?;
    conn.query_row("SELECT value FROM settings WHERE key = ?1", params![NAME_KEY], |r| {
        r.get::<_, String>(0)
    })
    .ok()
    .and_then(|raw| serde_json::from_str::<String>(&raw).ok())
}

/// Songs in this computer's own library that aren't the example hymns every
/// new library starts with.
fn home_has_own_songs(db: &Db) -> bool {
    let home = db.home();
    let ids: Vec<String> = crate::seed_hymns::SEED_HYMNS.iter().map(|h| h.id.to_string()).collect();
    let placeholders = vec!["?"; ids.len()].join(",");
    home.query_row(
        &format!("SELECT EXISTS(SELECT 1 FROM songs WHERE id NOT IN ({placeholders}))"),
        rusqlite::params_from_iter(ids.iter()),
        |r| r.get::<_, bool>(0),
    )
    .unwrap_or(true)
}

/// Every library on this computer. This computer's own library is left out
/// once there's an organization's library and it holds nothing but the
/// example hymns — someone who only uses their church's songs never sees a
/// second library (unless it's the one open).
pub fn list(db: &Db) -> Vec<LibraryInfo> {
    let open = db.open_path();
    let home_path = db.home_path().to_path_buf();
    let mut orgs = Vec::new();
    if let Some(dir) = home_path.parent().map(|d| d.join("orgs")) {
        if let Ok(entries) = std::fs::read_dir(&dir) {
            for entry in entries.flatten() {
                let path = entry.path().join("library.db");
                let Some(id) = entry.file_name().to_str().map(str::to_string) else { continue };
                if !path.exists() || org_path(&home_path, &id).is_err() {
                    continue;
                }
                orgs.push(LibraryInfo {
                    name: org_name(&path).unwrap_or_else(|| "Organization".into()),
                    open: path == open,
                    id,
                    kind: "organization",
                });
            }
        }
    }
    orgs.sort_by(|a, b| a.name.to_lowercase().cmp(&b.name.to_lowercase()));
    let home_open = open == home_path;
    let mut out = Vec::new();
    if orgs.is_empty() || home_open || home_has_own_songs(db) {
        out.push(LibraryInfo {
            id: HOME_ID.into(),
            name: "This computer".into(),
            kind: "computer",
            open: home_open,
        });
    }
    out.extend(orgs);
    out
}

fn path_for(db: &Db, id: &str) -> Result<PathBuf> {
    if id == HOME_ID {
        return Ok(db.home_path().to_path_buf());
    }
    let path = org_path(db.home_path(), id)?;
    if !path.exists() {
        bail!("there's no library {id:?} on this computer");
    }
    Ok(path)
}

/// Open library `id` and remember it for next time.
pub fn open(db: &Db, id: &str) -> Result<()> {
    let path = path_for(db, id)?;
    let previous = relative(db.home_path(), &db.open_path());
    // Recorded first: the engine and the assistant read it, so the app must
    // never show a library they don't.
    remember(&db.home(), &relative(db.home_path(), &path))?;
    if let Err(e) = db.switch_to(&path) {
        let _ = remember(&db.home(), &previous);
        return Err(e);
    }
    log::info!("opened library at {}", path.display());
    Ok(())
}

/// Make (or rename) an organization's library, without opening it.
pub fn create(db: &Db, id: &str, name: &str) -> Result<()> {
    let path = org_path(db.home_path(), id)?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    // Open through `Db` so the file gets the same migrations as any library.
    let made = crate::db::open_library_file(&path)?;
    made.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?1, ?2, ?3) \
         ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        params![NAME_KEY, serde_json::to_string(name)?, unix_ms()],
    )?;
    Ok(())
}

fn unix_ms() -> i64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
}

// --- Commands ------------------------------------------------------------------

#[tauri::command]
pub async fn list_libraries(state: tauri::State<'_, Arc<Db>>) -> Result<Vec<LibraryInfo>, String> {
    Ok(list(&state))
}

/// Switch to another library. Every window reloads its songs and services
/// on `library-changed`, as it does after an AI assistant's edit.
#[tauri::command]
pub async fn open_library(
    app: tauri::AppHandle,
    state: tauri::State<'_, Arc<Db>>,
    id: String,
) -> Result<(), String> {
    use tauri::Manager;
    // A service reads the open library all the way through (edits, history,
    // the leader's song list, the engine's search): it can't change under it.
    if app.state::<crate::keep_awake::KeepAwake>().presenting() {
        return Err("Finish the service before switching libraries.".into());
    }
    open(&state, &id).map_err(|e| e.to_string())?;
    let _ = app.emit(crate::library_watch::LIBRARY_CHANGED_EVENT, ());
    Ok(())
}

/// Development builds only, until organization sync makes these for real.
#[tauri::command]
pub async fn create_library(
    app: tauri::AppHandle,
    state: tauri::State<'_, Arc<Db>>,
    id: String,
    name: String,
) -> Result<(), String> {
    if !cfg!(debug_assertions) {
        return Err("Not available in this build.".into());
    }
    create(&state, &id, &name).map_err(|e| e.to_string())?;
    let _ = app.emit(crate::library_watch::LIBRARY_CHANGED_EVENT, ());
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db::NewSong;

    fn song(t: &str) -> NewSong {
        NewSong {
            title: t.into(),
            body_text: "[v]\nx".into(),
            ccli: None,
            author: None,
            copyright: None,
            default_tempo_wps: None,
            slide_order: None,
        }
    }

    fn titles(db: &Db) -> Vec<String> {
        let mut t: Vec<String> = db.list_songs().unwrap().into_iter().map(|s| s.title).collect();
        t.sort();
        t
    }

    #[test]
    fn libraries_are_separate_files_and_settings_stay_home() {
        let dir = tempfile::tempdir().unwrap();
        let home = dir.path().join("library.db");
        let db = Arc::new(Db::open(&home).unwrap());
        db.create_song(song("Mine")).unwrap();
        let mut s = crate::settings::get(&db).unwrap();
        s.default_tempo_wps = 3.5;
        crate::settings::set(&db, &s).unwrap();

        create(&db, "org-1", "Grace Church").unwrap();
        open(&db, "org-1").unwrap();
        assert!(titles(&db).is_empty(), "an organization's library starts empty");
        db.create_song(song("Theirs")).unwrap();
        // Settings still come from (and go to) the home file.
        assert_eq!(crate::settings::get(&db).unwrap().default_tempo_wps, 3.5);

        open(&db, HOME_ID).unwrap();
        assert!(titles(&db).contains(&"Mine".to_string()));
        assert!(!titles(&db).contains(&"Theirs".to_string()));

        open(&db, "org-1").unwrap();
        assert_eq!(titles(&db), vec!["Theirs".to_string()]);
        // Remembered for the next start (and for the engine and --mcp).
        assert_eq!(remembered_path(&home), org_path(&home, "org-1").unwrap());
    }

    #[test]
    fn this_computer_hides_when_it_only_has_the_examples() {
        let dir = tempfile::tempdir().unwrap();
        let home = dir.path().join("library.db");
        let db = Db::open(&home).unwrap();
        assert_eq!(list(&db).len(), 1, "one library: no switcher");

        create(&db, "org-1", "Grace Church").unwrap();
        open(&db, "org-1").unwrap();
        let shown = list(&db);
        assert_eq!(shown.len(), 1);
        assert_eq!(shown[0].name, "Grace Church");
        assert!(shown[0].open);

        open(&db, HOME_ID).unwrap();
        db.create_song(song("Mine")).unwrap();
        open(&db, "org-1").unwrap();
        let names: Vec<_> = list(&db).into_iter().map(|l| l.name).collect();
        assert_eq!(names, vec!["This computer", "Grace Church"]);
    }

    #[test]
    fn an_assistant_is_told_when_the_app_switches_library() {
        let dir = tempfile::tempdir().unwrap();
        let home = dir.path().join("library.db");
        let app = Db::open(&home).unwrap();
        create(&app, "org-1", "Grace Church").unwrap();
        let assistant = crate::mcp::tools::Library::new(Arc::new(Db::open(&home).unwrap()), None);
        let args = serde_json::json!({});
        assert!(assistant.call("list_setlists", &args).is_ok());

        open(&app, "org-1").unwrap();
        let err = assistant.call("list_setlists", &args).unwrap_err().to_string();
        assert!(err.contains("switched to another library"), "{err}");
        // Told once; after that it works on the library that's open.
        assert!(assistant.call("list_setlists", &args).is_ok());
    }

    #[test]
    fn library_ids_never_become_other_paths() {
        let home = Path::new("/data/library.db");
        for bad in ["", "..", "../x", "a/b", "this-computer", "a b"] {
            assert!(org_path(home, bad).is_err(), "{bad:?}");
        }
        assert_eq!(resolve(home, "../../etc/passwd"), home);
        assert_eq!(resolve(home, "/etc/library.db"), home);
        assert_eq!(resolve(home, "C:\\x\\library.db"), home);
        assert_eq!(resolve(home, "orgs/../x/library.db"), home);
        assert_eq!(resolve(home, "orgs/gone/library.db"), home, "a missing file opens home");
    }
}
