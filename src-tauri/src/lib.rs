mod account;
mod assistant_link;
mod band;
mod charts;
mod cloud;
mod commands;
mod db;
mod diagnostics;
mod feedback;
mod keep_awake;
mod library_export;
mod library_watch;
mod mcp;
mod other_apps;
mod peer_sync;
mod recordings;
mod references;
mod screens;
mod scriptures;
mod seed_hymns;
mod services;
mod setlists;
mod settings;
mod sidecar;
mod telemetry;

use std::sync::Arc;

use tauri::Manager;
use tokio::sync::watch;

use crate::db::Db;
use crate::sidecar::EngineState;

/// Run as a headless MCP server for AI assistants instead of the app (`--mcp`).
pub fn run_mcp_stdio(args: &[String]) -> i32 {
    mcp::run_stdio(args)
}

/// Holds runtime state shared across Tauri commands.
struct AppState {
    engine: watch::Receiver<EngineState>,
}

/// Wait for the AI engine and return its port — or, if it can't run, the
/// reason, immediately rather than never.
#[tauri::command]
async fn get_sidecar_port(state: tauri::State<'_, AppState>) -> Result<u16, String> {
    let mut rx = state.engine.clone();
    loop {
        match &*rx.borrow() {
            EngineState::Ready { port } => return Ok(*port),
            EngineState::Failed { reason } => return Err(reason.clone()),
            EngineState::Starting => {}
        }
        rx.changed()
            .await
            .map_err(|_| "the AI engine's supervisor went away".to_string())?;
    }
}

/// The AI engine's state right now, without waiting. The UI polls this to
/// show whether live AI is available and, if not, why.
#[tauri::command]
fn get_ai_engine_status(state: tauri::State<'_, AppState>) -> EngineState {
    state.engine.borrow().clone()
}

/// A UI console error or warning, into the app's log file (see
/// `src/lib/uiLog.ts`). Async so it runs off the main thread: an error report
/// reads the log's tail.
#[tauri::command]
async fn log_from_ui(level: String, message: String) {
    let message: String = message.chars().take(4000).collect();
    match level.as_str() {
        "error" => {
            log::error!(target: "ui", "{message}");
            telemetry::report_ui_error(&message);
        }
        "warn" => log::warn!(target: "ui", "{message}"),
        _ => log::info!(target: "ui", "{message}"),
    }
}

/// A file the song import couldn't read: reported if error reports are on
/// (`telemetry::report_unreadable_import`). Never the file's contents.
#[tauri::command]
async fn report_unreadable_import(extension: String, reason: String, signature: String) {
    log::info!(target: "ui", "import couldn't read a .{extension} file: {reason} ({signature})");
    telemetry::report_unreadable_import(&extension, &reason, &signature);
}

/// Write a diagnostics report — this computer, what the UI says about live
/// AI (`ui_report`), and the recent log — to Downloads, show it in Finder,
/// and return its path.
///
/// Off the main thread: it runs `sysctl`/`sw_vers` and reads up to 8 MB of log.
#[tauri::command]
async fn save_diagnostics(
    app: tauri::AppHandle,
    state: tauri::State<'_, AppState>,
    ui_report: serde_json::Value,
) -> Result<String, String> {
    let engine = serde_json::to_string(&*state.engine.borrow()).unwrap_or_default();
    let version = app.package_info().version.to_string();
    tauri::async_runtime::spawn_blocking(move || {
        let report = diagnostics::build_report(&version, &engine, &ui_report);
        let path = diagnostics::save_report(&report)?;
        if let Err(e) = diagnostics::reveal(&path) {
            log::warn!("couldn't show the diagnostics file: {e}");
        }
        Ok(path.display().to_string())
    })
    .await
    .map_err(|e| format!("couldn't save diagnostics: {e}"))?
}

/// Open the folder the log file is in.
#[tauri::command]
fn open_log_folder() -> Result<(), String> {
    let dir = diagnostics::log_dir().ok_or("no log folder on this system")?;
    diagnostics::reveal(&dir.join("church-slides.log"))
}

/// The open-source licences bundled with the app (THIRD-PARTY-NOTICES.txt,
/// written by scripts/third_party_notices.py), for Settings.
#[tauri::command]
async fn read_third_party_notices(app: tauri::AppHandle) -> Result<String, String> {
    let path = app
        .path()
        .resource_dir()
        .map_err(|e| e.to_string())?
        .join("THIRD-PARTY-NOTICES.txt");
    tauri::async_runtime::spawn_blocking(move || {
        std::fs::read_to_string(&path).map_err(|e| format!("couldn't read {}: {e}", path.display()))
    })
    .await
    .map_err(|e| e.to_string())?
}

/// Label of the second-screen audience window.
const AUDIENCE_LABEL: &str = "audience";

