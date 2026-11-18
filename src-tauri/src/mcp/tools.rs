//! The library tools an outside AI (Claude Desktop, Cowork, Cursor, …) can
//! call: search and edit songs, build and edit setlists.
//!
//! Everything here goes through `Db` and the public `setlists` API, the same
//! functions the UI's Tauri commands use — this module adds no storage of its
//! own. Its shapes are deliberately *not* the UI's: rows are trimmed to what a
//! model needs to decide (no timestamps in ms, no body text in listings), and
//! a setlist is edited by stating its whole song list rather than item by
//! item, which is one call a model gets right instead of five it can get
//! half right.

use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use anyhow::{anyhow, bail, Context, Result};
use rusqlite::params;
use serde_json::{json, Map, Value};

use crate::db::{Db, NewSong, SongRow};
use crate::charts::{self, ChartMeta};
use crate::setlists;

use super::app_link;
use super::matching;

/// How many pre-AI snapshots to keep before pruning the oldest.
const BACKUPS_KEPT: usize = 10;

/// A client like Claude Desktop keeps one server process alive across many
/// conversations, so "first change of the process" isn't "first change of
/// the conversation". A change after this long idle takes a fresh snapshot.
const SNAPSHOT_STALE_AFTER: Duration = Duration::from_secs(30 * 60);

/// Tool implementations over one open library.
pub struct Library {
    db: Arc<Db>,
    /// Where to snapshot the library before the first change of a session.
    /// `None` skips snapshots (tests).
    backup_dir: Option<PathBuf>,
    /// The latest snapshot, and when the library last changed after it.
    backup: Mutex<Option<(PathBuf, Instant)>>,
    /// The running app's data folder, for work handed to the app (imports).
    app_data_dir: Option<PathBuf>,
}

impl Library {
    pub fn new(db: Arc<Db>, backup_dir: Option<PathBuf>) -> Self {
        Self {
            db,
            backup_dir,
            backup: Mutex::new(None),
            app_data_dir: None,
        }
    }

    pub fn with_app(mut self, app_data_dir: Option<PathBuf>) -> Self {
        self.app_data_dir = app_data_dir;
        self
    }

    /// Run a tool. `Err` is a failed call the model should see and recover
    /// from (bad id, missing field) — the server reports it as `isError`.
    pub fn call(&self, name: &str, args: &Value) -> Result<Value> {
        // Work on whichever library the app has open now: the person may
        // have switched since this assistant session started.
        let open = crate::libraries::remembered_path(self.db.home_path());
        if open != self.db.open_path() {
            self.db.switch_to(&open)?;
            // A snapshot of the other library doesn't cover this one.
            *self.backup.lock().unwrap() = None;
        }
        let args = args.as_object().cloned().unwrap_or_default();
        match name {
            "list_songs" => self.list_songs(&args),
            "find_songs" => self.find_songs(&args),
            "find_song_files" => self.find_song_files(&args),
            "get_song" => self.get_song(&args),
            "create_song" => self.create_song(&args),
            "update_song" => self.update_song(&args),
            "delete_song" => self.delete_song(&args),
            "list_setlists" => self.list_setlists(),
            "get_setlist" => self.get_setlist(&args),
            "create_setlist" => self.create_setlist(&args),
            "update_setlist" => self.update_setlist(&args),
            "delete_setlist" => self.delete_setlist(&args),
            "add_scripture_to_setlist" => self.add_scripture_to_setlist(&args),
            "add_text_to_setlist" => self.add_text_to_setlist(&args),
            "set_song_key_for_setlist" => self.set_song_key_for_setlist(&args),
            "import_song_files" => self.import_song_files(&args),
            other => bail!("unknown tool: {other}"),
        }
    }

    // --- Songs ---------------------------------------------------------------

    fn list_songs(&self, args: &Map<String, Value>) -> Result<Value> {
        let query = opt_str(args, "query")?.unwrap_or_default();
        let limit = args
            .get("limit")
            .and_then(Value::as_u64)
            .unwrap_or(50)
            .clamp(1, 200) as usize;
        let offset = args.get("offset").and_then(Value::as_u64).unwrap_or(0) as usize;
        let has_chords = args.get("has_chords").and_then(Value::as_bool);
        let key = opt_str(args, "key")?.map(|k| k.trim().to_lowercase()).filter(|k| !k.is_empty());
        let full = opt_str(args, "detail")?.as_deref() == Some("full");

        let usage = song_usage(&self.db)?;
        let charts = charts::list_chart_meta(&self.db)?;
        let rows: Vec<SongRow> = self
            .db
            .search_songs(&query)?
            .into_iter()
            .filter(|s| {
                let chart = charts.get(&s.id);
                has_chords.is_none_or(|want| chart.is_some_and(|c| c.has_chords) == want)
                    && key.as_ref().is_none_or(|k| {
                        chart.and_then(|c| c.key.as_deref()).is_some_and(|ck| ck.to_lowercase() == *k)
                    })
            })
            .collect();
        let total = rows.len();
        let songs: Vec<Value> = rows
            .iter()
            .skip(offset)
            .take(limit)
            .map(|s| {
                let mut v = song_summary(s, usage.get(&s.id), charts.get(&s.id));
                if !full {
                    // The whole library in one answer is big; sections and
                    // slide order are for when a song is being looked at.
                    for field in ["sections", "slide_order", "ccli"] {
                        v.as_object_mut().map(|o| o.remove(field));
                    }
                }
                v
            })
            .collect();
        let next = offset + songs.len();
        Ok(json!({
            "total_matches": total,
            "offset": offset,
            "next_offset": if next < total { json!(next) } else { Value::Null },
            "songs": songs,
        }))
    }

    fn get_song(&self, args: &Map<String, Value>) -> Result<Value> {
        let id = req_str(args, "id")?;
        let song = self
            .db
            .get_song(&id)?
            .ok_or_else(|| anyhow!("no song with id {id}"))?;
        let usage = song_usage(&self.db)?;
        let charts = charts::list_chart_meta(&self.db)?;
        let mut out = song_summary(&song, usage.get(&song.id), charts.get(&song.id));
        out["body_text"] = json!(song.body_text);
        out["copyright"] = json!(song.copyright);
        Ok(out)
    }

    /// A whole pasted list in one call: for each song the user named, the
    /// library songs it could be, best first.
    fn find_songs(&self, args: &Map<String, Value>) -> Result<Value> {
        let asked = args
            .get("songs")
            .and_then(Value::as_array)
            .filter(|a| !a.is_empty())
            .ok_or_else(|| anyhow!("missing required argument: songs (a list of {{title, hint}})"))?;
        let library = self.db.list_songs()?;
        let usage = song_usage(&self.db)?;
        let charts = charts::list_chart_meta(&self.db)?;
        let mut out = Vec::with_capacity(asked.len());
        for entry in asked {
            let (title, hint) = match entry {
                Value::String(t) => (t.clone(), None),
                Value::Object(o) => (
                    o.get("title").and_then(Value::as_str).unwrap_or_default().to_string(),
                    o.get("hint").and_then(Value::as_str).map(str::to_string).filter(|h| !h.trim().is_empty()),
                ),
                _ => bail!("songs must be a list of {{title, hint}} objects"),
            };
            if title.trim().is_empty() {
                bail!("every song needs a title");
            }
            let mut scored: Vec<(f64, &SongRow)> = library
                .iter()
                .map(|s| {
                    let t = matching::title_score(&title, &s.title);
                    let score = match &hint {
                        Some(h) => 0.75 * t + 0.25 * matching::hint_score(h, &s.body_text),
                        None => t,
                    };
                    (score, s)
                })
                .filter(|(score, _)| *score >= 0.35)
                .collect();
            scored.sort_by(|a, b| b.0.total_cmp(&a.0));
            scored.truncate(3);
            // Confident: a strong match with no close second. Two near-equal
            // matches are usually duplicate copies of one song — the model
            // should pick (the one with chords, the one that's been used).
            let confident = scored.first().is_some_and(|(top, _)| *top >= 0.85)
                && scored.get(1).is_none_or(|(second, _)| scored[0].0 - second > 0.1);
            let matches: Vec<Value> = scored
                .iter()
                .map(|(score, s)| {
                    let mut v = song_summary(s, usage.get(&s.id), charts.get(&s.id));
                    for field in ["sections", "slide_order", "ccli"] {
                        v.as_object_mut().map(|o| o.remove(field));
                    }
                    v["score"] = json!((score * 100.0).round() / 100.0);
                    v
                })
                .collect();
            out.push(json!({ "asked": title, "confident": confident, "matches": matches }));
        }
        Ok(json!({ "results": out }))
    }

