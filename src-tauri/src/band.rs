//! Band screens: a small web server on the local network.
//!
//! Musicians open a page on their phone or tablet (by QR code) and see the
//! current song's lyrics and chords, following the operator. The worship
//! leader's page adds a palette of the setlist's songs; tapping one asks the
//! operator window to move to that song, and tapping a slide moves to that
//! slide. The leader PIN is kept across launches (`stored_pin`).
//!
//! Everything stays in the building: this binds to the LAN, and nothing goes
//! through the internet.
//!
//! **The operator window is the source of truth.** This server holds no
//! presentation logic. The operator publishes two snapshots — the setlist
//! (songs, slides, charts, keys) and the position (which slide is up) — and
//! the server keeps the latest of each and fans them out. A device that
//! connects or reconnects gets both immediately, so a tablet that slept or
//! dropped off the Wi-Fi is right again the moment it's back. Leader commands
//! travel the other way as a Tauri event; the operator applies them and the
//! result comes back out as a new position, so every screen (operator
//! included) agrees by construction.
//!
//! Why Rust and not the Python sidecar: the sidecar is the AI process. If it
//! restarts, or isn't in use at all, the band's screens must not go dark.

use std::net::{IpAddr, Ipv4Addr, SocketAddr};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::collections::HashMap;
use std::time::{Duration, Instant};

use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::extract::{ConnectInfo, Path, State};
use axum::http::{header, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Redirect, Response};
use axum::routing::get;
use axum::Router;
use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, Manager};

use crate::db::Db;
use tokio::sync::broadcast;

/// A fixed port, so the address a musician bookmarked last week still works.
/// Falls back to any free port if something else holds it.
pub const PREFERRED_PORT: u16 = 7777;

/// Event carrying a leader's command to the operator window.
pub const COMMAND_EVENT: &str = "band://command";
/// Event carrying the number of connected band devices.
pub const CLIENTS_EVENT: &str = "band://clients";
/// Event carrying the number of devices with the leader controls unlocked.
pub const LEADERS_EVENT: &str = "band://leaders";

#[derive(Debug, Clone, serde::Serialize)]
pub struct BandInfo {
    pub port: u16,
    /// Addresses devices can reach this computer on, best guess first.
    pub urls: Vec<String>,
    /// Required to use the leader controls. Musicians' view is open.
    pub pin: String,
    pub clients: usize,
    /// Devices with the leader controls unlocked.
    pub leaders: usize,
}

struct Shared {
    app: AppHandle,
    port: u16,
    pin: String,
    setlist: Mutex<Option<Value>>,
    position: Mutex<Option<Value>>,
    /// `{"type":"hosted","live":…}`: whether the organization's hosted live
    /// room is reachable. A device that fell back here from the hosted room
    /// goes back to it when this says so.
    hosted: Mutex<Option<Value>>,
    /// `{"type":"jump_hold","data":…}`: an AI jump to another song waiting
    /// for the leader. Kept so a leader who reconnects mid-countdown sees it.
    jump_hold: Mutex<Option<Value>>,
    tx: broadcast::Sender<String>,
    clients: AtomicUsize,
    leaders: AtomicUsize,
    /// Wrong leader PINs per device address (`PinGuard`).
    pins: Mutex<PinGuard>,
}

/// Wrong leader PINs allowed from one address before it has to wait.
const PIN_TRIES: u32 = 5;
/// How long an address waits after `PIN_TRIES` wrong PINs.
const PIN_LOCKOUT: Duration = Duration::from_secs(300);

/// Slows PIN guessing. Four digits are 10,000 tries, which one connection
/// sending `hello` in a loop used to get through in seconds; now each
/// address gets `PIN_TRIES` and then waits `PIN_LOCKOUT`, so a guess at the
/// whole range takes days. A right PIN clears the count.
#[derive(Default)]
struct PinGuard {
    wrong: HashMap<IpAddr, (u32, Option<Instant>)>,
}

