//! Crash and error reports, sent to Sentry — only once someone has said yes,
//! when first asked or in Settings → "Send error reports"
//! (`Settings::send_error_reports`, `error_reports_asked`).
//!
//! The shell reports for itself and for the UI: its own panics, its own
//! `log::error!`s, the UI's console errors (`log_from_ui`), and the AI engine
//! exiting unexpectedly. The engine reports its own Python exceptions through
//! sentry-sdk (`sidecar/auto_presenter/error_reports.py`), to the same
//! project, under the same switch — it reads the setting from the same
//! database, so turning it off stops both at once.
//!
//! Reports carry what helps fix a bug: messages, device names, song titles,
//! and the end of the app's log. Two things are kept out:
//!   - what someone *said*: spoken text is logged inside «…» and replaced
//!     (`scrub`), because the microphone also hears prayer requests;
//!   - the user's folder name in paths, and this computer's name. Reports
//!     are tied to an anonymous install id instead, plus a contact only if
//!     the user gave one.
//! Nothing is sent until the stored choice has been read (`set_enabled`),
//! and a choice that can't be read counts as off (`settings::report_choice`).
//! Only errors are sent — no performance tracing or profiling, whose overhead
//! would land on the live listening pipeline.

use std::collections::VecDeque;
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU8, Ordering};
use std::sync::{Arc, Mutex, OnceLock};

use sentry::protocol::{Attachment, Breadcrumb, Event, Value};
use sentry::User;
use sentry::Level;

/// Where reports go, baked in at compile time from `CHURCH_SLIDES_SENTRY_DSN`
/// (set by `scripts/release.sh` from `release.local.env`, and by the Windows
/// workflow from a repository variable). A build without it — anyone's own
/// build from source — sends nothing and never asks.
pub const DSN: Option<&str> = option_env!("CHURCH_SLIDES_SENTRY_DSN");

/// Reports from one run of the app, at most: a loop logging errors must not
/// send hundreds of copies (Sentry would group them, but the user's
/// connection still pays for each).
const MAX_EVENTS_PER_RUN: u32 = 50;
/// Exception text carried in a report, at most.
const MAX_EXCEPTION_VALUE_CHARS: usize = 500;

/// The user's choice, as far as this run knows it. It starts `UNKNOWN`:
/// nothing is sent until the stored setting has been read (a panic opening
/// the database must not be reported for someone who turned reports off).
/// Breadcrumbs are still collected while it's unknown — they only ever leave
/// inside a report — and dropped once it's off.
const UNKNOWN: u8 = 0;
const ON: u8 = 1;
const OFF: u8 = 2;
static CHOICE: AtomicU8 = AtomicU8::new(UNKNOWN);
static ACTIVE: AtomicBool = AtomicBool::new(false);
static SENT: AtomicU32 = AtomicU32::new(0);

/// Turn reporting on or off for this run (the setting changed, or was read
/// at startup). Takes effect for the next report.
pub fn set_enabled(on: bool) {
    CHOICE.store(if on { ON } else { OFF }, Ordering::Relaxed);
}

fn sending() -> bool {
    CHOICE.load(Ordering::Relaxed) == ON
}

/// Whether reports go anywhere from this build: only a build given a DSN
/// (official releases); then release builds always, a dev build only with
/// `CHURCH_SLIDES_SENTRY=1` (to test reporting itself) — so development
/// doesn't fill the project with noise.
pub fn active_in_this_build() -> bool {
    DSN.is_some()
        && (!cfg!(debug_assertions) || std::env::var("CHURCH_SLIDES_SENTRY").as_deref() == Ok("1"))
}

/// Anonymous and random, made on first launch: lets reports from one
/// computer be told apart and counted without saying whose it is.
pub fn install_id() -> &'static str {
    static ID: std::sync::OnceLock<String> = std::sync::OnceLock::new();
    ID.get_or_init(|| {
        let Some(dir) = dirs::data_dir().map(|d| d.join("com.churchslides.app")) else {
            return ulid::Ulid::new().to_string();
        };
        let path = dir.join("install-id");
        if let Ok(id) = std::fs::read_to_string(&path) {
            let id = id.trim().to_string();
            if !id.is_empty() {
                return id;
            }
        }
        let id = ulid::Ulid::new().to_string();
        let _ = std::fs::create_dir_all(&dir).and_then(|_| std::fs::write(&path, &id));
        id
    })
}