    /// Chord sheets for songs, looked up by file name in a folder of charts
    /// (subfolders too). The same sheet saved into many weekly service
    /// folders is one entry, with its newest copy and how many there are.
    fn find_song_files(&self, args: &Map<String, Value>) -> Result<Value> {
        let folder = req_str(args, "folder")?;
        let asked = args
            .get("songs")
            .and_then(Value::as_array)
            .filter(|a| !a.is_empty())
            .ok_or_else(|| anyhow!("missing required argument: songs (song ids or titles)"))?;
        let files = app_link::list_song_files(&folder)?;
        let root = app_link::expand_home(&folder);
        let charts = charts::list_chart_meta(&self.db)?;

        struct Sheet {
            score: f64,
            newest: (i64, String, String),
            copies: usize,
            key: Option<String>,
        }
        let mut out = Vec::with_capacity(asked.len());
        for entry in asked {
            let (song, title) = match entry.as_str().map(str::trim).or_else(|| {
                entry.get("song_id").or_else(|| entry.get("title")).and_then(Value::as_str)
            }) {
                Some(s) if s.starts_with("song_") => {
                    let row = self.db.get_song(s)?.ok_or_else(|| anyhow!("no song with id {s}"))?;
                    let title = row.title.clone();
                    (Some(row), title)
                }
                Some(t) if !t.is_empty() => (None, t.to_string()),
                _ => bail!("songs must be song ids or titles"),
            };
            // Keyed by lowercase file name: copies of one sheet in every
            // week's folder are one sheet.
            let mut sheets: HashMap<String, Sheet> = HashMap::new();
            for f in &files {
                let path = std::path::Path::new(&f.path);
                let stem = path.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default();
                let score = matching::file_score(&title, &stem);
                if score < 0.6 {
                    continue;
                }
                let modified = std::fs::metadata(path)
                    .and_then(|m| m.modified())
                    .ok()
                    .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                    .map(|d| d.as_millis() as i64)
                    .unwrap_or(0);
                let in_folder = path
                    .parent()
                    .and_then(|p| p.strip_prefix(&root).ok())
                    .map(|p| p.to_string_lossy().into_owned())
                    .unwrap_or_default();
                let file_name = path.file_name().map(|n| n.to_string_lossy().to_lowercase()).unwrap_or_default();
                let sheet = sheets.entry(file_name).or_insert_with(|| Sheet {
                    score,
                    newest: (modified, f.path.clone(), in_folder.clone()),
                    copies: 0,
                    key: matching::key_from_file_name(&stem),
                });
                sheet.copies += 1;
                if modified > sheet.newest.0 {
                    sheet.newest = (modified, f.path.clone(), in_folder);
                }
            }
            let mut sheets: Vec<Sheet> = sheets.into_values().collect();
            sheets.sort_by(|a, b| b.score.total_cmp(&a.score).then(b.newest.0.cmp(&a.newest.0)));
            let found: Vec<Value> = sheets
                .iter()
                .take(6)
                .map(|s| {
                    json!({
                        "path": s.newest.1,
                        "key_in_name": s.key,
                        "folder": if s.newest.2.is_empty() { Value::Null } else { json!(s.newest.2) },
                        "modified": date(s.newest.0),
                        "copies": s.copies,
                        "score": (s.score * 100.0).round() / 100.0,
                    })
                })
                .collect();
            let chart = song.as_ref().and_then(|s| charts.get(&s.id));
            out.push(json!({
                "song_id": song.as_ref().map(|s| s.id.clone()),
                "title": title,
                "has_chords": chart.is_some_and(|c| c.has_chords),
                "key": chart.and_then(|c| c.key.clone()),
                "sheets": found,
            }));
        }
        Ok(json!({ "folder": root.display().to_string(), "files_searched": files.len(), "results": out }))
    }

    fn create_song(&self, args: &Map<String, Value>) -> Result<Value> {
        let input = NewSong {
            title: req_str(args, "title")?,
            body_text: req_str(args, "body_text")?,
            ccli: opt_str(args, "ccli")?,
            author: opt_str(args, "author")?,
            copyright: opt_str(args, "copyright")?,
            default_tempo_wps: None,
            slide_order: opt_str(args, "slide_order")?,
        };
        if input.title.trim().is_empty() {
            bail!("title must not be empty");
        }
        let warnings = lint_song(&input.body_text, input.slide_order.as_deref());
        let sections = section_tags(&input.body_text);
        self.snapshot()?;
        let id = self.db.create_song(input)?;
        Ok(json!({
            "id": id,
            "sections": sections,
            "warnings": warnings,
            "backup": self.backup_note(),
        }))
    }

    fn update_song(&self, args: &Map<String, Value>) -> Result<Value> {
        let id = req_str(args, "id")?;
        let cur = self
            .db
            .get_song(&id)?
            .ok_or_else(|| anyhow!("no song with id {id}"))?;
        // A patch: absent keys keep their value; an explicit null clears a
        // nullable one.
        let patch = |key: &str, old: Option<String>| -> Result<Option<String>> {
            Ok(if args.contains_key(key) { opt_str(args, key)? } else { old })
        };
        let input = NewSong {
            title: match opt_str(args, "title")? {
                Some(t) if !t.trim().is_empty() => t,
                Some(_) => bail!("title must not be empty"),
                None => cur.title.clone(),
            },
            body_text: opt_str(args, "body_text")?.unwrap_or(cur.body_text.clone()),
            ccli: patch("ccli", cur.ccli.clone())?,
            author: patch("author", cur.author.clone())?,
            copyright: patch("copyright", cur.copyright.clone())?,
            default_tempo_wps: cur.default_tempo_wps,
            slide_order: patch("slide_order", cur.slide_order.clone())?,
        };
        let warnings = lint_song(&input.body_text, input.slide_order.as_deref());
        let sections = section_tags(&input.body_text);
        let chart_key = match args.get("chart_key") {
            None => None,
            Some(v) => Some(key_arg(v, "chart_key")?),
        };
        self.snapshot()?;
        self.db.update_song(&id, input)?;
        if let Some(key) = chart_key {
            if !charts::set_chart_key(&self.db, &id, key.as_deref())? {
                bail!("this song has no chord chart to set a key on — import its sheet first (the rest of the edit was saved)");
            }
        }
        Ok(json!({
            "id": id,
            "sections": sections,
            "warnings": warnings,
            "backup": self.backup_note(),
        }))
    }

    fn delete_song(&self, args: &Map<String, Value>) -> Result<Value> {
        let id = req_str(args, "id")?;
        let song = self
            .db
            .get_song(&id)?
            .ok_or_else(|| anyhow!("no song with id {id}"))?;
        let in_setlists = setlists_containing(&self.db, &id)?;
        self.snapshot()?;
        self.db.delete_song(&id)?;
        Ok(json!({
            "deleted": song.title,
            "removed_from_setlists": in_setlists,
            "backup": self.backup_note(),
        }))
    }

    // --- Setlists ------------------------------------------------------------

    fn list_setlists(&self) -> Result<Value> {
        let counts: HashMap<String, i64> = {
            let conn = self.db.conn().lock().unwrap();
            let mut stmt = conn.prepare(
                "SELECT setlist_id, COUNT(*) FROM setlist_items GROUP BY setlist_id",
            )?;
            let rows = stmt
                .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?
                .collect::<Result<_, _>>()?;
            rows
        };
        let rows = setlists::list_setlists(&self.db)?;
        let out: Vec<Value> = rows
            .iter()
            .map(|s| {
                json!({
                    "id": s.id,
                    "name": s.name,
                    "song_count": counts.get(&s.id).copied().unwrap_or(0),
                    "created": date(s.created_at),
                    "last_presented": s.last_used_at.map(date),
                })
            })
            .collect();
        Ok(json!({ "setlists": out }))
    }

    fn get_setlist(&self, args: &Map<String, Value>) -> Result<Value> {
        let id = req_str(args, "id")?;
        self.setlist_view(&id)
    }

    fn create_setlist(&self, args: &Map<String, Value>) -> Result<Value> {
        let name = req_str(args, "name")?;
        if name.trim().is_empty() {
            bail!("name must not be empty");
        }
        let songs = setlist_songs_arg(args)?.unwrap_or_default();
        let song_ids: Vec<String> = songs.iter().map(|(id, _)| id.clone()).collect();
        check_songs_exist(&self.db, &song_ids)?;
        self.snapshot()?;
        let id = setlists::create_setlist(&self.db, &name)?;
        set_setlist_songs(&self.db, &id, &song_ids)?;
        self.apply_keys(&id, &songs)?;
        let mut out = self.setlist_view(&id)?;
        out["backup"] = json!(self.backup_note());
        Ok(out)
    }

    fn update_setlist(&self, args: &Map<String, Value>) -> Result<Value> {
        let id = req_str(args, "id")?;
        if setlists::get_setlist(&self.db, &id)?.is_none() {
            bail!("no setlist with id {id}");
        }
        let name = opt_str(args, "name")?;
        let songs = setlist_songs_arg(args)?;
        if let Some(songs) = &songs {
            let ids: Vec<String> = songs.iter().map(|(id, _)| id.clone()).collect();
            check_songs_exist(&self.db, &ids)?;
        }
        self.snapshot()?;
        if let Some(name) = name.filter(|n| !n.trim().is_empty()) {
            setlists::update_setlist_name(&self.db, &id, &name)?;
        }
        if let Some(songs) = songs {
            let ids: Vec<String> = songs.iter().map(|(id, _)| id.clone()).collect();
            set_setlist_songs(&self.db, &id, &ids)?;
            self.apply_keys(&id, &songs)?;
        }
        let mut out = self.setlist_view(&id)?;
        out["backup"] = json!(self.backup_note());
        Ok(out)
    }

