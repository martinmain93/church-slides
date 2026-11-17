//! Which display each of this app's output windows goes on.
//!
//! Placement stays automatic: a new audience or band screen window goes
//! fullscreen on a display nobody is sitting at. What this adds is a way to
//! correct it, from the operator's "Screens" dialog, and to keep the
//! correction:
//!
//! - a window the operator moved goes back to that display next time, as long
//!   as it's connected (displays are told apart by name and size);
//! - a display marked "Don't use" (a stream encoder's output, a confidence
//!   monitor run by something else) is never picked automatically.
//!
//! The choices live in `screens.json` in the app's data folder: they describe
//! this computer's displays, so they don't belong in the library or settings
//! that move between computers.

use std::collections::HashMap;
use std::path::PathBuf;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use tauri::Manager;

const PREFS_FILE: &str = "screens.json";
/// How long Identify shows each display's number.
const IDENTIFY_FOR: Duration = Duration::from_secs(6);

#[derive(Debug, Default, Clone, Serialize, Deserialize)]
struct ScreenPrefs {
    /// Window label (`audience`, `band-1`, …) → the display it was moved to.
    #[serde(default)]
    assign: HashMap<String, String>,
    /// Displays never picked automatically.
    #[serde(default)]
    avoid: Vec<String>,
}

fn prefs_path(app: &tauri::AppHandle) -> Option<PathBuf> {
    app.path().app_data_dir().ok().map(|d| d.join(PREFS_FILE))
}

fn load_prefs(app: &tauri::AppHandle) -> ScreenPrefs {
    prefs_path(app)
        .and_then(|p| std::fs::read(p).ok())
        .and_then(|b| serde_json::from_slice(&b).ok())
        .unwrap_or_default()
}

fn save_prefs(app: &tauri::AppHandle, prefs: &ScreenPrefs) -> Result<(), String> {
    let path = prefs_path(app).ok_or("no app data folder")?;
    let bytes = serde_json::to_vec_pretty(prefs).map_err(|e| e.to_string())?;
    std::fs::write(path, bytes).map_err(|e| format!("couldn't save the screen choices: {e}"))
}

/// How a display is recognised from one service to the next: its name and
/// size. Positions change whenever someone rearranges displays; these don't.
fn display_key(m: &tauri::Monitor) -> String {
    let size = m.size();
    format!("{}|{}x{}", m.name().map(String::as_str).unwrap_or("Display"), size.width, size.height)
}

/// Displays the app's windows are on, by position, and which windows.
fn windows_by_display(app: &tauri::AppHandle) -> Vec<(tauri::PhysicalPosition<i32>, String)> {
    app.webview_windows()
        .into_iter()
        .filter(|(label, _)| !label.starts_with("identify-"))
        .filter_map(|(label, w)| w.current_monitor().ok().flatten().map(|m| (*m.position(), label)))
        .collect()
}

/// The display a new `label` window should go on, if any: where the operator
/// last put that window, else the first display with none of this app's
/// windows on it that isn't marked "Don't use". `None` means open it as a
/// normal window on this display, to be dragged by hand.
pub fn pick_monitor(app: &tauri::AppHandle, label: &str) -> Option<tauri::Monitor> {
    let main = app.get_webview_window("main")?;
    let monitors = main.available_monitors().ok()?;
    let prefs = load_prefs(app);
    let taken = windows_by_display(app);
    let main_at = main.current_monitor().ok().flatten().map(|m| *m.position());
    let free = |m: &tauri::Monitor| !taken.iter().any(|(p, l)| p == m.position() && l != label);

    if let Some(key) = prefs.assign.get(label) {
        if let Some(m) = monitors
            .iter()
            .find(|m| &display_key(m) == key && Some(*m.position()) != main_at && free(m))
        {
            return Some(m.clone());
        }
    }
    monitors
        .into_iter()
        .find(|m| free(m) && !prefs.avoid.contains(&display_key(m)))
}

#[derive(Debug, Serialize)]
pub struct ScreenInfo {
    key: String,
    name: String,
    /// Logical position and size, for drawing the arrangement.
    x: f64,
    y: f64,
    width: f64,
    height: f64,
    /// Physical pixels, for the label ("1920×1080").
    pixel_width: u32,
    pixel_height: u32,
    /// The operator's own display (the main window is on it).
    operator: bool,
    avoid: bool,
    /// This app's output windows on it (`audience`, `band-1`, …).
    windows: Vec<String>,
}

/// Every connected display, in the order the system lists them — the order
/// Identify numbers them in.
#[tauri::command]
pub fn list_screens(app: tauri::AppHandle) -> Result<Vec<ScreenInfo>, String> {
    let main = app.get_webview_window("main").ok_or("no operator window")?;
    let monitors = main.available_monitors().map_err(|e| e.to_string())?;
    let prefs = load_prefs(&app);
    let on = windows_by_display(&app);
    let main_at = main.current_monitor().ok().flatten().map(|m| *m.position());
    Ok(monitors
        .iter()
        .map(|m| {
            let scale = m.scale_factor();
            let pos = m.position().to_logical::<f64>(scale);
            let size = m.size().to_logical::<f64>(scale);
            let key = display_key(m);
            let mut windows: Vec<String> = on
                .iter()
                .filter(|(p, l)| p == m.position() && l != "main" && l != "debug")
                .map(|(_, l)| l.clone())
                .collect();
            windows.sort();
            ScreenInfo {
                name: m.name().cloned().unwrap_or_else(|| "Display".into()),
                x: pos.x,
                y: pos.y,
                width: size.width,
                height: size.height,
                pixel_width: m.size().width,
                pixel_height: m.size().height,
                operator: Some(*m.position()) == main_at,
                avoid: prefs.avoid.contains(&key),
                windows,
                key,
            }
        })
        .collect())
}

