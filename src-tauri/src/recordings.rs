//! Old service recordings, deleted at startup (`Settings::recording_keep_days`).
//!
//! A recording is the whole room — prayer requests, visitors, children — so it
//! shouldn't sit on a church's computer forever because someone once ticked a
//! box. The engine writes them (`sidecar/auto_presenter/recording/sink.py`)
//! into `recordings/` beside the library database; this runs before the engine
//! starts, so nothing is mid-write.
//!
//! A recording whose timeline someone approved in the Golden Recording Editor
//! is kept: that's deliberate work, not a leftover.

use std::path::Path;
use std::time::{Duration, SystemTime};

/// Delete recordings last written more than `keep_days` ago (0 keeps all).
/// Returns how many were removed.
pub fn prune(root: &Path, keep_days: u32, now: SystemTime) -> usize {
    if keep_days == 0 {
        return 0;
    }
    let Ok(entries) = std::fs::read_dir(root) else { return 0 };
    let max_age = Duration::from_secs(u64::from(keep_days) * 86_400);
    let mut removed = 0;
    for entry in entries.flatten() {
        let dir = entry.path();
        if !dir.is_dir() || approved(&dir) {
            continue;
        }
        let written = std::fs::metadata(dir.join("recording.json"))
            .or_else(|_| std::fs::metadata(dir.join("audio.wav")))
            .or_else(|_| entry.metadata())
            .and_then(|m| m.modified());
        let Ok(written) = written else { continue };
        if now.duration_since(written).is_ok_and(|age| age > max_age) {
            match std::fs::remove_dir_all(&dir) {
                Ok(()) => removed += 1,
                Err(e) => log::warn!("couldn't delete an old recording: {e}"),
            }
        }
    }
    if removed > 0 {
        log::info!("deleted {removed} recording(s) older than {keep_days} days");
    }
    removed
}

fn approved(dir: &Path) -> bool {
    std::fs::read_to_string(dir.join("golden.json"))
        .ok()
        .and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok())
        .and_then(|v| v.get("approved").and_then(serde_json::Value::as_bool))
        .unwrap_or(false)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn recording(root: &Path, id: &str, golden: Option<&str>) {
        let dir = root.join(id);
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("recording.json"), "{}").unwrap();
        if let Some(g) = golden {
            std::fs::write(dir.join("golden.json"), g).unwrap();
        }
    }

    #[test]
    fn deletes_old_recordings_but_keeps_approved_ones() {
        let root = tempfile::tempdir().unwrap();
        recording(root.path(), "old", None);
        recording(root.path(), "machine-golden", Some(r#"{"approved":false}"#));
        recording(root.path(), "approved", Some(r#"{"approved":true}"#));
        let later = SystemTime::now() + Duration::from_secs(31 * 86_400);
        assert_eq!(prune(root.path(), 30, later), 2);
        assert!(!root.path().join("old").exists());
        assert!(!root.path().join("machine-golden").exists());
        assert!(root.path().join("approved").exists());
    }

    #[test]
    fn keeps_recent_ones_and_everything_at_zero() {
        let root = tempfile::tempdir().unwrap();
        recording(root.path(), "recent", None);
        let soon = SystemTime::now() + Duration::from_secs(29 * 86_400);
        assert_eq!(prune(root.path(), 30, soon), 0);
        let much_later = SystemTime::now() + Duration::from_secs(400 * 86_400);
        assert_eq!(prune(root.path(), 0, much_later), 0);
        assert!(root.path().join("recent").exists());
    }

    #[test]
    fn a_missing_folder_is_nothing_to_do() {
        assert_eq!(prune(Path::new("/nonexistent/recordings"), 30, SystemTime::now()), 0);
    }
}