impl PinGuard {
    /// Whether `from` may try a PIN now.
    fn may_try(&mut self, from: IpAddr, now: Instant) -> bool {
        match self.wrong.get(&from) {
            Some((_, Some(until))) if now < *until => false,
            Some((_, Some(_))) => {
                self.wrong.remove(&from);
                true
            }
            _ => true,
        }
    }

    fn record(&mut self, from: IpAddr, ok: bool, now: Instant) {
        if ok {
            self.wrong.remove(&from);
            return;
        }
        let entry = self.wrong.entry(from).or_insert((0, None));
        entry.0 += 1;
        if entry.0 >= PIN_TRIES {
            entry.1 = Some(now + PIN_LOCKOUT);
        }
    }
}

/// Held in Tauri state; `None` until the operator switches band screens on.
#[derive(Default)]
pub struct BandServer {
    inner: Mutex<Option<(Arc<Shared>, BandInfo)>>,
    /// Serialises `start`, so two quick clicks can't bind two servers.
    starting: tokio::sync::Mutex<()>,
}

fn random_pin() -> String {
    let n = ulid::Ulid::new().random() % 10_000;
    format!("{n:04}")
}

/// Settings-table key for the leader PIN.
const PIN_KEY: &str = "band_leader_pin";

/// The leader PIN, made once and then kept: the leader's device remembers it,
/// and a new one every launch meant it was rejected every week — with, in
/// single-screen mode, nowhere on the laptop to read the new one.
fn stored_pin(db: &Db) -> String {
    use rusqlite::{params, OptionalExtension};
    let conn = db.home();
    // Values in `settings` are JSON-encoded (see migrations/002_settings.sql).
    let existing = conn
        .query_row("SELECT value FROM settings WHERE key = ?1", params![PIN_KEY], |r| r.get::<_, String>(0))
        .optional()
        .ok()
        .flatten()
        .and_then(|raw| serde_json::from_str::<String>(&raw).ok())
        .filter(|p| p.len() == 4 && p.chars().all(|c| c.is_ascii_digit()));
    if let Some(pin) = existing {
        return pin;
    }
    let pin = random_pin();
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0);
    if let Err(e) = conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?1, ?2, ?3) \
         ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        params![PIN_KEY, serde_json::to_string(&pin).unwrap_or_default(), now],
    ) {
        // Still usable this session; it just won't survive a restart.
        log::warn!("band: couldn't save the leader PIN: {e}");
    }
    pin
}

/// IPv4 addresses on real interfaces, private (LAN) ranges first.
pub(crate) fn lan_addresses() -> Vec<Ipv4Addr> {
    let mut out: Vec<Ipv4Addr> = local_ip_address::list_afinet_netifas()
        .unwrap_or_default()
        .into_iter()
        .filter_map(|(_, ip)| match ip {
            IpAddr::V4(v4) if !v4.is_loopback() && !v4.is_link_local() => Some(v4),
            _ => None,
        })
        .collect();
    out.sort_by_key(|ip| (!ip.is_private(), ip.octets()));
    out.dedup();
    out
}

impl BandServer {
    pub fn info(&self) -> Option<BandInfo> {
        self.inner.lock().unwrap().as_ref().map(|(shared, info)| BandInfo {
            clients: shared.clients.load(Ordering::Relaxed),
            leaders: shared.leaders.load(Ordering::Relaxed),
            urls: current_urls(info.port),
            ..info.clone()
        })
    }

