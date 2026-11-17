//! Signing in to a Church Slides server, as `docs/server-protocol/` describes.
//!
//! Entirely optional: nothing else in the app waits for it, and a computer
//! that never signs in behaves exactly as before. Signed in, the app holds:
//!
//! - `account.json` in the app data folder: which server, its endpoints, the
//!   client id this app uses there, and who is signed in (no secrets);
//! - the refresh token in the system keychain;
//! - the current access token in memory only.
//!
//! The flow is OAuth 2.1 in the system browser with PKCE and a loopback
//! redirect (RFC 8252), against whatever server discovery points at.

use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use anyhow::{anyhow, bail, Context, Result};
use base64::Engine as _;
use rand::RngCore;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};

/// The official server. `CHURCH_SLIDES_SERVER` overrides it (staging, a local
/// `wrangler dev`, or anyone else's server).
pub const DEFAULT_SERVER: &str = "https://api.churchslides.app";
/// This app's Client ID Metadata Document (served from the website).
pub const DESKTOP_CLIENT_ID: &str = "https://churchslides.app/oauth/desktop-client.json";
const SCOPES: &str = "account offline_access";
const CLIENT_NAME: &str = "Church Slides";

pub fn server_url() -> String {
    std::env::var("CHURCH_SLIDES_SERVER")
        .ok()
        .map(|s| s.trim().trim_end_matches('/').to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| DEFAULT_SERVER.to_string())
}

// --- What we keep ---------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct AccountConfig {
    pub server: String,
    pub api: String,
    pub issuer: String,
    pub resource: String,
    pub client_id: String,
    pub token_endpoint: String,
    #[serde(default)]
    pub revocation_endpoint: Option<String>,
    pub user_id: String,
    pub email: String,
    #[serde(default)]
    pub display_name: Option<String>,
    #[serde(default)]
    pub memberships: Vec<Membership>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Membership {
    pub org_id: String,
    pub org_name: String,
    pub role: String,
}

#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct AccountStatus {
    pub signed_in: bool,
    pub user_id: Option<String>,
    pub email: Option<String>,
    pub display_name: Option<String>,
    pub memberships: Vec<Membership>,
    pub server: String,
    pub server_is_default: bool,
}

fn config_path(app_data: &Path) -> PathBuf {
    app_data.join("account.json")
}

fn load(app_data: &Path) -> Option<AccountConfig> {
    serde_json::from_str(&std::fs::read_to_string(config_path(app_data)).ok()?).ok()
}

fn save(app_data: &Path, cfg: &AccountConfig) -> Result<()> {
    std::fs::create_dir_all(app_data)?;
    let tmp = config_path(app_data).with_extension("json.tmp");
    std::fs::write(&tmp, serde_json::to_vec_pretty(cfg)?)?;
    std::fs::rename(tmp, config_path(app_data))?;
    Ok(())
}

pub fn status(app_data: &Path) -> AccountStatus {
    let server = server_url();
    let server_is_default = server == DEFAULT_SERVER;
    match load(app_data) {
        Some(cfg) if matches!(secrets().get(), Ok(Some(_))) => AccountStatus {
            signed_in: true,
            user_id: Some(cfg.user_id.clone()),
            email: Some(cfg.email),
            display_name: cfg.display_name,
            memberships: cfg.memberships,
            server: cfg.server.clone(),
            server_is_default: cfg.server == DEFAULT_SERVER,
        },
        _ => AccountStatus { signed_in: false, user_id: None, email: None, display_name: None, memberships: vec![], server, server_is_default },
    }
}

/// Forget everything local about the account. Never fails halfway: a keychain
/// error still removes the file, so the app shows signed out.
fn clear(app_data: &Path) {
    *ACCESS.lock().unwrap_or_else(|e| e.into_inner()) = None;
    let _ = secrets().delete();
    let _ = std::fs::remove_file(config_path(app_data));
}

// --- Keychain -------------------------------------------------------------------

#[cfg_attr(test, allow(dead_code))]
const KEYCHAIN_SERVICE: &str = "com.churchslides.app.account";
#[cfg_attr(test, allow(dead_code))]
const KEYCHAIN_USER: &str = "refresh-token";

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
            Err(e) => Err(anyhow!("Couldn't read the sign-in from the keychain: {e}")),
        }
    }
    fn set(&self, secret: &str) -> Result<()> {
        keyring::Entry::new(KEYCHAIN_SERVICE, KEYCHAIN_USER)?
            .set_password(secret)
            .map_err(|e| anyhow!("Couldn't save the sign-in to the keychain: {e}"))
    }
    fn delete(&self) -> Result<()> {
        match keyring::Entry::new(KEYCHAIN_SERVICE, KEYCHAIN_USER)?.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(e) => Err(anyhow!("Couldn't remove the sign-in from the keychain: {e}")),
        }
    }
}

