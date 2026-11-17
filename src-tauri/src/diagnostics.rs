//! The app's log file, and the diagnostics report built from it.
//!
//! A release build used to keep no log at all: the shell logged to stderr,
//! which a Finder-launched app throws away, and the AI engine's own stderr
//! went the same way. So when the AI misbehaved on someone else's laptop there
//! was nothing to look at, and messages that said "the app's log has the
//! details" pointed at nothing.
//!
//! Now everything lands in one rotating file:
//!   - the shell's own `log` records;
//!   - the engine's stdout and stderr, line by line (`sidecar.rs`), under the
//!     `engine` target;
//!   - the UI's console errors and warnings (`log_from_ui`), under `ui`.
//!
//! `save_diagnostics` writes that, plus a description of this computer and
//! what the UI says about live AI, to one text file someone can email.

use std::fs::{self, File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

use log::{Level, LevelFilter, Log, Metadata, Record};

#[cfg(not(feature = "ui-driver"))]
const IDENTIFIER: &str = "com.churchslides.app";
/// The performance suite's build (`ui-driver`) keeps its own log, beside its
/// own data folder, rather than writing into the real app's.
#[cfg(feature = "ui-driver")]
const IDENTIFIER: &str = "com.churchslides.app.perf";
const FILE_NAME: &str = "church-slides.log";
/// Rotate past this size, keeping `KEEP` older files: 20 MB at most on disk.
const MAX_BYTES: u64 = 5 * 1024 * 1024;
const KEEP: usize = 3;
/// How much log a diagnostics report carries — the most recent part.
const REPORT_LOG_BYTES: u64 = 8 * 1024 * 1024;

/// Where the log lives: `~/Library/Logs/com.churchslides.app` on macOS (where
/// Console.app looks), the local app-data folder elsewhere.
pub fn log_dir() -> Option<PathBuf> {
    if cfg!(target_os = "macos") {
        dirs::home_dir().map(|h| h.join("Library/Logs").join(IDENTIFIER))
    } else {
        dirs::data_local_dir().map(|d| d.join(IDENTIFIER).join("logs"))
    }
}

fn rotated(dir: &Path, n: usize) -> PathBuf {
    if n == 0 {
        dir.join(FILE_NAME)
    } else {
        dir.join(format!("church-slides.{n}.log"))
    }
}

struct FileLogger {
    dir: Option<PathBuf>,
    file: Mutex<Option<File>>,
}

impl FileLogger {
    /// Readable by this user only: the log has what was said into the
    /// microphone, song lyrics and network addresses.
    fn open(dir: &Path) -> Option<File> {
        fs::create_dir_all(dir).ok()?;
        let mut options = OpenOptions::new();
        options.create(true).append(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
            options.mode(0o600);
            let file = options.open(rotated(dir, 0)).ok()?;
            // A file made before this (0644) is tightened too.
            let _ = file.set_permissions(fs::Permissions::from_mode(0o600));
            return Some(file);
        }
        #[cfg(not(unix))]
        options.open(rotated(dir, 0)).ok()
    }

    fn rotate(dir: &Path) {
        for n in (0..KEEP).rev() {
            let _ = fs::rename(rotated(dir, n), rotated(dir, n + 1));
        }
    }

    fn write_line(&self, line: &str) {
        let Some(dir) = &self.dir else { return };
        let Ok(mut slot) = self.file.lock() else { return };
        let too_big = slot
            .as_ref()
            .and_then(|f| f.metadata().ok())
            .is_some_and(|m| m.len() > MAX_BYTES);
        if too_big {
            *slot = None;
            Self::rotate(dir);
        }
        if slot.is_none() {
            *slot = Self::open(dir);
        }
        if let Some(f) = slot.as_mut() {
            let _ = f.write_all(line.as_bytes());
        }
    }
}

/// Our own crate and the forwarded sources at info; everyone else's chatter
/// (the webview, HTTP clients) only when it's a warning.
fn wanted(meta: &Metadata) -> bool {
    let target = meta.target();
    let ours = target.starts_with("auto_presenter")
        || target == "engine"
        || target == "ui";
    meta.level() <= if ours { Level::Info } else { Level::Warn }
}

impl Log for FileLogger {
    fn enabled(&self, meta: &Metadata) -> bool {
        wanted(meta)
    }

    fn log(&self, record: &Record) {
        if !wanted(record.metadata()) {
            return;
        }
        let line = format!(
            "{} {:<5} {}: {}\n",
            timestamp(SystemTime::now()),
            record.level(),
            record.target(),
            record.args()
        );
        crate::telemetry::on_log(record.level(), record.target(), &record.args().to_string());
        // Still on stderr, for `pnpm tauri dev` in a terminal.
        let _ = std::io::stderr().write_all(line.as_bytes());
        self.write_line(&line);
    }

    fn flush(&self) {
        if let Ok(mut slot) = self.file.lock() {
            if let Some(f) = slot.as_mut() {
                let _ = f.flush();
            }
        }
    }
}

/// Install the logger. Call once, first thing in `run()`.
pub fn init() {
    let dir = log_dir();
    let logger = FileLogger { dir: dir.clone(), file: Mutex::new(None) };
    if log::set_boxed_logger(Box::new(logger)).is_ok() {
        log::set_max_level(LevelFilter::Info);
    }
    log::info!(
        "Church Slides {} starting on {} {} ({})",
        env!("CARGO_PKG_VERSION"),
        std::env::consts::OS,
        os_version().unwrap_or_default(),
        std::env::consts::ARCH,
    );
    match dir {
        Some(d) => log::info!("log file: {}", rotated(&d, 0).display()),
        None => eprintln!("no log directory on this system; logging to stderr only"),
    }
}

/// The engine writes `<time> <LEVEL> <logger>: <message>`; a traceback's
/// continuation lines have no level, so they keep the one before them.
pub fn engine_line_level(line: &str, previous: Level) -> Level {
    let starts_with_time = line.as_bytes().first().is_some_and(u8::is_ascii_digit);
    if !starts_with_time {
        return if line.starts_with("Traceback") { Level::Error } else { previous };
    }
    if line.contains(" ERROR ") || line.contains(" CRITICAL ") {
        Level::Error
    } else if line.contains(" WARNING ") {
        Level::Warn
    } else {
        Level::Info
    }
}

// --- timestamps, without a date crate ---------------------------------------

/// `2026-10-07 11:36:53.123Z`.
fn timestamp(t: SystemTime) -> String {
    let d = t.duration_since(UNIX_EPOCH).unwrap_or_default();
    let secs = d.as_secs();
    let (y, m, day) = civil_from_days((secs / 86_400) as i64);
    let s = secs % 86_400;
    format!(
        "{y:04}-{m:02}-{day:02} {:02}:{:02}:{:02}.{:03}Z",
        s / 3600,
        (s % 3600) / 60,
        s % 60,
        d.subsec_millis()
    )
}

/// Days since 1970-01-01 to a proleptic Gregorian date (Howard Hinnant's
/// algorithm).
fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (yoe + era * 400 + i64::from(m <= 2), m, d)
}