    /// Start the server if it isn't running. Idempotent.
    pub async fn start(&self, app: AppHandle) -> Result<BandInfo, String> {
        let _starting = self.starting.lock().await;
        if let Some(info) = self.info() {
            return Ok(info);
        }
        let listener = match tokio::net::TcpListener::bind(SocketAddr::from(([0, 0, 0, 0], PREFERRED_PORT))).await {
            Ok(l) => l,
            Err(e) => {
                log::warn!("band: port {PREFERRED_PORT} unavailable ({e}); using a free port");
                tokio::net::TcpListener::bind(SocketAddr::from(([0, 0, 0, 0], 0)))
                    .await
                    .map_err(|e| format!("could not open a network port for band screens: {e}"))?
            }
        };
        let port = listener.local_addr().map_err(|e| e.to_string())?.port();
        let (tx, _) = broadcast::channel(64);
        let pin = stored_pin(&app.state::<Arc<Db>>());
        let shared = Arc::new(Shared {
            app,
            port,
            pin,
            setlist: Mutex::new(None),
            position: Mutex::new(None),
            hosted: Mutex::new(None),
            jump_hold: Mutex::new(None),
            tx,
            clients: AtomicUsize::new(0),
            leaders: AtomicUsize::new(0),
            pins: Mutex::new(PinGuard::default()),
        });
        let info = BandInfo { port, urls: current_urls(port), pin: shared.pin.clone(), clients: 0, leaders: 0 };

        let router = Router::new()
            .route("/", get(|| async { Redirect::temporary("/band") }))
            .route("/band", get(serve_page))
            .route("/leader", get(serve_page))
            .route("/ws", get(ws_upgrade))
            .route("/*path", get(serve_asset))
            .with_state(shared.clone());

        // Heartbeat: browsers can't see WebSocket pings, so a periodic message
        // is how a device notices a dead connection and reconnects.
        let tick_tx = shared.tx.clone();
        tauri::async_runtime::spawn(async move {
            let mut every = tokio::time::interval(Duration::from_secs(4));
            loop {
                every.tick().await;
                let _ = tick_tx.send(json!({ "type": "tick" }).to_string());
            }
        });
        tauri::async_runtime::spawn(async move {
            let service = router.into_make_service_with_connect_info::<SocketAddr>();
            if let Err(e) = axum::serve(listener, service).await {
                log::error!("band server stopped: {e}");
            }
        });
        // How many, not the addresses: the log goes out with error reports.
        log::info!("band screens on port {port} ({} network addresses)", info.urls.len());

        *self.inner.lock().unwrap() = Some((shared, info.clone()));
        Ok(info)
    }

    /// Store and broadcast a snapshot from the operator. `kind` is
    /// "setlist" or "position".
    pub fn publish(&self, kind: &str, payload: Value) -> Result<(), String> {
        let guard = self.inner.lock().unwrap();
        let Some((shared, _)) = guard.as_ref() else { return Ok(()) };
        let slot = match kind {
            "setlist" => &shared.setlist,
            "position" => &shared.position,
            "hosted" => {
                let msg = json!({ "type": "hosted", "live": payload.get("live").and_then(Value::as_bool).unwrap_or(false) });
                *shared.hosted.lock().unwrap() = Some(msg.clone());
                let _ = shared.tx.send(msg.to_string());
                return Ok(());
            }
            // Null when nothing waits: sent so the bar goes, but not kept.
            "jump_hold" => {
                let msg = json!({ "type": "jump_hold", "data": payload });
                *shared.jump_hold.lock().unwrap() = if payload.is_null() { None } else { Some(msg.clone()) };
                let _ = shared.tx.send(msg.to_string());
                return Ok(());
            }
            other => return Err(format!("unknown band snapshot kind {other:?}")),
        };
        let msg = json!({ "type": kind, "data": payload });
        *slot.lock().unwrap() = Some(msg.clone());
        let _ = shared.tx.send(msg.to_string());
        Ok(())
    }

    /// The service ended: tell devices there's nothing to follow.
    pub fn clear(&self) {
        let guard = self.inner.lock().unwrap();
        let Some((shared, _)) = guard.as_ref() else { return };
        *shared.setlist.lock().unwrap() = None;
        *shared.position.lock().unwrap() = None;
        *shared.jump_hold.lock().unwrap() = None;
        let _ = shared.tx.send(json!({ "type": "idle" }).to_string());
    }
}

