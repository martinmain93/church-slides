//! Two computers sharing an organization's library through a stand-in for
//! the server that applies the protocol's rules (docs/server-protocol,
//! "Sync"). Edits go through the app's own write paths, so these also prove
//! the triggers see them.

use std::collections::{BTreeMap, HashMap};
use std::sync::Arc;

use serde_json::{json, Map, Value};

use super::local::{self, Pending, PushResult, Pulled};
use crate::db::{Db, NewSong};
use crate::setlists;

#[derive(Clone)]
struct Rec {
    fields: Map<String, Value>,
    versions: HashMap<String, (i64, String)>,
    seq: i64,
    deleted: bool,
}

/// The server's rules, as the protocol states them.
#[derive(Default)]
struct Server {
    seq: i64,
    records: BTreeMap<(String, String), Rec>,
    refs: Vec<((String, String), (String, String))>,
    revisions: Vec<(String, String)>,
    /// Record types this person's role can't change.
    forbidden: Vec<&'static str>,
}

impl Server {
    fn next(&mut self) -> i64 {
        self.seq += 1;
        self.seq
    }

    fn push(&mut self, device: &str, changes: &[Pending]) -> Vec<PushResult> {
        changes.iter().map(|c| self.apply(device, c)).collect()
    }

    fn apply(&mut self, device: &str, c: &Pending) -> PushResult {
        let ok = |seq: i64, conflict: bool| PushResult {
            op: c.op.clone(), status: "applied".into(), seq: Some(seq), conflict: conflict.then_some(true), code: None, message: None,
        };
        let no = |code: &str| PushResult {
            op: c.op.clone(), status: "refused".into(), seq: None, conflict: None, code: Some(code.into()), message: Some(code.into()),
        };
        if self.forbidden.contains(&c.ty.as_str()) {
            return no("forbidden");
        }
        let key = (c.ty.clone(), c.id.clone());
        let Some(set) = &c.set else {
            let Some(rec) = self.records.get(&key).cloned() else { return ok(0, false) };
            if rec.deleted {
                return ok(rec.seq, false);
            }
            let used = self.refs.iter().any(|(from, to)| {
                to == &key && from != &key && from.0 != "service" && self.records.get(from).is_some_and(|r| !r.deleted)
            });
            if used {
                return no("in_use");
            }
            let seq = self.next();
            let r = self.records.get_mut(&key).unwrap();
            r.deleted = true;
            r.seq = seq;
            return ok(seq, false);
        };
        let mut rec = self.records.get(&key).cloned().unwrap_or(Rec { fields: Map::new(), versions: HashMap::new(), seq: 0, deleted: false });
        if rec.deleted {
            return no("deleted");
        }
        let mut conflict = false;
        for (k, v) in set {
            if k == "body_text" {
                if let (Some((s, d)), Some(Value::String(old))) = (rec.versions.get(k), rec.fields.get(k)) {
                    if *s > c.base && d != device && Some(v) != rec.fields.get(k) {
                        self.revisions.push((c.id.clone(), old.clone()));
                        conflict = true;
                    }
                }
            }
            if v.is_null() {
                rec.fields.remove(k);
            } else {
                rec.fields.insert(k.clone(), v.clone());
            }
        }
        let seq = self.next();
        for k in set.keys() {
            rec.versions.insert(k.clone(), (seq, device.to_string()));
        }
        rec.seq = seq;
        self.records.insert(key.clone(), rec);
        if let Some(refs) = set.get("refs") {
            self.refs.retain(|(from, _)| from != &key);
            for r in refs.as_array().into_iter().flatten().filter_map(Value::as_str) {
                let (t, i) = r.split_once(':').unwrap();
                let to = (t.to_string(), i.to_string());
                self.refs.push((key.clone(), to.clone()));
                if self.records.get(&to).is_some_and(|r| r.deleted) {
                    let s = self.next();
                    let r = self.records.get_mut(&to).unwrap();
                    r.deleted = false;
                    r.seq = s;
                }
            }
        }
        ok(seq, conflict)
    }

