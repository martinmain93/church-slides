//! Online band relay: deploy it into the church's own Cloudflare account,
//! remember where it is, and mint rooms for services.
//!
//! This is the fallback for when band devices can't reach the slides
//! computer over the church's Wi-Fi (guest networks that isolate devices,
//! a separate stage network, phones on 4G). The relay — a Worker plus one
//! Durable Object per service (`relay/src`) — runs on the church's free
//! Cloudflare plan: nothing here uses anyone else's account or credentials.
//!
//! Signing in is either Cloudflare OAuth (PKCE, opened in the browser), when
//! this build has an OAuth client id, or a pasted API token made from
//! Cloudflare's "Edit Cloudflare Workers" template. Either way the token is
//! only needed to (re)deploy; services use the relay's own host key. The
//! token lives in the system keychain (macOS Keychain, Windows Credential
//! Manager), never in the settings file.

use std::path::{Path, PathBuf};
use std::time::Duration;

use anyhow::{anyhow, bail, Context, Result};
use base64::Engine as _;
use hmac::{Hmac, Mac};
use rand::RngCore;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

/// The relay Worker, built by `pnpm build:relay`.
const WORKER_JS: &str = include_str!("../../relay/dist/worker.js");
const SCRIPT_NAME: &str = "church-slides-relay";
const API: &str = "https://api.cloudflare.com/client/v4";

/// The Cloudflare API base. Tests point it at a local fake; nothing else can.
#[cfg(test)]
static API_OVERRIDE: std::sync::Mutex<Option<String>> = std::sync::Mutex::new(None);

fn api() -> String {
    #[cfg(test)]
    if let Some(base) = API_OVERRIDE.lock().unwrap().clone() {
        return base;
    }
    API.to_string()
}
const COMPATIBILITY_DATE: &str = "2025-09-01";

/// Set at build time once a Cloudflare OAuth client is registered for the
/// app (Cloudflare dashboard → Manage account → OAuth clients). Without it,
/// setup offers the API-token route only.
const OAUTH_CLIENT_ID: Option<&str> = option_env!("CHURCH_SLIDES_CF_OAUTH_CLIENT_ID");
const OAUTH_AUTHORIZE: &str = "https://dash.cloudflare.com/oauth2/auth";
const OAUTH_TOKEN: &str = "https://dash.cloudflare.com/oauth2/token";
/// The redirect URI registered with the OAuth client must match exactly.
const OAUTH_PORT: u16 = 47_219;
const OAUTH_SCOPES: &str = "account:read workers_scripts:write offline_access";

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct CloudConfig {
    pub account_id: String,
    pub account_name: String,
    pub worker_url: String,
    /// Shared secret between this computer and the relay (a Worker secret).
    pub host_key: String,
    /// Fingerprint of the relay code that was deployed, to spot app updates.
    pub deployed_version: String,
    /// This computer's room. Fixed, so the band's QR codes and bookmarks keep
    /// working every time the room is opened again (and after a restart);
    /// "Make new links" replaces it.
    #[serde(default)]
    pub room: Option<String>,
    /// Where earlier builds kept the token. Read once to move it into the
    /// keychain, never written again.
    #[serde(default, rename = "token", skip_serializing)]
    legacy_token: Option<String>,
    #[serde(default, rename = "refresh_token", skip_serializing)]
    legacy_refresh_token: Option<String>,
}

/// The Cloudflare token(s), kept in the system keychain as one item.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct Credentials {
    /// API token or OAuth access token — only used to (re)deploy.
    pub token: String,
    #[serde(default)]
    pub refresh_token: Option<String>,
}

// --- Keychain ----------------------------------------------------------------

// Unused in test builds, which swap in an in-memory store.
#[cfg_attr(test, allow(dead_code))]
const KEYCHAIN_SERVICE: &str = "com.churchslides.app.cloud-relay";
#[cfg_attr(test, allow(dead_code))]
const KEYCHAIN_USER: &str = "cloudflare";

/// Somewhere to keep one secret. The keychain in the app; memory in tests.
trait SecretStore: Send + Sync {
    fn get(&self) -> Result<Option<String>>;
    fn set(&self, secret: &str) -> Result<()>;
    fn delete(&self) -> Result<()>;
}

