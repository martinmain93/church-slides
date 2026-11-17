//! The app's end of the AI-assistant link (`mcp::app_link` is the other).
//!
//! An assistant's `--mcp` process asks the running app to import song files.
//! The importers are TypeScript, so the request is handed to the main
//! window: it reads each file through `assistant_read_file`, runs the same
//! import the Library screen does, and answers with `assistant_import_done`.
//!
//! The web view may only read files that belong to a request in flight.

use std::collections::{HashMap, HashSet};
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde_json::{json, Value};
use tauri::Emitter;
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::{oneshot, watch};

use crate::mcp::app_link::{LinkInfo, Request, Response, LINK_FILE};

pub const IMPORT_EVENT: &str = "assistant-import";
const MAIN_WINDOW: &str = "main";
/// A freshly launched app needs a moment before its window is listening.
const READY_WAIT: Duration = Duration::from_secs(30);
const IMPORT_TIMEOUT: Duration = Duration::from_secs(15 * 60);
const MAX_REQUEST_BYTES: u64 = 4 * 1024 * 1024;

struct Pending {
    files: HashSet<String>,
    reply: oneshot::Sender<Response>,
}

#[derive(Clone)]
pub struct AssistantLink {
    pending: Arc<Mutex<HashMap<String, Pending>>>,
    ready: Arc<watch::Sender<bool>>,
}

impl Default for AssistantLink {
    fn default() -> Self {
        Self {
            pending: Arc::default(),
            ready: Arc::new(watch::channel(false).0),
        }
    }
}

impl AssistantLink {
    /// Listen on a loopback port and advertise it in the data folder.
    pub fn start(&self, app: tauri::AppHandle, data_dir: &Path) {
        let link = self.clone();
        let link_path = data_dir.join(LINK_FILE);
        tauri::async_runtime::spawn(async move {
            let listener = match TcpListener::bind("127.0.0.1:0").await {
                Ok(l) => l,
                Err(e) => {
                    log::warn!("assistant link: can't listen: {e}");
                    return;
                }
            };
            let port = listener.local_addr().map(|a| a.port()).unwrap_or(0);
            let token = format!("{}{}", ulid::Ulid::new(), ulid::Ulid::new());
            let info = LinkInfo { port, token: token.clone() };
            if let Err(e) = write_private(&link_path, &serde_json::to_string(&info).unwrap_or_default()) {
                log::warn!("assistant link: can't write {}: {e}", link_path.display());
                return;
            }
            log::info!("assistant link on 127.0.0.1:{port}");
            loop {
                let Ok((stream, _)) = listener.accept().await else { continue };
                let (link, app, token) = (link.clone(), app.clone(), token.clone());
                tauri::async_runtime::spawn(async move {
                    link.serve_one(stream, &app, &token).await;
                });
            }
        });
    }

    async fn serve_one(&self, stream: TcpStream, app: &tauri::AppHandle, token: &str) {
        let (read, mut write) = stream.into_split();
        let mut line = String::new();
        let mut reader = BufReader::new(read).take(MAX_REQUEST_BYTES);
        let resp = match reader.read_line(&mut line).await {
            Ok(_) => match serde_json::from_str::<Request>(&line) {
                Ok(req) if req.token == token => self.handle(req, app).await,
                Ok(_) => fail("bad token"),
                Err(e) => fail(&format!("bad request: {e}")),
            },
            Err(e) => fail(&format!("reading request: {e}")),
        };
        let text = serde_json::to_string(&resp).unwrap_or_default();
        let _ = write.write_all(format!("{text}\n").as_bytes()).await;
    }