/// Who to follow up with, if the user wrote it in Settings (an email or a
/// church name); reports stay anonymous otherwise.
pub fn set_contact(contact: &str) {
    if !ACTIVE.load(Ordering::Relaxed) {
        return;
    }
    let contact = contact.trim();
    let mut user = User { id: Some(install_id().to_string()), ..Default::default() };
    if contact.contains('@') {
        user.email = Some(contact.to_string());
    } else if !contact.is_empty() {
        user.username = Some(contact.to_string());
    }
    sentry::configure_scope(|scope| scope.set_user(Some(user)));
}

pub fn release() -> String {
    format!("church-slides@{}", env!("CARGO_PKG_VERSION"))
}

pub fn environment() -> &'static str {
    if cfg!(debug_assertions) {
        "development"
    } else {
        "production"
    }
}

/// Start the client. Keep the guard alive for the life of the app: dropping
/// it flushes what's queued.
pub fn init() -> Option<sentry::ClientInitGuard> {
    let dsn = DSN.filter(|_| active_in_this_build())?;
    let mut options = sentry::ClientOptions::new();
    options.release = Some(release().into());
    options.environment = Some(environment().into());
    options.send_default_pii = false;
    // The hostname is often the owner's name ("Sarahs-MacBook-Air").
    options.server_name = None;
    options.max_breadcrumbs = 60;
    // `CHURCH_SLIDES_SENTRY_DEBUG=1`: the SDK says on stderr what it sent and
    // whether Sentry took it — for checking reporting itself.
    options.debug = std::env::var("CHURCH_SLIDES_SENTRY_DEBUG").as_deref() == Ok("1");
    options.before_send = Some(Arc::new(before_send));
    // Every log line passes through here, so it doesn't wait for this
    // computer's names; `before_send` scrubs each breadcrumb again, with them.
    options.before_breadcrumb = Some(Arc::new(|crumb| {
        (CHOICE.load(Ordering::Relaxed) != OFF).then(|| {
            let names = HOST_NAMES.get().map(Vec::as_slice).unwrap_or(&[]);
            scrub_breadcrumb_with(crumb, names)
        })
    }));
    // Looked up off the startup path (`scutil` is a process spawn); a report
    // made before it finishes waits for it in `scrub`.
    std::thread::spawn(|| {
        HOST_NAMES.get_or_init(this_computers_names);
    });
    let guard = sentry::init((dsn, options));
    sentry::configure_scope(|scope| {
        scope.set_tag("component", "app");
        scope.set_tag("arch", std::env::consts::ARCH);
    });
    ACTIVE.store(true, Ordering::Relaxed);
    set_contact("");
    Some(guard)
}