    fn delete_setlist(&self, args: &Map<String, Value>) -> Result<Value> {
        let id = req_str(args, "id")?;
        let detail = setlists::get_setlist(&self.db, &id)?
            .ok_or_else(|| anyhow!("no setlist with id {id}"))?;
        self.snapshot()?;
        setlists::delete_setlist(&self.db, &id)?;
        Ok(json!({ "deleted": detail.setlist.name, "backup": self.backup_note() }))
    }

    /// A passage the pastor asked for. Only the reference is stored here: the
    /// verse text lives in the app's Bible, which fills it in the next time
    /// the setlist is opened (this server has no Bible of its own).
    fn add_scripture_to_setlist(&self, args: &Map<String, Value>) -> Result<Value> {
        let setlist_id = req_str(args, "setlist_id")?;
        let reference = req_str(args, "reference")?;
        if reference.trim().is_empty() {
            bail!("reference must not be empty");
        }
        let mut content = json!({ "reference": reference.trim() });
        if let Some(t) = opt_str(args, "translation")? {
            content["translation"] = json!(t);
        }
        let after = opt_str(args, "after_item_id")?;
        self.snapshot()?;
        let item_id = setlists::add_service_item(
            &self.db,
            &setlist_id,
            "scripture",
            &content.to_string(),
            None,
            after.as_deref(),
        )?;
        let mut out = self.setlist_view(&setlist_id)?;
        out["added_item_id"] = json!(item_id);
        out["backup"] = json!(self.backup_note());
        Ok(out)
    }

    fn add_text_to_setlist(&self, args: &Map<String, Value>) -> Result<Value> {
        let setlist_id = req_str(args, "setlist_id")?;
        let title = req_str(args, "title")?;
        if title.trim().is_empty() {
            bail!("title must not be empty");
        }
        let slides = opt_str_list(args, "slides")?.unwrap_or_default();
        let slides: Vec<&str> = slides.iter().map(|s| s.trim()).filter(|s| !s.is_empty()).collect();
        if slides.is_empty() {
            bail!("give at least one slide of text");
        }
        // The same shape as a song edited for one setlist (migration 004).
        let override_json: Vec<Value> = slides
            .iter()
            .map(|lyrics| json!({ "section": { "type": "other", "number": 1 }, "lyrics": lyrics }))
            .collect();
        let after = opt_str(args, "after_item_id")?;
        self.snapshot()?;
        let item_id = setlists::add_service_item(
            &self.db,
            &setlist_id,
            "text",
            &json!({ "title": title.trim() }).to_string(),
            Some(&Value::Array(override_json).to_string()),
            after.as_deref(),
        )?;
        let mut out = self.setlist_view(&setlist_id)?;
        out["added_item_id"] = json!(item_id);
        out["backup"] = json!(self.backup_note());
        Ok(out)
    }

    fn set_song_key_for_setlist(&self, args: &Map<String, Value>) -> Result<Value> {
        let setlist_id = req_str(args, "setlist_id")?;
        let key = match args.get("key") {
            None => bail!("missing required argument: key (\"original\" for the song's own key)"),
            Some(v) => key_arg(v, "key")?,
        };
        let detail = setlists::get_setlist(&self.db, &setlist_id)?
            .ok_or_else(|| anyhow!("no setlist with id {setlist_id}"))?;
        // An item id, or a song id when the song is in the setlist once.
        let item = match (opt_str(args, "item_id")?, opt_str(args, "song_id")?) {
            (Some(item_id), _) => detail
                .items
                .iter()
                .find(|i| i.item.id == item_id)
                .ok_or_else(|| anyhow!("no item with id {item_id} in this setlist"))?,
            (None, Some(song_id)) => {
                let mut hits = detail.items.iter().filter(|i| i.item.song_id.as_deref() == Some(song_id.as_str()));
                let first = hits.next().ok_or_else(|| anyhow!("song {song_id} isn't in this setlist"))?;
                if hits.next().is_some() {
                    bail!("song {song_id} is in this setlist more than once — pass the item_id instead");
                }
                first
            }
            (None, None) => bail!("give item_id or song_id"),
        };
        let (item_id, song_id) = (item.item.id.clone(), item.item.song_id.clone().unwrap_or_default());
        self.snapshot()?;
        self.set_item_key(&setlist_id, &item_id, &song_id, key)?;
        let mut out = self.setlist_view(&setlist_id)?;
        out["backup"] = json!(self.backup_note());
        Ok(out)
    }

    /// Per-service keys from a `songs` list, onto the setlist's song items in
    /// order (they match it one to one after `set_setlist_songs`). A song
    /// with no key given keeps whatever key it had.
    fn apply_keys(&self, setlist_id: &str, songs: &[(String, Option<Option<String>>)]) -> Result<()> {
        if songs.iter().all(|(_, k)| k.is_none()) {
            return Ok(());
        }
        let detail = setlists::get_setlist(&self.db, setlist_id)?
            .ok_or_else(|| anyhow!("no setlist with id {setlist_id}"))?;
        let items = detail.items.iter().filter(|i| i.item.kind == "song");
        for (item, (song_id, key)) in items.zip(songs) {
            if let Some(key) = key {
                self.set_item_key(setlist_id, &item.item.id, song_id, key.clone())?;
            }
        }
        Ok(())
    }

    /// Set one song's key for this service. Asking for the chart's own key
    /// stores none, so the song follows its chart if that's ever corrected.
    fn set_item_key(&self, setlist_id: &str, item_id: &str, song_id: &str, key: Option<String>) -> Result<()> {
        let own = charts::list_chart_meta(&self.db)?.get(song_id).and_then(|c| c.key.clone());
        let key = key.filter(|k| {
            own.as_deref().is_none_or(|o| {
                matching::semitone_shift(o, k) != Some(0) || o.ends_with('m') != k.ends_with('m')
            })
        });
        setlists::set_setlist_item_key(&self.db, setlist_id, item_id, key.as_deref())
    }

    fn setlist_view(&self, id: &str) -> Result<Value> {
        let d = setlists::get_setlist(&self.db, id)?
            .ok_or_else(|| anyhow!("no setlist with id {id}"))?;
        let charts = charts::list_chart_meta(&self.db)?;
        let items: Vec<Value> = d
            .items
            .iter()
            .map(|i| {
                let mut v = json!({
                    "item_id": i.item.id,
                    "kind": i.item.kind,
                    "song_id": i.item.song_id,
                    "title": i.song.as_ref().map(|s| s.title.clone())
                        .or_else(|| i.item.content.as_deref().and_then(setlists::item_label)),
                    // Edited for this setlist only in the app; library edits
                    // to the song won't show here until that's reverted.
                    "has_setlist_only_edits": i.item.kind == "song" && i.item.slides_override.is_some(),
                });
                if i.item.kind == "song" {
                    let chart = i.item.song_id.as_deref().and_then(|sid| charts.get(sid));
                    let original = chart.and_then(|c| c.key.clone());
                    // The key this service plays it in: its own, else the chart's.
                    let key = i.item.song_key.clone().or_else(|| original.clone());
                    // How far the band moves it: "+2" is up a tone.
                    let shift = key
                        .as_deref()
                        .zip(original.as_deref())
                        .and_then(|(k, o)| matching::semitone_shift(o, k))
                        .filter(|s| *s != 0);
                    v["key"] = json!(key);
                    v["original_key"] = json!(original);
                    v["transpose"] = json!(shift.map(|s| format!("{s:+}")));
                    v["has_chords"] = json!(chart.is_some_and(|c| c.has_chords));
                }
                v
            })
            .collect();
        Ok(json!({
            "id": d.setlist.id,
            "name": d.setlist.name,
            "last_presented": d.setlist.last_used_at.map(date),
            "songs": items,
        }))
    }

    // --- Import ------------------------------------------------------------