/// Open the audience projection window (or focus it if already open).
///
/// Created from Rust rather than the JS `WebviewWindow` API: custom commands
/// aren't ACL-gated, so this avoids the webview-creation capability dance that
/// silently failed from the frontend. The window loads the same bundle with
/// `?window=audience`, which the React entry point detects.
///
/// With `fill_spare`, a monitor none of this app's windows is on (the
/// projector, almost always — or wherever the operator last moved it, see
/// `screens`) gets the window, fullscreen — so starting a
/// service needs no dragging. Returns whether that happened; with no spare
/// monitor it opens as a normal window on this one, to be dragged by hand.
#[tauri::command]
async fn open_audience_window(app: tauri::AppHandle, fill_spare: Option<bool>) -> Result<bool, String> {
    if let Some(win) = app.get_webview_window(AUDIENCE_LABEL) {
        let _ = win.set_focus();
        return Ok(win.is_fullscreen().unwrap_or(false));
    }
    let spare = if fill_spare.unwrap_or(false) { screens::pick_monitor(&app, AUDIENCE_LABEL) } else { None };
    let mut builder = tauri::WebviewWindowBuilder::new(
        &app,
        AUDIENCE_LABEL,
        tauri::WebviewUrl::App("index.html?window=audience".into()),
    )
    .title("Church Slides — Audience")
    .inner_size(1280.0, 720.0)
    .resizable(true)
    .decorations(true);
    if let Some(m) = &spare {
        let pos = m.position().to_logical::<f64>(m.scale_factor());
        builder = builder.position(pos.x + 40.0, pos.y + 40.0);
    }
    let win = builder
        .build()
        .map_err(|e| format!("failed to open audience window: {e}"))?;
    if spare.is_some() {
        win.set_fullscreen(true).map_err(|e| e.to_string())?;
    }
    Ok(spare.is_some())
}

/// How many displays are connected — the start button presents on a second
/// one when there is one, and on this screen when there isn't.
#[tauri::command]
fn monitor_count(app: tauri::AppHandle) -> usize {
    app.get_webview_window("main")
        .and_then(|w| w.available_monitors().ok())
        .map(|m| m.len())
        .unwrap_or(1)
}