/// Move one of the app's output windows to a display, fullscreen there (or,
/// on the operator's own display, as a normal window that leaves the
/// operator screen usable), and remember the choice for next time.
#[tauri::command]
pub async fn move_window_to_screen(app: tauri::AppHandle, label: String, key: String) -> Result<(), String> {
    if label == "main" || label == "debug" {
        return Err("only the audience and band screens can be moved".into());
    }
    let win = app.get_webview_window(&label).ok_or("that window isn't open")?;
    let main = app.get_webview_window("main").ok_or("no operator window")?;
    let monitors = main.available_monitors().map_err(|e| e.to_string())?;
    let target = monitors
        .iter()
        .find(|m| display_key(m) == key)
        .ok_or("that display isn't connected any more")?;
    let operator = main.current_monitor().ok().flatten().map(|m| *m.position()) == Some(*target.position());

    // A fullscreen window has to leave fullscreen before it can move (on
    // macOS that's an animation), then go fullscreen on its new display.
    if win.is_fullscreen().unwrap_or(false) {
        win.set_fullscreen(false).map_err(|e| e.to_string())?;
        for _ in 0..30 {
            tokio::time::sleep(Duration::from_millis(50)).await;
            if !win.is_fullscreen().unwrap_or(false) {
                break;
            }
        }
        // The exit animation reports done slightly before the window can move.
        tokio::time::sleep(Duration::from_millis(250)).await;
    }
    let pos = target.position().to_logical::<f64>(target.scale_factor());
    win.set_position(tauri::LogicalPosition::new(pos.x + 40.0, pos.y + 40.0))
        .map_err(|e| e.to_string())?;
    if !operator {
        tokio::time::sleep(Duration::from_millis(150)).await;
        win.set_fullscreen(true).map_err(|e| e.to_string())?;
    }

    let mut prefs = load_prefs(&app);
    if operator {
        // Next time, the usual automatic choice rather than this screen.
        prefs.assign.remove(&label);
    } else {
        prefs.assign.insert(label, key);
    }
    save_prefs(&app, &prefs)
}

/// Mark a display "Don't use" (or clear it). Only automatic placement reads
/// it: a window already there stays until it's moved.
#[tauri::command]
pub fn set_screen_avoid(app: tauri::AppHandle, key: String, avoid: bool) -> Result<(), String> {
    let mut prefs = load_prefs(&app);
    prefs.avoid.retain(|k| k != &key);
    if avoid {
        prefs.assign.retain(|_, k| k != &key);
        prefs.avoid.push(key);
    }
    save_prefs(&app, &prefs)
}

/// Show each display's number (the order `list_screens` gives) in a small
/// window in its middle for a few seconds, so the operator can tell which
/// display is which.
#[tauri::command]
pub async fn identify_screens(app: tauri::AppHandle) -> Result<(), String> {
    let main = app.get_webview_window("main").ok_or("no operator window")?;
    let monitors = main.available_monitors().map_err(|e| e.to_string())?;
    let mut labels = Vec::new();
    for (i, m) in monitors.iter().enumerate() {
        let label = format!("identify-{}", i + 1);
        if app.get_webview_window(&label).is_some() {
            continue;
        }
        let scale = m.scale_factor();
        let pos = m.position().to_logical::<f64>(scale);
        let size = m.size().to_logical::<f64>(scale);
        let (w, h) = (360.0, 240.0);
        let name = m.name().cloned().unwrap_or_default();
        let url = format!(
            "index.html?window=identify&n={}&name={}",
            i + 1,
            urlencode(&name)
        );
        let built = tauri::WebviewWindowBuilder::new(&app, &label, tauri::WebviewUrl::App(url.into()))
            .title("Church Slides — Identify")
            .inner_size(w, h)
            .position(pos.x + (size.width - w) / 2.0, pos.y + (size.height - h) / 2.0)
            .decorations(false)
            .resizable(false)
            .always_on_top(true)
            .skip_taskbar(true)
            .focused(false)
            .build();
        match built {
            Ok(_) => labels.push(label),
            Err(e) => log::warn!("identify window on display {}: {e}", i + 1),
        }
    }
    let app2 = app.clone();
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(IDENTIFY_FOR).await;
        for label in labels {
            if let Some(w) = app2.get_webview_window(&label) {
                let _ = w.close();
            }
        }
    });
    Ok(())
}

fn urlencode(s: &str) -> String {
    s.bytes()
        .map(|b| match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => (b as char).to_string(),
            _ => format!("%{b:02X}"),
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn prefs_read_back_and_tolerate_missing_fields() {
        let p: ScreenPrefs = serde_json::from_str("{}").unwrap();
        assert!(p.assign.is_empty() && p.avoid.is_empty());
        let mut p = ScreenPrefs::default();
        p.assign.insert("audience".into(), "DELL U2720Q|3840x2160".into());
        p.avoid.push("Blackmagic|1920x1080".into());
        let back: ScreenPrefs = serde_json::from_str(&serde_json::to_string(&p).unwrap()).unwrap();
        assert_eq!(back.assign["audience"], "DELL U2720Q|3840x2160");
        assert_eq!(back.avoid, vec!["Blackmagic|1920x1080".to_string()]);
    }

    #[test]
    fn display_names_are_url_safe() {
        assert_eq!(urlencode("LG HDR 4K"), "LG%20HDR%204K");
        assert_eq!(urlencode("Built-in Retina Display"), "Built-in%20Retina%20Display");
    }
}
