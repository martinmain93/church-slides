//! A line of communication from the `--mcp` process to the running app, for
//! work only the app can do — today, reading song files with the importers,
//! which are TypeScript and run in the app's web view.
//!
//! The app listens on a loopback port and writes `{port, token}` to
//! `assistant-link.json` in its data folder; a client must present the token.
//! One JSON line each way per connection.
//!
//! This file holds both ends' shared pieces and the client. The app's side,
//! which needs Tauri, is `crate::assistant_link`.

use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::net::{Shutdown, TcpStream};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use anyhow::{anyhow, bail, Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::Value;

pub const LINK_FILE: &str = "assistant-link.json";

/// How long to wait for the app to start and be ready when we launch it.
const LAUNCH_WAIT: Duration = Duration::from_secs(45);
/// A big folder of PDFs takes a while; this is a ceiling, not an estimate.
const IMPORT_TIMEOUT: Duration = Duration::from_secs(15 * 60);

#[derive(Debug, Serialize, Deserialize)]
pub struct LinkInfo {
    pub port: u16,
    pub token: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ImportFile {
    pub path: String,
    /// Shown to the importer and in results; the path relative to the folder
    /// it was found in, or the bare file name.
    pub name: String,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct Request {
    pub token: String,
    pub op: String,
    pub files: Vec<ImportFile>,
    pub dry_run: bool,
    /// File path → library song id: add that file's chords to this song,
    /// whatever it would have matched. Older apps ignore it.
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub attach_to: HashMap<String, String>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct Response {
    pub ok: bool,
    #[serde(default)]
    pub result: Value,
    #[serde(default)]
    pub error: Option<String>,
}

/// Ask the running app to import `files`, starting it first if it isn't
/// running. Returns the app's report.
pub fn import_files(
    data_dir: &Path,
    files: Vec<ImportFile>,
    dry_run: bool,
    attach_to: HashMap<String, String>,
) -> Result<Value> {
    let stream = connect_or_launch(data_dir)?;
    let link = read_link(data_dir)?;
    let req = Request { token: link.token, op: "import_files".into(), files, dry_run, attach_to };
    exchange(stream, &req)
}

fn exchange(mut stream: TcpStream, req: &Request) -> Result<Value> {
    stream.set_read_timeout(Some(IMPORT_TIMEOUT))?;
    writeln!(stream, "{}", serde_json::to_string(req)?)?;
    stream.flush()?;
    let _ = stream.shutdown(Shutdown::Write);
    let mut line = String::new();
    BufReader::new(&stream)
        .read_line(&mut line)
        .context("waiting for Church Slides to finish the import")?;
    if line.trim().is_empty() {
        bail!("Church Slides closed the connection without answering (was it quit mid-import?)");
    }
    let resp: Response = serde_json::from_str(&line).context("reading Church Slides' reply")?;
    if resp.ok {
        Ok(resp.result)
    } else {
        Err(anyhow!(resp.error.unwrap_or_else(|| "the import failed".into())))
    }
}

fn read_link(data_dir: &Path) -> Result<LinkInfo> {
    let text = std::fs::read_to_string(data_dir.join(LINK_FILE))?;
    Ok(serde_json::from_str(&text)?)
}

fn try_connect(data_dir: &Path) -> Option<TcpStream> {
    let link = read_link(data_dir).ok()?;
    TcpStream::connect_timeout(&([127, 0, 0, 1], link.port).into(), Duration::from_secs(2)).ok()
}

fn connect_or_launch(data_dir: &Path) -> Result<TcpStream> {
    if let Some(s) = try_connect(data_dir) {
        return Ok(s);
    }
    launch_app()?;
    let deadline = Instant::now() + LAUNCH_WAIT;
    while Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(500));
        if let Some(s) = try_connect(data_dir) {
            return Ok(s);
        }
    }
    bail!("Church Slides didn't start in time. Open it, then try again.")
}

/// Start the app this binary belongs to. Only from an installed `.app`: a
/// development binary would become a child of the assistant's process.
fn launch_app() -> Result<()> {
    let exe = std::env::current_exe()?;
    let bundle = app_bundle(&exe)
        .ok_or_else(|| anyhow!("Church Slides isn't running. Open it, then try again."))?;
    std::process::Command::new("open")
        .arg(&bundle)
        .status()
        .with_context(|| format!("opening {}", bundle.display()))?;
    Ok(())
}

fn app_bundle(exe: &Path) -> Option<PathBuf> {
    exe.ancestors()
        .find(|p| p.extension().is_some_and(|e| e == "app"))
        .map(Path::to_path_buf)
}

// --- Finding files ----------------------------------------------------------------

/// Song files the importers read. Mirrors `SONG_FILE_EXTENSIONS` in
/// `src/lib/chords/import/index.ts`; used only to pick files out of folders
/// (a file named explicitly is always passed through).
const SONG_FILE_EXTENSIONS: &[&str] = &[
    "pdf", "cho", "chopro", "chordpro", "crd", "pro", "onsong", "txt", "usr", "xml",
    "openlyrics", "opensong", "song",
];
const MAX_FILES: usize = 1000;
const MAX_FILE_BYTES: u64 = 25 * 1024 * 1024;
const MAX_DEPTH: usize = 8;

#[derive(Debug, Default, Serialize)]
pub struct Found {
    pub files: Vec<ImportFile>,
    /// Paths passed over, with why.
    pub skipped: Vec<(String, String)>,
}

/// Expand files and folders into the files to import.
pub fn find_files(paths: &[String]) -> Result<Found> {
    let mut found = Found::default();
    for raw in paths {
        let path = expand_home(raw);
        if !path.is_absolute() {
            bail!("\"{raw}\" isn't a full path — give the whole path, e.g. /Users/you/Documents/Charts");
        }
        let meta = std::fs::metadata(&path).with_context(|| format!("can't open {raw}"))?;
        if meta.is_dir() {
            walk(&path, &path, 0, &mut found);
        } else {
            let name = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
            push_file(&path, name, meta.len(), &mut found);
        }
        if found.files.len() > MAX_FILES {
            bail!("more than {MAX_FILES} song files — import a smaller folder at a time");
        }
    }
    Ok(found)
}

/// Every song file under a folder, with no cap — for looking sheets up by
/// name, where a church's whole archive (thousands of files across years of
/// service folders) is the normal case.
pub fn list_song_files(folder: &str) -> Result<Vec<ImportFile>> {
    let path = expand_home(folder);
    if !path.is_absolute() {
        bail!("\"{folder}\" isn't a full path — give the whole path, e.g. /Users/you/Documents/Charts");
    }
    if !std::fs::metadata(&path).with_context(|| format!("can't open {folder}"))?.is_dir() {
        bail!("{folder} isn't a folder");
    }
    let mut found = Found::default();
    walk(&path, &path, 0, &mut found);
    Ok(found.files)
}

fn walk(root: &Path, dir: &Path, depth: usize, found: &mut Found) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        found.skipped.push((dir.display().to_string(), "couldn't read the folder".into()));
        return;
    };
    let mut entries: Vec<_> = entries.filter_map(|e| e.ok()).collect();
    entries.sort_by_key(|e| e.file_name());
    for e in entries {
        let path = e.path();
        let hidden = e.file_name().to_string_lossy().starts_with('.');
        let Ok(meta) = e.metadata() else { continue };
        if hidden {
            continue;
        }
        if meta.is_dir() {
            if depth < MAX_DEPTH {
                walk(root, &path, depth + 1, found);
            }
            continue;
        }
        let ext = path
            .extension()
            .map(|x| x.to_string_lossy().to_lowercase())
            .unwrap_or_default();
        if !SONG_FILE_EXTENSIONS.contains(&ext.as_str()) {
            continue;
        }
        // A display name, the same on every OS: "Week 1/Cornerstone.pdf", not
        // "Week 1\\Cornerstone.pdf" on Windows. `path` stays native for opening.
        let name = path
            .strip_prefix(root)
            .unwrap_or(&path)
            .components()
            .map(|c| c.as_os_str().to_string_lossy())
            .collect::<Vec<_>>()
            .join("/");
        push_file(&path, name, meta.len(), found);
    }
}

