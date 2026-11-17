//! Registering this app as an MCP server with Claude Desktop (which Cowork
//! shares), plus the command line for any other client.
//!
//! Claude Desktop reads local servers from `claude_desktop_config.json`
//! under `mcpServers`. We add or replace one key and leave everything else in
//! the file byte-for-byte as the user had it — apart from re-serialising it —
//! and keep a `.bak` of the previous version.

use std::path::{Path, PathBuf};

use anyhow::{bail, Context, Result};
use serde::Serialize;
use serde_json::{json, Map, Value};

/// Our key under `mcpServers`.
const SERVER_KEY: &str = "church-slides";

#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum ClaudeDesktopState {
    /// Claude Desktop doesn't appear to be installed (no config folder).
    NotInstalled,
    /// Installed, but we aren't registered.
    NotConnected,
    /// Registered, pointing at this copy of the app.
    Connected,
    /// Registered, but pointing somewhere else (the app moved, or a dev build).
    Stale,
}

#[derive(Debug, Clone, Serialize)]
pub struct McpConnectionInfo {
    /// The executable a client should launch.
    pub command: String,
    pub args: Vec<String>,
    /// A ready-to-paste `mcpServers` block for clients configured by JSON.
    pub config_snippet: String,
    pub claude_desktop: ClaudeDesktopState,
    pub claude_desktop_config_path: Option<String>,
}

fn claude_desktop_config_path() -> Option<PathBuf> {
    dirs::config_dir().map(|d| d.join("Claude").join("claude_desktop_config.json"))
}

fn server_entry(exe: &Path) -> Value {
    json!({ "command": exe.to_string_lossy(), "args": ["--mcp"] })
}

fn read_config(path: &Path) -> Result<Map<String, Value>> {
    if !path.exists() {
        return Ok(Map::new());
    }
    let text = std::fs::read_to_string(path)
        .with_context(|| format!("reading {}", path.display()))?;
    if text.trim().is_empty() {
        return Ok(Map::new());
    }
    match serde_json::from_str::<Value>(&text) {
        Ok(Value::Object(m)) => Ok(m),
        // Never overwrite a file we can't understand — the user's other
        // servers are in it.
        _ => bail!(
            "{} isn't valid JSON, so it was left alone. Fix or remove it in Claude's \
             Settings → Developer, then try again.",
            path.display()
        ),
    }
}

fn state_in(config: &Map<String, Value>, exe: &Path) -> ClaudeDesktopState {
    match config.get("mcpServers").and_then(|s| s.get(SERVER_KEY)) {
        None => ClaudeDesktopState::NotConnected,
        Some(entry) if entry.get("command") == server_entry(exe).get("command") => {
            ClaudeDesktopState::Connected
        }
        Some(_) => ClaudeDesktopState::Stale,
    }
}

fn info_for(exe: &Path, config_path: Option<&Path>) -> McpConnectionInfo {
    let entry = server_entry(exe);
    let snippet = json!({ "mcpServers": { SERVER_KEY: entry } });
    let claude_desktop = match config_path {
        Some(p) if p.parent().is_some_and(Path::exists) => {
            read_config(p).map(|c| state_in(&c, exe)).unwrap_or(ClaudeDesktopState::NotConnected)
        }
        _ => ClaudeDesktopState::NotInstalled,
    };
    McpConnectionInfo {
        command: exe.to_string_lossy().into_owned(),
        args: vec!["--mcp".into()],
        config_snippet: serde_json::to_string_pretty(&snippet).unwrap_or_default(),
        claude_desktop,
        claude_desktop_config_path: config_path.map(|p| p.to_string_lossy().into_owned()),
    }
}

/// Add (or repoint) our entry. Returns the state after writing.
fn connect(config_path: &Path, exe: &Path) -> Result<()> {
    let mut config = read_config(config_path)?;
    let servers = config
        .entry("mcpServers")
        .or_insert_with(|| Value::Object(Map::new()));
    let Some(servers) = servers.as_object_mut() else {
        bail!("mcpServers in {} isn't an object; left alone", config_path.display());
    };
    servers.insert(SERVER_KEY.into(), server_entry(exe));
    write_config(config_path, &config)
}

