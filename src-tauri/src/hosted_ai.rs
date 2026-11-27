//! The organization's AI allowance, for this computer's AI engine
//! (docs/server-protocol, "AI gateway").
//!
//! While someone who runs services is signed in, this keeps a key for the
//! server's AI gateway in `hosted_ai.json` in the app's folder, for the
//! organization whose library is open (else the first one they run services
//! for, as the live set does). The AI engine reads it (`api_keys.py`) and uses
//! the gateway wherever it would need a Groq key the person hasn't entered.
//! A key of their own always wins.
//!
//! The file sits beside the other keys (`instruction_api_keys.json`), with the
//! same protection; the key only reaches the gateway and lasts 15 minutes, so
//! it's renewed every few minutes while the app runs.
//! Signing out removes it (`account::clear`).

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::db::Db;

pub const FILE: &str = "hosted_ai.json";

/// Roles that run services, and so may use the organization's allowance.
const RUNS_SERVICES: &[&str] = &["owner", "admin", "operator"];
/// A key is renewed when less than this is left of it. Keys last 15 minutes,
/// so this renews every 5 and leaves 10 to ride out a dropped connection.
const RENEW_BEFORE_MS: i64 = 10 * 60 * 1000;
const CHECK_EVERY: Duration = Duration::from_secs(60);
/// Retry soon after a failure: a key that runs out stops online speech.
const AFTER_FAILURE: Duration = Duration::from_secs(2 * 60);
/// The server has no allowance for this organization (or no AI gateway at
/// all): ask again much later rather than every two minutes.
const AFTER_NOT_OFFERED: Duration = Duration::from_secs(60 * 60);

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct HostedAi {
    pub org_id: String,
    pub org_name: String,
    pub base_url: String,
    pub key: String,
    /// Unix milliseconds.
    pub expires_at: i64,
    /// Hosted features the organization's plan has on now ("ai", "speech").
    #[serde(default)]
    pub features: Vec<String>,
    /// Live speech on the organization's speech server, when its plan has it
    /// and today's allowance isn't used up (the engine's
    /// `api_keys.hosted_speech`).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub speech: Option<HostedSpeech>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct HostedSpeech {
    pub url: String,
    pub token: String,
    /// Unix milliseconds.
    pub expires_at: i64,
}

pub fn path(app_data: &Path) -> PathBuf {
    app_data.join(FILE)
}

fn read(app_data: &Path) -> Option<HostedAi> {
    serde_json::from_str(&std::fs::read_to_string(path(app_data)).ok()?).ok()
}

fn write(app_data: &Path, h: &HostedAi) -> anyhow::Result<()> {
    let p = path(app_data);
    let tmp = p.with_extension("json.tmp");
    std::fs::write(&tmp, serde_json::to_vec_pretty(h)?)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&tmp, std::fs::Permissions::from_mode(0o600));
    }
    std::fs::rename(tmp, p)?;
    Ok(())
}

pub fn forget(app_data: &Path) {
    let _ = std::fs::remove_file(path(app_data));
}

/// Which organization's allowance this computer uses: the open library's,
/// if the person runs services there; with this computer's own library open,
/// the first one they run services for.
pub fn chosen_org(memberships: &[crate::account::Membership], open_org: Option<&str>) -> Option<(String, String)> {
    let runs = |m: &&crate::account::Membership| RUNS_SERVICES.contains(&m.role.as_str());
    match open_org {
        Some(id) => memberships.iter().filter(runs).find(|m| m.org_id == id),
        None => memberships.iter().find(runs),
    }
    .map(|m| (m.org_id.clone(), m.org_name.clone()))
}

fn now_ms() -> i64 {
    crate::db::unix_ms()
}

/// Keep `hosted_ai.json` right for as long as the app runs.
pub fn start(db: Arc<Db>, app_data: PathBuf) {
    tauri::async_runtime::spawn(async move {
        loop {
            let wait = match refresh(&db, &app_data).await {
                Ok(()) => CHECK_EVERY,
                Err(e) => {
                    log::info!("AI allowance key: {e:#}");
                    if not_offered(&e) {
                        AFTER_NOT_OFFERED
                    } else {
                        AFTER_FAILURE
                    }
                }
            };
            tokio::time::sleep(wait).await;
        }
    });
}

