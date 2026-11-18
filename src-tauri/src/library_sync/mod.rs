//! Keeping an organization's library in step with its server
//! (docs/roadmap/LIBRARIES_AND_SYNC.md; the protocol is in
//! docs/server-protocol/README.md, "Sync").
//!
//! While an organization's library is open, one loop sends this computer's
//! changes (a few seconds after they're made) and pulls everyone else's: at
//! once when the app window's change-notice socket says so (`sync_now`), and
//! every minute regardless. There's no Sync button: a status shows only when
//! something's waiting or wrong.

pub mod local;

use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use anyhow::{Context, Result};
use serde::Serialize;
use serde_json::{json, Value};
use tauri::{Emitter, Manager};

use crate::db::Db;

pub const STATUS_EVENT: &str = "library-sync";

/// How often changes are looked for while some wait, and how often the
/// server is asked anyway.
const PUSH_EVERY: Duration = Duration::from_secs(3);
const PULL_EVERY: Duration = Duration::from_secs(60);

#[derive(Debug, Clone, Serialize, PartialEq, Default)]
pub struct SyncStatus {
    /// The organization whose library is open, if any.
    pub org_id: Option<String>,
    /// "off" (this computer's library), "syncing", "ok", "offline",
    /// "signed_out" or "error".
    pub state: &'static str,
    /// Changes made here that haven't gone up yet.
    pub waiting: i64,
    /// What went wrong, or a change the server refused, in words.
    pub message: Option<String>,
}

#[derive(Default)]
pub struct LibrarySync {
    status: Mutex<SyncStatus>,
    kick: tokio::sync::Notify,
}

/// The organization an open library file belongs to: `<app data>/orgs/<id>/library.db`.
pub fn org_of(home_path: &Path, open: &Path) -> Option<String> {
    if open == home_path {
        return None;
    }
    let id = open.parent()?.file_name()?.to_str()?.to_string();
    let orgs = open.parent()?.parent()?;
    (orgs.file_name()? == "orgs" && Some(orgs.parent()?) == home_path.parent()).then_some(id)
}

impl LibrarySync {
    pub fn status(&self) -> SyncStatus {
        self.status.lock().unwrap().clone()
    }

    /// Pull now (a change notice came, or a library was opened).
    pub fn kick(&self) {
        self.kick.notify_one();
    }

    fn set(&self, app: &tauri::AppHandle, status: SyncStatus) {
        let mut cur = self.status.lock().unwrap();
        if *cur != status {
            *cur = status.clone();
            let _ = app.emit(STATUS_EVENT, status);
        }
    }

    /// Run the loop for the life of the app.
    pub fn start(self: &Arc<Self>, app: tauri::AppHandle, db: Arc<Db>, app_data: PathBuf) {
        let me = self.clone();
        tauri::async_runtime::spawn(async move {
            let mut last_pull: Option<(String, Instant)> = None;
            let mut backoff = PUSH_EVERY;
            loop {
                let pull_due = |org: &str, last: &Option<(String, Instant)>| {
                    last.as_ref().map_or(true, |(o, at)| o != org || at.elapsed() >= PULL_EVERY)
                };
                let org = org_of(db.home_path(), &db.open_path());
                let mut wait = PUSH_EVERY;
                match &org {
                    None => {
                        me.set(&app, SyncStatus { state: "off", ..Default::default() });
                        last_pull = None;
                    }
                    Some(org) => {
                        let waiting = with_conn(&db, local::waiting).unwrap_or(0);
                        if waiting > 0 || pull_due(org, &last_pull) {
                            let previous = me.status();
                            me.set(&app, SyncStatus { org_id: Some(org.clone()), state: "syncing", waiting, message: previous.message.clone() });
                            let changed = || {
                                let _ = app.emit(crate::library_watch::LIBRARY_CHANGED_EVENT, ());
                            };
                            match cycle(&db, &app_data, org, &changed).await {
                                Ok(message) => {
                                    last_pull = Some((org.clone(), Instant::now()));
                                    backoff = PUSH_EVERY;
                                    let waiting = with_conn(&db, local::waiting).unwrap_or(0);
                                    // A refusal stays shown until the next one, so it isn't missed.
                                    let message = message.or(previous.message.filter(|_| previous.state == "ok"));
                                    me.set(&app, SyncStatus { org_id: Some(org.clone()), state: "ok", waiting, message });
                                }
                                Err(e) => {
                                    let signed_out = e.is::<crate::account::SignedOut>();
                                    let text = format!("{e:#}");
                                    let offline = text.contains("Couldn't reach the server");
                                    log::info!("library sync for {org}: {text}");
                                    me.set(&app, SyncStatus {
                                        org_id: Some(org.clone()),
                                        state: if signed_out { "signed_out" } else if offline { "offline" } else { "error" },
                                        waiting,
                                        message: (!signed_out && !offline).then_some(text),
                                    });
                                    wait = backoff;
                                    backoff = (backoff * 2).min(PULL_EVERY);
                                }
                            }
                        }
                    }
                }
                tokio::select! {
                    _ = me.kick.notified() => { last_pull = None; }
                    _ = tokio::time::sleep(wait) => {}
                }
            }
        });
    }
}