    /// Read song files with the app's own importers (PDF chord sheets,
    /// ChordPro, OnSong, …). They run in the app, so this hands the files to
    /// it — launching it if needed — and relays its report.
    fn import_song_files(&self, args: &Map<String, Value>) -> Result<Value> {
        let paths = opt_str_list(args, "paths")?
            .filter(|p| !p.is_empty())
            .ok_or_else(|| anyhow!("missing required argument: paths"))?;
        let dry_run = args.get("dry_run").and_then(Value::as_bool).unwrap_or(false);
        let app_dir = self
            .app_data_dir
            .clone()
            .ok_or_else(|| anyhow!("importing needs the Church Slides app, which couldn't be located"))?;

        let found = app_link::find_files(&paths)?;
        if found.files.is_empty() {
            bail!(
                "no song files there (looked for {}){}",
                "PDF, ChordPro, OnSong, OpenLyrics, OpenSong, SongSelect and .txt files",
                if found.skipped.is_empty() { String::new() } else { format!("; skipped: {:?}", found.skipped) }
            );
        }
        // Keys as the files were found, so "~/…" and the full path both work.
        let mut attach_to = HashMap::new();
        if let Some(map) = args.get("attach_to").and_then(Value::as_object) {
            for (path, song) in map {
                let song_id = song.as_str().ok_or_else(|| anyhow!("attach_to: song ids must be strings"))?;
                let full = app_link::expand_home(path).display().to_string();
                if !found.files.iter().any(|f| f.path == full) {
                    bail!("attach_to: {path} isn't one of the files being imported — list it in paths too");
                }
                check_songs_exist(&self.db, &[song_id.to_string()])?;
                attach_to.insert(full, song_id.to_string());
            }
        }
        if !dry_run {
            self.snapshot()?;
        }
        let mut report = app_link::import_files(&app_dir, found.files, dry_run, attach_to)?;
        if !found.skipped.is_empty() {
            report["files_not_read"] = json!(found
                .skipped
                .iter()
                .map(|(p, why)| json!({ "path": p, "reason": why }))
                .collect::<Vec<_>>());
        }
        if !dry_run {
            report["backup"] = json!(self.backup_note());
        }
        Ok(report)
    }

    // --- Safety --------------------------------------------------------------

    /// Copy the whole library aside before a run of changes, so anything an
    /// assistant does can be undone by restoring one file.
    fn snapshot(&self) -> Result<()> {
        let Some(dir) = &self.backup_dir else { return Ok(()) };
        let mut taken = self.backup.lock().unwrap();
        if let Some((_, last_change)) = taken.as_mut() {
            if last_change.elapsed() < SNAPSHOT_STALE_AFTER {
                *last_change = Instant::now();
                return Ok(());
            }
        }
        std::fs::create_dir_all(dir)
            .with_context(|| format!("creating {}", dir.display()))?;
        let path = dir.join(format!("library-before-ai-{}.db", timestamp_slug()));
        {
            let conn = self.db.conn().lock().unwrap();
            conn.execute("VACUUM INTO ?1", params![path.to_string_lossy()])
                .context("snapshotting the library before changing it")?;
        }
        prune_backups(dir);
        log::info!("pre-AI snapshot at {}", path.display());
        *taken = Some((path, Instant::now()));
        Ok(())
    }

    fn backup_note(&self) -> Option<String> {
        self.backup
            .lock()
            .unwrap()
            .as_ref()
            .map(|(p, _)| format!("Library as it was before these changes: {}", p.display()))
    }
}

// --- Tool specs ---------------------------------------------------------------