#[tauri::command]
async fn close_audience_window(app: tauri::AppHandle) -> Result<(), String> {
    if let Some(win) = app.get_webview_window(AUDIENCE_LABEL) {
        win.close().map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
async fn toggle_audience_fullscreen(app: tauri::AppHandle) -> Result<(), String> {
    if let Some(win) = app.get_webview_window(AUDIENCE_LABEL) {
        let on = win.is_fullscreen().map_err(|e| e.to_string())?;
        win.set_fullscreen(!on).map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
fn is_audience_window_open(app: tauri::AppHandle) -> bool {
    app.get_webview_window(AUDIENCE_LABEL).is_some()
}

const DEBUG_LABEL: &str = "debug";

/// Open the debug panel as its own window (or focus it if already open).
///
/// Same Rust-side construction as the audience window, and for the same
/// reason — custom commands aren't ACL-gated. It loads the bundle with
/// `?window=debug`, which `App` detects.
///
/// The panel docks over the bottom-right of the operator screen, which is
/// where the setlist and the slide grid are. Detaching is what makes it
/// usable on a second monitor beside the app rather than on top of it.
/// Every tab reads module-scoped live stores fed by the shared sidecar
/// socket, and each window opens its own, so the panel needs nothing from
/// the shell it came out of.
#[tauri::command]
async fn open_debug_window(app: tauri::AppHandle) -> Result<(), String> {
    if let Some(win) = app.get_webview_window(DEBUG_LABEL) {
        let _ = win.set_focus();
        return Ok(());
    }
    tauri::WebviewWindowBuilder::new(
        &app,
        DEBUG_LABEL,
        tauri::WebviewUrl::App("index.html?window=debug".into()),
    )
    .title("Church Slides — Debug")
    .inner_size(660.0, 560.0)
    .resizable(true)
    .decorations(true)
    .build()
    .map_err(|e| format!("failed to open debug window: {e}"))?;
    Ok(())
}

#[tauri::command]
async fn close_debug_window(app: tauri::AppHandle) -> Result<(), String> {
    if let Some(win) = app.get_webview_window(DEBUG_LABEL) {
        win.close().map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
fn is_debug_window_open(app: tauri::AppHandle) -> bool {
    app.get_webview_window(DEBUG_LABEL).is_some()
}

/// Band screens on this computer's own extra monitors, for team members
/// without a tablet. Each is an ordinary band-screen client in its own
/// window: it loads the same page phones do and follows the operator over the
/// band server's socket on localhost, so nothing about presenting changes.
///
/// These are display monitors nobody sits at, so the window goes straight to
/// fullscreen on a monitor no other app window is on. With no spare monitor
/// it opens as a normal window on this one, to be dragged and made
/// fullscreen by hand (F).
///
/// Labelled `band-1`, `band-2`, … reusing the lowest free number, so "band
/// screen 2" keeps its own text size and capo (stored per label, see
/// `BandView`) across being closed and reopened.
#[tauri::command]
async fn open_band_window(
    app: tauri::AppHandle,
    band: tauri::State<'_, band::BandServer>,
) -> Result<String, String> {
    let info = band.start(app.clone()).await?;
    let n = (1..)
        .find(|n| app.get_webview_window(&format!("band-{n}")).is_none())
        .expect("a free band window number");
    let label = format!("band-{n}");
    let spare = screens::pick_monitor(&app, &label);
    let mut builder = tauri::WebviewWindowBuilder::new(
        &app,
        &label,
        tauri::WebviewUrl::App(format!("index.html?window=band&bandPort={}", info.port).into()),
    )
    .title(format!("Band screen {n} — Church Slides"))
    .inner_size(1024.0, 768.0)
    .resizable(true);
    if let Some(m) = &spare {
        let pos = m.position().to_logical::<f64>(m.scale_factor());
        builder = builder.position(pos.x + 40.0, pos.y + 40.0);
    }
    let win = builder
        .build()
        .map_err(|e| format!("failed to open a band screen window: {e}"))?;
    if spare.is_some() {
        win.set_fullscreen(true).map_err(|e| e.to_string())?;
    }
    Ok(label)
}

/// Close every band screen window on this computer — they're on monitors
/// nobody is sitting at, so the operator closes them from here.
#[tauri::command]
fn close_band_windows(app: tauri::AppHandle) -> usize {
    let mut closed = 0;
    for (label, win) in app.webview_windows() {
        if label.starts_with("band-") && win.close().is_ok() {
            closed += 1;
        }
    }
    closed
}

/// Fullscreen on and off for whichever window asks — a band screen on a
/// spare monitor has no other way to lose its title bar. Returns the new state.
#[tauri::command]
fn toggle_own_fullscreen(window: tauri::WebviewWindow) -> Result<bool, String> {
    let on = !window.is_fullscreen().map_err(|e| e.to_string())?;
    window.set_fullscreen(on).map_err(|e| e.to_string())?;
    Ok(on)
}

/// How many band screen windows are open on this computer.
#[tauri::command]
fn count_band_windows(app: tauri::AppHandle) -> usize {
    app.webview_windows().keys().filter(|l| l.starts_with("band-")).count()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // The user's error-report choice, read from the database file before
    // anything can go wrong (opening it can panic). Until it's known nothing
    // is sent; a choice that can't be read counts as off.
    if let Some(dir) = dirs::data_dir() {
        let path = db::default_db_path(dir.join("com.churchslides.app"));
        telemetry::set_enabled(settings::report_choice(&path));
    }
    // Before the logger, so its first lines are already breadcrumbs. Held
    // for the life of the app: dropping it flushes queued reports.
    let _error_reports = telemetry::init();
    diagnostics::init();

    let (engine_tx, engine_rx) = watch::channel(EngineState::Starting);

    let mut builder = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init());

    // Agent UI driver. Compiled out of `tauri build`; the plugin's WS
    // server never binds in a production binary. The `ui-driver` feature
    // keeps it in the performance suite's optimised build only.
    #[cfg(all(any(debug_assertions, feature = "ui-driver"), not(windows)))]
    {
        builder = builder.plugin(
            tauri_plugin_mcp_bridge::Builder::new()
                .bind_address("127.0.0.1")
                .build(),
        );
    }

    builder
        .manage(AppState { engine: engine_rx })
        .manage(band::BandServer::default())
        .manage(keep_awake::KeepAwake::default())
        .manage(assistant_link::AssistantLink::default())
        .setup(move |app| {
            // Open / migrate / seed the library DB before exposing commands.
            let app_data_dir = app
                .path()
                .app_data_dir()
                .expect("app_data_dir resolves on supported platforms");
            let db_path = db::default_db_path(&app_data_dir);
            let db = Arc::new(
                Db::open(&db_path)
                    .unwrap_or_else(|e| panic!("failed to open db at {db_path:?}: {e:#}")),
            );
            // The user's choice about error reports, from here on.
            match settings::get(&db) {
                Ok(s) => {
                    telemetry::set_enabled(s.send_error_reports);
                    telemetry::set_contact(&s.report_contact);
                    if let Some(dir) = db_path.parent() {
                        let root = dir.join("recordings");
                        let keep = s.recording_keep_days;
                        std::thread::spawn(move || {
                            recordings::prune(&root, keep, std::time::SystemTime::now())
                        });
                    }
                }
                Err(e) => {
                    telemetry::set_enabled(false);
                    log::warn!("couldn't read settings; error reports off for this run: {e:#}");
                }
            }
            library_watch::spawn(app.handle().clone(), db.clone());
            app.state::<assistant_link::AssistantLink>()
                .start(app.handle().clone(), &app_data_dir);
            // Announce this operator on the LAN and watch for others.
            let sync = peer_sync::PeerSync::new(app.handle().clone(), &db);
            app.manage(db);
            app.manage(sync);
            let handle_for_sync = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                handle_for_sync.state::<peer_sync::PeerSync>().start().await;
            });
            log::info!("opened library at {}", db_path.display());

            let handle = app.handle().clone();
            let db_path_for_sidecar = db_path.clone();
            tauri::async_runtime::spawn(async move {
                sidecar::run_sidecar(handle, &db_path_for_sidecar, engine_tx).await;
            });

            Ok(())
        })
        // Closing the operator window is quitting the app. The app only quit
        // once its last window had gone, so closing the operator window with
        // the audience window up (or a band screen, or the debug panel) left
        // those on their monitors, and the engine running behind them, with
        // nothing to close them from.
        .on_window_event(|window, event| {
            if window.label() == "main"
                && matches!(event, tauri::WindowEvent::CloseRequested { .. })
            {
                log::info!("operator window closed; quitting");
                window.app_handle().exit(0);
            }
        })
        .invoke_handler(tauri::generate_handler![
            get_sidecar_port,
            get_ai_engine_status,
            log_from_ui,
            report_unreadable_import,
            save_diagnostics,
            open_log_folder,
            feedback::app_identity,
            feedback::send_feedback,
            feedback::open_link,
            read_third_party_notices,
            open_audience_window,
            close_audience_window,
            toggle_audience_fullscreen,
            is_audience_window_open,
            monitor_count,
            screens::list_screens,
            screens::move_window_to_screen,
            screens::set_screen_avoid,
            screens::identify_screens,
            open_debug_window,
            close_debug_window,
            is_debug_window_open,
            open_band_window,
            toggle_own_fullscreen,
            count_band_windows,
            close_band_windows,
            other_apps::detect_other_apps,
            other_apps::read_other_app,
            commands::list_songs,
            commands::list_songs_sorted,
            commands::search_songs,
            commands::list_recent_services,
            commands::get_song,
            commands::create_song,
            commands::update_song,
            commands::delete_song,
            commands::get_settings,
            commands::update_settings,
            commands::export_library,
            commands::import_library,
            commands::list_setlists,
            commands::get_setlist,
            commands::create_setlist,
            commands::update_setlist_name,
            commands::delete_setlist,
            commands::add_setlist_item,
            commands::add_service_item,
            commands::set_setlist_item_content,
            commands::duplicate_setlist,
            commands::list_readings,
            commands::save_reading,
            commands::delete_reading,
            commands::remove_setlist_item,
            commands::set_setlist_item_slides,
            commands::set_setlist_item_look,
            commands::set_setlist_item_key,
            commands::reorder_setlist,
            commands::mark_setlist_used,
            commands::get_song_charts,
            commands::list_chart_keys,
            commands::list_chart_meta,
            commands::delete_song_chart,
            commands::import_charts,
            commands::list_song_references,
            commands::save_song_reference,
            commands::delete_song_reference,
            commands::band_start,
            commands::band_info,
            commands::band_publish,
            commands::band_clear,
            commands::band_library_index,
            commands::account_status,
            commands::account_refresh,
            commands::account_sign_in,
            commands::account_sign_out,
            commands::account_sign_out_everywhere,
            commands::cloud_relay_status,
            commands::cloud_relay_setup_token,
            commands::cloud_relay_setup_oauth,
            commands::cloud_relay_redeploy,
            commands::cloud_relay_health,
            commands::cloud_relay_forget,
            commands::cloud_relay_new_room,
            commands::cloud_relay_new_links,
            commands::cloud_relay_open_page,
            commands::set_keep_awake,
            peer_sync::peer_sync_info,
            peer_sync::peer_list_peers,
            peer_sync::peer_add_manual,
            peer_sync::peer_send_setlist,
            peer_sync::peer_send_library,
            peer_sync::peer_offer_status,
            peer_sync::peer_request_access,
            peer_sync::peer_has_access,
            peer_sync::peer_access_status,
            peer_sync::peer_remote_setlists,
            peer_sync::peer_fetch_setlist,
            peer_sync::peer_fetch_library,
            peer_sync::peer_incoming_offer,
            peer_sync::peer_respond_offer,
            peer_sync::peer_respond_access,
            peer_sync::peer_apply_bundle,
            mcp::install::mcp_connection_info,
            mcp::install::mcp_connect_claude_desktop,
            mcp::install::mcp_disconnect_claude_desktop,
            assistant_link::assistant_link_ready,
            assistant_link::assistant_read_file,
            assistant_link::assistant_import_done,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