fn current_urls(port: u16) -> Vec<String> {
    let mut urls: Vec<String> = lan_addresses().iter().map(|ip| format!("http://{ip}:{port}")).collect();
    if urls.is_empty() {
        urls.push(format!("http://localhost:{port}"));
    }
    urls
}

// --- HTTP --------------------------------------------------------------------

/// The React bundle's `index.html`. In a dev build (`tauri dev`) the embedded
/// assets may be a stale `dist/` from an old production build, so send the
/// device to the Vite dev server instead, telling it where the socket is.
async fn serve_page(
    State(shared): State<Arc<Shared>>,
    headers: axum::http::HeaderMap,
    uri: axum::http::Uri,
) -> Response {
    let role = if uri.path().starts_with("/leader") { "leader" } else { "band" };
    if tauri::is_dev() {
        if let Some(dev) = shared.app.config().build.dev_url.clone() {
            // Same host the device reached us on. For a real phone the dev
            // server must listen on the LAN too (TAURI_DEV_HOST); on this
            // computer, open localhost:<port>/band.
            let host = headers
                .get(header::HOST)
                .and_then(|h| h.to_str().ok())
                .and_then(|h| h.rsplit_once(':').map(|(name, _)| name.to_string()).or(Some(h.to_string())))
                .unwrap_or_else(|| "localhost".into());
            let dev_port = dev.port().unwrap_or(1420);
            let query = uri.query().map(|q| format!("&{q}")).unwrap_or_default();
            return Redirect::temporary(&format!(
                "http://{host}:{dev_port}/?window={role}&bandPort={}{query}",
                shared.port
            ))
            .into_response();
        }
    }
    match shared.app.asset_resolver().get("index.html".into()) {
        Some(asset) => asset_response(asset.bytes, &asset.mime_type),
        None => (StatusCode::NOT_FOUND, "app bundle not found").into_response(),
    }
}

async fn serve_asset(State(shared): State<Arc<Shared>>, Path(path): Path<String>) -> Response {
    match shared.app.asset_resolver().get(path.clone()) {
        Some(asset) => asset_response(asset.bytes, &asset.mime_type),
        None => (StatusCode::NOT_FOUND, "not found").into_response(),
    }
}

fn asset_response(bytes: Vec<u8>, mime: &str) -> Response {
    let mut res = bytes.into_response();
    if let Ok(v) = HeaderValue::from_str(mime) {
        res.headers_mut().insert(header::CONTENT_TYPE, v);
    }
    res.headers_mut().insert(header::CACHE_CONTROL, HeaderValue::from_static("no-cache"));
    res
}

// --- WebSocket ---------------------------------------------------------------

async fn ws_upgrade(
    ws: WebSocketUpgrade,
    ConnectInfo(addr): ConnectInfo<SocketAddr>,
    State(shared): State<Arc<Shared>>,
) -> Response {
    ws.on_upgrade(move |socket| client_session(socket, shared, addr.ip()))
}

fn emit_clients(shared: &Shared) {
    let _ = shared.app.emit(CLIENTS_EVENT, shared.clients.load(Ordering::Relaxed));
}

/// Count this device in or out of the unlocked leaders, and tell the app.
fn set_leader(shared: &Shared, was: bool, now: bool) {
    if was == now {
        return;
    }
    if now {
        shared.leaders.fetch_add(1, Ordering::Relaxed);
    } else {
        shared.leaders.fetch_sub(1, Ordering::Relaxed);
    }
    let _ = shared.app.emit(LEADERS_EVENT, shared.leaders.load(Ordering::Relaxed));
}

