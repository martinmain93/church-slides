//! Typed app settings, persisted in the `settings` SQLite table as JSON.
//!
//! Adding a new setting: extend [`Settings`] with the new field and a
//! default value. Old persisted JSON loads cleanly because serde's
//! `#[serde(default)]` fills in missing fields. Removing or renaming a
//! field is a migration — bump `SETTINGS_SCHEMA_VERSION` and translate
//! the old shape in [`Settings::from_json`].

use std::path::Path;
use std::sync::Arc;

use anyhow::{Context, Result};
use rusqlite::params;

use crate::db::Db;

const SETTINGS_KEY: &str = "settings_v1";

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct Settings {
    /// Words-per-second prior used by the AI matcher's tempo estimator.
    /// Phase 5+ reads this. Default 2.0 wps.
    #[serde(default = "default_tempo_wps")]
    pub default_tempo_wps: f32,

    /// Theme. Currently locked to "dark" but we surface the field so the
    /// UI can render the (disabled) toggle and we don't need a migration
    /// when light mode lands.
    #[serde(default = "default_theme")]
    pub theme: String,

    /// When true, every service started from the presentation view auto-
    /// records audio, a transcript and slide-change events to disk — the
    /// golden-benchmark data. That's the whole room (prayer requests,
    /// visitors, children), so it's off in release builds until someone
    /// turns it on under Developer tools and agrees to the notice
    /// (`recording_acknowledged`); dev builds record by default.
    #[serde(default = "default_record_services")]
    pub record_services: bool,

    /// Someone agreed to the recording notice. Release builds before 0.0.9
    /// recorded by default; a stored `record_services: true` without this is
    /// that old default, not a choice, so `get` reads it as off.
    #[serde(default)]
    pub recording_acknowledged: bool,

    /// Recordings older than this many days are deleted when the app opens
    /// (`recordings::prune`); 0 keeps them.
    #[serde(default = "default_recording_keep_days")]
    pub recording_keep_days: u32,

    /// Everything about the Bible/scripture feature, nested so this struct
    /// grows one field rather than one per display option. `enabled` inside
    /// it is the feature switch, and it defaults off. See
    /// `crate::scriptures::ScriptureSettings`.
    #[serde(default)]
    pub scripture: crate::scriptures::ScriptureSettings,

    /// Send crash and error reports to the developers (`telemetry.rs`, and
    /// the engine's `error_reports.py`, which reads this from the database).
    /// Never includes audio or what was said. Only counts once
    /// `error_reports_asked`: the first-run prompt offers it ticked, and
    /// nothing is sent until that answer is saved.
    #[serde(default)]
    pub send_error_reports: bool,

    /// Someone answered the first-run question about error reports (or
    /// changed the switch in Settings). Before 0.0.9 reports were on by
    /// default, so a stored `send_error_reports: true` without this wasn't
    /// a choice, and reads as off until they're asked.
    #[serde(default)]
    pub error_reports_asked: bool,

    /// Optional: an email or church name to put on error reports, so the
    /// developers can follow up. Empty leaves only the random install id.
    #[serde(default)]
    pub report_contact: String,

    /// Share anonymous usage statistics (`src/lib/usage.ts`), which includes
    /// sending this install's random id, version, OS version and chip with
    /// update checks so the developers can count installs in use (`stats/`
    /// Worker). `None` until the user has been asked, which the app does
    /// once; off until they say yes.
    #[serde(default)]
    pub share_usage: Option<bool>,

    /// Act on spoken requests ("go to the chorus", "bring up John 3:16")
    /// during a service. `None` until someone chooses, which lets the engine
    /// decide: on when a hosted model's key is on file.
    #[serde(default)]
    pub spoken_requests: Option<bool>,

    /// Listen online: send the service's audio to the organization's speech
    /// server, on a computer without Apple silicon whose plan has it
    /// (`hosted_ai.rs`). `None` until the operator is asked, which the app
    /// does once; off until they say yes.
    #[serde(default)]
    pub online_speech: Option<bool>,
}

fn default_tempo_wps() -> f32 {
    2.0
}
fn default_theme() -> String {
    "dark".to_string()
}
fn default_record_services() -> bool {
    cfg!(debug_assertions)
}
/// Release builds keep 30 days. Dev builds keep everything: a tuning
/// machine's recordings are its evaluation corpus.
fn default_recording_keep_days() -> u32 {
    if cfg!(debug_assertions) {
        0
    } else {
        30
    }
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            default_tempo_wps: default_tempo_wps(),
            theme: default_theme(),
            record_services: default_record_services(),
            recording_acknowledged: false,
            recording_keep_days: default_recording_keep_days(),
            scripture: Default::default(),
            send_error_reports: false,
            error_reports_asked: false,
            report_contact: String::new(),
            share_usage: None,
            spoken_requests: None,
            online_speech: None,
        }
    }
}