#[cfg_attr(test, allow(dead_code))]
struct Keychain;

impl SecretStore for Keychain {
    fn get(&self) -> Result<Option<String>> {
        match keyring::Entry::new(KEYCHAIN_SERVICE, KEYCHAIN_USER)?.get_password() {
            Ok(s) => Ok(Some(s)),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(e) => Err(anyhow!("Couldn't read the Cloudflare token from the keychain: {e}")),
        }
    }
    fn set(&self, secret: &str) -> Result<()> {
        keyring::Entry::new(KEYCHAIN_SERVICE, KEYCHAIN_USER)?
            .set_password(secret)
            .map_err(|e| anyhow!("Couldn't save the Cloudflare token to the keychain: {e}"))
    }
    fn delete(&self) -> Result<()> {
        match keyring::Entry::new(KEYCHAIN_SERVICE, KEYCHAIN_USER)?.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(e) => Err(anyhow!("Couldn't remove the Cloudflare token from the keychain: {e}")),
        }
    }
}

#[cfg(test)]
static TEST_SECRETS: std::sync::Mutex<Option<String>> = std::sync::Mutex::new(None);

#[cfg(test)]
struct MemoryStore;

#[cfg(test)]
impl SecretStore for MemoryStore {
    fn get(&self) -> Result<Option<String>> {
        Ok(TEST_SECRETS.lock().unwrap().clone())
    }
    fn set(&self, secret: &str) -> Result<()> {
        *TEST_SECRETS.lock().unwrap() = Some(secret.to_string());
        Ok(())
    }
    fn delete(&self) -> Result<()> {
        *TEST_SECRETS.lock().unwrap() = None;
        Ok(())
    }
}

fn secrets() -> Box<dyn SecretStore> {
    #[cfg(test)]
    return Box::new(MemoryStore);
    #[cfg(not(test))]
    Box::new(Keychain)
}

fn save_credentials(creds: &Credentials) -> Result<()> {
    secrets().set(&serde_json::to_string(creds)?)
}

pub fn load_credentials() -> Result<Option<Credentials>> {
    Ok(match secrets().get()? {
        Some(json) => Some(serde_json::from_str(&json).context("The saved Cloudflare token is unreadable — set up again")?),
        None => None,
    })
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct CloudStatus {
    pub configured: bool,
    pub worker_url: Option<String>,
    pub account_name: Option<String>,
    /// The app carries a newer relay than the one deployed.
    pub needs_update: bool,
    /// This build can sign in with Cloudflare in the browser.
    pub oauth_available: bool,
    /// This build carries a real relay (not the dev stub).
    pub relay_built: bool,
}

#[derive(Debug, Clone, serde::Serialize)]
#[serde(tag = "outcome", rename_all = "snake_case")]
pub enum SetupOutcome {
    Done { status: CloudStatus },
    /// The login can see several Cloudflare accounts: pick one, then call again.
    ChooseAccount { accounts: Vec<AccountChoice> },
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct AccountChoice {
    pub id: String,
    pub name: String,
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct CloudRoom {
    /// e.g. https://church-slides-relay.grace-church.workers.dev
    pub base_url: String,
    pub room: String,
    /// Device key for this room — goes in the QR code.
    pub key: String,
    /// The slides computer's socket URL (carries the host key: never shown).
    pub host_socket: String,
}

pub fn relay_version() -> String {
    let digest = Sha256::digest(WORKER_JS.as_bytes());
    digest[..8].iter().map(|b| format!("{b:02x}")).collect()
}

fn relay_built() -> bool {
    !WORKER_JS.starts_with("// stub")
}

fn config_path(app_data: &Path) -> PathBuf {
    app_data.join("cloud_relay.json")
}

pub fn load(app_data: &Path) -> Option<CloudConfig> {
    let text = std::fs::read_to_string(config_path(app_data)).ok()?;
    let mut cfg: CloudConfig = serde_json::from_str(&text).ok()?;
    // An earlier build kept the token in this file: move it to the keychain
    // and rewrite the file without it. If the keychain refuses, leave both
    // alone and try again next time.
    if let Some(token) = cfg.legacy_token.take() {
        let creds = Credentials { token, refresh_token: cfg.legacy_refresh_token.take() };
        match save_credentials(&creds).and_then(|()| save(app_data, &cfg)) {
            Ok(()) => log::info!("moved the Cloudflare token into the keychain"),
            Err(e) => log::warn!("couldn't move the Cloudflare token into the keychain: {e:#}"),
        }
    }
    Some(cfg)
}

fn save(app_data: &Path, cfg: &CloudConfig) -> Result<()> {
    std::fs::create_dir_all(app_data)?;
    let path = config_path(app_data);
    std::fs::write(&path, serde_json::to_string_pretty(cfg)?)?;
    // No token here, but the host key drives this church's rooms: keep it to
    // this user.
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600))?;
    }
    Ok(())
}