async fn client_session(mut socket: WebSocket, shared: Arc<Shared>, from: IpAddr) {
    shared.clients.fetch_add(1, Ordering::Relaxed);
    emit_clients(&shared);
    let mut rx = shared.tx.subscribe();
    let mut leader = false;

    // Catch the device up before anything else.
    let snapshot: Vec<Value> = [&shared.setlist, &shared.position]
        .iter()
        .filter_map(|slot| slot.lock().unwrap().clone())
        .collect();
    let mut ok = true;
    if snapshot.is_empty() {
        ok = socket.send(Message::Text(json!({ "type": "idle" }).to_string())).await.is_ok();
    }
    let hosted = shared.hosted.lock().unwrap().clone();
    let held = shared.jump_hold.lock().unwrap().clone();
    for msg in snapshot.into_iter().chain(hosted).chain(held) {
        if !ok {
            break;
        }
        ok = socket.send(Message::Text(msg.to_string())).await.is_ok();
    }

    while ok {
        tokio::select! {
            out = rx.recv() => match out {
                Ok(text) => ok = socket.send(Message::Text(text)).await.is_ok(),
                // Fell behind (a very slow device): resend the current state
                // rather than a gap of stale deltas.
                Err(broadcast::error::RecvError::Lagged(_)) => {
                    for slot in [&shared.setlist, &shared.position] {
                        let msg = slot.lock().unwrap().clone();
                        if let Some(m) = msg {
                            ok = ok && socket.send(Message::Text(m.to_string())).await.is_ok();
                        }
                    }
                }
                Err(broadcast::error::RecvError::Closed) => break,
            },
            incoming = socket.recv() => match incoming {
                Some(Ok(Message::Text(text))) => {
                    let was_leader = leader;
                    let reply = handle_client_message(&shared, &text, &mut leader, from);
                    set_leader(&shared, was_leader, leader);
                    if let Some(r) = reply {
                        ok = socket.send(Message::Text(r.to_string())).await.is_ok();
                    }
                }
                Some(Ok(Message::Close(_))) | None | Some(Err(_)) => break,
                Some(Ok(_)) => {}
            },
        }
    }

    set_leader(&shared, leader, false);
    shared.clients.fetch_sub(1, Ordering::Relaxed);
    emit_clients(&shared);
}

/// Returns a reply for this client only, if any.
fn handle_client_message(shared: &Shared, text: &str, leader: &mut bool, from: IpAddr) -> Option<Value> {
    let msg: Value = serde_json::from_str(text).ok()?;
    match msg.get("type").and_then(Value::as_str)? {
        "hello" => {
            let wants_leader = msg.get("role").and_then(Value::as_str) == Some("leader");
            if !wants_leader {
                return Some(json!({ "type": "welcome", "role": "band" }));
            }
            let now = Instant::now();
            let mut pins = shared.pins.lock().unwrap();
            if !pins.may_try(from, now) {
                *leader = false;
                return Some(json!({
                    "type": "welcome", "role": "band", "pinRejected": true, "tooManyTries": true,
                }));
            }
            let pin_ok = msg.get("pin").and_then(Value::as_str) == Some(shared.pin.as_str());
            pins.record(from, pin_ok, now);
            *leader = pin_ok;
            Some(json!({
                "type": "welcome",
                "role": if pin_ok { "leader" } else { "band" },
                "pinRejected": !pin_ok,
            }))
        }
        // The whole library, compact, for the leader's search screen. Sent
        // once and searched on the device, so typing never waits on Wi-Fi.
        "library_request" => {
            if !*leader {
                return Some(json!({ "type": "error", "message": "The library needs the leader PIN" }));
            }
            let db = shared.app.state::<Arc<Db>>();
            match library_index(&db) {
                Ok(songs) => Some(json!({ "type": "library", "data": songs })),
                Err(e) => Some(json!({ "type": "error", "message": format!("Couldn't read the library: {e}") })),
            }
        }
        "command" => {
            if !*leader {
                return Some(json!({ "type": "error", "message": "Leader controls need the PIN" }));
            }
            let command = msg.get("command")?.clone();
            let _ = shared.app.emit(COMMAND_EVENT, command);
            None
        }
        _ => None,
    }
}