pub fn get(db: &Arc<Db>) -> Result<Settings> {
    let conn = db.home();
    let mut stmt = conn.prepare("SELECT value FROM settings WHERE key = ?1")?;
    let mut rows = stmt.query(params![SETTINGS_KEY])?;
    if let Some(row) = rows.next()? {
        let raw: String = row.get(0)?;
        match serde_json::from_str::<Settings>(&raw) {
            Ok(s) => Ok(without_unasked_reports(without_unagreed_recording(s))),
            Err(err) => {
                log::warn!("settings JSON parse failed; falling back to defaults: {err}");
                // Except error reports: an opted-out user whose settings
                // can't be parsed must not start sending them. (Usage
                // statistics default to not asked, so they stay off.)
                let reports = report_choice_from_raw(Some(&raw));
                Ok(Settings {
                    send_error_reports: reports,
                    error_reports_asked: reports,
                    ..Settings::default()
                })
            }
        }
    } else {
        Ok(Settings::default())
    }
}

/// A release build records only for someone who agreed to the notice: an
/// older release saved `record_services: true` as its default.
fn without_unagreed_recording(mut s: Settings) -> Settings {
    if !cfg!(debug_assertions) && !s.recording_acknowledged {
        s.record_services = false;
    }
    s
}

/// Error reports go only to someone who was asked and said yes.
fn without_unasked_reports(mut s: Settings) -> Settings {
    s.send_error_reports &= s.error_reports_asked;
    s
}

/// Whether error reports may be sent, from the stored settings JSON (`None`:
/// no row yet). Only a yes to the question counts: a fresh install, a row
/// from before the question, and a row that can't be understood are all
/// off. Read from the raw keys, so a damaged *other* field doesn't lose them.
/// Mirrored in `error_reports.py`.
pub fn report_choice_from_raw(raw: Option<&str>) -> bool {
    let Some(raw) = raw else { return false };
    match serde_json::from_str::<serde_json::Value>(raw) {
        Ok(serde_json::Value::Object(map)) => {
            let yes = |key: &str| map.get(key).and_then(serde_json::Value::as_bool) == Some(true);
            yes("send_error_reports") && yes("error_reports_asked")
        }
        _ => false,
    }
}

/// The error-report choice, read straight from the database file before the
/// app has opened (or migrated) it — so the reporter knows it before anything
/// can go wrong. No file, or no settings table yet, is a fresh install that
/// hasn't been asked (off); so is any failure to read it.
pub fn report_choice(db_path: &Path) -> bool {
    use rusqlite::{Connection, OpenFlags};
    if !db_path.exists() {
        return false;
    }
    let read = || -> rusqlite::Result<Option<String>> {
        let conn = Connection::open_with_flags(
            db_path,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )?;
        conn.busy_timeout(std::time::Duration::from_secs(1))?;
        let has_table: bool = conn.query_row(
            "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'settings')",
            [],
            |r| r.get(0),
        )?;
        if !has_table {
            return Ok(None);
        }
        let mut stmt = conn.prepare("SELECT value FROM settings WHERE key = ?1")?;
        let mut rows = stmt.query(params![SETTINGS_KEY])?;
        match rows.next()? {
            Some(row) => Ok(Some(row.get(0)?)),
            None => Ok(None),
        }
    };
    match read() {
        Ok(raw) => report_choice_from_raw(raw.as_deref()),
        Err(_) => false,
    }
}

pub fn set(db: &Arc<Db>, settings: &Settings) -> Result<()> {
    let json = serde_json::to_string(settings).context("serializing settings")?;
    let now = unix_ms();
    let conn = db.home();
    conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?1, ?2, ?3) \
         ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        params![SETTINGS_KEY, json, now],
    )?;
    Ok(())
}