// --- this computer ----------------------------------------------------------

fn command_output(cmd: &str, args: &[&str]) -> Option<String> {
    let out = std::process::Command::new(cmd).args(args).output().ok()?;
    out.status
        .success()
        .then(|| String::from_utf8_lossy(&out.stdout).trim().to_string())
        .filter(|s| !s.is_empty())
}

fn os_version() -> Option<String> {
    if cfg!(target_os = "macos") {
        command_output("sw_vers", &["-productVersion"])
    } else {
        None
    }
}

fn system_lines() -> Vec<String> {
    let mut lines = vec![
        format!("OS: {} {}", std::env::consts::OS, os_version().unwrap_or_default()),
        format!("Architecture: {}", std::env::consts::ARCH),
        format!(
            "Processor cores: {}",
            std::thread::available_parallelism().map(|n| n.get()).unwrap_or(0)
        ),
    ];
    if cfg!(target_os = "macos") {
        if let Some(cpu) = command_output("sysctl", &["-n", "machdep.cpu.brand_string"]) {
            lines.push(format!("Processor: {cpu}"));
        }
        if let Some(model) = command_output("sysctl", &["-n", "hw.model"]) {
            lines.push(format!("Model: {model}"));
        }
        if let Some(bytes) =
            command_output("sysctl", &["-n", "hw.memsize"]).and_then(|s| s.parse::<u64>().ok())
        {
            lines.push(format!("Memory: {} GB", bytes / (1024 * 1024 * 1024)));
        }
        // Under Rosetta, an Intel build on Apple silicon: slower AI, and a
        // common cause of "it's slow on my new Mac".
        if command_output("sysctl", &["-n", "sysctl.proc_translated"]).as_deref() == Some("1") {
            lines.push("Running under Rosetta (Intel build on Apple silicon)".into());
        }
    }
    lines
}

