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
    // The organizations this person is in: named as the server names them,
    // and listed before their library is downloaded (opening one downloads it).
    if let Some(app_data) = home_path.parent() {
        for m in crate::account::memberships(app_data) {
            if org_path(&home_path, &m.org_id).is_err() {
                continue;
            }
            match orgs.iter_mut().find(|o| o.id == m.org_id) {
                Some(o) => o.name = m.org_name,
                None => orgs.push(LibraryInfo { id: m.org_id, name: m.org_name, kind: "organization", open: false }),
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
        // An organization this person is in, opened for the first time: an
        // empty file, which sync fills.
        let member = db
            .home_path()
            .parent()
            .map(crate::account::memberships)
            .unwrap_or_default()
            .into_iter()
            .find(|m| m.org_id == id);
        match member {
            Some(m) => create(db, id, &m.org_name)?,
            None => bail!("there's no library {id:?} on this computer"),
        }
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

// --- Moving this computer's library into an organization ----------------------
//
// Most churches already have their songs on the slides computer. Its owner
// can make them the organization's, once: the library MOVES (songs, charts,
// scripture links, services, history, its look and learned timing), and this
// computer's own library starts empty. A backup of it is kept beside it.

fn library_is_empty(path: &Path) -> bool {
    if !path.exists() {
        return true;
    }
    let Ok(conn) = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY) else { return false };
    conn.query_row(
        "SELECT NOT EXISTS(SELECT 1 FROM songs) AND NOT EXISTS(SELECT 1 FROM setlists) AND NOT EXISTS(SELECT 1 FROM readings)",
        [],
        |r| r.get::<_, bool>(0),
    )
    .unwrap_or(false)
}

/// What this computer can check by itself: it has songs of its own, and the
/// organization's library here is empty.
pub fn can_move_here(db: &Db, org_id: &str) -> bool {
    org_path(db.home_path(), org_id).is_ok_and(|p| library_is_empty(&p)) && home_has_own_songs(db)
}

fn remove_db_files(path: &Path) {
    for suffix in ["", "-wal", "-shm", "-journal"] {
        let _ = std::fs::remove_file(format!("{}{suffix}", path.display()));
    }
}

/// Move this computer's own library into organization `org_id` and open it.
pub fn move_home_into(db: &Db, org_id: &str, name: &str) -> Result<()> {
    let home_path = db.home_path().to_path_buf();
    let dir = home_path.parent().context("library has no folder")?.to_path_buf();
    let target = org_path(&home_path, org_id)?;
    if !library_is_empty(&target) {
        bail!("{name} already has songs on this computer.");
    }
    // The organization's (empty) file may be the one open: let it go.
    open(db, HOME_ID)?;
    let stamp = chrono_stamp();
    let backup = dir.join(format!("library-before-move-{stamp}.db"));
    let moving = target.with_file_name("library.db.moving");
    std::fs::create_dir_all(target.parent().context("no folder")?)?;
    let _ = std::fs::remove_file(&moving);
    {
        let home = db.home();
        home.execute("VACUUM INTO ?1", [backup.to_string_lossy()])
            .context("couldn't back up this computer's library")?;
        home.execute("VACUUM INTO ?1", [moving.to_string_lossy()])?;
    }
    {
        // This computer's settings stay with this computer.
        let copy = Connection::open(&moving)?;
        copy.execute("DELETE FROM settings", [])?;
        copy.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?1, ?2, ?3)",
            params![NAME_KEY, serde_json::to_string(name)?, unix_ms()],
        )?;
    }
    remove_db_files(&target);
    std::fs::rename(&moving, &target)?;
    // Learned timing goes with the songs it was learned on.
    let learning = dir.join("learning.db");
    if learning.exists() {
        let to = crate::library_sync::local::learning_path(&target);
        remove_db_files(&to);
        std::fs::rename(&learning, &to)?;
    }
    open(db, org_id)?;
    let moved: i64 = db.conn().lock().unwrap().query_row("SELECT COUNT(*) FROM songs", [], |r| r.get(0))?;
    let had: i64 = db.home().query_row("SELECT COUNT(*) FROM songs", [], |r| r.get(0))?;
    if moved != had {
        bail!("the move didn't copy every song ({moved} of {had}); this computer's library is unchanged");
    }
    db.home().execute_batch(
        "BEGIN;
         DELETE FROM setlist_items; DELETE FROM setlists;
         DELETE FROM service_songs; DELETE FROM services;
         DELETE FROM song_charts; DELETE FROM song_scriptures; DELETE FROM song_references;
         DELETE FROM songs; DELETE FROM readings;
         COMMIT;",
    )?;
    log::info!("moved this computer's library ({had} songs) into {org_id}; backup at {}", backup.display());
    Ok(())
}

fn chrono_stamp() -> String {
    format!("{}", unix_ms())
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
    app.state::<Arc<crate::library_sync::LibrarySync>>().kick();
    Ok(())
}

/// The organization's role for this person, as last heard from the server.
fn role_in(app: &tauri::AppHandle, org_id: &str) -> Option<String> {
    use tauri::Manager;
    let app_data = app.path().app_data_dir().ok()?;
    crate::account::memberships(&app_data).into_iter().find(|m| m.org_id == org_id).map(|m| m.role)
}

/// Whether the server's copy of the organization's library is empty.
async fn server_library_empty(app: &tauri::AppHandle, org_id: &str) -> Result<bool, String> {
    use tauri::Manager;
    let app_data = app.path().app_data_dir().map_err(|e| e.to_string())?;
    let page = crate::account::api(&app_data, "GET", &format!("/orgs/{org_id}/sync/pull?after=0"), None)
        .await
        .map_err(|e| format!("{e:#}"))?;
    Ok(page.get("records").and_then(serde_json::Value::as_array).is_some_and(|r| r.is_empty()))
}

/// Offer "Use this computer's library" for `org_id`: its owner, songs here,
/// and nothing in the organization's library yet (here or on the server).
#[tauri::command]
pub async fn can_move_library(app: tauri::AppHandle, state: tauri::State<'_, Arc<Db>>, org_id: String) -> Result<bool, String> {
    if role_in(&app, &org_id).as_deref() != Some("owner") || !can_move_here(&state, &org_id) {
        return Ok(false);
    }
    Ok(server_library_empty(&app, &org_id).await.unwrap_or(false))
}

#[tauri::command]
pub async fn move_library_into(app: tauri::AppHandle, state: tauri::State<'_, Arc<Db>>, org_id: String) -> Result<(), String> {
    use tauri::Manager;
    if app.state::<crate::keep_awake::KeepAwake>().presenting() {
        return Err("Finish the service before moving the library.".into());
    }
    if role_in(&app, &org_id).as_deref() != Some("owner") {
        return Err("Only the organization's owner can move a library into it.".into());
    }
    if !server_library_empty(&app, &org_id).await? {
        return Err("The organization already has songs, so this library can't be moved into it.".into());
    }
    let app_data = app.path().app_data_dir().map_err(|e| e.to_string())?;
    let name = crate::account::memberships(&app_data)
        .into_iter()
        .find(|m| m.org_id == org_id)
        .map(|m| m.org_name)
        .unwrap_or_else(|| "Organization".into());
    let db = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || move_home_into(&db, &org_id, &name))
        .await
        .map_err(|e| e.to_string())?
        .map_err(|e| format!("{e:#}"))?;
    let _ = app.emit(crate::library_watch::LIBRARY_CHANGED_EVENT, ());
    app.state::<Arc<crate::library_sync::LibrarySync>>().kick();
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