#[cfg(test)]
static TEST_SECRET: Mutex<Option<String>> = Mutex::new(None);
#[cfg(test)]
static TEST_READS: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);

#[cfg(test)]
struct MemoryStore;

#[cfg(test)]
impl SecretStore for MemoryStore {
    fn get(&self) -> Result<Option<String>> {
        TEST_READS.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        Ok(TEST_SECRET.lock().unwrap().clone())
    }
    fn set(&self, secret: &str) -> Result<()> {
        *TEST_SECRET.lock().unwrap() = Some(secret.to_string());
        Ok(())
    }
    fn delete(&self) -> Result<()> {
        *TEST_SECRET.lock().unwrap() = None;
        Ok(())
    }
}

fn backend() -> Box<dyn SecretStore> {
    #[cfg(test)]
    return Box::new(MemoryStore);
    #[cfg(not(test))]
    Box::new(Keychain)
}

/// The refresh token as last read from or written to the keychain: `None`
/// until the first read. macOS can ask the person before each keychain
/// access, so the keychain is read once per launch and touched again only
/// when the token changes or is removed.
static REFRESH: Mutex<Option<Option<String>>> = Mutex::new(None);

struct Cached;

impl SecretStore for Cached {
    fn get(&self) -> Result<Option<String>> {
        let mut cache = REFRESH.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(known) = cache.as_ref() {
            return Ok(known.clone());
        }
        let read = backend().get()?;
        *cache = Some(read.clone());
        Ok(read)
    }
    fn set(&self, secret: &str) -> Result<()> {
        let mut cache = REFRESH.lock().unwrap_or_else(|e| e.into_inner());
        backend().set(secret)?;
        *cache = Some(Some(secret.to_string()));
        Ok(())
    }
    fn delete(&self) -> Result<()> {
        let mut cache = REFRESH.lock().unwrap_or_else(|e| e.into_inner());
        // Forget it in memory even if the keychain refuses, so the app shows
        // signed out either way.
        *cache = Some(None);
        backend().delete()
    }
}

fn secrets() -> Cached {
    Cached
}

/// The access token, in memory only: (token, valid until).
static ACCESS: Mutex<Option<(String, Instant)>> = Mutex::new(None);

// --- Talking to the server --------------------------------------------------------

fn client() -> Result<reqwest::Client> {
    Ok(reqwest::Client::builder().timeout(Duration::from_secs(20)).build()?)
}

fn b64url(bytes: &[u8]) -> String {
    base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(bytes)
}

fn random_b64(n: usize) -> String {
    let mut raw = vec![0u8; n];
    rand::thread_rng().fill_bytes(&mut raw);
    b64url(&raw)
}

fn str_field(v: &Value, k: &str) -> Option<String> {
    v.get(k).and_then(Value::as_str).map(String::from)
}

struct ServerInfo {
    api: String,
    issuer: String,
    resource: String,
    authorization_endpoint: String,
    token_endpoint: String,
    revocation_endpoint: Option<String>,
    client_id: String,
}

/// Discovery, the authorization server's metadata, and this app's client id
/// there (the metadata document if the server reads those, else one dynamic
/// registration).
async fn discover(server: &str) -> Result<ServerInfo> {
    let http = client()?;
    let disc: Value = http
        .get(format!("{server}/.well-known/church-slides"))
        .send()
        .await
        .with_context(|| format!("Couldn't reach {server}"))?
        .error_for_status()
        .context("This server doesn't speak the Church Slides protocol")?
        .json()
        .await?;
    let auth = disc.get("auth").ok_or_else(|| anyhow!("This server has no sign-in"))?;
    let issuer = str_field(auth, "issuer").ok_or_else(|| anyhow!("Discovery has no issuer"))?;
    let resource = str_field(auth, "resource").ok_or_else(|| anyhow!("Discovery has no resource"))?;
    let api = str_field(&disc, "api").unwrap_or_else(|| "/v1".into());

    let meta: Value = http
        .get(format!("{}/.well-known/oauth-authorization-server", issuer.trim_end_matches('/')))
        .send()
        .await?
        .error_for_status()?
        .json()
        .await?;
    if str_field(&meta, "issuer").as_deref() != Some(issuer.as_str()) {
        bail!("The sign-in server's metadata names a different issuer");
    }
    let authorization_endpoint = str_field(&meta, "authorization_endpoint").ok_or_else(|| anyhow!("No authorization endpoint"))?;
    let token_endpoint = str_field(&meta, "token_endpoint").ok_or_else(|| anyhow!("No token endpoint"))?;
    let revocation_endpoint = str_field(&meta, "revocation_endpoint");

    let client_id = if meta.get("client_id_metadata_document_supported").and_then(Value::as_bool) == Some(true) {
        DESKTOP_CLIENT_ID.to_string()
    } else if let Some(reg) = str_field(&meta, "registration_endpoint") {
        let resp: Value = http
            .post(reg)
            .json(&serde_json::json!({
                "client_name": CLIENT_NAME,
                "redirect_uris": ["http://127.0.0.1/callback"],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
                "application_type": "native",
            }))
            .send()
            .await?
            .error_for_status()
            .context("The server refused to register this app")?
            .json()
            .await?;
        str_field(&resp, "client_id").ok_or_else(|| anyhow!("Registration returned no client id"))?
    } else {
        bail!("This server can't sign in this app (no client metadata documents or registration)");
    };

    Ok(ServerInfo { api, issuer, resource, authorization_endpoint, token_endpoint, revocation_endpoint, client_id })
}

