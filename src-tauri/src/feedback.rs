//! "Send feedback" (Settings and the sidebar), and what this install says
//! about itself to the stats Worker (`stats/`).
//!
//! Feedback goes to the same Sentry project as error reports, as Sentry's own
//! feedback type, so it lands in the developers' feedback inbox (and email)
//! next to any error from the same install. The Rust SDK has no feedback API,
//! so this posts the envelope itself — which also lets the UI say whether it
//! arrived, and offer email instead when it didn't (a church hall with no
//! Wi-Fi).
//!
//! Someone pressing Send is asking for it to go, so feedback doesn't depend
//! on the "Send error reports" switch. The log is only attached when they
//! tick the box, and is scrubbed like an error report's.

use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::telemetry;

/// Characters of feedback text, at most: a few paragraphs.
const MAX_MESSAGE_CHARS: usize = 5000;
const LOG_LINES: usize = 300;

/// What the app sends with update checks and usage statistics: nothing that
/// says whose computer it is.
#[derive(Debug, Clone, Serialize)]
pub struct AppIdentity {
    pub install_id: String,
    pub version: String,
    /// "macos" or "windows".
    pub os: String,
    pub os_version: Option<String>,
    /// The build's chip: "aarch64" or "x86_64".
    pub arch: String,
}

pub fn identity() -> AppIdentity {
    AppIdentity {
        install_id: telemetry::install_id().to_string(),
        version: env!("CARGO_PKG_VERSION").to_string(),
        os: std::env::consts::OS.to_string(),
        os_version: os_version(),
        arch: std::env::consts::ARCH.to_string(),
    }
}

fn os_version() -> Option<String> {
    use sentry::protocol::Context;
    match sentry::integrations::contexts::utils::os_context()? {
        Context::Os(os) => os.version,
        _ => None,
    }
}

#[tauri::command]
pub fn app_identity() -> AppIdentity {
    identity()
}

/// Where the privacy policy lives, and who to email when feedback can't be
/// sent. The UI opens only these (`open_link`).
const SITE: &str = "https://church-slides.netlify.app/";
const FEEDBACK_EMAIL: &str = "martinmain93@gmail.com";

/// Open a page of the app's website, or an email to the developers, in the
/// default browser or mail app. Anything else is refused: the UI has no
/// reason to open arbitrary addresses.
#[tauri::command]
pub fn open_link(url: String) -> Result<(), String> {
    if !link_allowed(&url) {
        return Err("not a link the app opens".into());
    }
    // Passed as one argument, never through a shell, so nothing in it is
    // interpreted.
    let spawned = if cfg!(target_os = "macos") {
        std::process::Command::new("open").arg(&url).spawn()
    } else if cfg!(windows) {
        std::process::Command::new("rundll32").args(["url.dll,FileProtocolHandler", &url]).spawn()
    } else {
        std::process::Command::new("xdg-open").arg(&url).spawn()
    };
    spawned.map(|_| ()).map_err(|e| e.to_string())
}

/// Hymn pages, linked from Library → Add hymns. The trailing slash keeps
/// `openhymnal.org.evil.com` out.
const OPEN_HYMNAL: &str = "http://openhymnal.org/";

/// Where to get a Groq key, linked from Settings → AI (spoken requests and
/// online speech). The trailing slash keeps look-alike hosts out.
const GROQ_CONSOLE: &str = "https://console.groq.com/";

fn link_allowed(url: &str) -> bool {
    if url.starts_with(SITE) || url.starts_with(OPEN_HYMNAL) || url.starts_with(GROQ_CONSOLE) {
        return true;
    }
    url.strip_prefix("mailto:")
        .and_then(|rest| rest.strip_prefix(FEEDBACK_EMAIL))
        .is_some_and(|rest| rest.is_empty() || rest.starts_with('?'))
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FeedbackKind {
    Problem,
    Idea,
    Praise,
    Other,
}

impl FeedbackKind {
    fn label(&self) -> &'static str {
        match self {
            Self::Problem => "problem",
            Self::Idea => "idea",
            Self::Praise => "praise",
            Self::Other => "other",
        }
    }
}

