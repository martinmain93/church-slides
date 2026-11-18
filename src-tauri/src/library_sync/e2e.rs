//! Two computers syncing through a real server running locally with its
//! test sign-in. Skipped unless asked for:
//!
//! ```text
//! (church-slides-server) pnpm dev:test-sign-in
//! CHURCH_SLIDES_SERVER=http://localhost:8787 cargo test --lib library_sync::e2e -- --ignored
//! ```
//!
//! Both computers are signed in as the same person (tests keep one sign-in
//! in memory); they're two copies of the library, so two devices.

use std::path::Path;
use std::sync::Arc;

use serde_json::{json, Value};

use crate::db::{Db, NewSong};
use crate::{account, setlists};

struct Computer {
    dir: tempfile::TempDir,
    db: Arc<Db>,
}

impl Computer {
    fn new(org: &str, account_from: Option<&Path>) -> Self {
        let dir = tempfile::tempdir().unwrap();
        if let Some(from) = account_from {
            std::fs::copy(from.join("account.json"), dir.path().join("account.json")).unwrap();
        }
        let db = Arc::new(Db::open(&dir.path().join("library.db")).unwrap());
        db.switch_to(&dir.path().join("orgs").join(org).join("library.db")).unwrap();
        Computer { dir, db }
    }

    async fn sync(&self, org: &str) -> Option<String> {
        super::cycle(&self.db, self.dir.path(), org, &|| {}).await.unwrap()
    }

    fn words(&self, id: &str) -> Option<String> {
        self.db.get_song(id).unwrap().map(|s| s.body_text)
    }
}

fn song(title: &str, words: &str) -> NewSong {
    NewSong { title: title.into(), body_text: words.into(), ccli: None, author: None, copyright: None, default_tempo_wps: None, slide_order: None }
}

#[tokio::test]
#[ignore = "needs the server running locally: see the module comment"]
async fn two_computers_through_a_local_server() {
    assert!(account::server_url().starts_with("http://localhost"), "set CHURCH_SLIDES_SERVER=http://localhost:8787");
    let first = tempfile::tempdir().unwrap();
    // The test sign-in approves at once; following its redirects reaches the
    // app's own loopback listener.
    account::sign_in(first.path(), None, |url| {
        let url = url.to_string();
        tokio::spawn(async move {
            let _ = reqwest::Client::new().get(url).send().await;
        });
        Ok(())
    })
    .await
    .unwrap();
    let made = account::api(first.path(), "POST", "/orgs", Some(json!({ "name": "Sync test" }))).await.unwrap();
    let org = made.pointer("/org/id").or_else(|| made.get("id")).and_then(Value::as_str).unwrap_or_else(|| panic!("{made}")).to_string();

    let a = Computer::new(&org, Some(first.path()));
    let b = Computer::new(&org, Some(first.path()));

    // A builds a library; B downloads it.
    let grace = a.db.create_song(song("Amazing Grace", "[Verse 1]\nAmazing grace")).unwrap();
    let other = a.db.create_song(song("Other", "x")).unwrap();
    let list = setlists::create_setlist(&a.db, "Sunday").unwrap();
    setlists::add_setlist_item(&a.db, &list, &grace).unwrap();
    assert_eq!(a.sync(&org).await, None);
    b.sync(&org).await;
    assert_eq!(b.words(&grace).as_deref(), Some("[Verse 1]\nAmazing grace"));
    assert_eq!(setlists::get_setlist(&b.db, &list).unwrap().unwrap().items.len(), 1);

    // Both offline: A changes the title, then the words; B changes the words too.
    a.db.update_song(&grace, song("Amazing Grace (My Chains)", "[Verse 1]\nfrom A")).unwrap();
    b.db.update_song(&grace, song("Amazing Grace", "[Verse 1]\nfrom B")).unwrap();
    a.sync(&org).await;
    b.sync(&org).await;
    a.sync(&org).await;
    for c in [&a, &b] {
        let s = c.db.get_song(&grace).unwrap().unwrap();
        assert_eq!(s.body_text, "[Verse 1]\nfrom B");
    }
    let revisions =
        account::api(first.path(), "GET", &format!("/orgs/{org}/sync/revisions?type=song&id={grace}"), None).await.unwrap();
    assert_eq!(revisions["revisions"].as_array().map(Vec::len), Some(1), "A's words were kept: {revisions}");

    // A deletes a song; it goes from B too. The song in a service can't go.
    a.db.delete_song(&other).unwrap();
    a.sync(&org).await;
    b.sync(&org).await;
    assert!(b.words(&other).is_none());

    // The change-notice ticket works for a member.
    let ticket = account::api(first.path(), "POST", &format!("/orgs/{org}/sync/ticket"), None).await.unwrap();
    assert!(ticket["url"].as_str().is_some_and(|u| u.contains("ticket=css_")), "{ticket}");

    // Nothing waits on either computer.
    for c in [&a, &b] {
        assert_eq!(super::local::waiting(&c.db.conn().lock().unwrap()).unwrap(), 0);
    }
}