/// Replace this computer's room: every QR code and bookmark made so far stops
/// working (for when a link has been shared further than it should).
pub fn new_links(app_data: &Path) -> Result<()> {
    let mut cfg = load(app_data).ok_or_else(|| anyhow!("Online band screens aren't set up yet"))?;
    cfg.room = Some(fresh_room_id());
    save(app_data, &cfg)
}

pub fn forget(app_data: &Path) -> Result<()> {
    secrets().delete()?;
    let path = config_path(app_data);
    if path.exists() {
        std::fs::remove_file(path)?;
    }
    Ok(())
}

pub fn status(app_data: &Path) -> CloudStatus {
    let cfg = load(app_data);
    CloudStatus {
        configured: cfg.is_some(),
        needs_update: cfg.as_ref().is_some_and(|c| c.deployed_version != relay_version()),
        worker_url: cfg.as_ref().map(|c| c.worker_url.clone()),
        account_name: cfg.map(|c| c.account_name),
        oauth_available: OAUTH_CLIENT_ID.is_some(),
        relay_built: relay_built(),
    }
}

fn random_hex(bytes: usize) -> String {
    let mut buf = vec![0u8; bytes];
    rand::thread_rng().fill_bytes(&mut buf);
    buf.iter().map(|b| format!("{b:02x}")).collect()
}

/// The device key for a room: HMAC-SHA256(host key, "room:<room>"), first 16
/// bytes as hex. Must match `roomKey` in `relay/src/worker.ts`.
pub fn room_key(host_key: &str, room: &str) -> String {
    let mut mac = Hmac::<Sha256>::new_from_slice(host_key.as_bytes()).expect("HMAC takes any key length");
    mac.update(format!("room:{room}").as_bytes());
    mac.finalize().into_bytes()[..16].iter().map(|b| format!("{b:02x}")).collect()
}

/// A room id: lowercase letters and digits only (the relay's room pattern).
fn fresh_room_id() -> String {
    ulid::Ulid::new().to_string().to_lowercase()
}

/// This computer's room — the same one every time, made on first use.
pub fn new_room(app_data: &Path, pin: &str) -> Result<CloudRoom> {
    let mut cfg = load(app_data).ok_or_else(|| anyhow!("Online band screens aren't set up yet"))?;
    let room = match cfg.room.clone() {
        Some(room) => room,
        None => {
            let room = fresh_room_id();
            cfg.room = Some(room.clone());
            save(app_data, &cfg)?;
            room
        }
    };
    // https → wss (a deployed relay); http → ws (`pnpm relay:dev` on this machine).
    let ws_base = cfg.worker_url.replacen("https://", "wss://", 1).replacen("http://", "ws://", 1);
    Ok(CloudRoom {
        key: room_key(&cfg.host_key, &room),
        host_socket: format!("{ws_base}/r/{room}/host?hk={}&pin={pin}", cfg.host_key),
        base_url: cfg.worker_url,
        room,
    })
}

// --- Cloudflare API ----------------------------------------------------------

fn client() -> Result<reqwest::Client> {
    Ok(reqwest::Client::builder().timeout(Duration::from_secs(60)).build()?)
}

/// Unwrap Cloudflare's `{ success, errors, result }` envelope.
async fn cf(resp: reqwest::Response) -> Result<Value> {
    let status = resp.status();
    let body: Value = resp.json().await.with_context(|| format!("Cloudflare replied {status} without JSON"))?;
    if body.get("success").and_then(Value::as_bool) == Some(true) {
        return Ok(body.get("result").cloned().unwrap_or(Value::Null));
    }
    let errors = body
        .get("errors")
        .and_then(Value::as_array)
        .map(|es| {
            es.iter()
                .map(|e| format!("{} ({})", e.get("message").and_then(Value::as_str).unwrap_or("?"), e.get("code").map(|c| c.to_string()).unwrap_or_default()))
                .collect::<Vec<_>>()
                .join("; ")
        })
        .unwrap_or_else(|| body.to_string());
    bail!("Cloudflare: {errors}")
}