const DONE_PAGE: &str = "Signed in. You can close this tab and go back to Church Slides.";
const FAILED_PAGE: &str = "Sign-in didn't complete. Go back to Church Slides and try again.";

/// Wait for the browser's one request to the loopback address; return the
/// authorization code once `state` (and `iss`, when sent) check out.
async fn await_code(listener: tokio::net::TcpListener, state: &str, issuer: &str) -> Result<String> {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    loop {
        let (mut sock, _) = listener.accept().await?;
        let mut buf = vec![0u8; 8192];
        let n = sock.read(&mut buf).await?;
        let request = String::from_utf8_lossy(&buf[..n]);
        let path = request.split_whitespace().nth(1).unwrap_or("/");
        let parsed = url::Url::parse(&format!("http://127.0.0.1{path}"))?;
        if parsed.path() != "/callback" {
            sock.write_all(b"HTTP/1.1 404 Not Found\r\ncontent-length: 0\r\n\r\n").await.ok();
            continue;
        }
        let get = |k: &str| parsed.query_pairs().find(|(q, _)| q == k).map(|(_, v)| v.into_owned());
        let state_ok = get("state").as_deref() == Some(state);
        let iss_ok = get("iss").map_or(true, |i| i == issuer);
        let code = get("code").filter(|_| state_ok && iss_ok);
        let page = if code.is_some() { DONE_PAGE } else { FAILED_PAGE };
        let body = format!(
            "<!doctype html><meta charset=utf-8><title>Church Slides</title><p style=\"font:16px system-ui;margin:3em\">{page}</p>"
        );
        sock.write_all(
            format!("HTTP/1.1 200 OK\r\ncontent-type: text/html; charset=utf-8\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}", body.len())
                .as_bytes(),
        )
        .await
        .ok();
        if let Some(err) = get("error") {
            if err == "access_denied" {
                bail!("Sign-in was cancelled");
            }
            bail!("Sign-in failed: {err}");
        }
        if !state_ok || !iss_ok {
            bail!("The sign-in reply didn't match this request; try again");
        }
        if let Some(code) = code {
            return Ok(code);
        }
    }
}

fn remember_access(resp: &Value) -> Result<String> {
    let access = str_field(resp, "access_token").ok_or_else(|| anyhow!("The server didn't return a token"))?;
    let ttl = resp.get("expires_in").and_then(Value::as_u64).unwrap_or(300);
    // Refresh a little early rather than send a token that expires in flight.
    let until = Instant::now() + Duration::from_secs(ttl.saturating_sub(30).max(1));
    *ACCESS.lock().unwrap_or_else(|e| e.into_inner()) = Some((access.clone(), until));
    Ok(access)
}