    fn pull(&self, after: i64) -> (Vec<Pulled>, i64) {
        let mut out: Vec<Pulled> = self
            .records
            .iter()
            .filter(|(_, r)| r.seq > after)
            .map(|((t, i), r)| Pulled { ty: t.clone(), id: i.clone(), seq: r.seq, deleted: r.deleted, fields: r.fields.clone() })
            .collect();
        out.sort_by_key(|r| r.seq);
        (out, self.seq)
    }
}

struct Computer {
    _dir: tempfile::TempDir,
    db: Arc<Db>,
}

impl Computer {
    fn new() -> Self {
        let dir = tempfile::tempdir().unwrap();
        let db = Arc::new(Db::open(&dir.path().join("library.db")).unwrap());
        db.switch_to(&dir.path().join("orgs").join("o1").join("library.db")).unwrap();
        Computer { _dir: dir, db }
    }

    /// One round, as the app's loop runs it: push everything, then pull.
    fn sync(&self, server: &mut Server) -> local::PushOutcome {
        let conn = self.db.conn().lock().unwrap();
        let device = local::device(&conn).unwrap();
        let mut outcome = local::PushOutcome::default();
        loop {
            let batch = local::prepare_push(&conn).unwrap();
            if batch.is_empty() {
                break;
            }
            let results = server.push(&device, &batch);
            let o = local::apply_push(&conn, &batch, &results).unwrap();
            outcome.refused.extend(o.refused);
            outcome.conflicts += o.conflicts;
        }
        let (records, seq) = server.pull(local::cursor(&conn).unwrap());
        local::apply_pull(&conn, &records, seq).unwrap();
        outcome
    }

    fn song(&self, title: &str, words: &str) -> String {
        self.db.create_song(new_song(title, words)).unwrap()
    }

    fn edit(&self, id: &str, title: &str, words: &str) {
        self.db.update_song(id, new_song(title, words)).unwrap();
    }

    fn words(&self, id: &str) -> Option<String> {
        self.db.get_song(id).unwrap().map(|s| s.body_text)
    }

    fn waiting(&self) -> i64 {
        local::waiting(&self.db.conn().lock().unwrap()).unwrap()
    }
}

fn new_song(title: &str, words: &str) -> NewSong {
    NewSong {
        title: title.into(),
        body_text: words.into(),
        ccli: None,
        author: None,
        copyright: None,
        default_tempo_wps: None,
        slide_order: None,
    }
}

#[test]
fn a_new_computer_downloads_the_library_and_edits_flow_both_ways() {
    let mut server = Server::default();
    let (a, b) = (Computer::new(), Computer::new());
    let song = a.song("Amazing Grace", "[Verse 1]\nAmazing grace");
    let list = setlists::create_setlist(&a.db, "Sunday", None).unwrap();
    setlists::add_setlist_item(&a.db, &list, &song).unwrap();
    assert!(a.waiting() >= 2);
    a.sync(&mut server);
    assert_eq!(a.waiting(), 0);

    b.sync(&mut server);
    assert_eq!(b.words(&song).as_deref(), Some("[Verse 1]\nAmazing grace"));
    let got = setlists::get_setlist(&b.db, &list).unwrap().unwrap();
    assert_eq!(got.items.len(), 1);
    assert_eq!(b.waiting(), 0, "writing the server's records doesn't count as a change here");

    b.edit(&song, "Amazing Grace", "[Verse 1]\nAmazing grace, how sweet");
    b.sync(&mut server);
    a.sync(&mut server);
    assert_eq!(a.words(&song).as_deref(), Some("[Verse 1]\nAmazing grace, how sweet"));
}

#[test]
fn offline_edits_to_different_fields_both_survive() {
    let mut server = Server::default();
    let (a, b) = (Computer::new(), Computer::new());
    let song = a.song("Untitled", "words");
    a.sync(&mut server);
    b.sync(&mut server);

    // Both offline: A renames, B changes the words.
    a.edit(&song, "Be Thou My Vision", "words");
    b.edit(&song, "Untitled", "new words");
    a.sync(&mut server);
    b.sync(&mut server);
    a.sync(&mut server);
    for c in [&a, &b] {
        let s = c.db.get_song(&song).unwrap().unwrap();
        assert_eq!((s.title.as_str(), s.body_text.as_str()), ("Be Thou My Vision", "new words"));
    }
    assert!(server.revisions.is_empty(), "nobody's words were replaced");
}

