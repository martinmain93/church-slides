//! Lets outside AI assistants work on the song library.
//!
//! The app's own binary doubles as a local MCP server: launched with `--mcp`
//! it skips Tauri entirely, opens the library database directly and speaks
//! MCP on stdin/stdout. Nothing here needs the app to be running; when it is,
//! it notices the database changed (`library_watch`) and refreshes its views.
//!
//! - `tools`   — the library tools themselves, over `Db`
//! - `server`  — the MCP/JSON-RPC wire protocol
//! - `install` — registering this binary with Claude Desktop, for Settings
//! - `app_link` — handing work to the running app (song-file imports)
//! - `matching` — fuzzy song-title and chord-sheet file-name matching

pub mod app_link;
pub mod install;
mod matching;
mod server;
pub(crate) mod tools;

use std::path::PathBuf;
use std::sync::Arc;

use crate::db::{self, Db};

/// Must match `identifier` in tauri.conf.json: it names the app-data folder
/// the running app keeps its library in. Guarded by a test below.
const APP_IDENTIFIER: &str = "com.churchslides.app";

/// The folder Tauri's `app_data_dir()` resolves to for this app.
pub fn app_data_dir() -> Option<PathBuf> {
    dirs::data_dir().map(|d| d.join(APP_IDENTIFIER))
}

/// Entry point for `--mcp`. Returns the process exit code.
///
/// `--db <path>` points at a different library (tests, a copy).
pub fn run_stdio(args: &[String]) -> i32 {
    // stdout is the protocol channel; logs must only go to stderr.
    let _ = env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("warn"))
        .target(env_logger::Target::Stderr)
        .try_init();

    let db_override = args
        .iter()
        .position(|a| a == "--db")
        .and_then(|i| args.get(i + 1))
        .map(PathBuf::from);
    let Some(db_path) = db_override.or_else(|| app_data_dir().map(db::default_db_path)) else {
        eprintln!("church-slides mcp: can't locate the app data folder");
        return 1;
    };
    let db = match Db::open(&db_path) {
        Ok(db) => Arc::new(db),
        Err(e) => {
            eprintln!("church-slides mcp: can't open library at {}: {e:#}", db_path.display());
            return 1;
        }
    };
    let backups = db_path.parent().map(|p| p.join("ai-backups"));
    let lib = tools::Library::new(db, backups).with_app(app_data_dir());

    let stdin = std::io::stdin();
    let stdout = std::io::stdout();
    match server::serve(&lib, stdin.lock(), stdout.lock()) {
        Ok(()) => 0,
        Err(e) => {
            eprintln!("church-slides mcp: {e}");
            1
        }
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn identifier_matches_tauri_config() {
        let conf: serde_json::Value =
            serde_json::from_str(include_str!("../../tauri.conf.json")).unwrap();
        assert_eq!(conf["identifier"], super::APP_IDENTIFIER);
    }
}
