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