async fn accounts(http: &reqwest::Client, token: &str) -> Result<Vec<AccountChoice>> {
    let api = api();
    let result = cf(http.get(format!("{api}/accounts?per_page=50")).bearer_auth(token).send().await?).await?;
    Ok(result
        .as_array()
        .cloned()
        .unwrap_or_default()
        .into_iter()
        .filter_map(|a| Some(AccountChoice { id: a.get("id")?.as_str()?.into(), name: a.get("name")?.as_str()?.into() }))
        .collect())
}

/// Upload the Worker with its Durable Object and host key.
async fn upload_worker(http: &reqwest::Client, token: &str, account: &str, host_key: &str, with_migration: bool) -> Result<()> {
    let api = api();
    let mut metadata = json!({
        "main_module": "worker.js",
        "compatibility_date": COMPATIBILITY_DATE,
        "bindings": [
            { "type": "durable_object_namespace", "name": "ROOMS", "class_name": "Room" },
            { "type": "secret_text", "name": "HOST_KEY", "text": host_key },
        ],
    });
    if with_migration {
        metadata["migrations"] = json!({ "new_tag": "v1", "new_sqlite_classes": ["Room"] });
    }
    let form = reqwest::multipart::Form::new()
        .part("metadata", reqwest::multipart::Part::text(metadata.to_string()).mime_str("application/json")?)
        .part(
            "worker.js",
            reqwest::multipart::Part::text(WORKER_JS).file_name("worker.js").mime_str("application/javascript+module")?,
        );
    cf(http.put(format!("{api}/accounts/{account}/workers/scripts/{SCRIPT_NAME}")).bearer_auth(token).multipart(form).send().await?)
        .await
        .map(|_| ())
}

/// The account's `<name>.workers.dev` subdomain, creating one if it has none.
async fn account_subdomain(http: &reqwest::Client, token: &str, account: &str) -> Result<String> {
    let api = api();
    let url = format!("{api}/accounts/{account}/workers/subdomain");
    if let Ok(result) = cf(http.get(&url).bearer_auth(token).send().await?).await {
        if let Some(sub) = result.get("subdomain").and_then(Value::as_str) {
            return Ok(sub.to_string());
        }
    }
    let wanted = format!("church-slides-{}", &random_hex(3));
    let result = cf(http.put(&url).bearer_auth(token).json(&json!({ "subdomain": wanted })).send().await?).await?;
    Ok(result.get("subdomain").and_then(Value::as_str).unwrap_or(&wanted).to_string())
}

/// Deploy (or update) the relay into `account` and save where it lives.
pub async fn deploy(app_data: &Path, token: &str, refresh_token: Option<String>, account: AccountChoice) -> Result<CloudStatus> {
    let api = api();
    if !relay_built() {
        bail!("This build doesn't include the online relay — run `pnpm build:relay` and rebuild");
    }
    let http = client()?;
    // Keep the host key and room on redeploy, so QR codes stay valid.
    let previous = load(app_data).filter(|c| c.account_id == account.id);
    let host_key = previous.as_ref().map(|c| c.host_key.clone()).unwrap_or_else(|| random_hex(32));
    let room = previous.and_then(|c| c.room);

    // First deploy declares the Durable Object class; later ones mustn't.
    if let Err(first) = upload_worker(&http, token, &account.id, &host_key, true).await {
        let msg = first.to_string().to_lowercase();
        if msg.contains("migration") || msg.contains("tag") {
            upload_worker(&http, token, &account.id, &host_key, false).await?;
        } else {
            return Err(first);
        }
    }
    let sub = account_subdomain(&http, token, &account.id).await?;
    cf(http
        .post(format!("{api}/accounts/{}/workers/scripts/{SCRIPT_NAME}/subdomain", account.id))
        .bearer_auth(token)
        .json(&json!({ "enabled": true, "previews_enabled": false }))
        .send()
        .await?)
    .await?;

    // The token goes to the keychain first: if that fails, nothing claims
    // to be set up with a token we can't find again.
    save_credentials(&Credentials { token: token.to_string(), refresh_token })?;
    let cfg = CloudConfig {
        account_id: account.id,
        account_name: account.name,
        worker_url: format!("https://{SCRIPT_NAME}.{sub}.workers.dev"),
        host_key,
        deployed_version: relay_version(),
        room,
        legacy_token: None,
        legacy_refresh_token: None,
    };
    save(app_data, &cfg)?;
    Ok(status(app_data))
}

