//! Tauri commands exposed to the React UI.

use std::sync::Arc;

use std::collections::HashMap;

use crate::band::{BandInfo, BandServer};
use crate::charts::{self, ChartImportItem, ChartImportSummary, ChartRow};
use crate::references::{self, NewReference, ReferenceSummary};
use crate::db::{Db, NewSong, SongRow, SortKey};
use crate::keep_awake::KeepAwake;
use crate::library_export::{
    export_library_json, import_library_json, ImportMode, ImportSummary,
};
use crate::setlists::{self, ReadingRow, SetlistDetail, SetlistRow};
use crate::settings::{self, Settings};

#[tauri::command]
pub async fn list_songs(state: tauri::State<'_, Arc<Db>>) -> Result<Vec<SongRow>, String> {
    state.list_songs().map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn list_songs_sorted(
    state: tauri::State<'_, Arc<Db>>,
    sort: SortKey,
) -> Result<Vec<SongRow>, String> {
    state.list_songs_sorted(sort).map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn search_songs(
    state: tauri::State<'_, Arc<Db>>,
    query: String,
) -> Result<Vec<SongRow>, String> {
    state.search_songs(&query).map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn get_song(
    state: tauri::State<'_, Arc<Db>>,
    id: String,
) -> Result<Option<SongRow>, String> {
    state.get_song(&id).map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn create_song(
    state: tauri::State<'_, Arc<Db>>,
    input: NewSong,
) -> Result<String, String> {
    state.create_song(input).map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn update_song(
    state: tauri::State<'_, Arc<Db>>,
    id: String,
    input: NewSong,
) -> Result<(), String> {
    state.update_song(&id, input).map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn delete_song(
    state: tauri::State<'_, Arc<Db>>,
    id: String,
) -> Result<(), String> {
    state.delete_song(&id).map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn get_settings(state: tauri::State<'_, Arc<Db>>) -> Result<Settings, String> {
    settings::get(&state).map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn update_settings(
    state: tauri::State<'_, Arc<Db>>,
    settings: Settings,
) -> Result<Settings, String> {
    crate::settings::set(&state, &settings).map_err(|e| e.to_string())?;
    // What was saved, as `get` reads it: reports count only once answered.
    let saved = crate::settings::get(&state).map_err(|e| e.to_string())?;
    crate::telemetry::set_enabled(saved.send_error_reports);
    crate::telemetry::set_contact(&saved.report_contact);
    Ok(saved)
}

#[tauri::command]
pub async fn export_library(state: tauri::State<'_, Arc<Db>>) -> Result<String, String> {
    export_library_json(&state).map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn import_library(
    state: tauri::State<'_, Arc<Db>>,
    json: String,
    mode: ImportMode,
) -> Result<ImportSummary, String> {
    import_library_json(&state, &json, mode).map_err(|e| e.to_string())
}

// --- Setlists ---------------------------------------------------------------

#[tauri::command]
pub async fn list_setlists(state: tauri::State<'_, Arc<Db>>) -> Result<Vec<SetlistRow>, String> {
    setlists::list_setlists(&state).map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn get_setlist(
    state: tauri::State<'_, Arc<Db>>,
    id: String,
) -> Result<Option<SetlistDetail>, String> {
    setlists::get_setlist(&state, &id).map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn create_setlist(
    state: tauri::State<'_, Arc<Db>>,
    name: String,
) -> Result<String, String> {
    setlists::create_setlist(&state, &name).map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn update_setlist_name(
    state: tauri::State<'_, Arc<Db>>,
    id: String,
    name: String,
) -> Result<(), String> {
    setlists::update_setlist_name(&state, &id, &name).map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn delete_setlist(
    state: tauri::State<'_, Arc<Db>>,
    id: String,
) -> Result<(), String> {
    setlists::delete_setlist(&state, &id).map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn add_setlist_item(
    state: tauri::State<'_, Arc<Db>>,
    setlist_id: String,
    song_id: String,
) -> Result<String, String> {
    setlists::add_setlist_item(&state, &setlist_id, &song_id).map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn add_service_item(
    state: tauri::State<'_, Arc<Db>>,
    setlist_id: String,
    kind: String,
    content: String,
    slides_override: Option<String>,
    after_item_id: Option<String>,
) -> Result<String, String> {
    setlists::add_service_item(
        &state,
        &setlist_id,
        &kind,
        &content,
        slides_override.as_deref(),
        after_item_id.as_deref(),
    )
    .map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn set_setlist_item_content(
    state: tauri::State<'_, Arc<Db>>,
    setlist_id: String,
    item_id: String,
    content: String,
) -> Result<(), String> {
    setlists::set_setlist_item_content(&state, &setlist_id, &item_id, &content)
        .map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn duplicate_setlist(
    state: tauri::State<'_, Arc<Db>>,
    source_id: String,
    name: String,
) -> Result<String, String> {
    setlists::duplicate_setlist(&state, &source_id, &name).map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn list_readings(state: tauri::State<'_, Arc<Db>>) -> Result<Vec<ReadingRow>, String> {
    setlists::list_readings(&state).map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn save_reading(
    state: tauri::State<'_, Arc<Db>>,
    title: String,
    body_text: String,
) -> Result<String, String> {
    setlists::save_reading(&state, &title, &body_text).map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn delete_reading(state: tauri::State<'_, Arc<Db>>, id: String) -> Result<(), String> {
    setlists::delete_reading(&state, &id).map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn remove_setlist_item(
    state: tauri::State<'_, Arc<Db>>,
    setlist_id: String,
    item_id: String,
) -> Result<(), String> {
    setlists::remove_setlist_item(&state, &setlist_id, &item_id).map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn set_setlist_item_slides(
    state: tauri::State<'_, Arc<Db>>,
    setlist_id: String,
    item_id: String,
    slides_override: Option<String>,
) -> Result<(), String> {
    setlists::set_setlist_item_slides(
        &state,
        &setlist_id,
        &item_id,
        slides_override.as_deref(),
    )
    .map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn set_setlist_item_look(
    state: tauri::State<'_, Arc<Db>>,
    setlist_id: String,
    item_id: String,
    look: Option<String>,
) -> Result<(), String> {
    setlists::set_setlist_item_look(&state, &setlist_id, &item_id, look.as_deref())
        .map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn set_setlist_item_key(
    state: tauri::State<'_, Arc<Db>>,
    setlist_id: String,
    item_id: String,
    key: Option<String>,
) -> Result<(), String> {
    setlists::set_setlist_item_key(&state, &setlist_id, &item_id, key.as_deref())
        .map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn reorder_setlist(
    state: tauri::State<'_, Arc<Db>>,
    setlist_id: String,
    item_ids: Vec<String>,
) -> Result<(), String> {
    setlists::reorder_setlist(&state, &setlist_id, &item_ids).map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn mark_setlist_used(
    state: tauri::State<'_, Arc<Db>>,
    id: String,
) -> Result<(), String> {
    setlists::mark_setlist_used(&state, &id).map_err(|e| e.to_string())
}

/// The most recent services and the songs each held, newest first — for
/// library search's "haven't played lately" filters.
#[tauri::command]
pub async fn list_recent_services(
    state: tauri::State<'_, Arc<Db>>,
    limit: u32,
) -> Result<Vec<crate::services::ServiceRow>, String> {
    crate::services::list_recent_services(&state, limit).map_err(|e| e.to_string())
}

// --- Chord charts ------------------------------------------------------------

#[tauri::command]
pub async fn get_song_charts(
    state: tauri::State<'_, Arc<Db>>,
    song_ids: Vec<String>,
) -> Result<HashMap<String, ChartRow>, String> {
    charts::get_charts(&state, &song_ids).map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn list_chart_keys(
    state: tauri::State<'_, Arc<Db>>,
) -> Result<HashMap<String, Option<String>>, String> {
    charts::list_chart_keys(&state).map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn list_chart_meta(
    state: tauri::State<'_, Arc<Db>>,
) -> Result<HashMap<String, charts::ChartMeta>, String> {
    charts::list_chart_meta(&state).map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn delete_song_chart(
    state: tauri::State<'_, Arc<Db>>,
    song_id: String,
) -> Result<(), String> {
    charts::delete_chart(&state, &song_id).map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn import_charts(
    state: tauri::State<'_, Arc<Db>>,
    items: Vec<ChartImportItem>,
) -> Result<ChartImportSummary, String> {
    charts::import_charts(&state, items).map_err(|e| e.to_string())
}

// --- Score-follower references (lead sheets, later recorded services) --------

#[tauri::command]
pub async fn list_song_references(
    state: tauri::State<'_, Arc<Db>>,
    song_id: Option<String>,
) -> Result<Vec<ReferenceSummary>, String> {
    references::list(&state, song_id.as_deref()).map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn save_song_reference(
    state: tauri::State<'_, Arc<Db>>,
    reference: NewReference,
) -> Result<ReferenceSummary, String> {
    references::save(&state, reference).map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn delete_song_reference(
    state: tauri::State<'_, Arc<Db>>,
    id: String,
) -> Result<(), String> {
    references::delete(&state, &id).map_err(|e| e.to_string())
}

// --- Band screens ------------------------------------------------------------

#[tauri::command]
pub async fn band_start(
    app: tauri::AppHandle,
    band: tauri::State<'_, BandServer>,
) -> Result<BandInfo, String> {
    band.start(app).await
}

#[tauri::command]
pub fn band_info(band: tauri::State<'_, BandServer>) -> Option<BandInfo> {
    band.info()
}

#[tauri::command]
pub fn band_publish(
    band: tauri::State<'_, BandServer>,
    kind: String,
    payload: serde_json::Value,
) -> Result<(), String> {
    band.publish(&kind, payload)
}

#[tauri::command]
pub fn band_clear(band: tauri::State<'_, BandServer>) {
    band.clear();
}

/// Keep the display awake while presenting (see `keep_awake`).
#[tauri::command]
pub fn set_keep_awake(state: tauri::State<'_, KeepAwake>, on: bool) -> Result<(), String> {
    state.set(on)
}

// --- Online band relay (church's own Cloudflare account) ---------------------

fn app_data(app: &tauri::AppHandle) -> Result<std::path::PathBuf, String> {
    use tauri::Manager;
    app.path().app_data_dir().map_err(|e| e.to_string())
}

// --- Account (optional sign-in to a Church Slides server) ----------------------

/// Off the main thread: the first read of the sign-in can wait on a macOS
/// Keychain prompt, and on the main thread that froze every window.
#[tauri::command]
pub async fn account_status(app: tauri::AppHandle) -> Result<crate::account::AccountStatus, String> {
    let dir = app_data(&app)?;
    off_main(move || Ok(crate::account::status(&dir))).await
}

/// Check the sign-in with the server; offline, returns what's known.
#[tauri::command]
pub async fn account_refresh(app: tauri::AppHandle) -> Result<crate::account::AccountStatus, String> {
    crate::account::refresh_status(&app_data(&app)?).await.map_err(|e| format!("{e:#}"))
}

#[tauri::command]
pub async fn account_sign_in(app: tauri::AppHandle, invite: Option<String>) -> Result<crate::account::AccountStatus, String> {
    use tauri_plugin_shell::ShellExt;
    let shell = app.shell();
    // Same opener as the Cloudflare sign-in; moving both to tauri-plugin-opener is separate work.
    #[allow(deprecated)]
    crate::account::sign_in(&app_data(&app)?, invite.as_deref(), |url| shell.open(url, None).map_err(|e| anyhow::anyhow!("{e}")))
        .await
        .map_err(|e| format!("{e:#}"))
}

/// A call to the signed-in API (organizations, members, invites).
#[tauri::command]
pub async fn account_api(
    app: tauri::AppHandle,
    method: String,
    path: String,
    body: Option<serde_json::Value>,
) -> Result<serde_json::Value, String> {
    crate::account::api(&app_data(&app)?, &method, &path, body).await.map_err(|e| format!("{e:#}"))
}

#[tauri::command]
pub async fn account_sign_out(app: tauri::AppHandle) -> Result<crate::account::AccountStatus, String> {
    crate::account::sign_out(&app_data(&app)?).await.map_err(|e| format!("{e:#}"))
}

#[tauri::command]
pub async fn account_sign_out_everywhere(app: tauri::AppHandle) -> Result<crate::account::AccountStatus, String> {
    crate::account::sign_out_everywhere(&app_data(&app)?).await.map_err(|e| format!("{e:#}"))
}

// The relay's commands read its keys from the Keychain, which can wait on a
// prompt: never on the main thread (see `account_status`).
#[tauri::command]
pub async fn cloud_relay_status(app: tauri::AppHandle) -> Result<crate::cloud::CloudStatus, String> {
    let dir = app_data(&app)?;
    off_main(move || Ok(crate::cloud::status(&dir))).await
}

/// Set up with an API token the operator pasted (Cloudflare's "Edit
/// Cloudflare Workers" template). `account_id` answers a `choose_account`.
#[tauri::command]
pub async fn cloud_relay_setup_token(
    app: tauri::AppHandle,
    token: String,
    account_id: Option<String>,
) -> Result<crate::cloud::SetupOutcome, String> {
    crate::cloud::setup_with_token(&app_data(&app)?, token.trim(), None, account_id)
        .await
        .map_err(|e| format!("{e:#}"))
}

/// Sign in with Cloudflare in the browser, then deploy.
#[tauri::command]
pub async fn cloud_relay_setup_oauth(
    app: tauri::AppHandle,
    account_id: Option<String>,
) -> Result<crate::cloud::SetupOutcome, String> {
    use tauri_plugin_shell::ShellExt;
    let shell = app.shell();
    #[allow(deprecated)]
    let (access, refresh) = crate::cloud::oauth_sign_in(|url| shell.open(url, None).map_err(|e| anyhow::anyhow!("{e}")))
        .await
        .map_err(|e| format!("{e:#}"))?;
    crate::cloud::setup_with_token(&app_data(&app)?, &access, refresh, account_id)
        .await
        .map_err(|e| format!("{e:#}"))
}

#[tauri::command]
pub async fn cloud_relay_redeploy(app: tauri::AppHandle) -> Result<crate::cloud::CloudStatus, String> {
    crate::cloud::redeploy(&app_data(&app)?).await.map_err(|e| format!("{e:#}"))
}

#[tauri::command]
pub async fn cloud_relay_health(app: tauri::AppHandle) -> Result<bool, String> {
    crate::cloud::health(&app_data(&app)?).await.map_err(|e| format!("{e:#}"))
}

#[tauri::command]
pub async fn cloud_relay_forget(app: tauri::AppHandle) -> Result<(), String> {
    let dir = app_data(&app)?;
    off_main(move || crate::cloud::forget(&dir).map_err(|e| e.to_string())).await
}

/// Replace this computer's room: old QR codes and bookmarks stop working.
#[tauri::command]
pub async fn cloud_relay_new_links(app: tauri::AppHandle) -> Result<(), String> {
    let dir = app_data(&app)?;
    off_main(move || crate::cloud::new_links(&dir).map_err(|e| e.to_string())).await
}

/// This computer's room (the same one each time it's opened).
#[tauri::command]
pub async fn cloud_relay_new_room(app: tauri::AppHandle, pin: String) -> Result<crate::cloud::CloudRoom, String> {
    let dir = app_data(&app)?;
    off_main(move || crate::cloud::new_room(&dir, &pin).map_err(|e| e.to_string())).await
}

/// Run blocking work (the Keychain) on a worker thread.
async fn off_main<T: Send + 'static>(work: impl FnOnce() -> Result<T, String> + Send + 'static) -> Result<T, String> {
    tauri::async_runtime::spawn_blocking(work).await.map_err(|e| e.to_string())?
}

/// The library index the leader's search screen uses — the slides computer
/// answers it for devices on the online relay (the local server reads it itself).
#[tauri::command]
pub async fn band_library_index(state: tauri::State<'_, Arc<Db>>) -> Result<Vec<serde_json::Value>, String> {
    crate::band::library_index(&state).map_err(|e| e.to_string())
}

/// Open a Cloudflare page (sign-up, API tokens) in the system browser. Only
/// Cloudflare's own sites: this isn't a general "open any URL" door.
#[tauri::command]
pub fn cloud_relay_open_page(app: tauri::AppHandle, url: String) -> Result<(), String> {
    use tauri_plugin_shell::ShellExt;
    let parsed = url::Url::parse(&url).map_err(|e| e.to_string())?;
    let allowed = parsed.scheme() == "https"
        && matches!(parsed.host_str(), Some("dash.cloudflare.com" | "developers.cloudflare.com" | "www.cloudflare.com"));
    if !allowed {
        return Err("Only Cloudflare pages can be opened from here".into());
    }
    #[allow(deprecated)]
    app.shell().open(url, None).map_err(|e| e.to_string())
}