/// Sign in through the browser. `open_browser` opens the authorization URL
/// (the system browser in the app; a scripted client in tests).
/// Sign in through the system browser. With an `invite` (a code or link),
/// the server lets a new person in without the pilot passcode and adds them
/// to that organization.
pub async fn sign_in(app_data: &Path, invite: Option<&str>, open_browser: impl FnOnce(&str) -> Result<()>) -> Result<AccountStatus> {
    let server = server_url();
    let info = discover(&server).await?;

    let verifier = random_b64(32);
    let challenge = b64url(&Sha256::digest(verifier.as_bytes()));
    let state = random_b64(16);
    let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0)).await.context("Couldn't listen for the sign-in reply")?;
    let redirect = format!("http://127.0.0.1:{}/callback", listener.local_addr()?.port());

    let mut url = url::Url::parse(&info.authorization_endpoint)?;
    url.query_pairs_mut()
        .append_pair("response_type", "code")
        .append_pair("client_id", &info.client_id)
        .append_pair("redirect_uri", &redirect)
        .append_pair("scope", SCOPES)
        .append_pair("state", &state)
        .append_pair("code_challenge", &challenge)
        .append_pair("code_challenge_method", "S256")
        .append_pair("resource", &info.resource);
    if let Some(invite) = invite.map(str::trim).filter(|i| !i.is_empty()) {
        url.query_pairs_mut().append_pair("invite", invite);
    }
    open_browser(url.as_str())?;

    let code = tokio::time::timeout(Duration::from_secs(300), await_code(listener, &state, &info.issuer))
        .await
        .map_err(|_| anyhow!("Sign-in timed out"))??;

    let resp = client()?
        .post(&info.token_endpoint)
        .form(&[
            ("grant_type", "authorization_code"),
            ("code", code.as_str()),
            ("redirect_uri", redirect.as_str()),
            ("client_id", info.client_id.as_str()),
            ("code_verifier", verifier.as_str()),
            ("resource", info.resource.as_str()),
        ])
        .send()
        .await?;
    let status = resp.status();
    let tokens: Value = resp.json().await.with_context(|| format!("The server replied {status}"))?;
    if !status.is_success() {
        bail!("The server refused the sign-in: {}", str_field(&tokens, "error_description").or(str_field(&tokens, "error")).unwrap_or_default());
    }
    let refresh = str_field(&tokens, "refresh_token").ok_or_else(|| anyhow!("The server didn't return a refresh token"))?;
    let access = remember_access(&tokens)?;
    secrets().set(&refresh)?;

    let mut cfg = AccountConfig {
        server: server.clone(),
        api: info.api,
        issuer: info.issuer,
        resource: info.resource,
        client_id: info.client_id,
        token_endpoint: info.token_endpoint,
        revocation_endpoint: info.revocation_endpoint,
        user_id: String::new(),
        email: String::new(),
        display_name: None,
        memberships: vec![],
    };
    let me = fetch_me(&cfg, &access).await?;
    apply_me(&mut cfg, &me)?;
    save(app_data, &cfg)?;
    Ok(status_of(&cfg))
}

fn status_of(cfg: &AccountConfig) -> AccountStatus {
    AccountStatus {
        signed_in: true,
        user_id: Some(cfg.user_id.clone()),
        email: Some(cfg.email.clone()),
        display_name: cfg.display_name.clone(),
        memberships: cfg.memberships.clone(),
        server: cfg.server.clone(),
        server_is_default: cfg.server == DEFAULT_SERVER,
    }
}

async fn fetch_me(cfg: &AccountConfig, access: &str) -> Result<Value> {
    let resp = client()?.get(format!("{}{}/me", cfg.server, cfg.api)).bearer_auth(access).send().await?;
    if resp.status() == reqwest::StatusCode::UNAUTHORIZED {
        return Err(SignedOut.into());
    }
    Ok(resp.error_for_status()?.json().await?)
}

fn apply_me(cfg: &mut AccountConfig, me: &Value) -> Result<()> {
    let user = me.get("user").ok_or_else(|| anyhow!("The server didn't say who signed in"))?;
    cfg.user_id = str_field(user, "id").unwrap_or_default();
    cfg.email = str_field(user, "email").unwrap_or_default();
    cfg.display_name = str_field(user, "displayName");
    cfg.memberships = serde_json::from_value(me.get("memberships").cloned().unwrap_or(Value::Array(vec![]))).unwrap_or_default();
    Ok(())
}

/// The server no longer accepts this device's sign-in (signed out elsewhere,
/// or expired). Local state has already been cleared.
#[derive(Debug)]
pub struct SignedOut;
impl std::fmt::Display for SignedOut {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("This computer was signed out. Sign in again to continue.")
    }
}
impl std::error::Error for SignedOut {}

/// A valid access token, refreshing (and rotating the refresh token) when
/// the one in memory has expired.
async fn access_token(app_data: &Path, cfg: &AccountConfig) -> Result<String> {
    if let Some((tok, until)) = ACCESS.lock().unwrap_or_else(|e| e.into_inner()).clone() {
        if Instant::now() < until {
            return Ok(tok);
        }
    }
    let refresh = secrets().get()?.ok_or(SignedOut)?;
    let resp = client()?
        .post(&cfg.token_endpoint)
        .form(&[
            ("grant_type", "refresh_token"),
            ("refresh_token", refresh.as_str()),
            ("client_id", cfg.client_id.as_str()),
            ("resource", cfg.resource.as_str()),
        ])
        .send()
        .await?;
    let status = resp.status();
    let body: Value = resp.json().await.unwrap_or(Value::Null);
    if status.is_client_error() && str_field(&body, "error").as_deref() == Some("invalid_grant") {
        clear(app_data);
        return Err(SignedOut.into());
    }
    if !status.is_success() {
        bail!("Couldn't refresh the sign-in ({status})");
    }
    if let Some(new_refresh) = str_field(&body, "refresh_token") {
        secrets().set(&new_refresh)?;
    }
    remember_access(&body)
}