// --- the report -------------------------------------------------------------

/// The newest `budget` bytes of log, oldest file first.
fn recent_log(dir: &Path, budget: u64) -> String {
    let mut files: Vec<PathBuf> = (0..=KEEP).map(|n| rotated(dir, n)).filter(|p| p.exists()).collect();
    files.reverse(); // oldest first
    let sizes: Vec<u64> = files.iter().map(|p| fs::metadata(p).map(|m| m.len()).unwrap_or(0)).collect();
    let total: u64 = sizes.iter().sum();
    let mut skip = total.saturating_sub(budget);
    let mut out = Vec::new();
    for (path, size) in files.iter().zip(sizes) {
        if skip >= size {
            skip -= size;
            continue;
        }
        let Ok(mut f) = File::open(path) else { continue };
        if f.seek(SeekFrom::Start(skip)).is_ok() {
            let _ = f.read_to_end(&mut out);
        }
        skip = 0;
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// The last `n` lines of the log, for an error report.
pub fn recent_lines(n: usize) -> String {
    let Some(dir) = log_dir() else { return String::new() };
    let text = recent_log(&dir, 128 * 1024);
    let lines: Vec<&str> = text.lines().collect();
    lines[lines.len().saturating_sub(n)..].join("\n")
}

pub fn build_report(app_version: &str, engine: &str, ui_report: &serde_json::Value) -> String {
    let mut r = String::new();
    r.push_str("Church Slides diagnostics\n=========================\n\n");
    r.push_str(&format!("Created: {}\n", timestamp(SystemTime::now())));
    r.push_str(&format!("App version: {app_version}\n"));
    for line in system_lines() {
        r.push_str(&line);
        r.push('\n');
    }
    r.push_str(&format!("AI engine: {engine}\n"));
    r.push_str("\nWhat the app says\n-----------------\n");
    r.push_str(&serde_json::to_string_pretty(ui_report).unwrap_or_default());
    r.push_str("\n\nLog (most recent last)\n----------------------\n");
    match log_dir() {
        Some(dir) => r.push_str(&recent_log(&dir, REPORT_LOG_BYTES)),
        None => r.push_str("(no log directory on this system)\n"),
    }
    r
}

/// Write the report to Downloads (or the home folder) and return its path.
pub fn save_report(report: &str) -> Result<PathBuf, String> {
    let folder = dirs::download_dir()
        .or_else(dirs::home_dir)
        .ok_or("couldn't find a Downloads folder")?;
    let stamp = timestamp(SystemTime::now())
        .replace([':', ' '], "-")
        .chars()
        .take(19)
        .collect::<String>();
    let path = folder.join(format!("Church-Slides-diagnostics-{stamp}.txt"));
    write_private(&path, report).map_err(|e| format!("couldn't save {}: {e}", path.display()))?;
    log::info!("saved diagnostics to {}", path.display());
    Ok(path)
}

/// Write a file only this user can read: a diagnostics report has the log,
/// with what was said into the microphone and network addresses in it.
fn write_private(path: &Path, text: &str) -> std::io::Result<()> {
    let mut options = OpenOptions::new();
    options.write(true).create(true).truncate(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    options.open(path)?.write_all(text.as_bytes())
}

/// Show `path` selected in Finder / Explorer.
pub fn reveal(path: &Path) -> Result<(), String> {
    let result = if cfg!(target_os = "macos") {
        std::process::Command::new("open").arg("-R").arg(path).spawn()
    } else if cfg!(windows) {
        std::process::Command::new("explorer")
            .arg(format!("/select,{}", path.display()))
            .spawn()
    } else {
        std::process::Command::new("xdg-open")
            .arg(path.parent().unwrap_or(path))
            .spawn()
    };
    result.map(|_| ()).map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    #[test]
    fn formats_utc_timestamps() {
        let t = UNIX_EPOCH + Duration::from_millis(1_791_373_013_123);
        assert_eq!(timestamp(t), "2026-10-07 11:36:53.123Z");
        assert_eq!(timestamp(UNIX_EPOCH), "1970-01-01 00:00:00.000Z");
        // Leap day.
        assert_eq!(&timestamp(UNIX_EPOCH + Duration::from_secs(951_782_400))[..10], "2000-02-29");
    }

    #[test]
    fn reads_engine_levels_and_tracebacks() {
        let l = engine_line_level("2026-10-07 11:00:00,000 WARNING x: slow", Level::Info);
        assert_eq!(l, Level::Warn);
        let l = engine_line_level("2026-10-07 11:00:00,000 ERROR x: boom", Level::Info);
        assert_eq!(l, Level::Error);
        assert_eq!(engine_line_level("Traceback (most recent call last):", Level::Info), Level::Error);
        assert_eq!(engine_line_level("  File \"x.py\", line 3", Level::Error), Level::Error);
        assert_eq!(engine_line_level("2026-10-07 11:00:00,000 INFO x: hi", Level::Error), Level::Info);
    }

    #[test]
    fn recent_log_keeps_the_newest_bytes_across_rotated_files() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(rotated(dir.path(), 2), "oldest\n").unwrap();
        fs::write(rotated(dir.path(), 1), "older\n").unwrap();
        fs::write(rotated(dir.path(), 0), "newest\n").unwrap();
        assert_eq!(recent_log(dir.path(), 1000), "oldest\nolder\nnewest\n");
        assert_eq!(recent_log(dir.path(), 9), "r\nnewest\n");
    }

    #[cfg(unix)]
    #[test]
    fn log_file_is_private_to_this_user() {
        use std::os::unix::fs::PermissionsExt;
        let dir = tempfile::tempdir().unwrap();
        fs::write(rotated(dir.path(), 0), "old\n").unwrap();
        fs::set_permissions(rotated(dir.path(), 0), fs::Permissions::from_mode(0o644)).unwrap();
        FileLogger::open(dir.path()).unwrap();
        let mode = fs::metadata(rotated(dir.path(), 0)).unwrap().permissions().mode();
        assert_eq!(mode & 0o777, 0o600);
        let fresh = tempfile::tempdir().unwrap();
        FileLogger::open(fresh.path()).unwrap();
        let mode = fs::metadata(rotated(fresh.path(), 0)).unwrap().permissions().mode();
        assert_eq!(mode & 0o777, 0o600);
    }

    #[cfg(unix)]
    #[test]
    fn diagnostics_file_is_private_to_this_user() {
        use std::os::unix::fs::PermissionsExt;
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("report.txt");
        write_private(&path, "hello").unwrap();
        assert_eq!(fs::read_to_string(&path).unwrap(), "hello");
        assert_eq!(fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o600);
    }

    #[test]
    fn rotates_when_the_file_grows_too_big() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(rotated(dir.path(), 0), vec![b'x'; (MAX_BYTES + 1) as usize]).unwrap();
        let logger = FileLogger {
            dir: Some(dir.path().to_path_buf()),
            file: Mutex::new(FileLogger::open(dir.path())),
        };
        logger.write_line("fresh\n");
        assert_eq!(fs::read_to_string(rotated(dir.path(), 0)).unwrap(), "fresh\n");
        assert!(rotated(dir.path(), 1).exists());
    }
}
