//! Read the song library and setlists of another presentation app installed
//! on this computer (ProPresenter 22, FreeShow, OpenLP, OpenSong), for the
//! "Bring your songs" import.
//!
//! This side only finds and reads the other app's own files and hands back
//! raw-but-compact data; turning it into songs happens in the UI
//! (`src/lib/otherApps/`), through the same importer as song files.
//!
//! Ground rules (research/spikes/competitor_import/README.md):
//! - `detect` looks only at app bundles and Application Support — never at
//!   ~/Documents, which macOS guards with a permission prompt. `read` runs
//!   only after the person chose to import.
//! - Another app's database is copied into memory and read there, never
//!   opened in place, so a running app is never disturbed.
//! - Nothing is written outside our own data, and nothing leaves the machine.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use anyhow::{anyhow, Context, Result};
use rusqlite::{Connection, OpenFlags};
use serde::Serialize;
use serde_json::{json, Value};

#[derive(Debug, Clone, Serialize)]
pub struct DetectedApp {
    pub id: &'static str,
    pub name: &'static str,
}

/// The apps we can read, in the order the UI offers them.
const APPS: [(&str, &str); 4] = [
    ("propresenter", "ProPresenter"),
    ("freeshow", "FreeShow"),
    ("openlp", "OpenLP"),
    ("opensong", "OpenSong"),
];

fn home() -> PathBuf {
    dirs::home_dir().unwrap_or_default()
}

/// ~/Library/Application Support on macOS, %APPDATA% on Windows.
fn app_data() -> PathBuf {
    dirs::data_dir().unwrap_or_default()
}

fn documents() -> PathBuf {
    dirs::document_dir().unwrap_or_else(|| home().join("Documents"))
}

/// Is `<name>.app` in /Applications or ~/Applications, directly or one
/// folder down ("OpenSong V2.1.2/OpenSong.app")? Listing /Applications needs
/// no permission.
#[cfg(target_os = "macos")]
fn has_bundle(name: &str) -> bool {
    let file = format!("{name}.app");
    [PathBuf::from("/Applications"), home().join("Applications")].iter().any(|dir| {
        if dir.join(&file).exists() {
            return true;
        }
        std::fs::read_dir(dir)
            .map(|entries| entries.flatten().any(|e| e.path().join(&file).exists()))
            .unwrap_or(false)
    })
}

#[cfg(not(target_os = "macos"))]
fn has_bundle(_name: &str) -> bool {
    false
}

fn propresenter_registry() -> PathBuf {
    app_data().join("RenewedVision/ProPresenter/Workspaces/registry-v1.sqlite3")
}

fn openlp_songs_db() -> Option<PathBuf> {
    ["Data", "data"]
        .iter()
        .map(|d| app_data().join("openlp").join(d).join("songs/songs.sqlite"))
        .find(|p| p.exists())
}

fn freeshow_app_data() -> PathBuf {
    app_data().join("freeshow")
}

/// Which supported apps are on this computer. Cheap and prompt-free.
pub fn detect() -> Vec<DetectedApp> {
    let windows = cfg!(target_os = "windows");
    APPS.iter()
        .filter(|(id, name)| match *id {
            "propresenter" => has_bundle(name) || propresenter_registry().exists(),
            "freeshow" => has_bundle(name) || freeshow_app_data().exists(),
            "openlp" => has_bundle(name) || openlp_songs_db().is_some(),
            // Its data lives only in Documents, which is safe to look at on
            // Windows but prompts on macOS.
            "opensong" => has_bundle(name) || (windows && documents().join("OpenSong").exists()),
            _ => false,
        })
        .map(|(id, name)| DetectedApp { id, name })
        .collect()
}

/// Read an app's library. The JSON shape per app is what
/// `src/lib/otherApps/*.ts` expects.
pub fn read(app: &str) -> Result<Value> {
    match app {
        "propresenter" => read_propresenter(),
        "freeshow" => read_freeshow(),
        "openlp" => read_openlp(),
        "opensong" => read_opensong(),
        other => Err(anyhow!("unknown app {other}")),
    }
}

/// A snapshot of another app's SQLite database, in memory. Uses SQLite's
/// own backup, so a database the app has open (WAL and all) copies cleanly.
fn copy_database(path: &Path) -> Result<Connection> {
    let src = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX)
        .with_context(|| format!("opening {}", path.display()))?;
    let mut dst = Connection::open_in_memory()?;
    {
        let backup = rusqlite::backup::Backup::new(&src, &mut dst)?;
        backup.run_to_completion(256, std::time::Duration::from_millis(5), None)?;
    }
    Ok(dst)
}

// --- ProPresenter 22 -----------------------------------------------------------