/// Check the sign-in with the server and pick up name and organization
/// changes. Offline, it leaves things as they are.
pub async fn refresh_status(app_data: &Path) -> Result<AccountStatus> {
    let Some(mut cfg) = load(app_data) else { return Ok(status(app_data)) };
    let result = async {
        let access = access_token(app_data, &cfg).await?;
        fetch_me(&cfg, &access).await
    }
    .await;
    match result {
        Ok(me) => {
            apply_me(&mut cfg, &me)?;
            save(app_data, &cfg)?;
            Ok(status_of(&cfg))
        }
        Err(e) if e.is::<SignedOut>() => {
            clear(app_data);
            Ok(status(app_data))
        }
        // Offline or the server is down: keep showing what we know.
        Err(_) => Ok(status(app_data)),
    }
}

/// Call the signed-in API at `path` (under the server's API root, e.g.
/// `/orgs/{id}/members`). An expired access token is refreshed and the call
/// retried once. A server error comes back as its readable message.
pub async fn api(app_data: &Path, method: &str, path: &str, body: Option<Value>) -> Result<Value> {
    if !path.starts_with('/') || path.contains("..") || path.contains("//") {
        bail!("Not an API path: {path}");
    }
    let method = match method {
        "GET" => reqwest::Method::GET,
        "POST" => reqwest::Method::POST,
        "PATCH" => reqwest::Method::PATCH,
        "DELETE" => reqwest::Method::DELETE,
        other => bail!("Unsupported method {other}"),
    };
    let cfg = load(app_data).ok_or(SignedOut)?;
    for attempt in 0..2 {
        let access = access_token(app_data, &cfg).await?;
        let mut req = client()?.request(method.clone(), format!("{}{}{}", cfg.server, cfg.api, path)).bearer_auth(access);
        if let Some(b) = &body {
            req = req.json(b);
        }
        let resp = req.send().await.context("Couldn't reach the server")?;
        let status = resp.status();
        if status == reqwest::StatusCode::UNAUTHORIZED && attempt == 0 {
            // The access token was refused before it expired (the server
            // restarted its keys, or it was revoked): get a new one.
            *ACCESS.lock().unwrap_or_else(|e| e.into_inner()) = None;
            continue;
        }
        let text = resp.text().await.unwrap_or_default();
        let value: Value = if text.is_empty() { Value::Null } else { serde_json::from_str(&text).unwrap_or(Value::Null) };
        if !status.is_success() {
            let message = value.pointer("/error/message").and_then(Value::as_str).map(String::from);
            bail!(message.unwrap_or_else(|| format!("The server replied {status}")));
        }
        return Ok(value);
    }
    Err(SignedOut.into())
}

/// Sign this computer out: revoke its refresh token (best effort) and forget it.
pub async fn sign_out(app_data: &Path) -> Result<AccountStatus> {
    if let (Some(cfg), Ok(Some(refresh))) = (load(app_data), secrets().get()) {
        if let Some(endpoint) = cfg.revocation_endpoint.as_deref() {
            let _ = client()?
                .post(endpoint)
                .timeout(Duration::from_secs(5))
                .form(&[("token", refresh.as_str()), ("token_type_hint", "refresh_token"), ("client_id", cfg.client_id.as_str())])
                .send()
                .await;
        }
    }
    clear(app_data);
    Ok(status(app_data))
}