fn disconnect(config_path: &Path) -> Result<()> {
    let mut config = read_config(config_path)?;
    let removed = config
        .get_mut("mcpServers")
        .and_then(Value::as_object_mut)
        .and_then(|s| s.remove(SERVER_KEY))
        .is_some();
    if removed {
        write_config(config_path, &config)?;
    }
    Ok(())
}

fn write_config(path: &Path, config: &Map<String, Value>) -> Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    if path.exists() {
        std::fs::copy(path, path.with_extension("json.bak"))
            .context("backing up the Claude Desktop config")?;
    }
    let text = serde_json::to_string_pretty(config)?;
    // Write-then-rename so a crash can't leave Claude with half a file.
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, text + "\n")?;
    std::fs::rename(&tmp, path)?;
    Ok(())
}

fn current_exe() -> Result<PathBuf, String> {
    std::env::current_exe().map_err(|e| format!("can't locate the app: {e}"))
}

// --- Tauri commands -----------------------------------------------------------

#[tauri::command]
pub fn mcp_connection_info() -> Result<McpConnectionInfo, String> {
    Ok(info_for(&current_exe()?, claude_desktop_config_path().as_deref()))
}

#[tauri::command]
pub fn mcp_connect_claude_desktop() -> Result<McpConnectionInfo, String> {
    let exe = current_exe()?;
    let path = claude_desktop_config_path().ok_or("can't locate Claude Desktop's settings")?;
    connect(&path, &exe).map_err(|e| format!("{e:#}"))?;
    Ok(info_for(&exe, Some(&path)))
}

#[tauri::command]
pub fn mcp_disconnect_claude_desktop() -> Result<McpConnectionInfo, String> {
    let exe = current_exe()?;
    let path = claude_desktop_config_path().ok_or("can't locate Claude Desktop's settings")?;
    disconnect(&path).map_err(|e| format!("{e:#}"))?;
    Ok(info_for(&exe, Some(&path)))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn connect_preserves_other_servers_and_settings() {
        let dir = tempfile::tempdir().unwrap();
        let cfg = dir.path().join("claude_desktop_config.json");
        std::fs::write(
            &cfg,
            r#"{"mcpServers":{"other":{"command":"x"}},"globalShortcut":"Cmd+K"}"#,
        )
        .unwrap();
        let exe = Path::new("/Applications/Church Slides.app/Contents/MacOS/auto-presenter");

        connect(&cfg, exe).unwrap();
        let c = read_config(&cfg).unwrap();
        assert_eq!(c["mcpServers"]["other"]["command"], "x");
        assert_eq!(c["globalShortcut"], "Cmd+K");
        assert_eq!(c["mcpServers"][SERVER_KEY]["args"], json!(["--mcp"]));
        assert_eq!(info_for(exe, Some(&cfg)).claude_desktop, ClaudeDesktopState::Connected);
        assert!(cfg.with_extension("json.bak").exists());

        let moved = Path::new("/Users/me/Downloads/Church Slides.app/Contents/MacOS/auto-presenter");
        assert_eq!(info_for(moved, Some(&cfg)).claude_desktop, ClaudeDesktopState::Stale);

        disconnect(&cfg).unwrap();
        let c = read_config(&cfg).unwrap();
        assert!(c["mcpServers"].get(SERVER_KEY).is_none());
        assert_eq!(c["mcpServers"]["other"]["command"], "x");
    }

    #[test]
    fn creates_config_when_missing_and_refuses_broken_json() {
        let dir = tempfile::tempdir().unwrap();
        let cfg = dir.path().join("claude_desktop_config.json");
        connect(&cfg, Path::new("/a/b")).unwrap();
        assert!(read_config(&cfg).unwrap()["mcpServers"][SERVER_KEY].is_object());

        std::fs::write(&cfg, "{ not json").unwrap();
        assert!(connect(&cfg, Path::new("/a/b")).is_err());
        assert_eq!(std::fs::read_to_string(&cfg).unwrap(), "{ not json");
    }

    #[test]
    fn missing_claude_folder_reads_as_not_installed() {
        let dir = tempfile::tempdir().unwrap();
        let cfg = dir.path().join("nope").join("claude_desktop_config.json");
        assert_eq!(info_for(Path::new("/a"), Some(&cfg)).claude_desktop, ClaudeDesktopState::NotInstalled);
    }
}