fn before_send(event: Event<'static>) -> Option<Event<'static>> {
    if !sending() {
        return None;
    }
    if SENT.fetch_add(1, Ordering::Relaxed) >= MAX_EVENTS_PER_RUN {
        return None;
    }
    Some(scrub_event(event))
}

fn scrub_event(mut event: Event<'static>) -> Event<'static> {
    event.server_name = None;
    if let Some(m) = event.message.take() {
        event.message = Some(scrub(&m));
    }
    // Exception text is kept (it's what a bug report needs) with speech and
    // paths removed, but cut short: a message can quote data it was given.
    for exc in event.exception.values.iter_mut() {
        if let Some(v) = exc.value.take() {
            exc.value = Some(scrub(&v).chars().take(MAX_EXCEPTION_VALUE_CHARS).collect());
        }
    }
    // Sent with the report, so scrubbed like everything else in it.
    let fingerprint: Vec<_> =
        event.fingerprint.iter().map(|part| std::borrow::Cow::Owned(scrub(part))).collect();
    event.fingerprint = fingerprint.into();
    for (_, v) in event.extra.iter_mut() {
        if let Value::String(s) = v {
            *s = scrub(s);
        }
    }
    let crumbs = std::mem::take(&mut event.breadcrumbs.values);
    event.breadcrumbs.values = crumbs.into_iter().map(scrub_breadcrumb).collect();
    event
}

fn scrub_breadcrumb(crumb: Breadcrumb) -> Breadcrumb {
    scrub_breadcrumb_with(crumb, host_names())
}

fn scrub_breadcrumb_with(mut crumb: Breadcrumb, names: &[String]) -> Breadcrumb {
    if let Some(m) = crumb.message.take() {
        crumb.message = Some(scrub_with_names(&m, names));
    }
    crumb
}

// --- what gets reported -----------------------------------------------------

/// Every log line becomes a breadcrumb — the trail leading up to a report —
/// and an error from the shell itself becomes a report. Engine errors are
/// reported by the engine; UI errors by `report_ui_error`.
pub fn on_log(level: log::Level, target: &str, message: &str) {
    if !ACTIVE.load(Ordering::Relaxed) {
        return;
    }
    let level = match level {
        log::Level::Error => Level::Error,
        log::Level::Warn => Level::Warning,
        _ => Level::Info,
    };
    sentry::add_breadcrumb(Breadcrumb {
        category: Some(target.to_string()),
        level,
        message: Some(message.to_string()),
        ..Default::default()
    });
    if level == Level::Error && target != "engine" && target != "ui" {
        capture("app", message, None);
    }
}

/// A console error from the UI: the first line is what it's grouped by, the
/// rest (usually a stack) goes along as detail.
pub fn report_ui_error(message: &str) {
    if !ACTIVE.load(Ordering::Relaxed) {
        return;
    }
    let (first, rest) = message.split_once('\n').unwrap_or((message, ""));
    capture("ui", first, (!rest.is_empty()).then_some(rest));
}

/// A file the song import couldn't read. Only its shape is sent — extension,
/// size and first bytes (`signature`) and why it was refused — never its
/// contents or name, so formats we don't read yet show up as their own issue.
pub fn report_unreadable_import(extension: &str, reason: &str, signature: &str) {
    if !ACTIVE.load(Ordering::Relaxed) {
        return;
    }
    let message = format!("Unreadable import: .{extension} — {reason}");
    sentry::with_scope(
        |scope| {
            scope.set_tag("component", "import");
            scope.set_tag("import_extension", extension);
            scope.set_fingerprint(Some(["import-unreadable", extension, reason].as_slice()));
            scope.set_extra("signature", Value::String(signature.to_string()));
        },
        || sentry::capture_message(&message, Level::Warning),
    );
}

/// The engine exited when nobody asked it to. `tail` is the end of its
/// output — for a native crash, the faulthandler's stack.
pub fn report_engine_exit(status: &str, tail: &EngineTail) {
    if !ACTIVE.load(Ordering::Relaxed) {
        return;
    }
    let message = format!("AI engine exited unexpectedly ({status})");
    let tail = tail.text();
    sentry::with_scope(
        |scope| {
            scope.set_tag("component", "engine");
            scope.set_fingerprint(Some(["engine-exit", status].as_slice()));
            scope.set_extra("engine_output_tail", Value::String(tail));
            attach_log_tail(scope);
        },
        || sentry::capture_message(&message, Level::Error),
    );
}

fn capture(component: &str, message: &str, detail: Option<&str>) {
    let key = grouping_key(message);
    sentry::with_scope(
        |scope| {
            scope.set_tag("component", component);
            scope.set_fingerprint(Some([component, key.as_str()].as_slice()));
            if let Some(d) = detail {
                scope.set_extra("detail", Value::String(d.to_string()));
            }
            attach_log_tail(scope);
        },
        || sentry::capture_message(message, Level::Error),
    );
}

/// What a message is grouped by: scrubbed (a fingerprint is sent with the
/// report), and with its numbers taken out, so "port 54358" and "port 54412"
/// are one issue.
fn grouping_key(message: &str) -> String {
    scrub(message).chars().map(|c| if c.is_ascii_digit() { '0' } else { c }).collect()
}

/// Lines of the app's log attached to each report — the engine's and the
/// UI's lines included, so the moments before the error are all there.
const LOG_TAIL_LINES: usize = 200;

fn attach_log_tail(scope: &mut sentry::Scope) {
    let tail = crate::diagnostics::recent_lines(LOG_TAIL_LINES);
    if tail.is_empty() {
        return;
    }
    scope.add_attachment(Attachment {
        buffer: scrub(&tail).into_bytes(),
        filename: "log-tail.txt".into(),
        content_type: Some("text/plain".into()),
        ty: None,
    });
}

/// The last lines the engine printed, for an exit report.
#[derive(Default)]
pub struct EngineTail(Mutex<VecDeque<String>>);

impl EngineTail {
    const LINES: usize = 60;

    pub fn push(&self, line: &str) {
        if let Ok(mut q) = self.0.lock() {
            if q.len() == Self::LINES {
                q.pop_front();
            }
            q.push_back(line.to_string());
        }
    }

    fn text(&self) -> String {
        self.0.lock().map(|q| q.iter().cloned().collect::<Vec<_>>().join("\n")).unwrap_or_default()
    }
}

// --- scrubbing --------------------------------------------------------------

/// Replace what someone said (logged inside «…»), the user's folder name in
/// paths (`/Users/sarah/…` → `/Users/~/…`) and this computer's name ("Sarah's
/// MacBook Air", which peer sync and Bonjour use). Everything else — audio
/// device names, song titles, error text — stays: it's what a report is for.
pub fn scrub(text: &str) -> String {
    scrub_with_names(text, host_names())
}

fn host_names() -> &'static [String] {
    HOST_NAMES.get_or_init(this_computers_names)
}

fn scrub_with_names(text: &str, names: &[String]) -> String {
    let mut out = remove_speech(&strip_user_dirs(text));
    for name in names {
        if out.contains(name.as_str()) {
            out = out.replace(name.as_str(), "<computer>");
        }
    }
    // After the computer's own names, which it would otherwise half-match.
    remove_owner_names(&out)
}

/// This computer's names, longest first (so "Sarahs-MacBook-Air.local" goes
/// before its prefix). Very short ones, and "localhost", would match ordinary
/// text, so they're left alone.
static HOST_NAMES: OnceLock<Vec<String>> = OnceLock::new();

fn this_computers_names() -> Vec<String> {
    let run = |cmd: &str, args: &[&str]| -> Option<String> {
        let out = std::process::Command::new(cmd).args(args).output().ok()?;
        out.status
            .success()
            .then(|| String::from_utf8_lossy(&out.stdout).trim().to_string())
            .filter(|s| !s.is_empty())
    };
    let mut names = Vec::new();
    if cfg!(target_os = "macos") {
        names.extend(run("scutil", &["--get", "ComputerName"]));
        names.extend(run("scutil", &["--get", "LocalHostName"]));
    }
    if let Some(host) = run("hostname", &[]) {
        if let Some((short, _)) = host.split_once('.') {
            names.push(short.to_string());
        }
        names.push(host);
    }
    names.retain(|n| n.chars().count() >= 4 && !n.eq_ignore_ascii_case("localhost"));
    names.sort_by_key(|n| std::cmp::Reverse(n.len()));
    names.dedup();
    names
}

/// Words that, after a possessive, make it a device named after its owner:
/// "Sarah's AirPods", "Pastor John’s iPhone Microphone". Matched as a prefix,
/// ignoring case. Mirrored in `error_reports.py`.
const DEVICE_WORDS: &[&str] = &[
    "airpods", "iphone", "ipad", "macbook", "imac", "mac", "microphone", "mic", "headphones",
    "headset", "earbuds", "buds", "beats", "phone", "galaxy", "pixel", "speaker", "laptop",
];

/// The owner's name in a device name ("Sarah's AirPods" → "<name>'s
/// AirPods"): audio devices are logged by name, and people name them after
/// themselves. Up to three capitalised words before the possessive go.
fn remove_owner_names(text: &str) -> String {
    let chars: Vec<char> = text.chars().collect();
    let mut cut: Vec<(usize, usize)> = Vec::new();
    for i in 0..chars.len() {
        if !matches!(chars[i], '\'' | '’')
            || chars.get(i + 1) != Some(&'s')
            || chars.get(i + 2) != Some(&' ')
        {
            continue;
        }
        let next: String = chars[i + 3..]
            .iter()
            .take_while(|c| c.is_alphanumeric())
            .flat_map(|c| c.to_lowercase())
            .collect();
        if !DEVICE_WORDS.iter().any(|w| next.starts_with(w)) {
            continue;
        }
        let word_start = |end: usize| {
            let mut j = end;
            while j > 0 && (chars[j - 1].is_alphanumeric() || chars[j - 1] == '-') {
                j -= 1;
            }
            j
        };
        let mut start = word_start(i);
        if start == i || !chars[start].is_uppercase() {
            continue;
        }
        for _ in 0..2 {
            if start < 2 || chars[start - 1] != ' ' {
                break;
            }
            let before = word_start(start - 1);
            if before == start - 1 || !chars[before].is_uppercase() {
                break;
            }
            start = before;
        }
        cut.push((start, i));
    }
    if cut.is_empty() {
        return text.to_string();
    }
    let mut out = String::with_capacity(text.len());
    let mut copied = 0;
    for (start, end) in cut {
        out.extend(&chars[copied..start]);
        out.push_str("<name>");
        copied = end;
    }
    out.extend(&chars[copied..]);
    out
}

fn remove_speech(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut rest = text;
    while let Some(open) = rest.find('«') {
        let after = open + '«'.len_utf8();
        let Some(len) = rest[after..].find('»') else { break };
        out.push_str(&rest[..open]);
        out.push_str("«speech removed»");
        rest = &rest[after + len + '»'.len_utf8()..];
    }
    out.push_str(rest);
    out
}

/// `/Users/<name>`, `/home/<name>` and `\Users\<name>` — the last with any
/// number of backslashes, since a Windows path inside JSON or a debug string
/// is escaped (`C:\\Users\\sarah`).
fn strip_user_dirs(text: &str) -> String {
    let b = text.as_bytes();
    let mut out = String::with_capacity(text.len());
    let mut copied = 0;
    let mut pos = 0;
    while pos < b.len() {
        let Some(start) = user_dir_at(b, pos) else {
            pos += 1;
            continue;
        };
        // `start` follows an ASCII marker and `end` is an ASCII separator or
        // the end of the text, so both are char boundaries.
        let end = name_end(b, start);
        let name = &text[start..end];
        let starts_blank = name.starts_with(|c: char| c.is_whitespace());
        if !name.is_empty() && !starts_blank && name != "~" && name != "Shared" {
            out.push_str(&text[copied..start]);
            out.push('~');
            copied = end;
        }
        pos = end.max(pos + 1);
    }
    out.push_str(&text[copied..]);
    out
}

/// Where a folder name that starts at `start` ends: the next separator.
/// Spaces are part of it ("Sarah Jones"), and so is an apostrophe followed by
/// a letter ("Sarah's"); a quote that closes a quoted path is not.
fn name_end(b: &[u8], start: usize) -> usize {
    let mut i = start;
    while i < b.len() {
        match b[i] {
            b'/' | b'\\' | b'"' | b'\n' | b'\r' | b'\t' => break,
            b'\'' if !b.get(i + 1).is_some_and(u8::is_ascii_alphabetic) => break,
            _ => i += 1,
        }
    }
    i
}

fn starts_with_ignore_case(b: &[u8], prefix: &[u8]) -> bool {
    b.len() >= prefix.len() && b[..prefix.len()].eq_ignore_ascii_case(prefix)
}

/// Where the folder name starts, if a user-folder marker begins at `pos`.
/// Any case (`c:\users\`); `/Volumes/<name>` is a drive, often named after
/// its owner.
fn user_dir_at(b: &[u8], pos: usize) -> Option<usize> {
    let rest = &b[pos..];
    for marker in [b"/Users/".as_slice(), b"/home/".as_slice(), b"/Volumes/".as_slice()] {
        if starts_with_ignore_case(rest, marker) {
            return Some(pos + marker.len());
        }
    }
    let lead = rest.iter().take_while(|&&c| c == b'\\').count();
    if lead == 0 || !starts_with_ignore_case(&rest[lead..], b"Users") {
        return None;
    }
    let after = lead + "Users".len();
    let trail = rest[after..].iter().take_while(|&&c| c == b'\\').count();
    (trail > 0).then_some(pos + after + trail)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn removes_owners_from_device_names_but_not_from_song_titles() {
        assert_eq!(
            scrub_with_names("input: Pastor John’s iPhone Microphone (48000 Hz)", &[]),
            "input: <name>’s iPhone Microphone (48000 Hz)"
        );
        assert_eq!(scrub_with_names("Sarah's AirPods Pro", &[]), "<name>'s AirPods Pro");
        assert_eq!(scrub_with_names("song: God's Great Dance Floor", &[]), "song: God's Great Dance Floor");
        assert_eq!(scrub_with_names("the band's mic", &[]), "the band's mic");
    }

    #[test]
    fn removes_what_was_said_and_keeps_the_rest() {
        assert_eq!(
            scrub("spoken instruction «please pray for Margaret» -> no action"),
            "spoken instruction «speech removed» -> no action"
        );
        assert_eq!(scrub("heard «a» and «b»"), "heard «speech removed» and «speech removed»");
        assert_eq!(
            scrub("couldn't open device 'MacBook Air Microphone'"),
            "couldn't open device 'MacBook Air Microphone'"
        );
        assert_eq!(scrub("unclosed «marker"), "unclosed «marker");
    }

    #[test]
    fn hides_the_user_folder_name() {
        assert_eq!(
            scrub(r#"File "/Users/sarah/Library/x.py", line 3"#),
            r#"File "/Users/~/Library/x.py", line 3"#
        );
        assert_eq!(scrub(r"C:\Users\sarah\AppData\x"), r"C:\Users\~\AppData\x");
        assert_eq!(scrub("/Users/Shared/x"), "/Users/Shared/x");
        assert_eq!(scrub("/Users/émile/x and /home/bob"), "/Users/~/x and /home/~");
    }

    #[test]
    fn hides_the_user_folder_in_escaped_windows_paths() {
        assert_eq!(scrub(r"C:\\Users\\sarah\\AppData"), r"C:\\Users\\~\\AppData");
        assert_eq!(scrub(r#"{"p":"C:\\\\Users\\\\sarah\\\\x"}"#), r#"{"p":"C:\\\\Users\\\\~\\\\x"}"#);
    }

    #[test]
    fn text_ending_at_a_marker_does_not_panic() {
        for text in ["/Users/", "/home/", r"\Users\", r"\\Users\\", "x /Users/", "/Users/é", r"\Users"] {
            let _ = scrub(text);
        }
        assert_eq!(scrub("/Users/"), "/Users/");
        assert_eq!(scrub(r"see \Users\"), r"see \Users\");
    }

    #[test]
    fn hides_folder_names_with_spaces_any_case_and_drives() {
        assert_eq!(scrub(r"C:\Users\Sarah Jones\x"), r"C:\Users\~\x");
        assert_eq!(scrub(r"c:\users\sarah\x"), r"c:\users\~\x");
        assert_eq!(scrub("/users/sarah/x"), "/users/~/x");
        assert_eq!(scrub("/Volumes/Sarah's Drive/Songs"), "/Volumes/~/Songs");
        assert_eq!(
            scrub(r#"File "/Users/Sarah Jones/app/x.py", line 3"#),
            r#"File "/Users/~/app/x.py", line 3"#
        );
        assert_eq!(scrub("open('/Users/sarah') failed"), "open('/Users/~') failed");
        assert_eq!(scrub("/Users/ x"), "/Users/ x");
    }

    #[test]
    fn the_fingerprint_is_scrubbed() {
        // `capture` groups by the message; the fingerprint goes out with it.
        let key = grouping_key("couldn't read /Users/sarah/x on port 54358 «pray for Ann»");
        assert_eq!(key, "couldn't read /Users/~/x on port 00000 «speech removed»");
        // And `before_send` scrubs whatever fingerprint an event arrives with.
        let mut event = Event::default();
        event.fingerprint = vec!["app".into(), "/Users/sarah/x «hi»".into()].into();
        event.exception.values.push(sentry::protocol::Exception {
            value: Some("y".repeat(5000)),
            ..Default::default()
        });
        let out = scrub_event(event);
        assert_eq!(out.fingerprint.as_ref(), ["app", "/Users/~/x «speech removed»"]);
        assert_eq!(out.exception.values[0].value.as_ref().unwrap().len(), MAX_EXCEPTION_VALUE_CHARS);
    }

    #[test]
    fn hides_this_computers_name() {
        let names = vec!["Sarah's MacBook Air".to_string(), "Sarahs-MacBook-Air".to_string()];
        assert_eq!(
            scrub_with_names(r#"peer sync as "Sarah's MacBook Air" on Sarahs-MacBook-Air.local"#, &names),
            r#"peer sync as "<computer>" on <computer>.local"#
        );
    }

    #[test]
    fn nothing_is_sent_until_the_choice_is_known() {
        // One test owns CHOICE: tests run in parallel threads.
        CHOICE.store(UNKNOWN, Ordering::Relaxed);
        assert!(before_send(Event::default()).is_none());
        set_enabled(false);
        assert!(before_send(Event::default()).is_none());
        set_enabled(true);
        assert!(before_send(Event::default()).is_some());
    }

    #[test]
    fn engine_tail_keeps_the_last_lines() {
        let tail = EngineTail::default();
        for i in 0..100 {
            tail.push(&format!("line {i}"));
        }
        let text = tail.text();
        assert!(text.starts_with("line 40\n"));
        assert!(text.ends_with("line 99"));
    }
}