#[test]
fn words_changed_on_both_computers_keep_a_revision() {
    let mut server = Server::default();
    let (a, b) = (Computer::new(), Computer::new());
    let song = a.song("Song", "first");
    a.sync(&mut server);
    b.sync(&mut server);

    a.edit(&song, "Song", "from A");
    b.edit(&song, "Song", "from B");
    a.sync(&mut server);
    let outcome = b.sync(&mut server);
    a.sync(&mut server);
    assert_eq!(outcome.conflicts, 1);
    assert_eq!(server.revisions, vec![(song.clone(), "from A".to_string())]);
    assert_eq!(a.words(&song).as_deref(), Some("from B"));
    assert_eq!(b.words(&song).as_deref(), Some("from B"));
}

#[test]
fn a_computers_own_back_to_back_edits_are_not_a_conflict() {
    let mut server = Server::default();
    let a = Computer::new();
    let song = a.song("Song", "one");
    a.sync(&mut server);
    a.edit(&song, "Song", "two");
    a.sync(&mut server);
    a.edit(&song, "Song", "three");
    assert_eq!(a.sync(&mut server).conflicts, 0);
    assert!(server.revisions.is_empty());
}

#[test]
fn a_change_made_while_a_pull_arrives_is_kept_and_still_goes_up() {
    let mut server = Server::default();
    let (a, b) = (Computer::new(), Computer::new());
    let song = a.song("Song", "words");
    a.sync(&mut server);
    b.sync(&mut server);

    a.edit(&song, "New title", "words");
    a.sync(&mut server);
    // B edits the words, then pulls A's title before pushing.
    b.edit(&song, "Song", "B's words");
    {
        let conn = b.db.conn().lock().unwrap();
        let (records, seq) = server.pull(local::cursor(&conn).unwrap());
        local::apply_pull(&conn, &records, seq).unwrap();
    }
    let s = b.db.get_song(&song).unwrap().unwrap();
    assert_eq!((s.title.as_str(), s.body_text.as_str()), ("New title", "B's words"));
    assert_eq!(b.waiting(), 1);
    b.sync(&mut server);
    a.sync(&mut server);
    assert_eq!(a.words(&song).as_deref(), Some("B's words"));
}

#[test]
fn deletes_reach_other_computers_and_a_used_song_comes_back() {
    let mut server = Server::default();
    let (a, b) = (Computer::new(), Computer::new());
    let gone = a.song("Gone", "x");
    let kept = a.song("Kept", "y");
    a.sync(&mut server);
    b.sync(&mut server);

    a.db.delete_song(&gone).unwrap();
    // Meanwhile B, offline, puts `kept` in a service while A deletes it.
    let list = setlists::create_setlist(&b.db, "Sunday", None).unwrap();
    setlists::add_setlist_item(&b.db, &list, &kept).unwrap();
    a.db.delete_song(&kept).unwrap();
    a.sync(&mut server);
    b.sync(&mut server);
    a.sync(&mut server);

    assert!(b.words(&gone).is_none(), "the delete reached B");
    assert!(a.words(&kept).is_some(), "B's service brought the song back for everyone");
    assert!(b.words(&kept).is_some());
}

#[test]
fn a_refused_change_is_undone_and_the_reason_kept() {
    let mut server = Server::default();
    let (a, b) = (Computer::new(), Computer::new());
    let song = a.song("Song", "words");
    a.sync(&mut server);
    b.sync(&mut server);

    // B's role can't edit songs (a set leader).
    server.forbidden = vec!["song"];
    b.edit(&song, "Song", "set leader's words");
    let outcome = b.sync(&mut server);
    assert_eq!(outcome.refused.first().map(|r| r.0.as_str()), Some("forbidden"));
    assert_eq!(b.words(&song).as_deref(), Some("words"), "back to the server's version");
    assert_eq!(b.waiting(), 0);
}