/// ProPresenter 22 keeps each document as a Loro CRDT snapshot in a SQLite
/// table. Decoded, the documents follow the ProPresenter 7 data model.
fn read_propresenter() -> Result<Value> {
    let registry = propresenter_registry();
    if !registry.exists() {
        return Err(anyhow!(
            "No ProPresenter 22 library found. For older ProPresenter versions, use File → Export in ProPresenter and import the export file."
        ));
    }
    let reg = copy_database(&registry)?;
    let roots: Vec<String> = reg
        .prepare("SELECT root_path FROM workspaces")?
        .query_map([], |r| r.get(0))?
        .collect::<rusqlite::Result<_>>()?;

    let mut presentations = Vec::new();
    let mut playlists = Vec::new();
    for root in roots {
        let db = Path::new(&root).join("DocumentStore/workspace-v1.sqlite3");
        if !db.exists() {
            continue;
        }
        read_propresenter_workspace(&db, &mut presentations, &mut playlists)?;
    }
    Ok(json!({ "presentations": presentations, "playlists": playlists }))
}

/// One workspace database's songs and playlists.
fn read_propresenter_workspace(db: &Path, presentations: &mut Vec<Value>, playlists: &mut Vec<Value>) -> Result<()> {
    let conn = copy_database(db)?;
    // Scope 0 is the shared document; scope 1 holds this machine's view state.
    let mut stmt = conn.prepare(
        "SELECT kind, core_path, snapshot FROM workspace_streams \
         WHERE scope = 0 AND snapshot IS NOT NULL AND kind IN ('Presentation', 'PlaylistLibrary')",
    )?;
    let rows = stmt.query_map([], |r| {
        Ok((r.get::<_, String>(0)?, r.get::<_, Option<String>>(1)?, r.get::<_, Vec<u8>>(2)?))
    })?;
    for row in rows {
        let (kind, path, snapshot) = row?;
        let doc = match decode_loro(&snapshot) {
            Ok(d) => d,
            Err(e) => {
                log::warn!("other_apps: skipping undecodable ProPresenter {kind} {path:?}: {e:#}");
                continue;
            }
        };
        let root = doc.get("root").cloned().unwrap_or(Value::Null);
        if kind == "Presentation" {
            if let Some(path) = path {
                presentations.push(pp_presentation(&path, &root));
            }
        } else {
            pp_collect_playlists(root.get("playlists").unwrap_or(&Value::Null), playlists);
        }
    }
    Ok(())
}

fn decode_loro(bytes: &[u8]) -> Result<Value> {
    let doc = loro::LoroDoc::new();
    doc.import(bytes).map_err(|e| anyhow!("{e}"))?;
    Ok(loro_to_json(&doc.get_deep_value()))
}

fn loro_to_json(v: &loro::LoroValue) -> Value {
    use loro::LoroValue as L;
    match v {
        L::Null => Value::Null,
        L::Bool(b) => Value::Bool(*b),
        L::Double(d) => json!(d),
        L::I64(i) => json!(i),
        // ProPresenter stores slide text as RTF bytes; RTF is 7-bit text.
        L::Binary(b) => Value::String(String::from_utf8_lossy(b).into_owned()),
        L::String(s) => Value::String(s.to_string()),
        L::List(items) => Value::Array(items.iter().map(loro_to_json).collect()),
        L::Map(m) => Value::Object(m.iter().map(|(k, v)| (k.to_string(), loro_to_json(v))).collect()),
        L::Container(_) => Value::Null,
    }
}

fn str_at<'a>(v: &'a Value, path: &[&str]) -> Option<&'a str> {
    path.iter().try_fold(v, |v, k| v.get(*k)).and_then(Value::as_str)
}

/// One presentation, cut down to what a song needs: its groups (sections)
/// with each slide's RTF, its arrangements, and its CCLI facts.
fn pp_presentation(path: &str, root: &Value) -> Value {
    let empty = Vec::new();
    let cues = root.get("cues").and_then(Value::as_object);
    let slide_rtf = |cue_id: &str| -> Vec<String> {
        let Some(cue) = cues.and_then(|c| c.get(cue_id)) else { return vec![] };
        let actions = cue.get("actions").and_then(Value::as_array).unwrap_or(&empty);
        actions
            .iter()
            .filter_map(|a| {
                let slide = a.pointer("/ActionTypeData/slide/Slide/presentation/base_slide")?;
                let elements = slide.get("elements")?.as_array()?;
                Some(elements.iter().filter_map(|e| str_at(e, &["element", "text", "rtf_data"]).map(str::to_owned)))
            })
            .flatten()
            .collect()
    };
    let groups: Vec<Value> = root
        .get("cue_group_placements")
        .and_then(Value::as_array)
        .unwrap_or(&empty)
        .iter()
        .map(|g| {
            let slides: Vec<Value> = g
                .get("children")
                .and_then(Value::as_array)
                .unwrap_or(&empty)
                .iter()
                .filter_map(|c| str_at(c, &["meta", "uuid"]))
                .map(|id| json!(slide_rtf(id)))
                .collect();
            json!({
                "id": str_at(g, &["meta", "uuid"]),
                "name": str_at(g, &["meta", "Kind", "group", "name"]),
                "slides": slides,
            })
        })
        .collect();
    let arrangements: Vec<Value> = root
        .get("arrangements")
        .and_then(Value::as_array)
        .unwrap_or(&empty)
        .iter()
        .map(|a| {
            json!({
                "id": a.get("uuid"),
                "name": a.get("name"),
                "groups": a.get("group_identifiers_v2").or_else(|| a.get("group_identifiers")),
            })
        })
        .collect();
    json!({
        "path": path,
        "ccli": root.get("ccli"),
        "selected_arrangement": root.get("selected_arrangement"),
        "groups": groups,
        "arrangements": arrangements,
    })
}