/// The server said there's no key to have here (404: no such route or
/// organization; 403: the role or plan doesn't include it).
fn not_offered(e: &anyhow::Error) -> bool {
    e.downcast_ref::<crate::account::ServerRefused>().is_some_and(|r| r.status == 403 || r.status == 404)
}

async fn refresh(db: &Db, app_data: &Path) -> anyhow::Result<()> {
    let memberships = crate::account::memberships(app_data);
    let open_org = crate::library_sync::org_of(db.home_path(), &db.open_path());
    let Some((org_id, org_name)) = chosen_org(&memberships, open_org.as_deref()) else {
        forget(app_data);
        return Ok(());
    };
    if read(app_data).is_some_and(|h| h.org_id == org_id && h.expires_at - now_ms() > RENEW_BEFORE_MS) {
        return Ok(());
    }
    let resp = match crate::account::api(app_data, "POST", &format!("/orgs/{org_id}/ai/key"), None).await {
        Ok(v) => v,
        Err(e) => {
            if e.is::<crate::account::SignedOut>() || not_offered(&e) {
                forget(app_data);
            }
            return Err(e);
        }
    };
    let field = |k: &str| resp.get(k).and_then(Value::as_str).map(String::from);
    let (Some(key), Some(base_url), Some(expires_at)) =
        (field("key"), field("baseUrl"), resp.get("expiresAt").and_then(Value::as_i64))
    else {
        anyhow::bail!("the server's reply had no key");
    };
    let features = resp
        .get("features")
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(Value::as_str).map(String::from).collect())
        .unwrap_or_default();
    let speech = speech_of(&resp);
    write(app_data, &HostedAi { org_id, org_name, base_url, key, expires_at, features, speech })?;
    log::info!("AI allowance key renewed");
    Ok(())
}

/// The answer's `speech`, when it has a usable one.
fn speech_of(resp: &Value) -> Option<HostedSpeech> {
    let s = resp.get("speech")?;
    Some(HostedSpeech {
        url: s.get("url")?.as_str()?.to_string(),
        token: s.get("token")?.as_str()?.to_string(),
        expires_at: s.get("expiresAt")?.as_i64()?,
    })
}

/// What the AI settings show: whose allowance this computer uses, if any.
#[derive(Debug, Clone, Serialize)]
pub struct HostedAiStatus {
    pub org_name: Option<String>,
    /// A Groq key of the person's own is saved; it's used instead.
    pub own_groq_key: bool,
    /// The organization's plan has online speech for this computer now.
    pub speech: bool,
}

/// Whether the AI engine has a Groq key of the person's own: in its key
/// file (`api_keys.py`), or the environment variable it reads first.
fn own_groq_key(app_data: &Path) -> bool {
    if std::env::var("AUTO_PRESENTER_GROQ_API_KEY").is_ok_and(|k| !k.is_empty()) {
        return true;
    }
    std::fs::read_to_string(app_data.join("instruction_api_keys.json"))
        .ok()
        .and_then(|t| serde_json::from_str::<Value>(&t).ok())
        .and_then(|v| v.get("groq").and_then(Value::as_str).map(|k| !k.is_empty()))
        .unwrap_or(false)
}