/// Sign every device and app out, this one included.
pub async fn sign_out_everywhere(app_data: &Path) -> Result<AccountStatus> {
    let cfg = load(app_data).ok_or(SignedOut)?;
    let access = access_token(app_data, &cfg).await?;
    client()?
        .post(format!("{}{}/me/sign-out-everywhere", cfg.server, cfg.api))
        .bearer_auth(access)
        .send()
        .await?
        .error_for_status()
        .context("The server couldn't sign everything out; try again")?;
    clear(app_data);
    Ok(status(app_data))
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::{
        extract::{Query, State},
        response::{IntoResponse, Redirect},
        routing::{get, post},
        Form, Json, Router,
    };
    use serde_json::json;
    use std::collections::HashMap;
    use std::sync::{Arc, Mutex as StdMutex};

    /// One test at a time: they share the in-memory keychain, the access
    /// token and CHURCH_SLIDES_SERVER.
    static LOCK: StdMutex<()> = StdMutex::new(());

    #[derive(Default)]
    struct Fake {
        base: String,
        cimd: bool,
        registered: Vec<Value>,
        /// code -> (redirect_uri, challenge)
        codes: HashMap<String, (String, String)>,
        refresh: Option<String>,
        revoked: Vec<String>,
        signed_out_everywhere: bool,
        invite: Option<String>,
    }
    type S = Arc<StdMutex<Fake>>;

    async fn fake_server(cimd: bool) -> (String, S) {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let base = format!("http://127.0.0.1:{}", listener.local_addr().unwrap().port());
        let state: S = Arc::new(StdMutex::new(Fake { base: base.clone(), cimd, ..Default::default() }));
        let app = Router::new()
            .route(
                "/.well-known/church-slides",
                get(|State(s): State<S>| async move {
                    let b = s.lock().unwrap().base.clone();
                    Json(json!({ "protocol": "0", "api": "/v1", "capabilities": ["account"],
                                 "auth": { "issuer": b, "resource": format!("{b}/v1"), "scopes": ["account", "offline_access"] } }))
                }),
            )
            .route(
                "/.well-known/oauth-authorization-server",
                get(|State(s): State<S>| async move {
                    let f = s.lock().unwrap();
                    let b = f.base.clone();
                    Json(json!({ "issuer": b, "authorization_endpoint": format!("{b}/authorize"),
                                 "token_endpoint": format!("{b}/oauth/token"), "revocation_endpoint": format!("{b}/oauth/token"),
                                 "registration_endpoint": format!("{b}/oauth/register"),
                                 "client_id_metadata_document_supported": f.cimd }))
                }),
            )
            .route(
                "/oauth/register",
                post(|State(s): State<S>, Json(body): Json<Value>| async move {
                    s.lock().unwrap().registered.push(body);
                    Json(json!({ "client_id": "dcr-client-1" }))
                }),
            )
            .route(
                "/authorize",
                get(|State(s): State<S>, Query(q): Query<HashMap<String, String>>| async move {
                    let mut f = s.lock().unwrap();
                    let expected_client = if f.cimd { DESKTOP_CLIENT_ID } else { "dcr-client-1" };
                    assert_eq!(q["client_id"], expected_client);
                    assert_eq!(q["code_challenge_method"], "S256");
                    assert_eq!(q["resource"], format!("{}/v1", f.base));
                    f.codes.insert("code-1".into(), (q["redirect_uri"].clone(), q["code_challenge"].clone()));
                    f.invite = q.get("invite").cloned();
                    let to = format!("{}?code=code-1&state={}&iss={}", q["redirect_uri"], q["state"], f.base);
                    Redirect::to(&to)
                }),
            )
            .route(
                "/oauth/token",
                post(|State(s): State<S>, Form(form): Form<HashMap<String, String>>| async move {
                    let mut f = s.lock().unwrap();
                    if form.contains_key("token") {
                        f.revoked.push(form["token"].clone());
                        return Json(json!({})).into_response();
                    }
                    match form["grant_type"].as_str() {
                        "authorization_code" => {
                            let (redirect, challenge) = f.codes.remove(&form["code"]).expect("known code");
                            assert_eq!(form["redirect_uri"], redirect);
                            assert_eq!(b64url(&Sha256::digest(form["code_verifier"].as_bytes())), challenge);
                        }
                        "refresh_token" => {
                            if f.signed_out_everywhere || f.refresh.as_deref() != Some(form["refresh_token"].as_str()) {
                                return (axum::http::StatusCode::BAD_REQUEST, Json(json!({ "error": "invalid_grant" }))).into_response();
                            }
                        }
                        other => panic!("unexpected grant {other}"),
                    }
                    let refresh = format!("refresh-{}", random_b64(6));
                    f.refresh = Some(refresh.clone());
                    Json(json!({ "access_token": "access-1", "refresh_token": refresh, "expires_in": 900, "token_type": "bearer" }))
                        .into_response()
                }),
            )
            .route(
                "/v1/me",
                get(|State(s): State<S>, headers: axum::http::HeaderMap| async move {
                    if s.lock().unwrap().signed_out_everywhere
                        || headers.get("authorization").and_then(|v| v.to_str().ok()) != Some("Bearer access-1")
                    {
                        return axum::http::StatusCode::UNAUTHORIZED.into_response();
                    }
                    Json(json!({ "user": { "id": "u1", "email": "ada@example.org", "displayName": "Ada" },
                                 "memberships": [{ "orgId": "o1", "orgName": "Grace Chapel", "role": "operator" }] }))
                    .into_response()
                }),
            )
            .route(
                "/v1/orgs",
                post(|headers: axum::http::HeaderMap, Json(body): Json<Value>| async move {
                    if headers.get("authorization").and_then(|v| v.to_str().ok()) != Some("Bearer access-1") {
                        return axum::http::StatusCode::UNAUTHORIZED.into_response();
                    }
                    (axum::http::StatusCode::CREATED, Json(json!({ "org": { "id": "o2", "name": body["name"] }, "role": "owner" })))
                        .into_response()
                }),
            )
            .route(
                "/v1/orgs/o1",
                axum::routing::delete(|| async {
                    (
                        axum::http::StatusCode::FORBIDDEN,
                        Json(json!({ "error": { "code": "forbidden", "message": "Your role in this organization doesn't allow that." } })),
                    )
                }),
            )
            .route(
                "/v1/me/sign-out-everywhere",
                post(|State(s): State<S>| async move {
                    s.lock().unwrap().signed_out_everywhere = true;
                    Json(json!({ "signedOut": 1 }))
                }),
            )
            .with_state(state.clone());
        tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
        (base, state)
    }

    /// Plays the browser: follow the authorize redirect to the app's loopback
    /// address, as a real browser would after the person signs in.
    fn browser(url: &str) -> Result<()> {
        let url = url.to_string();
        tokio::spawn(async move {
            let c = reqwest::Client::builder().redirect(reqwest::redirect::Policy::limited(3)).build().unwrap();
            let _ = c.get(url).send().await;
        });
        Ok(())
    }

    fn reset() {
        *TEST_SECRET.lock().unwrap() = None;
        *REFRESH.lock().unwrap() = None;
        *ACCESS.lock().unwrap() = None;
    }

    #[tokio::test]
    #[allow(clippy::await_holding_lock)]
    async fn signs_in_with_the_metadata_document_and_survives_a_restart() {
        let _g = LOCK.lock().unwrap_or_else(|e| e.into_inner());
        reset();
        let (base, fake) = fake_server(true).await;
        std::env::set_var("CHURCH_SLIDES_SERVER", &base);
        let dir = tempfile::tempdir().unwrap();

        let st = sign_in(dir.path(), None, browser).await.unwrap();
        assert!(st.signed_in);
        assert_eq!(st.email.as_deref(), Some("ada@example.org"));
        assert_eq!(st.memberships[0].org_name, "Grace Chapel");
        assert!(!st.server_is_default);
        assert!(fake.lock().unwrap().registered.is_empty(), "no registration when the server reads metadata documents");

        // "Restart": the access token in memory is gone; the keychain and
        // account.json remain, and a refresh brings the session back.
        *ACCESS.lock().unwrap() = None;
        *REFRESH.lock().unwrap() = None;
        let reads = TEST_READS.load(std::sync::atomic::Ordering::SeqCst);
        assert!(status(dir.path()).signed_in);
        let st = refresh_status(dir.path()).await.unwrap();
        assert!(st.signed_in);
        *ACCESS.lock().unwrap() = None;
        refresh_status(dir.path()).await.unwrap();
        assert!(status(dir.path()).signed_in);
        // macOS may prompt on each keychain access: one read per launch.
        assert_eq!(TEST_READS.load(std::sync::atomic::Ordering::SeqCst) - reads, 1);

        let st = sign_out(dir.path()).await.unwrap();
        assert!(!st.signed_in);
        assert_eq!(fake.lock().unwrap().revoked.len(), 1);
        assert!(!config_path(dir.path()).exists());
    }

    #[tokio::test]
    #[allow(clippy::await_holding_lock)]
    async fn passes_an_invite_to_the_sign_in_page() {
        let _g = LOCK.lock().unwrap_or_else(|e| e.into_inner());
        reset();
        let (base, fake) = fake_server(true).await;
        std::env::set_var("CHURCH_SLIDES_SERVER", &base);
        let dir = tempfile::tempdir().unwrap();
        sign_in(dir.path(), Some("  https://api.example/join/csi_abc  "), browser).await.unwrap();
        assert_eq!(fake.lock().unwrap().invite.as_deref(), Some("https://api.example/join/csi_abc"));
    }

    #[tokio::test]
    #[allow(clippy::await_holding_lock)]
    async fn calls_the_api_and_reports_its_errors_plainly() {
        let _g = LOCK.lock().unwrap_or_else(|e| e.into_inner());
        reset();
        let (base, _fake) = fake_server(true).await;
        std::env::set_var("CHURCH_SLIDES_SERVER", &base);
        let dir = tempfile::tempdir().unwrap();
        sign_in(dir.path(), None, browser).await.unwrap();

        // A token the server no longer accepts, though it hasn't expired:
        // refreshed and retried once.
        *ACCESS.lock().unwrap() = Some(("stale".into(), Instant::now() + Duration::from_secs(600)));
        let created = api(dir.path(), "POST", "/orgs", Some(json!({ "name": "Grace" }))).await.unwrap();
        assert_eq!(created["org"]["name"], "Grace");

        let err = api(dir.path(), "DELETE", "/orgs/o1", None).await.unwrap_err();
        assert_eq!(err.to_string(), "Your role in this organization doesn't allow that.");

        assert!(api(dir.path(), "GET", "https://elsewhere.example/x", None).await.is_err());
        assert!(api(dir.path(), "GET", "/../oauth/token", None).await.is_err());
        assert!(api(dir.path(), "PUT", "/orgs", None).await.is_err());
    }

    #[tokio::test]
    #[allow(clippy::await_holding_lock)]
    async fn registers_dynamically_when_the_server_needs_it() {
        let _g = LOCK.lock().unwrap_or_else(|e| e.into_inner());
        reset();
        let (base, fake) = fake_server(false).await;
        std::env::set_var("CHURCH_SLIDES_SERVER", &base);
        let dir = tempfile::tempdir().unwrap();
        let st = sign_in(dir.path(), None, browser).await.unwrap();
        assert!(st.signed_in);
        let reg = fake.lock().unwrap().registered[0].clone();
        assert_eq!(reg["token_endpoint_auth_method"], "none");
        assert_eq!(reg["redirect_uris"][0], "http://127.0.0.1/callback");
    }

    #[tokio::test]
    #[allow(clippy::await_holding_lock)]
    async fn signing_out_everywhere_signs_this_computer_out_too() {
        let _g = LOCK.lock().unwrap_or_else(|e| e.into_inner());
        reset();
        let (base, _fake) = fake_server(true).await;
        std::env::set_var("CHURCH_SLIDES_SERVER", &base);
        let dir = tempfile::tempdir().unwrap();
        sign_in(dir.path(), None, browser).await.unwrap();
        let st = sign_out_everywhere(dir.path()).await.unwrap();
        assert!(!st.signed_in);
        assert!(TEST_SECRET.lock().unwrap().is_none());
    }

    #[tokio::test]
    #[allow(clippy::await_holding_lock)]
    async fn notices_when_signed_out_from_elsewhere() {
        let _g = LOCK.lock().unwrap_or_else(|e| e.into_inner());
        reset();
        let (base, fake) = fake_server(true).await;
        std::env::set_var("CHURCH_SLIDES_SERVER", &base);
        let dir = tempfile::tempdir().unwrap();
        sign_in(dir.path(), None, browser).await.unwrap();
        // Another device chose "Sign out everywhere".
        fake.lock().unwrap().signed_out_everywhere = true;
        *ACCESS.lock().unwrap() = None;
        let st = refresh_status(dir.path()).await.unwrap();
        assert!(!st.signed_in);
        assert!(!config_path(dir.path()).exists());
    }

    #[tokio::test]
    #[allow(clippy::await_holding_lock)]
    async fn stays_signed_in_while_offline() {
        let _g = LOCK.lock().unwrap_or_else(|e| e.into_inner());
        reset();
        let (base, _fake) = fake_server(true).await;
        std::env::set_var("CHURCH_SLIDES_SERVER", &base);
        let dir = tempfile::tempdir().unwrap();
        sign_in(dir.path(), None, browser).await.unwrap();
        // Point the saved account at a server that isn't there.
        let mut cfg = load(dir.path()).unwrap();
        cfg.token_endpoint = "http://127.0.0.1:9/oauth/token".into();
        save(dir.path(), &cfg).unwrap();
        *ACCESS.lock().unwrap() = None;
        let st = refresh_status(dir.path()).await.unwrap();
        assert!(st.signed_in, "a network failure isn't a sign-out");
    }

    #[test]
    fn rejects_a_reply_for_another_request() {
        let rt = tokio::runtime::Runtime::new().unwrap();
        rt.block_on(async {
            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            let port = listener.local_addr().unwrap().port();
            let wait = tokio::spawn(async move { await_code(listener, "right-state", "https://issuer").await });
            let _ = reqwest::get(format!("http://127.0.0.1:{port}/callback?code=c&state=wrong-state")).await;
            assert!(wait.await.unwrap().is_err());

            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            let port = listener.local_addr().unwrap().port();
            let wait = tokio::spawn(async move { await_code(listener, "s", "https://issuer").await });
            let _ = reqwest::get(format!("http://127.0.0.1:{port}/callback?code=c&state=s&iss=https://evil")).await;
            assert!(wait.await.unwrap().is_err(), "a reply from another issuer is refused (mix-up attack)");
        });
    }
}