/// Set up with a token: pick the account (asking if there are several), deploy.
pub async fn setup_with_token(app_data: &Path, token: &str, refresh_token: Option<String>, account_id: Option<String>) -> Result<SetupOutcome> {
    let http = client()?;
    let all = accounts(&http, token).await.context("Couldn't list your Cloudflare accounts with that token")?;
    let chosen = match account_id {
        Some(id) => all.into_iter().find(|a| a.id == id).ok_or_else(|| anyhow!("That account isn't available to this token"))?,
        None if all.len() == 1 => all.into_iter().next().unwrap(),
        None if all.is_empty() => bail!("This token can't see any Cloudflare account — give it Account access"),
        None => return Ok(SetupOutcome::ChooseAccount { accounts: all }),
    };
    Ok(SetupOutcome::Done { status: deploy(app_data, token, refresh_token, chosen).await? })
}

/// Redeploy with the saved token (after an app update changed the relay).
pub async fn redeploy(app_data: &Path) -> Result<CloudStatus> {
    let cfg = load(app_data).ok_or_else(|| anyhow!("Online band screens aren't set up yet"))?;
    let account = AccountChoice { id: cfg.account_id.clone(), name: cfg.account_name.clone() };
    let creds = load_credentials()?
        .ok_or_else(|| anyhow!("The Cloudflare token isn't in the keychain any more — choose Disconnect Cloudflare, then set up again"))?;
    let (token, refresh) = match (&creds.refresh_token, OAUTH_CLIENT_ID) {
        (Some(refresh), Some(client_id)) => match refresh_access_token(client_id, refresh).await {
            Ok((access, new_refresh)) => (access, new_refresh.or(creds.refresh_token.clone())),
            Err(_) => (creds.token.clone(), creds.refresh_token.clone()),
        },
        _ => (creds.token.clone(), creds.refresh_token.clone()),
    };
    deploy(app_data, &token, refresh, account).await
}

/// Is the deployed relay answering yet? (A brand-new workers.dev subdomain
/// can take a minute or two to start resolving.)
pub async fn health(app_data: &Path) -> Result<bool> {
    let cfg = load(app_data).ok_or_else(|| anyhow!("Online band screens aren't set up yet"))?;
    let resp = client()?.get(format!("{}/health", cfg.worker_url)).timeout(Duration::from_secs(8)).send().await;
    Ok(matches!(resp, Ok(r) if r.status().is_success()))
}

// --- OAuth (PKCE, loopback redirect) -------------------------------------------

fn b64url(bytes: &[u8]) -> String {
    base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(bytes)
}