    async fn handle(&self, req: Request, app: &tauri::AppHandle) -> Response {
        if req.op != "import_files" {
            return fail(&format!("unknown request: {}", req.op));
        }
        if !self.wait_ready().await {
            return fail("Church Slides is open but its window isn't ready — try again in a moment");
        }
        let id = ulid::Ulid::new().to_string();
        let (tx, rx) = oneshot::channel();
        let files: HashSet<String> = req.files.iter().map(|f| f.path.clone()).collect();
        self.pending
            .lock()
            .unwrap()
            .insert(id.clone(), Pending { files, reply: tx });

        let payload = json!({ "id": id, "files": req.files, "dry_run": req.dry_run, "attach_to": req.attach_to });
        let resp = if let Err(e) = app.emit_to(MAIN_WINDOW, IMPORT_EVENT, payload) {
            fail(&format!("couldn't reach the app window: {e}"))
        } else {
            match tokio::time::timeout(IMPORT_TIMEOUT, rx).await {
                Ok(Ok(resp)) => resp,
                Ok(Err(_)) => fail("the import was abandoned"),
                Err(_) => fail("the import took too long"),
            }
        };
        self.pending.lock().unwrap().remove(&id);
        resp
    }

    async fn wait_ready(&self) -> bool {
        let mut rx = self.ready.subscribe();
        tokio::time::timeout(READY_WAIT, rx.wait_for(|r| *r)).await.is_ok_and(|r| r.is_ok())
    }

    fn may_read(&self, id: &str, path: &str) -> bool {
        self.pending
            .lock()
            .unwrap()
            .get(id)
            .is_some_and(|p| p.files.contains(path))
    }

    fn finish(&self, id: &str, resp: Response) -> Result<(), String> {
        let pending = self.pending.lock().unwrap().remove(id);
        match pending {
            Some(p) => {
                let _ = p.reply.send(resp);
                Ok(())
            }
            None => Err(format!("no import in progress with id {id}")),
        }
    }
}

fn fail(msg: &str) -> Response {
    Response { ok: false, result: Value::Null, error: Some(msg.to_string()) }
}

/// Owner-only file: the token in it is what lets a process drive imports.
fn write_private(path: &Path, text: &str) -> std::io::Result<()> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, text)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&tmp, std::fs::Permissions::from_mode(0o600))?;
    }
    std::fs::rename(&tmp, path)
}

// --- Tauri commands -------------------------------------------------------------

/// The main window is listening for import requests.
#[tauri::command]
pub fn assistant_link_ready(link: tauri::State<'_, AssistantLink>) {
    link.ready.send_replace(true);
}

/// Bytes of one file named in an import request.
#[tauri::command]
pub async fn assistant_read_file(
    link: tauri::State<'_, AssistantLink>,
    id: String,
    path: String,
) -> Result<tauri::ipc::Response, String> {
    if !link.may_read(&id, &path) {
        return Err("that file isn't part of this import".into());
    }
    let bytes = tokio::fs::read(&path)
        .await
        .map_err(|e| format!("can't read {path}: {e}"))?;
    Ok(tauri::ipc::Response::new(bytes))
}

#[tauri::command]
pub fn assistant_import_done(
    link: tauri::State<'_, AssistantLink>,
    id: String,
    ok: bool,
    result: Value,
    error: Option<String>,
) -> Result<(), String> {
    link.finish(&id, Response { ok, result, error })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_files_in_a_live_request_are_readable() {
        let link = AssistantLink::default();
        let (tx, mut rx) = oneshot::channel();
        link.pending.lock().unwrap().insert(
            "r1".into(),
            Pending { files: HashSet::from(["/songs/a.pdf".to_string()]), reply: tx },
        );
        assert!(link.may_read("r1", "/songs/a.pdf"));
        assert!(!link.may_read("r1", "/etc/passwd"));
        assert!(!link.may_read("r2", "/songs/a.pdf"));

        link.finish("r1", Response { ok: true, result: json!(1), error: None }).unwrap();
        assert!(rx.try_recv().unwrap().ok);
        assert!(!link.may_read("r1", "/songs/a.pdf"), "done means closed");
        assert!(link.finish("r1", fail("x")).is_err());
    }
}