fn with_conn<T>(db: &Db, f: impl FnOnce(&rusqlite::Connection) -> Result<T>) -> Result<T> {
    let conn = db.conn().lock().unwrap();
    f(&conn)
}

/// Run `f` on the open library only if it's still `org`'s (a switch may have
/// happened while the network was busy).
fn on_org<T>(db: &Db, org: &str, f: impl FnOnce(&rusqlite::Connection) -> Result<T>) -> Result<Option<T>> {
    let conn = db.conn().lock().unwrap();
    if org_of(db.home_path(), &db.open_path()).as_deref() != Some(org) {
        return Ok(None);
    }
    f(&conn).map(Some)
}

/// One round: everything waiting goes up, then everything new comes down.
/// Returns a refusal to show, if the server turned a change down.
/// `changed` is called when records here changed (to refresh the windows).
async fn cycle(db: &Db, app_data: &Path, org: &str, changed: &(dyn Fn() + Sync)) -> Result<Option<String>> {
    let mut message = None;
    let mut conflicts = 0;
    // Push, a batch at a time.
    for _ in 0..20 {
        let Some((device, batch)) = on_org(db, org, |c| Ok((local::device(c)?, local::prepare_push(c)?)))? else {
            return Ok(None);
        };
        if batch.is_empty() {
            break;
        }
        let body = json!({ "device": device, "changes": batch.iter().map(local::Pending::to_json).collect::<Vec<_>>() });
        let resp = crate::account::api(app_data, "POST", &format!("/orgs/{org}/sync/push"), Some(body)).await?;
        let results: Vec<local::PushResult> =
            serde_json::from_value(resp.get("results").cloned().unwrap_or(Value::Null)).context("reading the server's reply")?;
        let Some(outcome) = on_org(db, org, |c| local::apply_push(c, &batch, &results))? else {
            return Ok(None);
        };
        conflicts += outcome.conflicts;
        if let Some((code, text)) = outcome.refused.last() {
            message = Some(refusal_text(code, text, org, app_data));
        }
        if !outcome.refused.is_empty() {
            changed();
        }
    }
    if conflicts > 0 {
        log::info!("library sync: {conflicts} song(s) changed on two computers; the other words were kept as a revision");
    }
    // Pull until the server has nothing newer.
    let Some(mut after) = on_org(db, org, local::cursor)? else { return Ok(None) };
    let mut pulled = 0;
    for _ in 0..1000 {
        let page = crate::account::api(app_data, "GET", &format!("/orgs/{org}/sync/pull?after={after}"), None).await?;
        let seq = page.get("seq").and_then(Value::as_i64).unwrap_or(after);
        if seq < after {
            on_org(db, org, local::reset)?;
            after = 0;
            continue;
        }
        let records: Vec<local::Pulled> =
            serde_json::from_value(page.get("records").cloned().unwrap_or(json!([]))).context("reading the server's records")?;
        let Some(n) = on_org(db, org, |c| local::apply_pull(c, &records, seq))? else { return Ok(None) };
        pulled += n;
        after = seq;
        if !page.get("more").and_then(Value::as_bool).unwrap_or(false) {
            break;
        }
    }
    if pulled > 0 {
        changed();
    }
    Ok(message)
}

fn refusal_text(code: &str, text: &str, org: &str, app_data: &Path) -> String {
    let name = crate::account::memberships(app_data)
        .into_iter()
        .find(|m| m.org_id == org)
        .map(|m| m.org_name)
        .unwrap_or_else(|| "this organization".into());
    match code {
        "forbidden" => format!("Couldn't save a change: your role can't edit that in {name}."),
        "in_use" => "Couldn't delete it: a service still uses it.".into(),
        "deleted" => "Someone deleted that on another computer.".into(),
        "too_large" => format!("Couldn't save a change: {text}"),
        _ => format!("Couldn't save a change: {text}"),
    }
}

// --- Commands ------------------------------------------------------------------

#[tauri::command]
pub fn library_sync_status(app: tauri::AppHandle) -> SyncStatus {
    app.state::<Arc<LibrarySync>>().status()
}

/// Pull now: the window's change-notice socket heard of a change.
#[tauri::command]
pub fn library_sync_now(app: tauri::AppHandle) {
    app.state::<Arc<LibrarySync>>().kick();
}

/// A one-use address for the open organization's change notices.
#[tauri::command]
pub async fn library_sync_ticket(app: tauri::AppHandle, org_id: String) -> Result<String, String> {
    if org_id.is_empty() || !org_id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_') {
        return Err("Not an organization id.".into());
    }
    let app_data = app.path().app_data_dir().map_err(|e| e.to_string())?;
    let resp = crate::account::api(&app_data, "POST", &format!("/orgs/{org_id}/sync/ticket"), None)
        .await
        .map_err(|e| format!("{e:#}"))?;
    resp.get("url").and_then(Value::as_str).map(String::from).ok_or_else(|| "The server sent no address.".into())
}

#[cfg(test)]
mod tests;
#[cfg(test)]
mod e2e;