/// The `tools/list` payload. Annotations let clients auto-approve reads and
/// ask before deletes.
pub fn specs() -> Value {
    let read = json!({ "readOnlyHint": true, "openWorldHint": false });
    let write = json!({ "readOnlyHint": false, "destructiveHint": false, "openWorldHint": false });
    let destroy = json!({ "readOnlyHint": false, "destructiveHint": true, "openWorldHint": false });
    let id = |what: &str| json!({ "type": "string", "description": format!("The {what}'s id") });

    json!([
        {
            "name": "list_songs",
            "title": "Search the song library",
            "description": "Search songs by title or lyric words (prefix match, all words must appear). \
                Omit query to list the whole library alphabetically. Returns ids, titles, first line, \
                key and whether it has chords, and how often/recently each song was used in a \
                setlist — not the lyrics (use get_song for those). Pages of 50: pass next_offset as \
                offset for the next page.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": { "type": "string", "description": "Words from the title or lyrics" },
                    "has_chords": { "type": "boolean", "description": "Only songs with (true) or without (false) a chord chart" },
                    "key": { "type": "string", "description": "Only songs whose chart is in this key, e.g. \"G\" or \"Em\"" },
                    "detail": {
                        "type": "string",
                        "enum": ["brief", "full"],
                        "default": "brief",
                        "description": "full adds each song's section tags, slide order and CCLI number"
                    },
                    "limit": { "type": "integer", "minimum": 1, "maximum": 200, "default": 50 },
                    "offset": { "type": "integer", "minimum": 0, "default": 0 }
                },
                "additionalProperties": false
            },
            "annotations": read,
        },
        {
            "name": "find_songs",
            "title": "Find a list of songs",
            "description": "Look up a whole list of songs at once — a setlist someone pasted or \
                typed. For each, the library songs it could be, best first, with a score and \
                confident true when one clearly fits. Split each line into the title and a hint: \
                a lyric the user quoted (\"chorus: the one I lean upon\"), which tells \
                apart songs with similar titles. Leave artists and notes like \"(Original)\" out \
                of the title. An entry with no matches isn't in the library.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "songs": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": { "type": "string" },
                                "hint": { "type": "string", "description": "A line of the lyrics the user quoted, if any" }
                            },
                            "required": ["title"]
                        }
                    }
                },
                "required": ["songs"],
                "additionalProperties": false
            },
            "annotations": read,
        },
        {
            "name": "find_song_files",
            "title": "Find chord sheets in a folder",
            "description": "Find each song's chord sheets in a folder of charts (subfolders too) by \
                file name, best match first. A sheet saved into many weekly service folders is one \
                entry: its newest copy, which folder that is, and how many copies there are. \
                key_in_name is the key the file name gives (\"Holy Holy Holy-C.pdf\" → C). Pass the \
                chosen paths to import_song_files to add chords to songs that lack them.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "folder": { "type": "string", "description": "Full path to the folder, e.g. /Users/me/Documents/Charts" },
                    "songs": { "type": "array", "items": { "type": "string" }, "description": "Song ids, or titles for songs not in the library yet" }
                },
                "required": ["folder", "songs"],
                "additionalProperties": false
            },
            "annotations": read,
        },
        {
            "name": "get_song",
            "title": "Read a song",
            "description": "A song's full lyrics (body_text, in the app's section-tag format), metadata and slide order.",
            "inputSchema": {
                "type": "object",
                "properties": { "id": id("song") },
                "required": ["id"],
                "additionalProperties": false
            },
            "annotations": read,
        },
        {
            "name": "create_song",
            "title": "Add a song",
            "description": "Add a song to the library. body_text must use the section-tag format \
                (see the server instructions). Search first to avoid duplicates. Returns the new id \
                plus warnings for format problems — fix them with update_song.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "title": { "type": "string" },
                    "body_text": { "type": "string", "description": "Lyrics with [Verse 1] / [Chorus] style tags; blank lines split slides" },
                    "slide_order": { "type": "string", "description": "Arrangement, e.g. \"V1, C, V2, C, B, C\". Omit to play each section once in written order" },
                    "author": { "type": "string" },
                    "ccli": { "type": "string", "description": "CCLI song number" },
                    "copyright": { "type": "string" }
                },
                "required": ["title", "body_text"],
                "additionalProperties": false
            },
            "annotations": write,
        },
        {
            "name": "update_song",
            "title": "Edit a song",
            "description": "Change any of a song's fields. Only the fields you pass change; pass null to clear \
                slide_order, author, ccli or copyright. Edits apply everywhere the song is used. \
                chart_key records the key the chord chart is written in when the sheet didn't say \
                (no chord moves — it's what per-service keys transpose from).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": id("song"),
                    "title": { "type": "string" },
                    "body_text": { "type": "string" },
                    "slide_order": { "type": ["string", "null"] },
                    "author": { "type": ["string", "null"] },
                    "ccli": { "type": ["string", "null"] },
                    "copyright": { "type": ["string", "null"] },
                    "chart_key": { "type": ["string", "null"], "description": "The key the song's chord chart is written in, e.g. \"D\"" }
                },
                "required": ["id"],
                "additionalProperties": false
            },
            "annotations": write,
        },
        {
            "name": "delete_song",
            "title": "Delete a song",
            "description": "Permanently delete a song. It is also removed from every setlist it's in.",
            "inputSchema": {
                "type": "object",
                "properties": { "id": id("song") },
                "required": ["id"],
                "additionalProperties": false
            },
            "annotations": destroy,
        },
        {
            "name": "list_setlists",
            "title": "List setlists",
            "description": "All setlists, most recently presented first, with song counts.",
            "inputSchema": { "type": "object", "properties": {}, "additionalProperties": false },
            "annotations": read,
        },
        {
            "name": "get_setlist",
            "title": "Read a setlist",
            "description": "A setlist's songs in running order, each with its key for this service \
                (key, the chart's own original_key, and transpose — how far that moves it, e.g. \
                \"+2\") and whether it has chords for the band.",
            "inputSchema": {
                "type": "object",
                "properties": { "id": id("setlist") },
                "required": ["id"],
                "additionalProperties": false
            },
            "annotations": read,
        },
        {
            "name": "create_setlist",
            "title": "Create a setlist",
            "description": "Create a setlist, optionally filled with songs in running order and \
                each one's key for this service. Every song must already be in the library — \
                create_song first if not.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": { "type": "string", "description": "e.g. \"Sunday 5 Oct — Morning\"" },
                    "songs": {
                        "type": "array",
                        "description": "Songs in running order, each with an optional key for this service (\"original\" = the chart's own key; leave key out to keep the song's current one)",
                        "items": {
                            "type": "object",
                            "properties": {
                                "song_id": { "type": "string" },
                                "key": { "type": ["string", "null"] }
                            },
                            "required": ["song_id"]
                        }
                    },
                    "song_ids": { "type": "array", "items": { "type": "string" }, "description": "Running order without keys — use songs instead to set keys too" }
                },
                "required": ["name"],
                "additionalProperties": false
            },
            "annotations": write,
        },
        {
            "name": "update_setlist",
            "title": "Edit a setlist",
            "description": "Rename a setlist and/or replace its song list. songs is the complete new \
                running order (a song may appear twice), and can set each song's key for this \
                service in the same call — so reordering and re-keying a set is one call. Songs \
                already in the setlist keep any setlist-only edits made in the app. Scripture and \
                text items aren't songs and aren't listed: they stay, each after the song it followed.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": id("setlist"),
                    "name": { "type": "string" },
                    "songs": {
                        "type": "array",
                        "description": "Songs in running order, each with an optional key for this service (\"original\" = the chart's own key; leave key out to keep the song's current one)",
                        "items": {
                            "type": "object",
                            "properties": {
                                "song_id": { "type": "string" },
                                "key": { "type": ["string", "null"] }
                            },
                            "required": ["song_id"]
                        }
                    },
                    "song_ids": { "type": "array", "items": { "type": "string" }, "description": "Running order without keys — use songs instead to set keys too" }
                },
                "required": ["id"],
                "additionalProperties": false
            },
            "annotations": write,
        },
        {
            "name": "add_scripture_to_setlist",
            "title": "Add a Bible passage to a setlist",
            "description": "Add a Bible passage the pastor wants on screen — the sermon reading, a \
                verse they'll cite — as its own item in a setlist. Give one passage per call, as a \
                reference like \"John 3:1-21\" or \"Romans 5:8\". The app fills in the verse text \
                from its installed Bible and splits it into slides when the setlist is next opened.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "setlist_id": id("setlist"),
                    "reference": { "type": "string", "description": "One passage, e.g. \"Ephesians 2:8-9\"" },
                    "translation": { "type": "string", "description": "A translation id the app has installed (e.g. \"web\", \"kjv\"); leave out for the service's default" },
                    "after_item_id": { "type": "string", "description": "Put it after this item (from get_setlist); leave out to add at the end" }
                },
                "required": ["setlist_id", "reference"],
                "additionalProperties": false
            },
            "annotations": write,
        },
        {
            "name": "add_text_to_setlist",
            "title": "Add text slides to a setlist",
            "description": "Add one-off text slides to a setlist: sermon points, announcements, a welcome, \
                names for a baptism, a responsive reading. Plain words only — no formatting, images \
                or layout; the church's lyrics style decides how they look.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "setlist_id": id("setlist"),
                    "title": { "type": "string", "description": "What the operator sees in the running order, e.g. \"Sermon points\"" },
                    "slides": { "type": "array", "items": { "type": "string" }, "description": "The text of each slide, in order" },
                    "after_item_id": { "type": "string", "description": "Put it after this item (from get_setlist); leave out to add at the end" }
                },
                "required": ["setlist_id", "title", "slides"],
                "additionalProperties": false
            },
            "annotations": write,
        },
        {
            "name": "set_song_key_for_setlist",
            "title": "Set a song's key for one service",
            "description": "Play a song in a different key in this setlist only — the library song \
                and other services keep theirs. The band and leader screens transpose the chord chart \
                to it. Pass \"original\" to go back to the chart's own key. get_setlist shows each \
                song's key, original_key and transpose. To change several songs' keys, or keys and \
                order together, use update_setlist with songs instead.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "setlist_id": id("setlist"),
                    "song_id": { "type": "string", "description": "The song's id, when it's in the setlist once" },
                    "item_id": { "type": "string", "description": "The song's item_id in get_setlist, for a song that's in it twice" },
                    "key": {
                        "type": ["string", "null"],
                        "description": "A key name: C, Db, D, Eb, E, F, F#, G, Ab, A, Bb, B — add m for minor (Em, F#m). \"original\" = the chart's key."
                    }
                },
                "required": ["setlist_id", "key"],
                "additionalProperties": false
            },
            "annotations": write,
        },
        {
            "name": "import_song_files",
            "title": "Import song files",
            "description": "Import songs from files or folders on this computer using Church Slides' own \
                importers: PDF chord sheets, ChordPro (.cho/.chopro), OnSong, OpenLyrics, OpenSong, \
                SongSelect (.usr/.txt) and plain-text charts. Folders are searched, subfolders too. \
                Keeps chords and keys for the band screens. A song already in the library (same CCLI \
                number or title) isn't duplicated: its chords are added and its lyrics kept, or it's \
                skipped if the file adds nothing. When a folder holds several copies of a song, the \
                best one is used. A notation PDF (a lead sheet, such as SongSelect's) doesn't become \
                a song: it attaches to its library song as the reference the AI follows the band's \
                melody and chords by, and is reported under lead_sheets (needs the AI engine running). \
                Opens the app if it isn't running. Use dry_run to preview what \
                would happen. Afterwards, check imported songs with get_song and tidy section \
                labels or slide order with update_song.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "array",
                        "items": { "type": "string" },
                        "description": "Full paths to files and/or folders, e.g. /Users/me/Documents/Charts"
                    },
                    "dry_run": { "type": "boolean", "default": false, "description": "Report what would be imported without saving anything" },
                    "attach_to": {
                        "type": "object",
                        "additionalProperties": { "type": "string" },
                        "description": "File path → song id: add that file's chords to this library song even if it matched another (a duplicate title, a wrong CCLI number). Each path must also be in paths."
                    }
                },
                "required": ["paths"],
                "additionalProperties": false
            },
            "annotations": { "readOnlyHint": false, "destructiveHint": false, "openWorldHint": false },
        },
        {
            "name": "delete_setlist",
            "title": "Delete a setlist",
            "description": "Permanently delete a setlist. The songs stay in the library.",
            "inputSchema": {
                "type": "object",
                "properties": { "id": id("setlist") },
                "required": ["id"],
                "additionalProperties": false
            },
            "annotations": destroy,
        }
    ])
}

// --- Setlist rewrite -----------------------------------------------------------

/// Make a setlist's items exactly `song_ids`, in order. An existing item is
/// reused for the first matching song id it hasn't been used for, so a song
/// that stays keeps its item — and with it any setlist-only slide edits.
fn set_setlist_songs(db: &Arc<Db>, setlist_id: &str, song_ids: &[String]) -> Result<()> {
    let mut conn = db.conn().lock().unwrap();
    let tx = conn.transaction()?;
    let existing: Vec<(String, Option<String>)> = {
        let mut stmt = tx.prepare(
            "SELECT id, song_id FROM setlist_items WHERE setlist_id = ?1 ORDER BY position",
        )?;
        let rows = stmt
            .query_map(params![setlist_id], |r| Ok((r.get(0)?, r.get(1)?)))?
            .collect::<Result<_, _>>()?;
        rows
    };
    // Scripture and text items have no song id, so `song_ids` can't name
    // them. They stay, each after the nearest earlier song item that stays
    // (the start, if none does): rewriting them away would lose what the
    // pastor asked for whenever an assistant reorders the songs.
    let all = existing;
    let existing: Vec<(String, Option<String>)> =
        all.iter().filter(|(_, s)| s.is_some()).cloned().collect();
    let mut used = vec![false; existing.len()];
    let mut order: Vec<String> = Vec::with_capacity(song_ids.len());
    for sid in song_ids {
        let reuse = existing
            .iter()
            .enumerate()
            .find(|(j, (_, s))| !used[*j] && s.as_deref() == Some(sid.as_str()))
            .map(|(j, _)| j);
        match reuse {
            Some(j) => {
                used[j] = true;
                order.push(existing[j].0.clone());
            }
            None => {
                let item_id = format!("item_{}", ulid::Ulid::new().to_string().to_lowercase());
                tx.execute(
                    "INSERT INTO setlist_items (id, setlist_id, kind, song_id, position) \
                     VALUES (?1, ?2, 'song', ?3, -1)",
                    params![item_id, setlist_id, sid],
                )?;
                order.push(item_id);
            }
        }
    }
    for (j, (item_id, _)) in existing.iter().enumerate() {
        if !used[j] {
            tx.execute("DELETE FROM setlist_items WHERE id = ?1", params![item_id])?;
        }
    }
    let kept: HashSet<&str> = order.iter().map(String::as_str).collect();
    let mut anchor: Option<&str> = None;
    let mut following: HashMap<Option<&str>, Vec<String>> = HashMap::new();
    for (item_id, song_id) in &all {
        match song_id {
            Some(_) if kept.contains(item_id.as_str()) => anchor = Some(item_id.as_str()),
            Some(_) => {}
            None => following.entry(anchor).or_default().push(item_id.clone()),
        }
    }
    let mut placed: Vec<String> = following.remove(&None).unwrap_or_default();
    for id in &order {
        placed.push(id.clone());
        placed.extend(following.remove(&Some(id.as_str())).unwrap_or_default());
    }
    let order = placed;
    for (pos, item_id) in order.iter().enumerate() {
        tx.execute(
            "UPDATE setlist_items SET position = ?1 WHERE id = ?2",
            params![pos as i64, item_id],
        )?;
    }
    tx.commit()?;
    Ok(())
}