#[tokio::test]
#[ignore = "needs the server running locally: see the module comment"]
async fn moving_a_library_in_reaches_a_second_computer() {
    assert!(account::server_url().starts_with("http://localhost"), "set CHURCH_SLIDES_SERVER=http://localhost:8787");
    let first = tempfile::tempdir().unwrap();
    account::sign_in(first.path(), None, |url| {
        let url = url.to_string();
        tokio::spawn(async move {
            let _ = reqwest::Client::new().get(url).send().await;
        });
        Ok(())
    })
    .await
    .unwrap();
    let made = account::api(first.path(), "POST", "/orgs", Some(json!({ "name": "Move test" }))).await.unwrap();
    let org = made.pointer("/org/id").and_then(Value::as_str).unwrap().to_string();

    // The slides computer: its own library, with a look and learned timing.
    let slides = tempfile::tempdir().unwrap();
    std::fs::copy(first.path().join("account.json"), slides.path().join("account.json")).unwrap();
    let db = Arc::new(Db::open(&slides.path().join("library.db")).unwrap());
    let ours = db.create_song(song("Our Song", "[Verse 1]\nours")).unwrap();
    crate::library_look::set(&db, &crate::library_look::LibraryLook { display: Some(r#"{"theme":"light"}"#.into()), scripture: None }).unwrap();
    {
        let conn = rusqlite::Connection::open(slides.path().join("learning.db")).unwrap();
        conn.execute_batch(
            "CREATE TABLE performances (id TEXT PRIMARY KEY, song_id TEXT NOT NULL, started_at_ms INTEGER NOT NULL,
               provenance TEXT NOT NULL, weight REAL NOT NULL, final INTEGER NOT NULL, data_json TEXT NOT NULL, updated_at_ms INTEGER NOT NULL);",
        )
        .unwrap();
        conn.execute("INSERT INTO performances VALUES ('perf1', ?1, 1, 'live', 1.0, 1, '{\"x\":1}', 1)", [&ours]).unwrap();
    }
    let songs: i64 = db.conn().lock().unwrap().query_row("SELECT COUNT(*) FROM songs", [], |r| r.get(0)).unwrap();
    crate::libraries::move_home_into(&db, &org, "Move test").unwrap();
    assert_eq!(super::cycle(&db, slides.path(), &org, &|| {}).await.unwrap(), None);
    assert_eq!(super::local::waiting(&db.conn().lock().unwrap()).unwrap(), 0);

    // A second computer gets all of it.
    let b = Computer::new(&org, Some(first.path()));
    b.sync(&org).await;
    let got: i64 = b.db.conn().lock().unwrap().query_row("SELECT COUNT(*) FROM songs", [], |r| r.get(0)).unwrap();
    assert_eq!(got, songs);
    assert_eq!(b.words(&ours).as_deref(), Some("[Verse 1]\nours"));
    assert_eq!(crate::library_look::get(&b.db).unwrap().and_then(|l| l.display).as_deref(), Some(r#"{"theme":"light"}"#));
    let learned = rusqlite::Connection::open(super::local::learning_path(&b.db.open_path()))
        .unwrap()
        .query_row("SELECT data_json FROM performances WHERE id = 'perf1'", [], |r| r.get::<_, String>(0))
        .unwrap();
    assert_eq!(learned, r#"{"x":1}"#);
}

/// Another computer edits a song in a running app's organization, so the app
/// can be watched picking it up:
/// `SYNC_E2E_ORG=<org> SYNC_E2E_SONG=<song id> CHURCH_SLIDES_SERVER=… cargo test --lib library_sync::e2e::another_computer_edits -- --ignored`
#[tokio::test]
#[ignore = "drives a running app's organization: see the doc comment"]
async fn another_computer_edits() {
    let (Ok(org), Ok(song_id)) = (std::env::var("SYNC_E2E_ORG"), std::env::var("SYNC_E2E_SONG")) else {
        panic!("set SYNC_E2E_ORG and SYNC_E2E_SONG");
    };
    let first = tempfile::tempdir().unwrap();
    account::sign_in(first.path(), None, |url| {
        let url = url.to_string();
        tokio::spawn(async move {
            let _ = reqwest::Client::new().get(url).send().await;
        });
        Ok(())
    })
    .await
    .unwrap();
    let c = Computer::new(&org, Some(first.path()));
    c.sync(&org).await;
    let mut s = c.db.get_song(&song_id).unwrap().expect("the song came down");
    s.body_text = format!("{}\n\n[Chorus]\nEdited on another computer", s.body_text);
    c.db.update_song(&song_id, song(&s.title, &s.body_text)).unwrap();
    c.sync(&org).await;
}