/// Every playlist in the tree (folders nest), with its presentations in order.
fn pp_collect_playlists(nodes: &Value, out: &mut Vec<Value>) {
    for node in nodes.as_array().into_iter().flatten() {
        let tag = str_at(node, &["meta", "Kind", "__tag"]);
        if tag == Some("playlist") {
            let items: Vec<Value> = node
                .get("children")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(|item| {
                    let p = item.pointer("/meta/Kind/item/ItemType/presentation")?;
                    let doc = str_at(p, &["document_path", "RelativeFilePath", "local", "path"])?;
                    Some(json!({ "document": doc, "arrangement": p.get("arrangement") }))
                })
                .collect();
            out.push(json!({ "name": str_at(node, &["meta", "name"]), "items": items }));
        }
        if let Some(children) = node.get("children") {
            pp_collect_playlists(children, out);
        }
    }
}

// --- FreeShow --------------------------------------------------------------------

fn read_json_file(path: &Path) -> Option<Value> {
    std::fs::read_to_string(path).ok().and_then(|t| serde_json::from_str(&t).ok())
}

fn read_freeshow() -> Result<Value> {
    let config = read_json_file(&freeshow_app_data().join("config.json"));
    let data_path = config
        .as_ref()
        .and_then(|c| c.get("dataPath"))
        .and_then(Value::as_str)
        .map(PathBuf::from)
        .unwrap_or_else(|| documents().join("FreeShow"));
    let shows_dir = config
        .as_ref()
        .and_then(|c| c.get("showsPath"))
        .and_then(Value::as_str)
        .map(PathBuf::from)
        .unwrap_or_else(|| data_path.join("Shows"));
    if !shows_dir.exists() {
        return Err(anyhow!("No FreeShow shows folder found at {}", shows_dir.display()));
    }
    let mut shows = Vec::new();
    for entry in std::fs::read_dir(&shows_dir)?.flatten() {
        let path = entry.path();
        if path.extension().and_then(|e| e.to_str()) != Some("show") {
            continue;
        }
        if let Some(show) = read_json_file(&path) {
            shows.push(show);
        }
    }
    // "Save user settings at data location" moves projects into Config/.
    let projects = read_json_file(&data_path.join("Config/projects.json"))
        .or_else(|| read_json_file(&freeshow_app_data().join("projects.json")));
    Ok(json!({ "shows": shows, "projects": projects }))
}

// --- OpenLP ------------------------------------------------------------------------

fn read_openlp() -> Result<Value> {
    let path = openlp_songs_db().ok_or_else(|| anyhow!("No OpenLP song database found"))?;
    read_openlp_db(&path)
}