fn check_songs_exist(db: &Db, ids: &[String]) -> Result<()> {
    let missing: Vec<&str> = ids
        .iter()
        .filter(|id| matches!(db.get_song(id), Ok(None)))
        .map(String::as_str)
        .collect();
    if !missing.is_empty() {
        bail!("no song with id: {}", missing.join(", "));
    }
    Ok(())
}

fn setlists_containing(db: &Db, song_id: &str) -> Result<Vec<String>> {
    let conn = db.conn().lock().unwrap();
    let mut stmt = conn.prepare(
        "SELECT DISTINCT s.name FROM setlist_items i JOIN setlists s ON s.id = i.setlist_id \
         WHERE i.song_id = ?1 ORDER BY s.name",
    )?;
    let names = stmt
        .query_map(params![song_id], |r| r.get(0))?
        .collect::<Result<_, _>>()?;
    Ok(names)
}

// --- Song views and lint ---------------------------------------------------------

struct Usage {
    times: i64,
    last_presented: Option<i64>,
}

/// Per song: how many setlists it's in, and when one of them was last
/// presented — what a model needs to plan "something we haven't sung lately".
fn song_usage(db: &Db) -> Result<HashMap<String, Usage>> {
    let conn = db.conn().lock().unwrap();
    let mut stmt = conn.prepare(
        "SELECT i.song_id, COUNT(*), MAX(s.last_used_at) \
         FROM setlist_items i JOIN setlists s ON s.id = i.setlist_id \
         WHERE i.song_id IS NOT NULL GROUP BY i.song_id",
    )?;
    let map = stmt
        .query_map([], |r| {
            Ok((
                r.get::<_, String>(0)?,
                Usage { times: r.get(1)?, last_presented: r.get(2)? },
            ))
        })?
        .collect::<Result<_, _>>()?;
    Ok(map)
}

fn song_summary(s: &SongRow, usage: Option<&Usage>, chart: Option<&ChartMeta>) -> Value {
    let first_line = s
        .body_text
        .lines()
        .map(str::trim)
        .find(|l| !l.is_empty() && !is_tag(l))
        .unwrap_or("");
    json!({
        "id": s.id,
        "title": s.title,
        "author": s.author,
        "ccli": s.ccli,
        "first_line": first_line,
        "sections": section_tags(&s.body_text),
        "slide_order": s.slide_order,
        "key": chart.and_then(|c| c.key.clone()),
        "has_chords": chart.is_some_and(|c| c.has_chords),
        "setlist_count": usage.map(|u| u.times).unwrap_or(0),
        "last_presented": usage.and_then(|u| u.last_presented).map(date),
    })
}

fn is_tag(line: &str) -> bool {
    let t = line.trim();
    t.len() > 2 && t.starts_with('[') && t.ends_with(']')
}

/// Section tags as written, in document order.
fn section_tags(body: &str) -> Vec<String> {
    body.lines()
        .filter(|l| is_tag(l))
        .map(|l| {
            let t = l.trim();
            t[1..t.len() - 1].trim().to_string()
        })
        .collect()
}

/// Normalise a section reference ("Verse 2", "v2", "Pre-Chorus", "C") to
/// `type:number`. A lint-only approximation of the parser's alias table
/// (`library/parser.py`) — it decides warnings, never what gets stored.
fn section_key(raw: &str) -> String {
    let s: String = raw
        .to_lowercase()
        .chars()
        .filter(|c| !c.is_whitespace() && *c != '-')
        .collect();
    let split = s.find(|c: char| c.is_ascii_digit()).unwrap_or(s.len());
    let (name, num) = s.split_at(split);
    let num: u32 = num.parse().unwrap_or(1);
    let kind = match name {
        "v" | "verse" => "verse",
        "c" | "chorus" => "chorus",
        "b" | "bridge" => "bridge",
        "p" | "prechorus" => "pre-chorus",
        "t" | "tag" => "tag",
        "i" | "intro" => "intro",
        "o" | "outro" | "ending" => "outro",
        "inst" | "instrumental" => "instrumental",
        _ => "other",
    };
    format!("{kind}:{num}")
}

/// Problems worth telling the model about, so it can fix its own output.
fn lint_song(body: &str, slide_order: Option<&str>) -> Vec<String> {
    let mut out = Vec::new();
    let tags = section_tags(body);
    if body.trim().is_empty() {
        out.push("body_text is empty — the song has no slides".into());
    } else if tags.is_empty() {
        out.push(
            "no [section] tags — the whole song becomes one untagged section; \
             add tags like [Verse 1] and [Chorus]"
                .into(),
        );
    }
    let known: Vec<String> = tags.iter().map(|t| section_key(t)).collect();
    if let Some(order) = slide_order.filter(|o| !o.trim().is_empty()) {
        for r in order.split(',').map(str::trim).filter(|r| !r.is_empty()) {
            if !known.contains(&section_key(r)) {
                out.push(format!(
                    "slide_order refers to \"{r}\" but the body has no such section (sections: {})",
                    tags.join(", ")
                ));
            }
        }
    }
    for line in body.lines().filter(|l| !is_tag(l)) {
        if line.chars().count() > 70 {
            out.push(format!(
                "a line is {} characters long and may wrap on screen: \"{}…\"",
                line.chars().count(),
                line.chars().take(40).collect::<String>()
            ));
            break;
        }
    }
    out
}

// --- Argument helpers ---------------------------------------------------------

fn req_str(args: &Map<String, Value>, key: &str) -> Result<String> {
    opt_str(args, key)?.ok_or_else(|| anyhow!("missing required argument: {key}"))
}

fn opt_str(args: &Map<String, Value>, key: &str) -> Result<Option<String>> {
    match args.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(s)) => Ok(Some(s.clone())),
        Some(Value::Number(n)) => Ok(Some(n.to_string())),
        Some(_) => bail!("{key} must be a string"),
    }
}

/// A key argument: a key name, or "original" (also null, "" or "default")
/// for the song's own key.
fn key_arg(v: &Value, name: &str) -> Result<Option<String>> {
    let k = match v {
        Value::Null => return Ok(None),
        Value::String(k) => k.trim(),
        _ => bail!("{name} must be a key name like \"Bb\", or \"original\""),
    };
    if k.is_empty() || ["original", "default", "null", "none", "own"].contains(&k.to_lowercase().as_str()) {
        return Ok(None);
    }
    if !setlists::is_key_name(k) {
        bail!("{name}: \"{k}\" isn't a key — use a name like G, Bb, F# or Em, or \"original\"");
    }
    Ok(Some(k.to_string()))
}

/// A setlist's songs as `songs: [{song_id, key}]` (or plain ids), else the
/// older `song_ids`. Each entry's key: absent = leave it, else `key_arg`.
#[allow(clippy::type_complexity)]
fn setlist_songs_arg(args: &Map<String, Value>) -> Result<Option<Vec<(String, Option<Option<String>>)>>> {
    if args.contains_key("songs") && args.contains_key("song_ids") {
        bail!("give songs or song_ids, not both");
    }
    let Some(list) = args.get("songs").filter(|v| !v.is_null()) else {
        return Ok(opt_str_list(args, "song_ids")?.map(|ids| ids.into_iter().map(|id| (id, None)).collect()));
    };
    let list = list.as_array().ok_or_else(|| anyhow!("songs must be a list"))?;
    list.iter()
        .map(|e| match e {
            Value::String(id) => Ok((id.clone(), None)),
            Value::Object(o) => {
                let id = o
                    .get("song_id")
                    .and_then(Value::as_str)
                    .ok_or_else(|| anyhow!("each entry in songs needs a song_id"))?;
                let key = o.get("key").map(|k| key_arg(k, "key")).transpose()?;
                Ok((id.to_string(), key))
            }
            _ => bail!("songs must be a list of {{song_id, key}}"),
        })
        .collect::<Result<_>>()
        .map(Some)
}

fn opt_str_list(args: &Map<String, Value>, key: &str) -> Result<Option<Vec<String>>> {
    match args.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::Array(items)) => items
            .iter()
            .map(|v| {
                v.as_str()
                    .map(str::to_string)
                    .ok_or_else(|| anyhow!("{key} must be a list of strings"))
            })
            .collect::<Result<_>>()
            .map(Some),
        Some(_) => bail!("{key} must be a list of strings"),
    }
}