/// One row per library song: what search needs, nothing more. `last_used`
/// and `uses` come from setlists, to show recently played songs before the
/// leader types anything.
pub fn library_index(db: &Db) -> anyhow::Result<Vec<Value>> {
    let conn = db.conn().lock().unwrap();
    let mut stmt = conn.prepare(
        "SELECT s.id, s.title, s.author, s.body_text, u.last_used, COALESCE(u.uses, 0), \
                c.original_key, c.song_id IS NOT NULL, \
                CAST(json_extract(c.chart_json, '$.tempo') AS REAL), json_extract(c.chart_json, '$.time') \
         FROM songs s \
         LEFT JOIN ( \
            SELECT si.song_id, MAX(COALESCE(sl.last_used_at, sl.created_at)) AS last_used, COUNT(*) AS uses \
            FROM setlist_items si JOIN setlists sl ON sl.id = si.setlist_id \
            WHERE si.kind = 'song' GROUP BY si.song_id \
         ) u ON u.song_id = s.id \
         LEFT JOIN song_charts c ON c.song_id = s.id",
    )?;
    let rows = stmt
        .query_map([], |row| {
            Ok(json!({
                "id": row.get::<_, String>(0)?,
                "title": row.get::<_, String>(1)?,
                "author": row.get::<_, Option<String>>(2)?,
                "body": row.get::<_, String>(3)?,
                "lastUsed": row.get::<_, Option<i64>>(4)?,
                "uses": row.get::<_, i64>(5)?,
                "key": row.get::<_, Option<String>>(6)?,
                "hasChart": row.get::<_, bool>(7)?,
                "tempo": row.get::<_, Option<f64>>(8)?,
                "time": row.get::<_, Option<String>>(9)?,
            }))
        })?
        .collect::<Result<Vec<_>, _>>()?;
    Ok(rows)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wrong_pins_lock_an_address_out_for_a_while() {
        let mut g = PinGuard::default();
        let a: IpAddr = "192.168.1.20".parse().unwrap();
        let b: IpAddr = "192.168.1.21".parse().unwrap();
        let t0 = Instant::now();
        for _ in 0..PIN_TRIES {
            assert!(g.may_try(a, t0));
            g.record(a, false, t0);
        }
        assert!(!g.may_try(a, t0), "a sixth guess straight away");
        assert!(g.may_try(b, t0), "another device is not locked out");
        assert!(g.may_try(a, t0 + PIN_LOCKOUT + Duration::from_secs(1)));
    }

    #[test]
    fn a_right_pin_clears_the_count() {
        let mut g = PinGuard::default();
        let a: IpAddr = "192.168.1.20".parse().unwrap();
        let t0 = Instant::now();
        for _ in 0..PIN_TRIES - 1 {
            g.record(a, false, t0);
        }
        g.record(a, true, t0);
        g.record(a, false, t0);
        assert!(g.may_try(a, t0));
    }

    #[test]
    fn pins_are_four_digits() {
        for _ in 0..50 {
            let p = random_pin();
            assert_eq!(p.len(), 4);
            assert!(p.chars().all(|c| c.is_ascii_digit()));
        }
    }

    #[test]
    fn leader_pin_survives_a_restart() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("t.db");
        let first = stored_pin(&Db::open(&path).unwrap());
        assert_eq!(first.len(), 4);
        assert_eq!(stored_pin(&Db::open(&path).unwrap()), first);
    }

    #[test]
    fn library_index_lists_every_song() {
        let dir = tempfile::tempdir().unwrap();
        let db = Db::open(&dir.path().join("t.db")).unwrap();
        let rows = library_index(&db).unwrap();
        assert_eq!(rows.len(), crate::seed_hymns::SEED_HYMNS.len());
        let grace = rows.iter().find(|r| r["title"] == "Amazing Grace").unwrap();
        assert_eq!(grace["uses"], 0);
        assert_eq!(grace["hasChart"], false);
        // "Fits next" reads these; null without a chart.
        assert!(grace["tempo"].is_null());
        assert!(grace["time"].is_null());
    }

    #[test]
    fn lan_addresses_exclude_loopback() {
        assert!(lan_addresses().iter().all(|ip| !ip.is_loopback()));
    }
}