fn unix_ms() -> i64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;
    use tempfile::TempDir;

    use super::*;

    fn fresh() -> (TempDir, Arc<Db>) {
        let dir = tempfile::tempdir().unwrap();
        let db = Arc::new(Db::open(&dir.path().join("t.db")).unwrap());
        (dir, db)
    }

    #[test]
    fn defaults_when_unset() {
        let (_d, db) = fresh();
        let s = get(&db).unwrap();
        assert_eq!(s.default_tempo_wps, 2.0);
        assert_eq!(s.theme, "dark");
        // Dev and test builds record by default; release builds don't.
        assert_eq!(s.record_services, cfg!(debug_assertions));
        assert_eq!(s.recording_keep_days, if cfg!(debug_assertions) { 0 } else { 30 });
    }

    #[test]
    fn a_release_build_ignores_the_old_recording_default() {
        let old = Settings { record_services: true, ..Settings::default() };
        assert_eq!(without_unagreed_recording(old).record_services, cfg!(debug_assertions));
        let agreed =
            Settings { record_services: true, recording_acknowledged: true, ..Settings::default() };
        assert!(without_unagreed_recording(agreed).record_services);
    }

    #[test]
    fn error_reports_wait_for_an_answer() {
        let (_d, db) = fresh();
        assert!(!get(&db).unwrap().send_error_reports);
        // Saved by an older version, which defaulted reports to on.
        set(&db, &Settings { send_error_reports: true, ..Settings::default() }).unwrap();
        assert!(!get(&db).unwrap().send_error_reports);
        let yes = Settings { send_error_reports: true, error_reports_asked: true, ..Settings::default() };
        set(&db, &yes).unwrap();
        assert!(get(&db).unwrap().send_error_reports);
    }

    #[test]
    fn roundtrip() {
        let (_d, db) = fresh();
        let mut s = Settings::default();
        s.default_tempo_wps = 1.7;
        set(&db, &s).unwrap();

        let loaded = get(&db).unwrap();
        assert_eq!(loaded.default_tempo_wps, 1.7);
    }

    #[test]
    fn unknown_fields_in_persisted_json_load_cleanly() {
        let (_d, db) = fresh();
        // Store a JSON blob with fields that don't exist on Settings: one from
        // the future, and one since removed (`enable_propresenter_import`,
        // which older installs have saved). serde ignores unknown fields.
        {
            let conn = db.conn().lock().unwrap();
            conn.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?1, ?2, ?3)",
                params![
                    SETTINGS_KEY,
                    r#"{"default_tempo_wps":3.0,"enable_propresenter_import":false,"theme":"dark","unknown_future_field":42}"#,
                    0,
                ],
            )
            .unwrap();
        }
        let s = get(&db).unwrap();
        assert_eq!(s.default_tempo_wps, 3.0);
    }

    #[test]
    fn corrupt_json_returns_defaults() {
        let (_d, db) = fresh();
        {
            let conn = db.conn().lock().unwrap();
            conn.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?1, ?2, ?3)",
                params![SETTINGS_KEY, "not json", 0],
            )
            .unwrap();
        }
        let s = get(&db).unwrap();
        assert_eq!(s.default_tempo_wps, 2.0); // default
        // ...except what's sent, which fails closed.
        assert!(!s.send_error_reports);
        assert_eq!(s.share_usage, None);
    }

    #[test]
    fn report_choice_reads_the_raw_keys_and_fails_closed() {
        // Not asked yet: a fresh install, or one from before the question.
        assert!(!report_choice_from_raw(None));
        assert!(!report_choice_from_raw(Some(r#"{"theme":"dark"}"#)));
        assert!(!report_choice_from_raw(Some(r#"{"send_error_reports":true}"#)));
        let yes = r#"{"send_error_reports":true,"error_reports_asked":true}"#;
        assert!(report_choice_from_raw(Some(yes)));
        let no = r#"{"send_error_reports":false,"error_reports_asked":true}"#;
        assert!(!report_choice_from_raw(Some(no)));
        // Another field is damaged: these still count.
        let damaged = r#"{"send_error_reports":true,"error_reports_asked":true,"theme":7}"#;
        assert!(report_choice_from_raw(Some(damaged)));
        assert!(!report_choice_from_raw(Some(r#"{"send_error_reports":"no","error_reports_asked":true}"#)));
        assert!(!report_choice_from_raw(Some("not json")));
        assert!(!report_choice_from_raw(Some("[]")));
    }

    #[test]
    fn get_keeps_an_opt_out_when_another_field_is_damaged() {
        let (_d, db) = fresh();
        {
            let conn = db.conn().lock().unwrap();
            conn.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?1, ?2, ?3)",
                params![SETTINGS_KEY, r#"{"send_error_reports":false,"theme":7}"#, 0],
            )
            .unwrap();
        }
        assert!(!get(&db).unwrap().send_error_reports);
    }

    #[test]
    fn report_choice_from_the_database_file() {
        let dir = tempfile::tempdir().unwrap();
        // Fresh install: no database yet.
        assert!(!report_choice(&dir.path().join("missing.db")));
        // A folder name with characters a URI would choke on. Windows allows
        // no "?" in a file name, so there it can't happen and isn't tried.
        let name = if cfg!(windows) {
            "St Mary's #2 100%"
        } else {
            "St Mary's #2 100%?"
        };
        let sub = dir.path().join(name);
        std::fs::create_dir_all(&sub).unwrap();
        let path = sub.join("library.db");
        let db = Arc::new(Db::open(&path).unwrap());
        assert!(!report_choice(&path), "migrated, no row yet: not asked, off");
        let mut s = Settings { error_reports_asked: true, ..Settings::default() };
        s.send_error_reports = false;
        set(&db, &s).unwrap();
        assert!(!report_choice(&path));
        s.send_error_reports = true;
        set(&db, &s).unwrap();
        assert!(report_choice(&path));
        // Not a database at all: off.
        let junk = dir.path().join("junk.db");
        std::fs::write(&junk, b"this is not sqlite, padded out past the header length").unwrap();
        assert!(!report_choice(&junk));
    }
}