#[test]
fn this_computers_own_library_never_records_changes() {
    let dir = tempfile::tempdir().unwrap();
    let home = Db::open(&dir.path().join("library.db")).unwrap();
    home.create_song(new_song("Mine", "only here")).unwrap();
    let conn = home.conn().lock().unwrap();
    assert!(!local::is_installed(&conn), "the home library has no sync tables");
    let triggers: i64 = conn
        .query_row("SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'sync_%'", [], |r| r.get(0))
        .unwrap();
    assert_eq!(triggers, 0);
}

#[test]
fn a_library_moved_in_with_songs_sends_all_of_them() {
    let dir = tempfile::tempdir().unwrap();
    let home = dir.path().join("library.db");
    let path = dir.path().join("orgs").join("o1").join("library.db");
    // A library made before it belonged to an organization, then moved in.
    {
        let db = Db::open(&home).unwrap();
        db.create_song(new_song("Old", "w")).unwrap();
    }
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::copy(&home, &path).unwrap();
    let conn = crate::db::open_library_file(&path).unwrap();
    let songs: i64 = conn.query_row("SELECT COUNT(*) FROM songs", [], |r| r.get(0)).unwrap();
    assert_eq!(local::waiting(&conn).unwrap(), songs);
    let batch = local::prepare_push(&conn).unwrap();
    assert!(batch.iter().any(|p| p.to_json()["set"]["title"] == json!("Old")));
}

#[test]
fn the_org_of_an_open_library_comes_from_its_path() {
    let home = std::path::Path::new("/data/library.db");
    assert_eq!(super::org_of(home, std::path::Path::new("/data/orgs/abc/library.db")).as_deref(), Some("abc"));
    assert_eq!(super::org_of(home, home), None);
    assert_eq!(super::org_of(home, std::path::Path::new("/elsewhere/orgs/abc/library.db")), None);
}