#[tauri::command]
pub fn hosted_ai_status(app: tauri::AppHandle) -> HostedAiStatus {
    use tauri::Manager;
    let Ok(dir) = app.path().app_data_dir() else {
        return HostedAiStatus { org_name: None, own_groq_key: false, speech: false };
    };
    let hosted = read(&dir).filter(|h| h.expires_at > now_ms());
    let speech = hosted.as_ref().is_some_and(|h| h.speech.as_ref().is_some_and(|s| s.expires_at > now_ms()));
    HostedAiStatus { org_name: hosted.map(|h| h.org_name), own_groq_key: own_groq_key(&dir), speech }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::account::Membership;

    fn m(id: &str, role: &str) -> Membership {
        Membership { org_id: id.into(), org_name: id.to_uppercase(), role: role.into() }
    }

    #[test]
    fn the_open_librarys_organization_decides_where_it_can() {
        let ms = [m("a", "member"), m("b", "operator"), m("c", "owner")];
        assert_eq!(chosen_org(&ms, None).map(|o| o.0), Some("b".into()), "this computer's library: the first that runs services");
        assert_eq!(chosen_org(&ms, Some("c")).map(|o| o.0), Some("c".into()));
        assert_eq!(chosen_org(&ms, Some("a")), None, "a member's organization isn't charged");
        assert_eq!(chosen_org(&[m("a", "set_leader")], None), None);
        assert_eq!(chosen_org(&[], None), None);
    }

    #[test]
    fn speech_comes_from_the_key_answer_when_its_all_there() {
        let full = serde_json::json!({"speech": {"url": "wss://s", "token": "t.s", "expiresAt": 5}});
        assert_eq!(speech_of(&full), Some(HostedSpeech { url: "wss://s".into(), token: "t.s".into(), expires_at: 5 }));
        assert_eq!(speech_of(&serde_json::json!({"speech": {"url": "wss://s"}})), None);
        assert_eq!(speech_of(&serde_json::json!({})), None);
        // a key file written before speech existed still reads
        let old: HostedAi =
            serde_json::from_str(r#"{"org_id":"o","org_name":"O","base_url":"b","key":"k","expires_at":1}"#).unwrap();
        assert_eq!((old.features.len(), old.speech), (0, None));
    }

    #[test]
    fn a_missing_route_or_refusal_waits_longer() {
        let refused = |status| anyhow::Error::from(crate::account::ServerRefused { status, message: "No such route.".into() });
        assert!(not_offered(&refused(404)));
        assert!(not_offered(&refused(403)));
        assert!(!not_offered(&refused(500)));
        assert!(!not_offered(&anyhow::anyhow!("Couldn't reach the server")));
    }

    #[test]
    fn written_for_the_engine_and_forgotten() {
        let dir = tempfile::tempdir().unwrap();
        let h = HostedAi {
            org_id: "o".into(),
            org_name: "O".into(),
            base_url: "https://x/ai/v1".into(),
            key: "csa_k".into(),
            expires_at: 1,
            features: vec!["ai".into(), "speech".into()],
            speech: Some(HostedSpeech { url: "wss://s".into(), token: "t.s".into(), expires_at: 2 }),
        };
        write(dir.path(), &h).unwrap();
        assert_eq!(read(dir.path()), Some(h));
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(std::fs::metadata(path(dir.path())).unwrap().permissions().mode() & 0o777, 0o600);
        }
        forget(dir.path());
        assert_eq!(read(dir.path()), None);
    }

    /// Against the server run locally, with a stand-in for Groq; writes the
    /// key file to `HOSTED_AI_E2E_DIR` for the AI engine's half of the check.
    #[tokio::test]
    #[ignore = "needs the server running locally"]
    async fn a_signed_in_operator_gets_the_organizations_allowance() {
        assert!(crate::account::server_url().starts_with("http://localhost"));
        let dir = std::path::PathBuf::from(std::env::var("HOSTED_AI_E2E_DIR").expect("HOSTED_AI_E2E_DIR"));
        std::fs::create_dir_all(&dir).unwrap();
        crate::account::sign_in(&dir, None, |url| {
            let url = url.to_string();
            tokio::spawn(async move {
                let _ = reqwest::Client::new().get(url).send().await;
            });
            Ok(())
        })
        .await
        .unwrap();
        crate::account::api(&dir, "POST", "/orgs", Some(serde_json::json!({ "name": "AI test" }))).await.unwrap();
        crate::account::refresh_status(&dir).await.unwrap();
        let db = Db::open(&dir.join("library.db")).unwrap();
        refresh(&db, &dir).await.unwrap();
        let h = read(&dir).expect("a key was written");
        assert!(h.key.starts_with("csa_"));
        assert_eq!(h.org_name, "AI test");
        // Renewed only when it's close to expiring.
        refresh(&db, &dir).await.unwrap();
        assert_eq!(read(&dir).unwrap().key, h.key);
    }
}