// --- Time -----------------------------------------------------------------------

/// Unix ms → `YYYY-MM-DD` (UTC). Models reason about dates, not epochs.
fn date(ms: i64) -> String {
    let (y, m, d) = civil_from_days(ms.div_euclid(86_400_000));
    format!("{y:04}-{m:02}-{d:02}")
}

fn timestamp_slug() -> String {
    let ms = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0);
    let secs = ms.div_euclid(1000).rem_euclid(86_400);
    format!(
        "{}-{:02}{:02}{:02}Z",
        date(ms),
        secs / 3600,
        secs / 60 % 60,
        secs % 60
    )
}

/// Howard Hinnant's days-since-epoch → civil date.
fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    let y = yoe + era * 400 + if m <= 2 { 1 } else { 0 };
    (y, m, d)
}

fn prune_backups(dir: &std::path::Path) {
    let Ok(entries) = std::fs::read_dir(dir) else { return };
    let mut files: Vec<PathBuf> = entries
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| {
            p.file_name()
                .and_then(|n| n.to_str())
                .is_some_and(|n| n.starts_with("library-before-ai-") && n.ends_with(".db"))
        })
        .collect();
    // Names sort chronologically.
    files.sort();
    let excess = files.len().saturating_sub(BACKUPS_KEPT);
    for old in &files[..excess] {
        let _ = std::fs::remove_file(old);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn lib() -> (tempfile::TempDir, Library) {
        let dir = tempfile::tempdir().unwrap();
        let db = Arc::new(Db::open(&dir.path().join("t.db")).unwrap());
        let backups = dir.path().join("backups");
        (dir, Library::new(db, Some(backups)))
    }

    fn create(l: &Library, title: &str) -> String {
        let out = l
            .call(
                "create_song",
                &json!({ "title": title, "body_text": format!("[Verse 1]\n{title} line\n\n[Chorus]\nChorus line") }),
            )
            .unwrap();
        out["id"].as_str().unwrap().to_string()
    }

    #[test]
    fn create_then_search_and_read() {
        let (_d, l) = lib();
        let id = create(&l, "Cornerstone");
        let found = l.call("list_songs", &json!({ "query": "corner", "detail": "full" })).unwrap();
        assert_eq!(found["songs"][0]["id"], json!(id));
        assert_eq!(found["songs"][0]["sections"], json!(["Verse 1", "Chorus"]));
        let song = l.call("get_song", &json!({ "id": id })).unwrap();
        assert!(song["body_text"].as_str().unwrap().contains("[Chorus]"));
    }

    #[test]
    fn update_is_a_patch_and_null_clears() {
        let (_d, l) = lib();
        let id = create(&l, "Alpha");
        l.call("update_song", &json!({ "id": id, "author": "Someone", "slide_order": "V1, C, C" }))
            .unwrap();
        let s = l.call("get_song", &json!({ "id": id })).unwrap();
        assert_eq!(s["title"], "Alpha");
        assert_eq!(s["author"], "Someone");
        assert_eq!(s["slide_order"], "V1, C, C");
        l.call("update_song", &json!({ "id": id, "slide_order": null })).unwrap();
        let s = l.call("get_song", &json!({ "id": id })).unwrap();
        assert_eq!(s["slide_order"], Value::Null);
        assert_eq!(s["author"], "Someone");
    }

    #[test]
    fn lint_flags_unknown_slide_order_refs_and_missing_tags() {
        assert!(lint_song("[Verse 1]\na\n\n[Chorus]\nb", Some("V1, Chorus, c1")).is_empty());
        let w = lint_song("[Verse 1]\na", Some("V1, B"));
        assert_eq!(w.len(), 1);
        assert!(w[0].contains("\"B\""));
        assert!(lint_song("just words", None)[0].contains("no [section] tags"));
        assert!(lint_song("[Pre-Chorus]\nx", Some("P, pre chorus")).is_empty());
    }

    #[test]
    fn the_song_list_pages_and_filters() {
        let (_d, l) = lib();
        let ids: Vec<String> = (0..5).map(|i| create(&l, &format!("Zephyr {i}"))).collect();
        crate::charts::import_charts(
            &l.db,
            vec![crate::charts::ChartImportItem {
                existing_song_id: Some(ids[2].clone()),
                song: None,
                chart_json: r#"{"sections":[{"label":"V","stanzas":[[{"text":"x","chords":[{"pos":0,"chord":"G"}]}]]}]}"#.into(),
                original_key: Some("G".into()),
                source_format: None,
                fill_metadata: None,
                relabel: None,
            }],
        )
        .unwrap();

        // The library starts with a few example hymns; "zephyr" finds only these.
        let page = l.call("list_songs", &json!({ "query": "zephyr", "limit": 2 })).unwrap();
        assert_eq!(page["total_matches"], json!(5));
        assert_eq!(page["songs"].as_array().unwrap().len(), 2);
        assert_eq!(page["next_offset"], json!(2));
        assert!(page["songs"][0].get("sections").is_none());
        let last = l.call("list_songs", &json!({ "query": "zephyr", "limit": 2, "offset": 4, "detail": "full" })).unwrap();
        assert_eq!(last["next_offset"], Value::Null);
        assert!(last["songs"][0].get("sections").is_some());

        let chords = l.call("list_songs", &json!({ "has_chords": true })).unwrap();
        assert_eq!(chords["total_matches"], json!(1));
        assert_eq!(chords["songs"][0]["id"], json!(ids[2]));
        let without = l.call("list_songs", &json!({ "query": "zephyr", "has_chords": false })).unwrap();
        assert_eq!(without["total_matches"], json!(4));
        assert_eq!(l.call("list_songs", &json!({ "key": "g" })).unwrap()["total_matches"], json!(1));
    }

    #[test]
    fn a_song_can_have_its_own_key_for_one_service() {
        let (_d, l) = lib();
        let a = create(&l, "A");
        crate::charts::import_charts(
            &l.db,
            vec![crate::charts::ChartImportItem {
                existing_song_id: Some(a.clone()),
                song: None,
                chart_json: r#"{"sections":[{"label":"Verse","stanzas":[[{"text":"x","chords":[{"pos":0,"chord":"G"}]}]]}]}"#.into(),
                original_key: Some("G".into()),
                source_format: None,
                fill_metadata: None,
                relabel: None,
            }],
        )
        .unwrap();
        let sl = l.call("create_setlist", &json!({ "name": "Sunday", "song_ids": [a] })).unwrap();
        assert_eq!(sl["songs"][0]["key"], json!("G"));
        assert_eq!(sl["songs"][0]["has_chords"], json!(true));
        let args = |key: Value| {
            json!({ "setlist_id": sl["id"], "item_id": sl["songs"][0]["item_id"], "key": key })
        };

        let out = l.call("set_song_key_for_setlist", &args(json!("Bb"))).unwrap();
        assert_eq!(out["songs"][0]["key"], json!("Bb"));
        assert_eq!(out["songs"][0]["original_key"], json!("G"));
        // The library song is untouched.
        assert_eq!(l.call("get_song", &json!({ "id": a })).unwrap()["key"], json!("G"));

        assert!(l.call("set_song_key_for_setlist", &args(json!("H#"))).is_err());
        let out = l.call("set_song_key_for_setlist", &args(Value::Null)).unwrap();
        assert_eq!(out["songs"][0]["key"], json!("G"));
    }

    fn chart(l: &Library, song_id: &str, key: Option<&str>) {
        crate::charts::import_charts(
            &l.db,
            vec![crate::charts::ChartImportItem {
                existing_song_id: Some(song_id.into()),
                song: None,
                chart_json: r#"{"key":null,"sections":[{"label":"Verse","stanzas":[[{"text":"x","chords":[{"pos":0,"chord":"G"}]}]]}]}"#.into(),
                original_key: key.map(str::to_string),
                source_format: None,
                fill_metadata: None,
                relabel: None,
            }],
        )
        .unwrap();
    }

    #[test]
    fn reorder_and_rekey_a_set_in_one_call() {
        let (_d, l) = lib();
        let (a, b, c) = (create(&l, "A"), create(&l, "B"), create(&l, "C"));
        chart(&l, &a, Some("Gb"));
        chart(&l, &b, Some("A"));
        chart(&l, &c, Some("E"));
        let sl = l
            .call("create_setlist", &json!({ "name": "S", "songs": [
                { "song_id": a, "key": "G" }, { "song_id": b, "key": "G" }, { "song_id": c, "key": "D" }
            ] }))
            .unwrap();
        let keys = |v: &Value| -> Vec<Value> {
            v["songs"].as_array().unwrap().iter().map(|s| json!([s["title"], s["key"], s["transpose"]])).collect()
        };
        assert_eq!(keys(&sl), vec![json!(["A", "G", "+1"]), json!(["B", "G", "-2"]), json!(["C", "D", "-2"])]);

        // New order; C back to its own key; A and B keep theirs.
        let out = l
            .call("update_setlist", &json!({ "id": sl["id"], "songs": [
                { "song_id": c, "key": "original" }, { "song_id": a }, b
            ] }))
            .unwrap();
        assert_eq!(keys(&out), vec![json!(["C", "E", null]), json!(["A", "G", "+1"]), json!(["B", "G", "-2"])]);

        // By song id, and naming the chart's own key stores none.
        let out = l
            .call("set_song_key_for_setlist", &json!({ "setlist_id": sl["id"], "song_id": a, "key": "Gb" }))
            .unwrap();
        assert_eq!(out["songs"][1]["key"], json!("Gb"));
        assert_eq!(out["songs"][1]["transpose"], Value::Null);
        assert!(l
            .call("update_setlist", &json!({ "id": sl["id"], "songs": [a], "song_ids": [a] }))
            .is_err());
    }

    #[test]
    fn a_chart_with_no_written_key_can_be_given_one() {
        let (_d, l) = lib();
        let a = create(&l, "A");
        let b = create(&l, "B");
        chart(&l, &a, None);
        l.call("update_song", &json!({ "id": a, "chart_key": "D" })).unwrap();
        assert_eq!(l.call("get_song", &json!({ "id": a })).unwrap()["key"], json!("D"));
        let stored = crate::charts::get_charts(&l.db, &[a.clone()]).unwrap();
        assert!(stored[&a].chart_json.contains(r#""key":"D""#));
        assert!(l.call("update_song", &json!({ "id": b, "chart_key": "D" })).is_err());
    }

    #[test]
    fn a_pasted_list_finds_its_songs() {
        let (_d, l) = lib();
        create(&l, "Shout To The Lord (Hillsong)");
        create(&l, "God You're So Good");
        create(&l, "God So Good");
        l.call(
            "create_song",
            &json!({ "title": "None But Jesus", "body_text": "[Chorus]\nThe one I lean upon\nNone but Jesus" }),
        )
        .unwrap();
        let out = l
            .call("find_songs", &json!({ "songs": [
                { "title": "Shout to the Lord" },
                { "title": "God you’re so good" },
                { "title": "None but Jesus", "hint": "the one I lean upon" },
                { "title": "Indescribable" }
            ] }))
            .unwrap();
        let r = &out["results"];
        assert_eq!(r[0]["matches"][0]["title"], json!("Shout To The Lord (Hillsong)"));
        assert_eq!(r[0]["confident"], json!(true));
        assert_eq!(r[1]["matches"][0]["title"], json!("God You're So Good"));
        assert_eq!(r[2]["matches"][0]["title"], json!("None But Jesus"));
        assert_eq!(r[3]["matches"], json!([]));
        assert_eq!(r[3]["confident"], json!(false));
    }

    #[test]
    fn sheets_are_found_by_name_and_copies_collapse() {
        let (d, l) = lib();
        let root = d.path().join("sheets");
        for (dir, name) in [
            ("", "Holy Holy Holy-C.pdf"),
            ("Week 1", "Holy Holy Holy-C.pdf"),
            ("Week 2", "Holy Holy Holy-C.pdf"),
            ("", "WorthyOfItAllBb.pdf"),
            ("", "Holy Spirit-chords-D.pdf"),
            ("", "notes.docx"),
        ] {
            std::fs::create_dir_all(root.join(dir)).unwrap();
            std::fs::write(root.join(dir).join(name), b"%PDF").unwrap();
        }
        let holy = create(&l, "Holy Holy Holy");
        let out = l
            .call("find_song_files", &json!({ "folder": root, "songs": [holy, "Worthy Of It All"] }))
            .unwrap();
        let r = &out["results"];
        assert_eq!(r[0]["sheets"][0]["copies"], json!(3));
        assert_eq!(r[0]["sheets"][0]["key_in_name"], json!("C"));
        assert_eq!(r[0]["sheets"].as_array().unwrap().len(), 1, "Holy Spirit isn't Holy Holy Holy");
        assert_eq!(r[1]["song_id"], Value::Null);
        assert_eq!(r[1]["sheets"][0]["key_in_name"], json!("Bb"));
    }

    #[test]
    fn setlist_rewrite_keeps_items_and_their_edits() {
        let (_d, l) = lib();
        let a = create(&l, "A");
        let b = create(&l, "B");
        let c = create(&l, "C");
        let sl = l
            .call("create_setlist", &json!({ "name": "Sunday", "song_ids": [a, b] }))
            .unwrap();
        let sl_id = sl["id"].as_str().unwrap().to_string();
        let b_item = sl["songs"][1]["item_id"].as_str().unwrap().to_string();
        setlists::set_setlist_item_slides(&l.db, &sl_id, &b_item, Some("[]")).unwrap();

        let out = l
            .call("update_setlist", &json!({ "id": sl_id, "song_ids": [c, b, c] }))
            .unwrap();
        let songs = out["songs"].as_array().unwrap();
        let ids: Vec<&str> = songs.iter().map(|s| s["song_id"].as_str().unwrap()).collect();
        assert_eq!(ids, vec![c.as_str(), b.as_str(), c.as_str()]);
        assert_eq!(songs[1]["item_id"], json!(b_item));
        assert_eq!(songs[1]["has_setlist_only_edits"], json!(true));
    }

    #[test]
    fn readings_survive_a_song_rewrite_after_the_song_they_followed() {
        let (_d, l) = lib();
        let a = create(&l, "A");
        let b = create(&l, "B");
        let c = create(&l, "C");
        let sl = l
            .call("create_setlist", &json!({ "name": "Sunday", "song_ids": [a, b] }))
            .unwrap();
        let sl_id = sl["id"].as_str().unwrap().to_string();
        let a_item = sl["songs"][0]["item_id"].as_str().unwrap().to_string();
        let out = l
            .call(
                "add_scripture_to_setlist",
                &json!({ "setlist_id": sl_id, "reference": "John 3:16", "after_item_id": a_item }),
            )
            .unwrap();
        assert_eq!(out["songs"][1]["kind"], json!("scripture"));
        assert_eq!(out["songs"][1]["title"], json!("John 3:16"));
        l.call(
            "add_text_to_setlist",
            &json!({ "setlist_id": sl_id, "title": "Points", "slides": ["One", " ", "Two"] }),
        )
        .unwrap();

        // B moves first, A stays, C joins: John 3:16 still follows A, and the
        // text item still follows B.
        let out = l
            .call("update_setlist", &json!({ "id": sl_id, "song_ids": [b, a, c] }))
            .unwrap();
        let titles: Vec<&str> = out["songs"]
            .as_array()
            .unwrap()
            .iter()
            .map(|s| s["title"].as_str().unwrap())
            .collect();
        assert_eq!(titles, vec!["B", "Points", "A", "John 3:16", "C"]);
        let detail = setlists::get_setlist(&l.db, &sl_id).unwrap().unwrap();
        let points = detail.items.iter().find(|i| i.item.kind == "text").unwrap();
        let slides: Value = serde_json::from_str(points.item.slides_override.as_deref().unwrap()).unwrap();
        assert_eq!(slides.as_array().unwrap().len(), 2, "blank slides dropped");

        // Dropping A: its reading moves to the nearest earlier song kept.
        let out = l
            .call("update_setlist", &json!({ "id": sl_id, "song_ids": [b, c] }))
            .unwrap();
        let titles: Vec<&str> = out["songs"]
            .as_array()
            .unwrap()
            .iter()
            .map(|s| s["title"].as_str().unwrap())
            .collect();
        assert_eq!(titles, vec!["B", "Points", "John 3:16", "C"]);
    }

    #[test]
    fn unknown_song_ids_change_nothing() {
        let (_d, l) = lib();
        let err = l
            .call("create_setlist", &json!({ "name": "X", "song_ids": ["song_nope"] }))
            .unwrap_err();
        assert!(err.to_string().contains("song_nope"));
        let all = l.call("list_setlists", &json!({})).unwrap();
        assert_eq!(all["setlists"].as_array().unwrap().len(), 0);
    }

    #[test]
    fn first_change_snapshots_once() {
        let (d, l) = lib();
        l.call("list_songs", &json!({})).unwrap();
        assert!(!d.path().join("backups").exists(), "reads don't snapshot");
        create(&l, "One");
        create(&l, "Two");
        let n = std::fs::read_dir(d.path().join("backups")).unwrap().count();
        assert_eq!(n, 1);
        // The snapshot is the library before the change: the example hymns only.
        let snap = Db::open(&std::fs::read_dir(d.path().join("backups")).unwrap().next().unwrap().unwrap().path()).unwrap();
        assert_eq!(snap.list_songs().unwrap().len(), crate::seed_hymns::SEED_HYMNS.len());
    }

    #[test]
    fn dates_are_civil() {
        assert_eq!(date(0), "1970-01-01");
        assert_eq!(date(1_759_190_400_000), "2025-09-30");
    }
}
