//! Operator-to-operator setlist (or whole-library) transfer on the local network.
//!
//! Every running copy of the app announces itself over Bonjour
//! (`_churchslides._tcp`) and watches for others, so an operator who opens
//! the app on a second laptop sees the first one appear without typing an
//! address. A transfer is a one-shot copy of one setlist: its songs (lyrics,
//! slide order, licensing facts), their chord charts, and the
//! per-setlist slide edits. The receiver decides song by song how it lands in
//! its own library — see `apply_bundle` and `src/lib/peerSync/plan.ts`, which
//! share the file importer's duplicate rules.
//!
//! Nothing moves without a person agreeing on the *other* machine:
//! - **Send**: the bundle is delivered, but the receiver's operator reviews
//!   and imports it (or declines).
//! - **Get**: the requester asks for access; the source's operator allows it,
//!   which hands the requester a token that lists and fetches setlists for
//!   the rest of this app session.
//!
//! Plain HTTP on the LAN, like the band screens — nothing goes through the
//! internet. Church Wi-Fi often isolates clients or drops multicast, so a
//! peer can also be added by address (`peer_add_manual`).

use std::collections::{HashMap, HashSet};
use std::net::{IpAddr, Ipv4Addr, SocketAddr};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use anyhow::{anyhow, bail, Context, Result};
use axum::extract::{DefaultBodyLimit, Path as UrlPath, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use rusqlite::{params, OptionalExtension};
use serde::{Deserialize, Serialize};
use serde_json::json;
use tauri::{AppHandle, Emitter, Manager};

use crate::db::Db;
use crate::{charts, setlists};

/// Fixed so a peer added by address keeps working; any free port otherwise.
pub const PREFERRED_PORT: u16 = 7778;
const SERVICE_TYPE: &str = "_churchslides._tcp.local.";
/// The newest bundle this copy reads. Bumped when a bundle changes in a way
/// an older receiver can't read; 2 added scripture and text items, 3 the
/// whole library.
pub const PROTOCOL: u32 = 3;

pub const PEERS_EVENT: &str = "peer-sync://peers";
pub const OFFER_EVENT: &str = "peer-sync://offer";
pub const ACCESS_EVENT: &str = "peer-sync://access-request";

const TOKEN_HEADER: &str = "x-peer-token";
/// Unanswered offers and access requests are dropped after this.
const PENDING_TTL: Duration = Duration::from_secs(15 * 60);
/// Caps what a stranger on the Wi-Fi can pile up in memory.
const MAX_PENDING: usize = 20;
const BACKUPS_KEPT: usize = 10;

// --- The bundle ------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SetlistBundle {
    pub protocol: u32,
    pub setlist_name: String,
    /// Each song once, however many times the set plays it.
    pub songs: Vec<BundleSong>,
    pub items: Vec<BundleItem>,
    /// Every song in the sender's library, and no setlist: the receiver adds
    /// the songs and makes nothing on Home.
    #[serde(default)]
    pub library: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BundleSong {
    /// The song's id on the sending machine; only links items to songs here.
    pub ref_id: String,
    pub title: String,
    pub body_text: String,
    pub slide_order: Option<String>,
    pub ccli: Option<String>,
    pub author: Option<String>,
    pub copyright: Option<String>,
    pub default_tempo_wps: Option<f64>,
    pub chart: Option<BundleChart>,
    // `scriptures` (passages attached to songs) was dropped on 2026-10-19.
    // Bundles from older copies still carry it and serde ignores it; older
    // copies receiving from this one default it to empty.
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BundleChart {
    pub chart_json: String,
    pub original_key: Option<String>,
    pub source_format: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BundleItem {
    /// Empty for a scripture or text item, which has no song.
    pub song_ref: String,
    /// The per-setlist slide edits (migration 004), verbatim.
    pub slides_override: Option<String>,
    /// "song", "scripture" or "text" (migration 009). Older bundles carry
    /// songs only and leave it out.
    #[serde(default = "song_kind")]
    pub kind: String,
    /// A scripture or text item's content, verbatim — the passage text it was
    /// prepared with travels too, so the receiver needs no Bible to show it.
    #[serde(default)]
    pub content: Option<String>,
    /// The item's own style (migration 011). Older bundles leave it out.
    #[serde(default)]
    pub look: Option<String>,
    /// The song's key for this service (migration 012). Older bundles leave it out.
    #[serde(default)]
    pub song_key: Option<String>,
}

fn song_kind() -> String {
    "song".into()
}

/// The protocol a bundle needs. Songs-only setlists stay at 1 so older
/// copies keep receiving them; one with scripture or text items is 2, which
/// an older copy refuses with "update this computer" rather than mangling.
const PROTOCOL_SERVICE_ITEMS: u32 = 2;
/// An older copy would apply a library as an empty setlist, so it's refused there.
const PROTOCOL_LIBRARY: u32 = 3;

pub fn build_bundle(db: &Arc<Db>, setlist_id: &str) -> Result<SetlistBundle> {
    let detail = setlists::get_setlist(db, setlist_id)?.ok_or_else(|| anyhow!("no setlist {setlist_id}"))?;
    let mut songs: Vec<BundleSong> = Vec::new();
    let mut seen = HashSet::new();
    let mut items = Vec::new();
    for it in &detail.items {
        if it.item.kind != "song" {
            items.push(BundleItem {
                song_ref: String::new(),
                slides_override: it.item.slides_override.clone(),
                kind: it.item.kind.clone(),
                content: it.item.content.clone(),
                look: it.item.look.clone(),
                song_key: None,
            });
            continue;
        }
        let Some(song) = &it.song else { continue };
        items.push(BundleItem {
            song_ref: song.id.clone(),
            slides_override: it.item.slides_override.clone(),
            kind: song_kind(),
            content: None,
            look: it.item.look.clone(),
            song_key: it.item.song_key.clone(),
        });
        if seen.insert(song.id.clone()) {
            songs.push(BundleSong {
                ref_id: song.id.clone(),
                title: song.title.clone(),
                body_text: song.body_text.clone(),
                slide_order: song.slide_order.clone(),
                ccli: song.ccli.clone(),
                author: song.author.clone(),
                copyright: song.copyright.clone(),
                default_tempo_wps: song.default_tempo_wps,
                chart: None,
            });
        }
    }
    attach_charts(db, &mut songs)?;
    let protocol = if items.iter().any(|i| i.kind != "song") { PROTOCOL_SERVICE_ITEMS } else { 1 };
    Ok(SetlistBundle { protocol, setlist_name: detail.setlist.name, songs, items, library: false })
}

/// Every song in the library, with its chords; no setlist.
pub fn build_library_bundle(db: &Arc<Db>) -> Result<SetlistBundle> {
    let mut songs: Vec<BundleSong> = db
        .list_songs()?
        .into_iter()
        .map(|song| BundleSong {
            ref_id: song.id,
            title: song.title,
            body_text: song.body_text,
            slide_order: song.slide_order,
            ccli: song.ccli,
            author: song.author,
            copyright: song.copyright,
            default_tempo_wps: song.default_tempo_wps,
            chart: None,
        })
        .collect();
    attach_charts(db, &mut songs)?;
    Ok(SetlistBundle { protocol: PROTOCOL_LIBRARY, setlist_name: String::new(), songs, items: Vec::new(), library: true })
}

fn attach_charts(db: &Arc<Db>, songs: &mut [BundleSong]) -> Result<()> {
    let ids: Vec<String> = songs.iter().map(|s| s.ref_id.clone()).collect();
    let mut charts = charts::get_charts(db, &ids)?;
    for s in songs {
        s.chart = charts.remove(&s.ref_id).map(|c| BundleChart {
            chart_json: c.chart_json,
            original_key: c.original_key,
            source_format: c.source_format,
        });
    }
    Ok(())
}

// --- Applying a received bundle ---------------------------------------------------

/// What to do with one incoming song. Chosen by the receiver's operator (or
/// automatically, when the library already holds an identical copy).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum SongDecision {
    /// Not in this library: add it.
    New,
    /// Use this library's song as it is. Missing chords and licensing facts are filled in from the incoming copy; nothing is
    /// overwritten.
    UseExisting { song_id: String },
    /// Overwrite this library's song with the incoming lyrics (and chart, if
    /// it has one). The library is snapshotted first.
    ReplaceExisting { song_id: String },
    /// Keep this library's song and add the incoming one beside it.
    KeepBoth,
}

#[derive(Debug, Clone, Serialize)]
pub struct ApplySummary {
    /// Empty for a whole library, which makes no setlist.
    pub setlist_id: String,
    pub setlist_name: String,
    pub songs_created: usize,
    pub songs_reused: usize,
    pub songs_replaced: usize,
    pub charts_added: usize,
    /// The library as it was before, when anything was overwritten.
    pub backup_path: Option<String>,
}

fn new_id(prefix: &str) -> String {
    format!("{prefix}_{}", ulid::Ulid::new().to_string().to_lowercase())
}

pub fn apply_bundle(
    db: &Arc<Db>,
    bundle: &SetlistBundle,
    decisions: &HashMap<String, SongDecision>,
    from_name: &str,
    backup_dir: Option<&Path>,
) -> Result<ApplySummary> {
    if bundle.protocol > PROTOCOL {
        bail!("This setlist came from a newer version of Church Slides — update this computer to receive it.");
    }
    for s in &bundle.songs {
        if !decisions.contains_key(&s.ref_id) {
            bail!("no decision for \"{}\"", s.title);
        }
    }
    let replacing = decisions.values().any(|d| matches!(d, SongDecision::ReplaceExisting { .. }));
    let backup_path = match (replacing, backup_dir) {
        (true, Some(dir)) => Some(snapshot(db, dir)?),
        _ => None,
    };

    let now = unix_ms();
    let mut conn = db.conn().lock().unwrap();
    let tx = conn.transaction()?;
    let mut summary = ApplySummary {
        setlist_id: if bundle.library { String::new() } else { new_id("setlist") },
        setlist_name: if bundle.library { String::new() } else { unique_setlist_name(&tx, &bundle.setlist_name, from_name)? },
        songs_created: 0,
        songs_reused: 0,
        songs_replaced: 0,
        charts_added: 0,
        backup_path: backup_path.map(|p| p.display().to_string()),
    };

    // Sender's song id -> this library's song id.
    let mut local: HashMap<&str, String> = HashMap::new();
    for s in &bundle.songs {
        let id = match &decisions[&s.ref_id] {
            SongDecision::New | SongDecision::KeepBoth => {
                let title = if matches!(decisions[&s.ref_id], SongDecision::KeepBoth) {
                    format!("{} ({from_name})", s.title)
                } else {
                    s.title.clone()
                };
                let id = new_id("song");
                tx.execute(
                    "INSERT INTO songs (id, title, body_text, ccli, author, copyright, default_tempo_wps, \
                        slide_order, created_at, updated_at) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?9)",
                    params![id, title, s.body_text, s.ccli, s.author, s.copyright, s.default_tempo_wps, s.slide_order, now],
                )?;
                summary.songs_created += 1;
                if let Some(c) = &s.chart {
                    upsert_chart(&tx, &id, c, now, true)?;
                    summary.charts_added += 1;
                }
                id
            }
            SongDecision::UseExisting { song_id } => {
                require_song(&tx, song_id)?;
                tx.execute(
                    "UPDATE songs SET \
                        ccli = COALESCE(NULLIF(TRIM(ccli), ''), ?2), \
                        author = COALESCE(NULLIF(TRIM(author), ''), ?3), \
                        copyright = COALESCE(NULLIF(TRIM(copyright), ''), ?4) \
                     WHERE id = ?1",
                    params![song_id, s.ccli, s.author, s.copyright],
                )?;
                if let Some(c) = &s.chart {
                    if upsert_chart(&tx, song_id, c, now, false)? {
                        summary.charts_added += 1;
                    }
                }
                summary.songs_reused += 1;
                song_id.clone()
            }
            SongDecision::ReplaceExisting { song_id } => {
                require_song(&tx, song_id)?;
                tx.execute(
                    "UPDATE songs SET title = ?2, body_text = ?3, slide_order = ?4, \
                        ccli = COALESCE(?5, ccli), author = COALESCE(?6, author), \
                        copyright = COALESCE(?7, copyright), updated_at = ?8 WHERE id = ?1",
                    params![song_id, s.title, s.body_text, s.slide_order, s.ccli, s.author, s.copyright, now],
                )?;
                if let Some(c) = &s.chart {
                    upsert_chart(&tx, song_id, c, now, true)?;
                    summary.charts_added += 1;
                }
                summary.songs_replaced += 1;
                song_id.clone()
            }
        };
        local.insert(s.ref_id.as_str(), id);
    }

    if bundle.library {
        tx.commit()?;
        return Ok(summary);
    }
    tx.execute(
        "INSERT INTO setlists (id, name, created_at) VALUES (?1, ?2, ?3)",
        params![summary.setlist_id, summary.setlist_name, now],
    )?;
    for (pos, it) in bundle.items.iter().enumerate() {
        if it.kind != "song" {
            if !setlists::SERVICE_ITEM_KINDS.contains(&it.kind.as_str()) {
                bail!("This setlist holds an item this version can't show — update this computer to receive it.");
            }
            tx.execute(
                "INSERT INTO setlist_items (id, setlist_id, kind, song_id, position, slides_override, content, look) \
                 VALUES (?1, ?2, ?3, NULL, ?4, ?5, ?6, ?7)",
                params![new_id("item"), summary.setlist_id, it.kind, pos as i64, it.slides_override, it.content, it.look],
            )?;
            continue;
        }
        let song_id = local
            .get(it.song_ref.as_str())
            .ok_or_else(|| anyhow!("setlist item refers to a song that wasn't sent"))?;
        tx.execute(
            "INSERT INTO setlist_items (id, setlist_id, kind, song_id, position, slides_override, look, song_key) \
             VALUES (?1, ?2, 'song', ?3, ?4, ?5, ?6, ?7)",
            params![new_id("item"), summary.setlist_id, song_id, pos as i64, it.slides_override, it.look, it.song_key],
        )?;
    }
    tx.commit()?;
    Ok(summary)
}

fn require_song(tx: &rusqlite::Transaction, id: &str) -> Result<()> {
    let n: i64 = tx.query_row("SELECT COUNT(*) FROM songs WHERE id = ?1", params![id], |r| r.get(0))?;
    if n == 0 {
        bail!("no song with id {id} in this library");
    }
    Ok(())
}

/// Returns whether a chart was written. `overwrite: false` only fills a gap.
fn upsert_chart(tx: &rusqlite::Transaction, song_id: &str, c: &BundleChart, now: i64, overwrite: bool) -> Result<bool> {
    let sql = if overwrite {
        "INSERT INTO song_charts (song_id, chart_json, original_key, source_format, updated_at) \
         VALUES (?1, ?2, ?3, ?4, ?5) ON CONFLICT(song_id) DO UPDATE SET chart_json = excluded.chart_json, \
         original_key = excluded.original_key, source_format = excluded.source_format, updated_at = excluded.updated_at"
    } else {
        "INSERT OR IGNORE INTO song_charts (song_id, chart_json, original_key, source_format, updated_at) \
         VALUES (?1, ?2, ?3, ?4, ?5)"
    };
    Ok(tx.execute(sql, params![song_id, c.chart_json, c.original_key, c.source_format, now])? > 0)
}

/// "Sunday" stays "Sunday" unless this library already has one; then it's
/// "Sunday (Sarah's MacBook)", numbered if even that's taken.
fn unique_setlist_name(tx: &rusqlite::Transaction, name: &str, from_name: &str) -> Result<String> {
    let taken = |n: &str| -> Result<bool> {
        Ok(tx.query_row("SELECT COUNT(*) FROM setlists WHERE name = ?1", params![n], |r| r.get::<_, i64>(0))? > 0)
    };
    if !taken(name)? {
        return Ok(name.to_string());
    }
    let base = format!("{name} ({from_name})");
    if !taken(&base)? {
        return Ok(base);
    }
    for i in 2.. {
        let n = format!("{base} {i}");
        if !taken(&n)? {
            return Ok(n);
        }
    }
    unreachable!()
}

fn snapshot(db: &Db, dir: &Path) -> Result<PathBuf> {
    std::fs::create_dir_all(dir).with_context(|| format!("creating {}", dir.display()))?;
    let path = dir.join(format!("library-before-sync-{}.db", unix_ms()));
    {
        let conn = db.conn().lock().unwrap();
        conn.execute("VACUUM INTO ?1", params![path.to_string_lossy()])
            .context("snapshotting the library before replacing songs")?;
    }
    if let Ok(entries) = std::fs::read_dir(dir) {
        let mut files: Vec<PathBuf> = entries
            .filter_map(|e| e.ok().map(|e| e.path()))
            .filter(|p| {
                p.file_name()
                    .and_then(|n| n.to_str())
                    .is_some_and(|n| n.starts_with("library-before-sync-") && n.ends_with(".db"))
            })
            .collect();
        files.sort();
        let excess = files.len().saturating_sub(BACKUPS_KEPT);
        for old in &files[..excess] {
            let _ = std::fs::remove_file(old);
        }
    }
    Ok(path)
}

fn unix_ms() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
}

// --- Peers and the server ---------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Hello {
    pub id: String,
    pub name: String,
    pub protocol: u32,
    #[serde(default)]
    pub app_version: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct Peer {
    pub id: String,
    pub name: String,
    /// `http://host:port`
    pub base_url: String,
    /// Added by address rather than found by Bonjour.
    pub manual: bool,
    #[serde(skip)]
    fullname: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct PeerSyncInfo {
    pub id: String,
    pub name: String,
    pub port: Option<u16>,
    /// What to type on the other laptop if it can't find this one.
    pub addresses: Vec<String>,
    /// Bonjour couldn't start; only manual addresses will work.
    pub discovery_error: Option<String>,
    /// The end of the Wi-Fi router's hardware address: two computers showing
    /// the same code are on the same network.
    pub network_code: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
enum Status {
    Pending,
    Accepted,
    Declined,
}

struct Offer {
    from: Hello,
    bundle: SetlistBundle,
    status: Status,
    at: Instant,
}

struct AccessRequest {
    from: Hello,
    status: Status,
    token: Option<String>,
    at: Instant,
}

struct Inner {
    app: AppHandle,
    me: Hello,
    port: Mutex<Option<u16>>,
    discovery_error: Mutex<Option<String>>,
    peers: Mutex<HashMap<String, Peer>>,
    offers: Mutex<HashMap<String, Offer>>,
    access: Mutex<HashMap<String, AccessRequest>>,
    /// Tokens this machine granted to others.
    granted: Mutex<HashSet<String>>,
    /// Tokens other machines granted to this one, by peer id.
    held: Mutex<HashMap<String, String>>,
    /// Kept alive for as long as the app runs; dropping it stops announcing.
    mdns: Mutex<Option<mdns_sd::ServiceDaemon>>,
    http: reqwest::Client,
}

/// Tauri state. Started once from `setup`.
pub struct PeerSync(Arc<Inner>);

impl PeerSync {
    pub fn new(app: AppHandle, db: &Db) -> Self {
        let me = Hello {
            id: instance_id(db),
            name: device_name(),
            protocol: PROTOCOL,
            app_version: app.package_info().version.to_string(),
        };
        let http = reqwest::Client::builder()
            .connect_timeout(Duration::from_secs(4))
            .timeout(Duration::from_secs(30))
            .build()
            .expect("http client");
        PeerSync(Arc::new(Inner {
            app,
            me,
            port: Mutex::new(None),
            discovery_error: Mutex::new(None),
            peers: Mutex::new(HashMap::new()),
            offers: Mutex::new(HashMap::new()),
            access: Mutex::new(HashMap::new()),
            granted: Mutex::new(HashSet::new()),
            held: Mutex::new(HashMap::new()),
            mdns: Mutex::new(None),
            http,
        }))
    }

    /// Bind the peer server, then announce it and start watching for others.
    pub async fn start(&self) {
        let inner = self.0.clone();
        let listener = match tokio::net::TcpListener::bind(SocketAddr::from((Ipv4Addr::UNSPECIFIED, PREFERRED_PORT))).await {
            Ok(l) => l,
            Err(_) => match tokio::net::TcpListener::bind(SocketAddr::from((Ipv4Addr::UNSPECIFIED, 0))).await {
                Ok(l) => l,
                Err(e) => {
                    log::error!("peer sync: couldn't open a port: {e}");
                    return;
                }
            },
        };
        let port = listener.local_addr().map(|a| a.port()).unwrap_or(PREFERRED_PORT);
        *inner.port.lock().unwrap() = Some(port);
        let router = router(inner.clone());
        tauri::async_runtime::spawn(async move {
            if let Err(e) = axum::serve(listener, router).await {
                log::error!("peer sync server stopped: {e}");
            }
        });
        // The id, not the name: the name is usually the owner's ("Sarah's
        // MacBook Air"), and the log goes out with error reports.
        log::info!("peer sync on port {port} as {}", inner.me.id);
        if let Err(e) = start_discovery(&inner, port) {
            log::warn!("peer sync: Bonjour unavailable, peers must be added by address: {e:#}");
            *inner.discovery_error.lock().unwrap() = Some(format!("{e:#}"));
        }
    }

    pub fn info(&self) -> PeerSyncInfo {
        let port = *self.0.port.lock().unwrap();
        PeerSyncInfo {
            id: self.0.me.id.clone(),
            name: self.0.me.name.clone(),
            port,
            addresses: port
                .map(|p| crate::band::lan_addresses().iter().map(|ip| format!("{ip}:{p}")).collect())
                .unwrap_or_default(),
            discovery_error: self.0.discovery_error.lock().unwrap().clone(),
            network_code: network_code(),
        }
    }

    pub fn peers(&self) -> Vec<Peer> {
        peer_list(&self.0)
    }

    fn peer_url(&self, peer_id: &str) -> Result<String, String> {
        self.0
            .peers
            .lock()
            .unwrap()
            .get(peer_id)
            .map(|p| p.base_url.clone())
            .ok_or_else(|| "That computer isn't on the network any more.".to_string())
    }
}

fn peer_list(inner: &Inner) -> Vec<Peer> {
    let mut v: Vec<Peer> = inner.peers.lock().unwrap().values().cloned().collect();
    v.sort_by(|a, b| a.name.to_lowercase().cmp(&b.name.to_lowercase()));
    v
}

fn emit_peers(inner: &Inner) {
    let _ = inner.app.emit(PEERS_EVENT, peer_list(inner));
}

/// A stable id for this installation, so a laptop that re-announces (new IP,
/// woke from sleep) is recognised as the same one.
fn instance_id(db: &Db) -> String {
    const KEY: &str = "peer_instance_id";
    let conn = db.home();
    let existing = conn
        .query_row("SELECT value FROM settings WHERE key = ?1", params![KEY], |r| r.get::<_, String>(0))
        .optional()
        .ok()
        .flatten()
        .and_then(|raw| serde_json::from_str::<String>(&raw).ok());
    if let Some(id) = existing {
        return id;
    }
    let id = ulid::Ulid::new().to_string().to_lowercase();
    let _ = conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?1, ?2, ?3) \
         ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        params![KEY, serde_json::to_string(&id).unwrap_or_default(), unix_ms()],
    );
    id
}

/// What people call this computer ("Sarah's MacBook Pro").
fn device_name() -> String {
    let run = |cmd: &str, args: &[&str]| {
        std::process::Command::new(cmd)
            .args(args)
            .output()
            .ok()
            .filter(|o| o.status.success())
            .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
            .filter(|s| !s.is_empty())
    };
    #[cfg(target_os = "macos")]
    if let Some(n) = run("scutil", &["--get", "ComputerName"]) {
        return n;
    }
    run("hostname", &[]).unwrap_or_else(|| "Church Slides".into())
}

fn start_discovery(inner: &Arc<Inner>, port: u16) -> Result<()> {
    use mdns_sd::{ServiceDaemon, ServiceEvent, ServiceInfo};
    let daemon = ServiceDaemon::new()?;
    let short = &inner.me.id[inner.me.id.len().saturating_sub(6)..];
    // Instance and host names must be unique on the network; people see
    // `name` from the TXT record, never these.
    let instance = format!("Church Slides {short}");
    let host = format!("church-slides-{short}.local.");
    let props = [
        ("id", inner.me.id.as_str()),
        ("name", inner.me.name.as_str()),
        ("protocol", "1"),
    ];
    let info = ServiceInfo::new(SERVICE_TYPE, &instance, &host, "", port, &props[..])?.enable_addr_auto();
    daemon.register(info)?;
    let rx = daemon.browse(SERVICE_TYPE)?;
    *inner.mdns.lock().unwrap() = Some(daemon);

    let inner = inner.clone();
    tauri::async_runtime::spawn(async move {
        while let Ok(event) = rx.recv_async().await {
            match event {
                ServiceEvent::ServiceResolved(info) => {
                    let Some(id) = info.get_property_val_str("id").map(str::to_string) else { continue };
                    if id == inner.me.id {
                        continue;
                    }
                    let Some(ip) = best_address(info.get_addresses().iter().copied()) else { continue };
                    let name = info.get_property_val_str("name").unwrap_or("Church Slides").to_string();
                    let peer = Peer {
                        id: id.clone(),
                        name,
                        base_url: format!("http://{ip}:{}", info.get_port()),
                        manual: false,
                        fullname: Some(info.get_fullname().to_string()),
                    };
                    inner.peers.lock().unwrap().insert(id, peer);
                    emit_peers(&inner);
                }
                ServiceEvent::ServiceRemoved(_, fullname) => {
                    let removed = {
                        let mut peers = inner.peers.lock().unwrap();
                        let before = peers.len();
                        peers.retain(|_, p| p.fullname.as_deref() != Some(fullname.as_str()));
                        before != peers.len()
                    };
                    if removed {
                        emit_peers(&inner);
                    }
                }
                _ => {}
            }
        }
    });
    Ok(())
}

/// The last 4 hex digits of the default router's hardware address. The
/// Wi-Fi name would be friendlier, but macOS and Windows 11 only give it to
/// apps with location permission; the router's address needs none. The
/// *last* digits, because the first ones name the router's maker.
fn network_code() -> Option<String> {
    let gw = netdev::get_default_gateway().ok()?;
    let octets = gw.mac_addr.octets();
    if octets == [0; 6] {
        return None;
    }
    Some(format!("{:02X}{:02X}", octets[4], octets[5]))
}

/// A private IPv4 address first — that's the one on the church Wi-Fi.
fn best_address(addrs: impl Iterator<Item = IpAddr>) -> Option<IpAddr> {
    let mut v4: Vec<Ipv4Addr> = addrs
        .filter_map(|a| match a {
            IpAddr::V4(v) if !v.is_loopback() && !v.is_link_local() => Some(v),
            _ => None,
        })
        .collect();
    v4.sort_by_key(|ip| (!ip.is_private(), ip.octets()));
    v4.first().copied().map(IpAddr::V4)
}

fn prune(inner: &Inner) {
    inner.offers.lock().unwrap().retain(|_, o| o.at.elapsed() < PENDING_TTL);
    inner.access.lock().unwrap().retain(|_, a| a.at.elapsed() < PENDING_TTL || a.status == Status::Accepted);
}

fn router(inner: Arc<Inner>) -> Router {
    Router::new()
        .route("/peer/hello", get(|State(i): State<Arc<Inner>>| async move { Json(i.me.clone()) }))
        .route("/peer/offer", post(receive_offer))
        .route("/peer/offer/:id", get(offer_status))
        .route("/peer/access", post(receive_access_request))
        .route("/peer/access/:id", get(access_status))
        .route("/peer/setlists", get(serve_setlists))
        .route("/peer/setlists/:id", get(serve_setlist))
        .route("/peer/library", get(serve_library))
        // A setlist with charts is tens of KB; a whole library with charts a
        // few MB. This is generous headroom for either.
        .layer(DefaultBodyLimit::max(64 * 1024 * 1024))
        .with_state(inner)
}

#[derive(Deserialize)]
struct OfferBody {
    from: Hello,
    bundle: SetlistBundle,
}

fn err(code: StatusCode, msg: &str) -> Response {
    (code, Json(json!({ "error": msg }))).into_response()
}

async fn receive_offer(State(inner): State<Arc<Inner>>, Json(body): Json<OfferBody>) -> Response {
    prune(&inner);
    if body.bundle.protocol > PROTOCOL {
        return err(StatusCode::UNPROCESSABLE_ENTITY, "The other computer runs an older Church Slides — update it to receive this.");
    }
    let id = new_id("offer");
    {
        let mut offers = inner.offers.lock().unwrap();
        if offers.len() >= MAX_PENDING {
            return err(StatusCode::TOO_MANY_REQUESTS, "Too many transfers waiting on the other computer.");
        }
        let _ = inner.app.emit(
            OFFER_EVENT,
            json!({
                "offer_id": id,
                "from": body.from,
                "setlist_name": body.bundle.setlist_name,
                "song_count": if body.bundle.library { body.bundle.songs.len() } else { body.bundle.items.len() },
                "library": body.bundle.library,
            }),
        );
        offers.insert(id.clone(), Offer { from: body.from, bundle: body.bundle, status: Status::Pending, at: Instant::now() });
    }
    Json(json!({ "offer_id": id })).into_response()
}

async fn offer_status(State(inner): State<Arc<Inner>>, UrlPath(id): UrlPath<String>) -> Response {
    match inner.offers.lock().unwrap().get(&id) {
        Some(o) => Json(json!({ "status": o.status })).into_response(),
        None => err(StatusCode::NOT_FOUND, "expired"),
    }
}

#[derive(Deserialize)]
struct AccessBody {
    from: Hello,
}

async fn receive_access_request(State(inner): State<Arc<Inner>>, Json(body): Json<AccessBody>) -> Response {
    prune(&inner);
    let id = new_id("access");
    let mut access = inner.access.lock().unwrap();
    if access.values().filter(|a| a.status == Status::Pending).count() >= MAX_PENDING {
        return err(StatusCode::TOO_MANY_REQUESTS, "Too many requests waiting on the other computer.");
    }
    let _ = inner.app.emit(ACCESS_EVENT, json!({ "request_id": id, "from": body.from }));
    access.insert(id.clone(), AccessRequest { from: body.from, status: Status::Pending, token: None, at: Instant::now() });
    Json(json!({ "request_id": id })).into_response()
}

async fn access_status(State(inner): State<Arc<Inner>>, UrlPath(id): UrlPath<String>) -> Response {
    match inner.access.lock().unwrap().get(&id) {
        Some(a) => Json(json!({ "status": a.status, "token": a.token })).into_response(),
        None => err(StatusCode::NOT_FOUND, "expired"),
    }
}

fn authorised(inner: &Inner, headers: &HeaderMap) -> bool {
    headers
        .get(TOKEN_HEADER)
        .and_then(|v| v.to_str().ok())
        .is_some_and(|t| inner.granted.lock().unwrap().contains(t))
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RemoteSetlist {
    pub id: String,
    pub name: String,
    pub song_count: usize,
    pub last_used_at: Option<i64>,
    pub created_at: i64,
}

async fn serve_setlists(State(inner): State<Arc<Inner>>, headers: HeaderMap) -> Response {
    if !authorised(&inner, &headers) {
        return err(StatusCode::FORBIDDEN, "not allowed");
    }
    let db = inner.app.state::<Arc<Db>>();
    let result = (|| -> Result<Vec<RemoteSetlist>> {
        let conn = db.conn().lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT s.id, s.name, s.last_used_at, s.created_at, \
                (SELECT COUNT(*) FROM setlist_items i WHERE i.setlist_id = s.id) \
             FROM setlists s ORDER BY COALESCE(s.last_used_at, s.created_at) DESC",
        )?;
        let rows = stmt
            .query_map([], |r| {
                Ok(RemoteSetlist {
                    id: r.get(0)?,
                    name: r.get(1)?,
                    last_used_at: r.get(2)?,
                    created_at: r.get(3)?,
                    song_count: r.get::<_, i64>(4)? as usize,
                })
            })?
            .collect::<Result<Vec<_>, _>>()?;
        Ok(rows)
    })();
    match result {
        Ok(rows) => Json(rows).into_response(),
        Err(e) => err(StatusCode::INTERNAL_SERVER_ERROR, &format!("{e:#}")),
    }
}

async fn serve_setlist(State(inner): State<Arc<Inner>>, headers: HeaderMap, UrlPath(id): UrlPath<String>) -> Response {
    if !authorised(&inner, &headers) {
        return err(StatusCode::FORBIDDEN, "not allowed");
    }
    let db = inner.app.state::<Arc<Db>>();
    match build_bundle(&db, &id) {
        Ok(b) => Json(b).into_response(),
        Err(e) => err(StatusCode::NOT_FOUND, &format!("{e:#}")),
    }
}

async fn serve_library(State(inner): State<Arc<Inner>>, headers: HeaderMap) -> Response {
    if !authorised(&inner, &headers) {
        return err(StatusCode::FORBIDDEN, "not allowed");
    }
    let db = inner.app.state::<Arc<Db>>();
    match build_library_bundle(&db) {
        Ok(b) => Json(b).into_response(),
        Err(e) => err(StatusCode::INTERNAL_SERVER_ERROR, &format!("{e:#}")),
    }
}

// --- Commands ---------------------------------------------------------------------

async fn read_json<T: serde::de::DeserializeOwned>(res: reqwest::Response) -> Result<T, String> {
    let status = res.status();
    if !status.is_success() {
        let msg = res
            .json::<serde_json::Value>()
            .await
            .ok()
            .and_then(|v| v.get("error").and_then(|e| e.as_str()).map(str::to_string))
            .unwrap_or_else(|| format!("the other computer answered {status}"));
        return Err(msg);
    }
    res.json::<T>().await.map_err(|e| format!("unexpected reply: {e}"))
}

fn net_err(e: reqwest::Error) -> String {
    if e.is_connect() || e.is_timeout() {
        "Couldn't reach the other computer. Check it's on the same Wi-Fi and Church Slides is open.".into()
    } else {
        format!("Network error: {e}")
    }
}

#[tauri::command]
pub fn peer_sync_info(sync: tauri::State<'_, PeerSync>) -> PeerSyncInfo {
    sync.info()
}

#[tauri::command]
pub fn peer_list_peers(sync: tauri::State<'_, PeerSync>) -> Vec<Peer> {
    sync.peers()
}

/// Add a computer by address ("192.168.1.20" or "192.168.1.20:7778"), for
/// networks where Bonjour doesn't get through.
#[tauri::command]
pub async fn peer_add_manual(sync: tauri::State<'_, PeerSync>, address: String) -> Result<Peer, String> {
    let addr = address.trim().trim_start_matches("http://").trim_end_matches('/');
    if addr.is_empty() {
        return Err("Type the other computer's address.".into());
    }
    let base_url = if addr.contains(':') { format!("http://{addr}") } else { format!("http://{addr}:{PREFERRED_PORT}") };
    let hello: Hello = read_json(sync.0.http.get(format!("{base_url}/peer/hello")).send().await.map_err(net_err)?).await?;
    if hello.id == sync.0.me.id {
        return Err("That's this computer's own address.".into());
    }
    let peer = Peer { id: hello.id.clone(), name: hello.name, base_url, manual: true, fullname: None };
    sync.0.peers.lock().unwrap().insert(hello.id, peer.clone());
    emit_peers(&sync.0);
    Ok(peer)
}

#[tauri::command]
pub async fn peer_send_setlist(
    sync: tauri::State<'_, PeerSync>,
    db: tauri::State<'_, Arc<Db>>,
    peer_id: String,
    setlist_id: String,
) -> Result<String, String> {
    let bundle = build_bundle(&db, &setlist_id).map_err(|e| format!("{e:#}"))?;
    send_offer(&sync, &peer_id, bundle).await
}

#[tauri::command]
pub async fn peer_send_library(
    sync: tauri::State<'_, PeerSync>,
    db: tauri::State<'_, Arc<Db>>,
    peer_id: String,
) -> Result<String, String> {
    let bundle = build_library_bundle(&db).map_err(|e| format!("{e:#}"))?;
    send_offer(&sync, &peer_id, bundle).await
}

async fn send_offer(sync: &PeerSync, peer_id: &str, bundle: SetlistBundle) -> Result<String, String> {
    let url = sync.peer_url(peer_id)?;
    let res = sync
        .0
        .http
        .post(format!("{url}/peer/offer"))
        .json(&json!({ "from": sync.0.me, "bundle": bundle }))
        .send()
        .await
        .map_err(net_err)?;
    let v: serde_json::Value = read_json(res).await?;
    v["offer_id"].as_str().map(str::to_string).ok_or_else(|| "unexpected reply".into())
}

/// "pending" | "accepted" | "declined" | "expired"
#[tauri::command]
pub async fn peer_offer_status(sync: tauri::State<'_, PeerSync>, peer_id: String, offer_id: String) -> Result<String, String> {
    let url = sync.peer_url(&peer_id)?;
    let res = sync.0.http.get(format!("{url}/peer/offer/{offer_id}")).send().await.map_err(net_err)?;
    if res.status() == StatusCode::NOT_FOUND {
        return Ok("expired".into());
    }
    let v: serde_json::Value = read_json(res).await?;
    Ok(v["status"].as_str().unwrap_or("pending").to_string())
}

#[tauri::command]
pub async fn peer_request_access(sync: tauri::State<'_, PeerSync>, peer_id: String) -> Result<String, String> {
    let url = sync.peer_url(&peer_id)?;
    let res = sync
        .0
        .http
        .post(format!("{url}/peer/access"))
        .json(&json!({ "from": sync.0.me }))
        .send()
        .await
        .map_err(net_err)?;
    let v: serde_json::Value = read_json(res).await?;
    v["request_id"].as_str().map(str::to_string).ok_or_else(|| "unexpected reply".into())
}

/// True once this computer holds a token for `peer_id` (asked before, allowed).
#[tauri::command]
pub fn peer_has_access(sync: tauri::State<'_, PeerSync>, peer_id: String) -> bool {
    sync.0.held.lock().unwrap().contains_key(&peer_id)
}

/// "pending" | "accepted" | "declined" | "expired". On "accepted" the token
/// is kept for the rest of the session.
#[tauri::command]
pub async fn peer_access_status(sync: tauri::State<'_, PeerSync>, peer_id: String, request_id: String) -> Result<String, String> {
    let url = sync.peer_url(&peer_id)?;
    let res = sync.0.http.get(format!("{url}/peer/access/{request_id}")).send().await.map_err(net_err)?;
    if res.status() == StatusCode::NOT_FOUND {
        return Ok("expired".into());
    }
    let v: serde_json::Value = read_json(res).await?;
    let status = v["status"].as_str().unwrap_or("pending").to_string();
    if let Some(token) = v["token"].as_str() {
        sync.0.held.lock().unwrap().insert(peer_id, token.to_string());
    }
    Ok(status)
}

fn held_token(sync: &PeerSync, peer_id: &str) -> Result<String, String> {
    sync.0
        .held
        .lock()
        .unwrap()
        .get(peer_id)
        .cloned()
        .ok_or_else(|| "Ask the other computer for access first.".to_string())
}

#[tauri::command]
pub async fn peer_remote_setlists(sync: tauri::State<'_, PeerSync>, peer_id: String) -> Result<Vec<RemoteSetlist>, String> {
    let url = sync.peer_url(&peer_id)?;
    let token = held_token(&sync, &peer_id)?;
    let res = sync.0.http.get(format!("{url}/peer/setlists")).header(TOKEN_HEADER, token).send().await.map_err(net_err)?;
    if res.status() == StatusCode::FORBIDDEN {
        // The other app restarted and forgot the grant.
        sync.0.held.lock().unwrap().remove(&peer_id);
        return Err("The other computer needs to allow access again.".into());
    }
    read_json(res).await
}

#[tauri::command]
pub async fn peer_fetch_setlist(
    sync: tauri::State<'_, PeerSync>,
    peer_id: String,
    setlist_id: String,
) -> Result<SetlistBundle, String> {
    let url = sync.peer_url(&peer_id)?;
    let token = held_token(&sync, &peer_id)?;
    let res = sync
        .0
        .http
        .get(format!("{url}/peer/setlists/{setlist_id}"))
        .header(TOKEN_HEADER, token)
        .send()
        .await
        .map_err(net_err)?;
    let bundle: SetlistBundle = read_json(res).await?;
    if bundle.protocol > PROTOCOL {
        return Err("That setlist came from a newer version of Church Slides — update this computer to receive it.".into());
    }
    Ok(bundle)
}

#[tauri::command]
pub async fn peer_fetch_library(sync: tauri::State<'_, PeerSync>, peer_id: String) -> Result<SetlistBundle, String> {
    let url = sync.peer_url(&peer_id)?;
    let token = held_token(&sync, &peer_id)?;
    let res = sync.0.http.get(format!("{url}/peer/library")).header(TOKEN_HEADER, token).send().await.map_err(net_err)?;
    if res.status() == StatusCode::NOT_FOUND {
        return Err("The other computer runs an older Church Slides — update it to copy its whole library.".into());
    }
    if res.status() == StatusCode::FORBIDDEN {
        sync.0.held.lock().unwrap().remove(&peer_id);
        return Err("The other computer needs to allow access again.".into());
    }
    read_json(res).await
}

#[derive(Serialize)]
pub struct IncomingOffer {
    from: Hello,
    bundle: SetlistBundle,
}

#[tauri::command]
pub fn peer_incoming_offer(sync: tauri::State<'_, PeerSync>, offer_id: String) -> Result<IncomingOffer, String> {
    sync.0
        .offers
        .lock()
        .unwrap()
        .get(&offer_id)
        .map(|o| IncomingOffer { from: o.from.clone(), bundle: o.bundle.clone() })
        .ok_or_else(|| "That transfer has expired — ask them to send it again.".into())
}

/// The receiver imported (`accepted`) or turned down an offer; the sender
/// sees it on its next status poll.
#[tauri::command]
pub fn peer_respond_offer(sync: tauri::State<'_, PeerSync>, offer_id: String, accepted: bool) {
    if let Some(o) = sync.0.offers.lock().unwrap().get_mut(&offer_id) {
        o.status = if accepted { Status::Accepted } else { Status::Declined };
    }
}

#[tauri::command]
pub fn peer_respond_access(sync: tauri::State<'_, PeerSync>, request_id: String, allow: bool) {
    let mut access = sync.0.access.lock().unwrap();
    let Some(a) = access.get_mut(&request_id) else { return };
    if allow {
        let token = ulid::Ulid::new().to_string();
        sync.0.granted.lock().unwrap().insert(token.clone());
        a.token = Some(token);
        a.status = Status::Accepted;
        log::info!("peer sync: allowed {} to copy setlists", a.from.id);
    } else {
        a.status = Status::Declined;
    }
}

#[tauri::command]
pub fn peer_apply_bundle(
    app: AppHandle,
    db: tauri::State<'_, Arc<Db>>,
    bundle: SetlistBundle,
    decisions: HashMap<String, SongDecision>,
    from_name: String,
) -> Result<ApplySummary, String> {
    let backups = app.path().app_data_dir().ok().map(|d| d.join("sync-backups"));
    apply_bundle(&db, &bundle, &decisions, &from_name, backups.as_deref()).map_err(|e| format!("{e:#}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn db() -> (tempfile::TempDir, Arc<Db>) {
        let dir = tempfile::tempdir().unwrap();
        let db = Arc::new(Db::open(&dir.path().join("t.db")).unwrap());
        (dir, db)
    }

    fn song(db: &Arc<Db>, title: &str, body: &str) -> String {
        db.create_song(crate::db::NewSong {
            title: title.into(),
            body_text: body.into(),
            ccli: None,
            author: None,
            copyright: None,
            default_tempo_wps: None,
            slide_order: None,
        })
        .unwrap()
    }

    /// A setlist on the "sender" with two songs, one played twice, one with
    /// a chart and a per-setlist edit.
    fn sender() -> (tempfile::TempDir, Arc<Db>, SetlistBundle) {
        let (dir, db) = db();
        let a = song(&db, "Oceans", "[v1]\nYou call me out upon the waters");
        let b = song(&db, "Way Maker", "[c]\nWay maker miracle worker");
        charts::import_charts(
            &db,
            vec![charts::ChartImportItem {
                existing_song_id: Some(a.clone()),
                song: None,
                chart_json: "{\"key\":\"D\"}".into(),
                original_key: Some("D".into()),
                source_format: Some("chordpro".into()),
                fill_metadata: None,
                relabel: None,
            }],
        )
        .unwrap();
        let set = setlists::create_setlist(&db, "Sunday").unwrap();
        setlists::add_setlist_item(&db, &set, &a).unwrap();
        let item_b = setlists::add_setlist_item(&db, &set, &b).unwrap();
        setlists::add_setlist_item(&db, &set, &a).unwrap();
        setlists::set_setlist_item_slides(&db, &set, &item_b, Some("[{\"lyrics\":\"edited\"}]")).unwrap();
        let bundle = build_bundle(&db, &set).unwrap();
        (dir, db, bundle)
    }

    #[test]
    fn bundle_carries_each_song_once_with_its_extras() {
        let (_d, _db, b) = sender();
        assert_eq!(b.setlist_name, "Sunday");
        assert_eq!(b.items.len(), 3);
        assert_eq!(b.songs.len(), 2);
        let oceans = b.songs.iter().find(|s| s.title == "Oceans").unwrap();
        assert_eq!(oceans.chart.as_ref().unwrap().original_key.as_deref(), Some("D"));
        assert_eq!(b.items[1].slides_override.as_deref(), Some("[{\"lyrics\":\"edited\"}]"));
    }

    #[test]
    fn a_bundle_from_an_older_copy_with_scriptures_still_reads() {
        let (_d, _db, b) = sender();
        let mut json: serde_json::Value = serde_json::to_value(&b).unwrap();
        json["songs"][0]["scriptures"] = serde_json::json!([{
            "reference": "Matthew 14:29", "translation": null, "section_key": null,
            "anchor_lyric": null, "every_repeat": true, "source": "manual",
            "strength": null, "note": null
        }]);
        let back: SetlistBundle = serde_json::from_value(json).unwrap();
        assert_eq!(back.songs.len(), b.songs.len());
    }

    #[test]
    fn apply_reuses_matches_and_adds_the_rest() {
        let (_d, _sdb, bundle) = sender();
        let (_d2, db) = db();
        let mine = song(&db, "Oceans", "[v1]\nYou call me out upon the waters");
        let oceans = bundle.songs.iter().find(|s| s.title == "Oceans").unwrap().ref_id.clone();
        let waymaker = bundle.songs.iter().find(|s| s.title == "Way Maker").unwrap().ref_id.clone();
        let decisions = HashMap::from([
            (oceans, SongDecision::UseExisting { song_id: mine.clone() }),
            (waymaker, SongDecision::New),
        ]);
        let before = db.list_songs().unwrap().len();
        let s = apply_bundle(&db, &bundle, &decisions, "Sarah's MacBook", None).unwrap();
        assert_eq!((s.songs_created, s.songs_reused, s.charts_added), (1, 1, 1));
        assert_eq!(db.list_songs().unwrap().len(), before + 1, "no duplicate Oceans");
        let set = setlists::get_setlist(&db, &s.setlist_id).unwrap().unwrap();
        assert_eq!(set.setlist.name, "Sunday");
        let ids: Vec<_> = set.items.iter().map(|i| i.item.song_id.clone().unwrap()).collect();
        assert_eq!(ids[0], mine);
        assert_eq!(ids[2], mine);
        assert_eq!(set.items[1].item.slides_override.as_deref(), Some("[{\"lyrics\":\"edited\"}]"));
        assert_eq!(charts::get_charts(&db, &[mine.clone()]).unwrap().len(), 1, "chart filled in");

        // The same transfer again: nothing new in the library, and the
        // setlist gets a distinguishable name.
        let again = apply_bundle(&db, &bundle, &HashMap::from([
            (bundle.songs[0].ref_id.clone(), SongDecision::UseExisting { song_id: mine.clone() }),
            (bundle.songs[1].ref_id.clone(), SongDecision::UseExisting { song_id: ids[1].clone() }),
        ]), "Sarah's MacBook", None).unwrap();
        assert_eq!(again.setlist_name, "Sunday (Sarah's MacBook)");
        assert_eq!(db.list_songs().unwrap().len(), before + 1);
    }

    #[test]
    fn replace_overwrites_and_snapshots_keep_both_adds() {
        let (_d, _sdb, bundle) = sender();
        let (dir, db) = db();
        let mine = song(&db, "Oceans", "[v1]\nMy different lyrics");
        let wm = song(&db, "Way Maker", "[c]\nOld words");
        let decisions = HashMap::from([
            (bundle.songs[0].ref_id.clone(), SongDecision::ReplaceExisting { song_id: mine.clone() }),
            (bundle.songs[1].ref_id.clone(), SongDecision::KeepBoth),
        ]);
        let s = apply_bundle(&db, &bundle, &decisions, "Sarah's MacBook", Some(&dir.path().join("bk"))).unwrap();
        assert!(std::path::Path::new(s.backup_path.as_ref().unwrap()).exists());
        assert_eq!(db.get_song(&mine).unwrap().unwrap().body_text, "[v1]\nYou call me out upon the waters");
        assert_eq!(db.get_song(&wm).unwrap().unwrap().body_text, "[c]\nOld words");
        assert!(db.list_songs().unwrap().iter().any(|s| s.title == "Way Maker (Sarah's MacBook)"));
    }

    #[test]
    fn apply_refuses_an_undecided_song_and_writes_nothing() {
        let (_d, _sdb, bundle) = sender();
        let (_d2, db) = db();
        let before = db.list_songs().unwrap().len();
        let decisions = HashMap::from([(bundle.songs[0].ref_id.clone(), SongDecision::New)]);
        assert!(apply_bundle(&db, &bundle, &decisions, "X", None).is_err());
        assert_eq!(db.list_songs().unwrap().len(), before);
        assert!(setlists::list_setlists(&db).unwrap().is_empty());
    }

    #[test]
    fn songs_only_bundles_stay_readable_by_older_copies() {
        let (_d, _db, b) = sender();
        assert_eq!(b.protocol, 1);
    }

    #[test]
    fn scripture_and_text_items_travel_with_their_content() {
        let (_d, sdb) = db();
        let a = song(&sdb, "Oceans", "[v1]\nYou call me out upon the waters");
        let set = setlists::create_setlist(&sdb, "Sunday").unwrap();
        setlists::add_setlist_item(&sdb, &set, &a).unwrap();
        let scripture = r#"{"reference":"John 3:16","verses":[{"ref":"John 3:16","text":"For God so loved"}]}"#;
        setlists::add_service_item(&sdb, &set, "scripture", scripture, None, None).unwrap();
        setlists::add_service_item(&sdb, &set, "text", r#"{"title":"Welcome"}"#, Some("[]"), None).unwrap();
        let bundle = build_bundle(&sdb, &set).unwrap();
        assert_eq!(bundle.protocol, 2);
        assert_eq!(bundle.songs.len(), 1);

        let (_d2, db) = db();
        let decisions = HashMap::from([(bundle.songs[0].ref_id.clone(), SongDecision::New)]);
        let s = apply_bundle(&db, &bundle, &decisions, "X", None).unwrap();
        let got = setlists::get_setlist(&db, &s.setlist_id).unwrap().unwrap();
        let kinds: Vec<&str> = got.items.iter().map(|i| i.item.kind.as_str()).collect();
        assert_eq!(kinds, vec!["song", "scripture", "text"]);
        assert_eq!(got.items[1].item.content.as_deref(), Some(scripture));
        assert_eq!(got.items[2].item.slides_override.as_deref(), Some("[]"));
        assert!(got.items[1].item.song_id.is_none());
    }

    #[test]
    fn whole_library_adds_songs_with_chords_and_makes_no_setlist() {
        let (_d, sdb, _) = sender();
        song(&sdb, "Not in any set", "[v1]\nJust in the library");
        let bundle = build_library_bundle(&sdb).unwrap();
        assert!(bundle.library);
        assert_eq!(bundle.protocol, 3, "an older copy refuses it rather than making an empty setlist");
        // Every song, the seeded example hymns included — not only the set's two.
        assert_eq!(bundle.songs.len(), sdb.list_songs().unwrap().len());
        assert!(bundle.songs.iter().any(|s| s.title == "Not in any set"));
        assert!(bundle.items.is_empty());
        assert!(bundle.songs.iter().find(|s| s.title == "Oceans").unwrap().chart.is_some());

        let (_d2, db) = db();
        let before = db.list_songs().unwrap().len();
        let decisions: HashMap<_, _> = bundle.songs.iter().map(|s| (s.ref_id.clone(), SongDecision::New)).collect();
        let s = apply_bundle(&db, &bundle, &decisions, "Sarah's MacBook", None).unwrap();
        assert_eq!(s.songs_created, bundle.songs.len());
        assert_eq!(s.charts_added, 1);
        assert!(s.setlist_id.is_empty());
        assert!(setlists::list_setlists(&db).unwrap().is_empty());
        assert_eq!(db.list_songs().unwrap().len(), before + bundle.songs.len());
    }

    #[test]
    fn network_code_is_four_hex_digits_when_there_is_a_router() {
        // No router on a CI machine is fine; with one, it's the short code.
        if let Some(code) = network_code() {
            println!("network code: {code}");
            assert_eq!(code.len(), 4);
            assert!(code.chars().all(|c| c.is_ascii_hexdigit() && !c.is_ascii_lowercase()));
        }
    }

    #[test]
    fn prefers_private_ipv4() {
        let addrs = ["fe80::1".parse().unwrap(), "8.8.8.8".parse().unwrap(), "192.168.1.9".parse().unwrap()];
        assert_eq!(best_address(addrs.into_iter()), Some("192.168.1.9".parse().unwrap()));
    }
}