/// Sign in with Cloudflare in the browser; returns (access, refresh) tokens.
pub async fn oauth_sign_in(open_browser: impl FnOnce(&str) -> Result<()>) -> Result<(String, Option<String>)> {
    let client_id = OAUTH_CLIENT_ID.ok_or_else(|| anyhow!("This build can't sign in with Cloudflare — use an API token"))?;
    let mut raw = [0u8; 32];
    rand::thread_rng().fill_bytes(&mut raw);
    let verifier = b64url(&raw);
    let challenge = b64url(&Sha256::digest(verifier.as_bytes()));
    let state = random_hex(16);
    let redirect = format!("http://localhost:{OAUTH_PORT}/oauth/callback");

    let listener = tokio::net::TcpListener::bind(("127.0.0.1", OAUTH_PORT))
        .await
        .with_context(|| format!("Couldn't listen on port {OAUTH_PORT} for the sign-in reply"))?;
    let mut url = url::Url::parse(OAUTH_AUTHORIZE)?;
    url.query_pairs_mut()
        .append_pair("response_type", "code")
        .append_pair("client_id", client_id)
        .append_pair("redirect_uri", &redirect)
        .append_pair("scope", OAUTH_SCOPES)
        .append_pair("state", &state)
        .append_pair("code_challenge", &challenge)
        .append_pair("code_challenge_method", "S256");
    open_browser(url.as_str())?;

    // Wait (up to 5 minutes) for the browser to come back with the code.
    let code = tokio::time::timeout(Duration::from_secs(300), async {
        loop {
            let (mut sock, _) = listener.accept().await?;
            use tokio::io::{AsyncReadExt, AsyncWriteExt};
            let mut buf = vec![0u8; 8192];
            let n = sock.read(&mut buf).await?;
            let request = String::from_utf8_lossy(&buf[..n]);
            let path = request.split_whitespace().nth(1).unwrap_or("/");
            let parsed = url::Url::parse(&format!("http://localhost{path}"))?;
            if parsed.path() != "/oauth/callback" {
                sock.write_all(b"HTTP/1.1 404 Not Found\r\ncontent-length: 0\r\n\r\n").await.ok();
                continue;
            }
            let get = |k: &str| parsed.query_pairs().find(|(q, _)| q == k).map(|(_, v)| v.into_owned());
            let ok = get("state").as_deref() == Some(state.as_str()) && get("code").is_some();
            let page = if ok {
                "Signed in. You can close this tab and go back to Church Slides."
            } else {
                "Sign-in didn't complete. Go back to Church Slides and try again."
            };
            let body = format!("<!doctype html><meta charset=utf-8><title>Church Slides</title><p style=\"font:16px system-ui;margin:3em\">{page}</p>");
            sock.write_all(format!("HTTP/1.1 200 OK\r\ncontent-type: text/html; charset=utf-8\r\ncontent-length: {}\r\n\r\n{body}", body.len()).as_bytes())
                .await
                .ok();
            if let Some(err) = get("error") {
                bail!("Cloudflare sign-in was refused: {err}");
            }
            if ok {
                return Ok::<String, anyhow::Error>(get("code").unwrap());
            }
        }
    })
    .await
    .map_err(|_| anyhow!("Sign-in timed out"))??;

    let resp: Value = client()?
        .post(OAUTH_TOKEN)
        .form(&[
            ("grant_type", "authorization_code"),
            ("code", code.as_str()),
            ("redirect_uri", redirect.as_str()),
            ("client_id", client_id),
            ("code_verifier", verifier.as_str()),
        ])
        .send()
        .await?
        .json()
        .await?;
    let access = resp.get("access_token").and_then(Value::as_str).ok_or_else(|| anyhow!("Cloudflare didn't return a token: {resp}"))?;
    Ok((access.to_string(), resp.get("refresh_token").and_then(Value::as_str).map(String::from)))
}