#[tauri::command]
pub async fn send_feedback(
    kind: FeedbackKind,
    message: String,
    contact: String,
    include_log: bool,
) -> Result<(), String> {
    let message = message.trim();
    if message.is_empty() {
        return Err("Write something first.".into());
    }
    let log = if include_log {
        let tail = crate::diagnostics::recent_lines(LOG_LINES);
        (!tail.is_empty()).then(|| telemetry::scrub(&tail))
    } else {
        None
    };
    let envelope = build_envelope(&kind, message, contact.trim(), log.as_deref(), &identity());
    let url = telemetry::DSN
        .and_then(envelope_url)
        .ok_or("no feedback address in this build")?;
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(20))
        .build()
        .map_err(|e| e.to_string())?;
    let res = client
        .post(url)
        .header("content-type", "application/x-sentry-envelope")
        .body(envelope)
        .send()
        .await
        .map_err(|e| {
            log::warn!("feedback not sent: {e}");
            "Couldn't reach the feedback service. Are you online?".to_string()
        })?;
    if !res.status().is_success() {
        let status = res.status();
        log::warn!("feedback refused: {status}");
        return Err(format!("The feedback service said no ({status})."));
    }
    log::info!("feedback sent ({})", kind.label());
    Ok(())
}

/// Sentry's envelope endpoint for a DSN (`https://<key>@<host>/<project>`).
fn envelope_url(dsn: &str) -> Option<String> {
    let rest = dsn.strip_prefix("https://")?;
    let (key, rest) = rest.split_once('@')?;
    let (host, project) = rest.split_once('/')?;
    Some(format!("https://{host}/api/{project}/envelope/?sentry_key={key}&sentry_version=7"))
}