#[test]
fn the_library_look_syncs_like_a_record() {
    let mut server = Server::default();
    let (a, b) = (Computer::new(), Computer::new());
    let look = crate::library_look::LibraryLook { display: Some(r#"{"theme":"light"}"#.into()), scripture: None };
    crate::library_look::set(&a.db, &look).unwrap();
    a.sync(&mut server);
    b.sync(&mut server);
    assert_eq!(crate::library_look::get(&b.db).unwrap(), Some(look));
}

fn learning_rows(path: &std::path::Path) -> Vec<String> {
    let conn = rusqlite::Connection::open(path).unwrap();
    let mut stmt = conn.prepare("SELECT id FROM performances ORDER BY id").unwrap();
    stmt.query_map([], |r| r.get(0)).unwrap().map(Result::unwrap).collect()
}

#[test]
fn learned_timing_goes_up_once_finished_and_comes_down_elsewhere() {
    let mut server = Server::default();
    let (a, b) = (Computer::new(), Computer::new());
    let (la, lb) = (local::learning_path(&a.db.open_path()), local::learning_path(&b.db.open_path()));
    {
        // The engine's rows: one finished, one still being learned.
        let conn = rusqlite::Connection::open(&la).unwrap();
        conn.execute_batch(
            "CREATE TABLE performances (id TEXT PRIMARY KEY, song_id TEXT NOT NULL, started_at_ms INTEGER NOT NULL,
               provenance TEXT NOT NULL, weight REAL NOT NULL, final INTEGER NOT NULL, data_json TEXT NOT NULL, updated_at_ms INTEGER NOT NULL);
             INSERT INTO performances VALUES ('p1', 's1', 1, 'live', 1.0, 1, '{}', 1);
             INSERT INTO performances VALUES ('p2', 's1', 2, 'live', 1.0, 0, '{}', 2);",
        )
        .unwrap();
    }
    let round = |c: &Computer, path: &std::path::Path, server: &mut Server| {
        let conn = c.db.conn().lock().unwrap();
        let device = local::device(&conn).unwrap();
        let batch = local::with_learning(&conn, path, || local::prepare_performances(&conn, 200)).unwrap();
        let results = server.push(&device, &batch);
        local::apply_push(&conn, &batch, &results).unwrap();
        let (records, seq) = server.pull(local::cursor(&conn).unwrap());
        local::with_learning(&conn, path, || local::apply_pull(&conn, &records, seq)).unwrap();
        batch.len()
    };
    assert_eq!(round(&a, &la, &mut server), 1, "only the finished one goes up");
    assert_eq!(round(&a, &la, &mut server), 0, "and only once");
    round(&b, &lb, &mut server);
    assert_eq!(learning_rows(&lb), vec!["p1".to_string()]);
    assert_eq!(round(&b, &lb, &mut server), 0, "a row that came down doesn't go back up");
}

#[test]
fn moving_this_computers_library_into_an_organization() {
    let dir = tempfile::tempdir().unwrap();
    let db = Db::open(&dir.path().join("library.db")).unwrap();
    let song = db.create_song(new_song("Our Song", "words")).unwrap();
    let db = Arc::new(db);
    let list = setlists::create_setlist(&db, "Sunday", None).unwrap();
    setlists::add_setlist_item(&db, &list, &song).unwrap();
    db.home()
        .execute("INSERT INTO settings (key, value, updated_at) VALUES ('some_setting', '\"kept\"', 0)", [])
        .unwrap();
    std::fs::write(dir.path().join("learning.db"), b"").unwrap();
    let songs_before: i64 = db.conn().lock().unwrap().query_row("SELECT COUNT(*) FROM songs", [], |r| r.get(0)).unwrap();

    assert!(crate::libraries::can_move_here(&db, "o1"));
    crate::libraries::move_home_into(&db, "o1", "Grace Chapel").unwrap();

    // The organization's library is open, with everything, all waiting to go up.
    assert_eq!(super::org_of(db.home_path(), &db.open_path()).as_deref(), Some("o1"));
    assert!(db.get_song(&song).unwrap().is_some());
    assert!(setlists::get_setlist(&db, &list).unwrap().is_some());
    let waiting = local::waiting(&db.conn().lock().unwrap()).unwrap();
    assert_eq!(waiting, songs_before + 1);
    // This computer's library is empty but keeps its settings.
    let home = db.home();
    let left: i64 = home.query_row("SELECT COUNT(*) FROM songs", [], |r| r.get(0)).unwrap();
    assert_eq!(left, 0);
    let kept: String = home.query_row("SELECT value FROM settings WHERE key = 'some_setting'", [], |r| r.get(0)).unwrap();
    assert_eq!(kept, "\"kept\"");
    let org_settings: i64 = db.conn().lock().unwrap().query_row("SELECT COUNT(*) FROM settings WHERE key = 'some_setting'", [], |r| r.get(0)).unwrap();
    assert_eq!(org_settings, 0, "settings don't go with the songs");
    drop(home);
    // Learned timing moved; a backup was kept.
    assert!(dir.path().join("orgs/o1/learning.db").exists());
    assert!(!dir.path().join("learning.db").exists());
    assert!(std::fs::read_dir(dir.path()).unwrap().flatten().any(|e| e.file_name().to_string_lossy().starts_with("library-before-move-")));
    // Only once: the organization has songs now.
    assert!(!crate::libraries::can_move_here(&db, "o1"));
}

/// The record formats every client writes (docs/server-protocol/records.md):
/// each sample written into a library and read back comes out the same, so
/// the desktop app reads and writes exactly the published shapes.
#[test]
fn the_published_sample_records_round_trip() {
    let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../docs/server-protocol/records");
    let mut samples: Vec<(String, String, Map<String, Value>)> = std::fs::read_dir(&dir)
        .unwrap()
        .map(|e| {
            let v: Value = serde_json::from_str(&std::fs::read_to_string(e.unwrap().path()).unwrap()).unwrap();
            (v["type"].as_str().unwrap().to_string(), v["id"].as_str().unwrap().to_string(), v["fields"].as_object().unwrap().clone())
        })
        .collect();
    assert!(samples.len() >= 6, "the samples are there");
    // Songs before the services that use them.
    let rank = |t: &str| ["song", "reading", "look", "setlist", "service"].iter().position(|x| *x == t).unwrap();
    samples.sort_by_key(|(t, _, _)| rank(t));
    let c = Computer::new();
    let conn = c.db.conn().lock().unwrap();
    for (ty, id, fields) in &samples {
        local::write(&conn, ty, id, fields).unwrap();
    }
    for (ty, id, fields) in &samples {
        assert_eq!(local::read(&conn, ty, id).unwrap().as_ref(), Some(fields), "{ty} {id}");
    }
}