async fn refresh_access_token(client_id: &str, refresh: &str) -> Result<(String, Option<String>)> {
    let resp: Value = client()?
        .post(OAUTH_TOKEN)
        .form(&[("grant_type", "refresh_token"), ("refresh_token", refresh), ("client_id", client_id)])
        .send()
        .await?
        .json()
        .await?;
    let access = resp.get("access_token").and_then(Value::as_str).ok_or_else(|| anyhow!("Refresh failed: {resp}"))?;
    Ok((access.to_string(), resp.get("refresh_token").and_then(Value::as_str).map(String::from)))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Tests share one in-memory "keychain"; take turns.
    static SECRETS_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

    #[test]
    fn room_key_matches_the_relay() {
        // Same as relay/test/smoke.mjs computes with Node's crypto.
        // Python: hmac.new(b"local-dev-host-key", b"room:abc12345", sha256).hexdigest()[:32]
        assert_eq!(room_key("local-dev-host-key", "abc12345"), "c53de8d9df2509189ba82579eaf9f831");
        assert_eq!(room_key("k", "r1"), room_key("k", "r1"));
        assert_ne!(room_key("k", "r1"), room_key("k", "r2"));
    }

    /// A stand-in for the Cloudflare API: records every call, and behaves
    /// like a fresh account (no workers.dev subdomain yet) that rejects a
    /// second "create the Durable Object class" migration.
    #[tokio::test]
    // The guard only keeps tests from sharing the in-memory keychain at once;
    // this test's own awaits don't contend for it.
    #[allow(clippy::await_holding_lock)]
    async fn deploys_into_an_account_and_redeploys_keeping_the_host_key() {
        let _guard = SECRETS_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        use std::sync::{Arc, Mutex};
        use axum::{body::Bytes, extract::State, http::{Method, Uri}, Json, Router};

        #[derive(Default)]
        struct Fake {
            calls: Vec<(String, String, String)>,
            migrated: bool,
            subdomain: Option<String>,
        }
        type S = Arc<Mutex<Fake>>;
        async fn handle(State(fake): State<S>, method: Method, uri: Uri, body: Bytes) -> Json<Value> {
            let body = String::from_utf8_lossy(&body).to_string();
            let mut f = fake.lock().unwrap();
            f.calls.push((method.to_string(), uri.path().to_string(), body.clone()));
            let ok = |result: Value| Json(json!({ "success": true, "errors": [], "result": result }));
            let err = |code: u32, msg: &str| Json(json!({ "success": false, "errors": [{ "code": code, "message": msg }] }));
            match (method.as_str(), uri.path()) {
                ("GET", "/accounts") => ok(json!([{ "id": "acc1", "name": "Grace Church" }])),
                ("PUT", "/accounts/acc1/workers/scripts/church-slides-relay") => {
                    if body.contains("new_sqlite_classes") {
                        if f.migrated {
                            return err(10079, "Migration tag precondition failed: already at v1");
                        }
                        f.migrated = true;
                    }
                    ok(json!({ "id": "church-slides-relay" }))
                }
                ("GET", "/accounts/acc1/workers/subdomain") => match &f.subdomain {
                    Some(sub) => ok(json!({ "subdomain": sub })),
                    None => err(10007, "This account doesn't have a workers.dev subdomain"),
                },
                ("PUT", "/accounts/acc1/workers/subdomain") => {
                    let wanted: Value = serde_json::from_str(&body).unwrap();
                    f.subdomain = wanted["subdomain"].as_str().map(String::from);
                    ok(json!({ "subdomain": f.subdomain }))
                }
                ("POST", "/accounts/acc1/workers/scripts/church-slides-relay/subdomain") => ok(json!({ "enabled": true })),
                _ => err(7003, "no route"),
            }
        }

        let fake: S = Arc::default();
        let app = Router::new().fallback(handle).with_state(fake.clone());
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
        *API_OVERRIDE.lock().unwrap() = Some(format!("http://{addr}"));

        let dir = tempfile::tempdir().unwrap();
        if !relay_built() {
            return; // a dev build without `pnpm build:relay`: nothing to upload
        }
        let first = setup_with_token(dir.path(), "tok", None, None).await.unwrap();
        let SetupOutcome::Done { status } = first else { panic!("expected a deploy") };
        let cfg = load(dir.path()).unwrap();
        assert!(status.configured && !status.needs_update);
        assert_eq!(status.account_name.as_deref(), Some("Grace Church"));
        assert!(cfg.worker_url.starts_with("https://church-slides-relay.church-slides-"));
        assert!(cfg.worker_url.ends_with(".workers.dev"));
        assert_eq!(cfg.host_key.len(), 64);

        {
            let f = fake.lock().unwrap();
            let upload = &f.calls.iter().find(|c| c.0 == "PUT" && c.1.ends_with("/church-slides-relay")).unwrap().2;
            // The Worker module, its Durable Object binding, and the host key as a secret.
            assert!(upload.contains("application/javascript+module"));
            assert!(upload.contains("\"class_name\":\"Room\""));
            assert!(upload.contains(&format!("\"text\":\"{}\"", cfg.host_key)));
            assert!(upload.contains("\"main_module\":\"worker.js\""));
            assert!(f.calls.iter().any(|c| c.0 == "POST" && c.1.ends_with("/subdomain")));
        }

        // The token is in the (test) keychain, not the settings file.
        assert_eq!(load_credentials().unwrap().unwrap().token, "tok");
        let on_disk = std::fs::read_to_string(config_path(dir.path())).unwrap();
        assert!(!on_disk.contains("tok\""), "token must not be written to the file: {on_disk}");

        // Redeploy (an app update): the migration is refused, the upload is
        // retried without it, and the host key — in every QR code — is kept.
        let again = redeploy(dir.path()).await.unwrap();
        assert!(again.configured);
        assert_eq!(load(dir.path()).unwrap().host_key, cfg.host_key);
        let f = fake.lock().unwrap();
        let uploads: Vec<_> = f.calls.iter().filter(|c| c.0 == "PUT" && c.1.ends_with("/church-slides-relay")).collect();
        assert_eq!(uploads.len(), 3, "first deploy, refused migration, retry without");
        assert!(!uploads[2].2.contains("new_sqlite_classes"));
        // The subdomain was created once, then reused.
        assert_eq!(f.calls.iter().filter(|c| c.0 == "PUT" && c.1.ends_with("/workers/subdomain")).count(), 1);
        drop(f);
        *API_OVERRIDE.lock().unwrap() = None;
    }

    /// Against the real system keychain (run with `--ignored`): proves the
    /// platform backend is compiled in — without it `keyring` quietly uses a
    /// store that forgets everything. Uses its own throwaway item.
    #[test]
    #[ignore]
    fn the_real_keychain_keeps_a_secret() {
        let service = "com.churchslides.app.cloud-relay.selftest";
        let write = keyring::Entry::new(service, "selftest").unwrap();
        write.set_password("hello-keychain").unwrap();
        // A separate entry object must see it: it really went to the keychain.
        let read = keyring::Entry::new(service, "selftest").unwrap();
        assert_eq!(read.get_password().unwrap(), "hello-keychain");
        read.delete_credential().unwrap();
        assert!(matches!(keyring::Entry::new(service, "selftest").unwrap().get_password(), Err(keyring::Error::NoEntry)));
    }

    #[test]
    fn an_old_settings_file_hands_its_token_to_the_keychain() {
        let _guard = SECRETS_LOCK.lock().unwrap();
        secrets().delete().unwrap();
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(
            config_path(dir.path()),
            r#"{"account_id":"a","account_name":"Grace","worker_url":"https://x.workers.dev",
               "host_key":"hk","deployed_version":"v","token":"old-token","refresh_token":"old-refresh"}"#,
        )
        .unwrap();
        let cfg = load(dir.path()).unwrap();
        assert_eq!(cfg.host_key, "hk");
        assert_eq!(
            load_credentials().unwrap(),
            Some(Credentials { token: "old-token".into(), refresh_token: Some("old-refresh".into()) })
        );
        let rewritten = std::fs::read_to_string(config_path(dir.path())).unwrap();
        assert!(!rewritten.contains("old-token") && !rewritten.contains("old-refresh"));
        // Disconnecting removes it from the keychain too.
        forget(dir.path()).unwrap();
        assert_eq!(load_credentials().unwrap(), None);
    }

    #[test]
    fn rooms_need_setup_and_carry_the_pin() {
        let _guard = SECRETS_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let dir = tempfile::tempdir().unwrap();
        assert!(new_room(dir.path(), "1234").is_err());
        save(
            dir.path(),
            &CloudConfig {
                account_id: "a".into(),
                account_name: "Grace".into(),
                worker_url: "https://church-slides-relay.grace.workers.dev".into(),
                host_key: "hk".into(),
                deployed_version: relay_version(),
                room: None,
                legacy_token: None,
                legacy_refresh_token: None,
            },
        )
        .unwrap();
        let room = new_room(dir.path(), "1234").unwrap();
        assert!(room.room.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit()));
        // The same room every time (closing and reopening keeps the links)…
        assert_eq!(new_room(dir.path(), "1234").unwrap().room, room.room);
        // …until new links are asked for.
        new_links(dir.path()).unwrap();
        let fresh = new_room(dir.path(), "1234").unwrap();
        assert_ne!(fresh.room, room.room);
        assert_eq!(new_room(dir.path(), "1234").unwrap().room, fresh.room);
        assert_eq!(room.key, room_key("hk", &room.room));
        assert!(room.host_socket.starts_with("wss://church-slides-relay.grace.workers.dev/r/"));
        assert!(room.host_socket.ends_with("&pin=1234"));
        assert!(!status(dir.path()).needs_update);
        forget(dir.path()).unwrap();
        assert!(!status(dir.path()).configured);
    }
}