fn event_id() -> String {
    use rand::RngCore;
    let mut bytes = [0u8; 16];
    rand::thread_rng().fill_bytes(&mut bytes);
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn build_envelope(
    kind: &FeedbackKind,
    message: &str,
    contact: &str,
    log: Option<&str>,
    who: &AppIdentity,
) -> Vec<u8> {
    let id = event_id();
    let message: String = message.chars().take(MAX_MESSAGE_CHARS).collect();
    let mut feedback = json!({ "message": message, "source": "app" });
    if contact.contains('@') {
        feedback["contact_email"] = json!(contact);
    } else if !contact.is_empty() {
        feedback["name"] = json!(contact);
    }
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0);
    let event = json!({
        "event_id": id,
        "timestamp": now,
        "platform": "other",
        "level": "info",
        "release": telemetry::release(),
        "environment": telemetry::environment(),
        "user": { "id": who.install_id },
        "tags": {
            "component": "app",
            "feedback_kind": kind.label(),
            "arch": who.arch,
            "os": who.os,
            "os_version": who.os_version.clone().unwrap_or_default(),
        },
        "contexts": {
            "feedback": feedback,
            "os": { "name": who.os, "version": who.os_version },
        },
    });
    let event = serde_json::to_vec(&event).unwrap_or_default();

    let mut out = Vec::new();
    let mut line = |v: serde_json::Value| {
        out.extend(serde_json::to_vec(&v).unwrap_or_default());
        out.push(b'\n');
    };
    line(json!({ "event_id": id, "dsn": telemetry::DSN.unwrap_or_default() }));
    line(json!({ "type": "feedback", "length": event.len() }));
    out.extend(&event);
    out.push(b'\n');
    if let Some(log) = log {
        out.extend(
            serde_json::to_vec(&json!({
                "type": "attachment",
                "length": log.len(),
                "filename": "log-tail.txt",
                "content_type": "text/plain",
            }))
            .unwrap_or_default(),
        );
        out.push(b'\n');
        out.extend(log.as_bytes());
        out.push(b'\n');
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn who() -> AppIdentity {
        AppIdentity {
            install_id: "01JA2B3C4D5E6F7G8H9JKMNPQR".into(),
            version: "0.0.9".into(),
            os: "macos".into(),
            os_version: Some("15.1.0".into()),
            arch: "aarch64".into(),
        }
    }

    fn items(envelope: &[u8]) -> Vec<serde_json::Value> {
        String::from_utf8_lossy(envelope)
            .lines()
            .filter_map(|l| serde_json::from_str(l).ok())
            .collect()
    }

    /// Sends a real feedback message to the Sentry project, to check it's
    /// accepted: `cargo test --lib -- --ignored sends_real_feedback`.
    #[test]
    #[ignore]
    fn sends_real_feedback() {
        let rt = tokio::runtime::Runtime::new().unwrap();
        rt.block_on(send_feedback(
            FeedbackKind::Other,
            "Test message from the feedback setup (cargo test) - safe to ignore.".into(),
            String::new(),
            false,
        ))
        .unwrap();
    }

    #[test]
    fn opens_only_the_site_and_the_feedback_email() {
        assert!(link_allowed("https://church-slides.netlify.app/privacy.html"));
        assert!(link_allowed("mailto:martinmain93@gmail.com?body=hello%20there"));
        assert!(!link_allowed("mailto:martinmain93@gmail.com.evil.com"));
        assert!(!link_allowed("https://church-slides.netlify.app.evil.com/"));
        assert!(!link_allowed("file:///etc/passwd"));
        assert!(link_allowed("http://openhymnal.org/Lyrics/Amazing_Grace-New_Britain.html"));
        assert!(!link_allowed("http://openhymnal.org.evil.com/"));
        assert!(link_allowed("https://console.groq.com/keys"));
        assert!(!link_allowed("https://console.groq.com.evil.com/keys"));
    }

    #[test]
    fn envelope_url_from_the_dsn() {
        assert_eq!(
            envelope_url("https://abc@o1.ingest.us.sentry.io/42").as_deref(),
            Some("https://o1.ingest.us.sentry.io/api/42/envelope/?sentry_key=abc&sentry_version=7")
        );
        assert_eq!(envelope_url("not a dsn"), None);
    }

    #[test]
    fn feedback_envelope_carries_message_contact_and_install() {
        let env = build_envelope(&FeedbackKind::Idea, "More fonts", "pat@example.org", None, &who());
        let items = items(&env);
        assert_eq!(items.len(), 3, "header, item header, event");
        assert_eq!(items[1]["type"], "feedback");
        let event = &items[2];
        assert_eq!(event["contexts"]["feedback"]["message"], "More fonts");
        assert_eq!(event["contexts"]["feedback"]["contact_email"], "pat@example.org");
        assert_eq!(event["user"]["id"], "01JA2B3C4D5E6F7G8H9JKMNPQR");
        assert_eq!(event["tags"]["feedback_kind"], "idea");
        // The declared length is the event line's exact size, as sent. (Not a
        // re-serialisation of the parsed event: its timestamp is a float, and
        // reading one back can move its last digit, so the length came out a
        // byte off now and then — seen on the Windows runner.)
        let sent = String::from_utf8_lossy(&env);
        let event_line = sent.lines().nth(2).unwrap();
        assert_eq!(items[1]["length"], event_line.len());
    }

    #[test]
    fn a_name_is_not_sent_as_an_email() {
        let env = build_envelope(&FeedbackKind::Other, "hi", "St Mary's", None, &who());
        let fb = &items(&env)[2]["contexts"]["feedback"];
        assert_eq!(fb["name"], "St Mary's");
        assert!(fb.get("contact_email").is_none());
    }

    #[test]
    fn the_log_is_attached_only_when_given() {
        let env = build_envelope(&FeedbackKind::Problem, "froze", "", Some("line one\nline two"), &who());
        let text = String::from_utf8_lossy(&env).to_string();
        assert!(text.contains(r#""type":"attachment""#));
        assert!(text.ends_with("line one\nline two\n"));
        let plain = build_envelope(&FeedbackKind::Problem, "froze", "", None, &who());
        assert!(!String::from_utf8_lossy(&plain).contains("attachment"));
    }
}