fn read_openlp_db(path: &Path) -> Result<Value> {
    let conn = copy_database(path)?;
    let mut authors: HashMap<i64, Vec<String>> = HashMap::new();
    {
        let mut stmt = conn.prepare(
            "SELECT x.song_id, a.display_name FROM authors_songs x JOIN authors a ON a.id = x.author_id ORDER BY x.rowid",
        )?;
        for row in stmt.query_map([], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?)))? {
            let (song, name) = row?;
            authors.entry(song).or_default().push(name);
        }
    }
    let mut stmt =
        conn.prepare("SELECT id, title, lyrics, verse_order, ccli_number, copyright FROM songs ORDER BY title")?;
    let songs = stmt
        .query_map([], |r| {
            Ok(json!({
                "id": r.get::<_, i64>(0)?,
                "title": r.get::<_, String>(1)?,
                "lyrics": r.get::<_, String>(2)?,
                "verse_order": r.get::<_, Option<String>>(3)?,
                "ccli_number": r.get::<_, Option<String>>(4)?,
                "copyright": r.get::<_, Option<String>>(5)?,
            }))
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?
        .into_iter()
        .map(|mut s| {
            let id = s["id"].as_i64().unwrap_or_default();
            s["authors"] = json!(authors.remove(&id).unwrap_or_default());
            s
        })
        .collect::<Vec<_>>();
    Ok(json!({ "songs": songs }))
}

// --- OpenSong ----------------------------------------------------------------------

/// Every file under `dir`, as (path relative to `dir`, text, modified time in
/// ms — a set's date). OpenSong song and set files have no extension.
fn read_tree(dir: &Path) -> Vec<Value> {
    let mut out = Vec::new();
    let mut stack = vec![dir.to_path_buf()];
    while let Some(d) = stack.pop() {
        for entry in std::fs::read_dir(&d).into_iter().flatten().flatten() {
            let path = entry.path();
            let name = entry.file_name().to_string_lossy().into_owned();
            if name.starts_with('.') {
                continue;
            }
            if path.is_dir() {
                stack.push(path);
            } else if let Ok(bytes) = std::fs::read(&path) {
                if bytes.len() > 512 * 1024 {
                    continue; // not a song; OpenSong songs are a few KB
                }
                let rel = path.strip_prefix(dir).unwrap_or(&path).to_string_lossy().replace('\\', "/");
                let modified = entry
                    .metadata()
                    .and_then(|m| m.modified())
                    .ok()
                    .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                    .map(|d| d.as_millis() as u64);
                out.push(json!({ "path": rel, "text": String::from_utf8_lossy(&bytes), "modified": modified }));
            }
        }
    }
    out
}

fn read_opensong() -> Result<Value> {
    let root = documents().join("OpenSong");
    if !root.join("Songs").exists() {
        return Err(anyhow!("No OpenSong folder found at {}", root.display()));
    }
    Ok(json!({
        "songs": read_tree(&root.join("Songs")),
        "sets": read_tree(&root.join("Sets")),
    }))
}

// --- Commands ------------------------------------------------------------------

#[tauri::command]
pub async fn detect_other_apps() -> Vec<DetectedApp> {
    tauri::async_runtime::spawn_blocking(detect).await.unwrap_or_default()
}

#[tauri::command]
pub async fn read_other_app(app: String) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || read(&app))
        .await
        .map_err(|e| e.to_string())?
        .map_err(|e| format!("{e:#}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixtures() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../data/fixtures/other_apps")
    }

    /// Compare with the committed `read.json` the UI tests read
    /// (`src/__tests__/otherApps.test.ts`); `UPDATE_FIXTURES=1` rewrites it.
    fn check_fixture(path: PathBuf, got: &Value) {
        if std::env::var("UPDATE_FIXTURES").is_ok() {
            std::fs::write(&path, serde_json::to_string_pretty(got).unwrap()).unwrap();
        }
        let want: Value = serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
        assert_eq!(&want, got, "{} is stale — rerun with UPDATE_FIXTURES=1", path.display());
    }

    /// The captured ProPresenter 22 sample decodes to three songs with their
    /// verses and the playlist that runs them (with one arrangement).
    #[test]
    fn reads_propresenter_sample() {
        let (mut presentations, mut playlists) = (Vec::new(), Vec::new());
        let db = fixtures().join("propresenter22/workspace-v1.sqlite3");
        read_propresenter_workspace(&db, &mut presentations, &mut playlists).unwrap();
        presentations.sort_by(|a, b| a["path"].as_str().cmp(&b["path"].as_str()));
        assert_eq!(presentations.len(), 3);
        let grace = presentations.iter().find(|p| p["path"] == "Default/Amazing Grace").unwrap();
        let groups = grace["groups"].as_array().unwrap();
        assert_eq!(groups.len(), 4);
        assert_eq!(groups[0]["name"], "Verse 1");
        assert!(groups[0]["slides"][0][0].as_str().unwrap().contains("Amazing grace how sweet"));
        assert_eq!(grace["arrangements"][0]["name"], "Short");
        assert_eq!(grace["arrangements"][0]["groups"].as_array().unwrap().len(), 5);
        let playlist = playlists.iter().find(|p| p["name"] == "Playlist").unwrap();
        assert_eq!(playlist["items"].as_array().unwrap().len(), 3);
        check_fixture(
            fixtures().join("propresenter22/read.json"),
            &json!({ "presentations": presentations, "playlists": playlists }),
        );
    }

    #[test]
    fn reads_openlp_sample() {
        let got = read_openlp_db(&fixtures().join("openlp/songs.sqlite")).unwrap();
        let songs = got["songs"].as_array().unwrap();
        assert_eq!(songs.len(), 3);
        let vision = songs.iter().find(|s| s["title"] == "Be Thou My Vision").unwrap();
        assert_eq!(vision["authors"], json!(["Irish Folk Hymn", "Elanor H. Hull"]));
        check_fixture(fixtures().join("openlp/read.json"), &got);
    }
}