fn push_file(path: &Path, name: String, len: u64, found: &mut Found) {
    if len > MAX_FILE_BYTES {
        found.skipped.push((path.display().to_string(), "larger than 25 MB".into()));
        return;
    }
    found.files.push(ImportFile { path: path.to_string_lossy().into_owned(), name });
}

pub fn expand_home(raw: &str) -> PathBuf {
    match raw.strip_prefix("~/") {
        Some(rest) => dirs::home_dir().map(|h| h.join(rest)).unwrap_or_else(|| PathBuf::from(raw)),
        None => PathBuf::from(raw),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn folders_yield_song_files_only_and_named_files_always() {
        let dir = tempfile::tempdir().unwrap();
        let d = dir.path();
        std::fs::create_dir_all(d.join("Week 1")).unwrap();
        std::fs::create_dir_all(d.join(".hidden")).unwrap();
        std::fs::write(d.join("Week 1/Cornerstone.pdf"), b"%PDF").unwrap();
        std::fs::write(d.join("Holy.cho"), b"{title: Holy}").unwrap();
        std::fs::write(d.join("photo.jpg"), b"x").unwrap();
        std::fs::write(d.join(".DS_Store"), b"x").unwrap();
        std::fs::write(d.join(".hidden/x.txt"), b"x").unwrap();
        std::fs::write(d.join("notes.docx"), b"x").unwrap();

        let found = find_files(&[d.to_string_lossy().into_owned()]).unwrap();
        let names: Vec<&str> = found.files.iter().map(|f| f.name.as_str()).collect();
        assert_eq!(names, vec!["Holy.cho", "Week 1/Cornerstone.pdf"]);

        // Named explicitly, an unusual file still goes to the importer, which
        // says what's wrong with it.
        let named = find_files(&[d.join("notes.docx").to_string_lossy().into_owned()]).unwrap();
        assert_eq!(named.files[0].name, "notes.docx");
    }

    #[test]
    fn relative_and_missing_paths_are_errors() {
        assert!(find_files(&["Charts".into()]).is_err());
        assert!(find_files(&["/definitely/not/here".into()]).is_err());
    }

    #[test]
    fn finds_the_app_bundle() {
        let exe = Path::new("/Applications/Church Slides.app/Contents/MacOS/auto-presenter");
        assert_eq!(app_bundle(exe).unwrap(), Path::new("/Applications/Church Slides.app"));
        assert!(app_bundle(Path::new("/repo/target/debug/auto-presenter")).is_none());
    }

    #[test]
    fn exchange_round_trips_with_a_server() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let server = std::thread::spawn(move || {
            let (s, _) = listener.accept().unwrap();
            let mut line = String::new();
            BufReader::new(&s).read_line(&mut line).unwrap();
            let req: Request = serde_json::from_str(&line).unwrap();
            let resp = Response {
                ok: true,
                result: serde_json::json!({ "files": req.files.len(), "dry": req.dry_run }),
                error: None,
            };
            writeln!(&s, "{}", serde_json::to_string(&resp).unwrap()).unwrap();
        });
        let stream = TcpStream::connect(("127.0.0.1", port)).unwrap();
        let req = Request {
            token: "t".into(),
            op: "import_files".into(),
            files: vec![ImportFile { path: "/a".into(), name: "a".into() }],
            dry_run: true,
            attach_to: HashMap::new(),
        };
        let out = exchange(stream, &req).unwrap();
        assert_eq!(out, serde_json::json!({ "files": 1, "dry": true }));
        server.join().unwrap();
    }
}
