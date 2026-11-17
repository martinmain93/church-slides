//! Spawning and managing the Python sidecar process.
//!
//! In dev builds, the sidecar runs as `uv run python -m auto_presenter` from the
//! repo's `sidecar/` directory so Python edits are picked up without rebundling.
//! In release builds, it runs the PyInstaller-frozen binary that
//! `scripts/build_sidecar.py` stages into `src-tauri/resources/sidecar/` and
//! `tauri.conf.json` bundles as a resource — a release build never looks at
//! the source tree, which on any machine but the one that compiled it isn't
//! there.
//!
//! `AUTO_PRESENTER_SIDECAR` overrides both: a path to a sidecar executable, or
//! `bundled` to make a dev build run the staged frozen binary (how to test the
//! release path without a full `tauri build`).

use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::Arc;

use anyhow::{anyhow, Context, Result};
use serde::Serialize;
use tauri::{AppHandle, Manager};
use std::time::{Duration, Instant};

use tokio::io::{AsyncBufReadExt, BufReader, Lines};
use tokio::process::{Child, ChildStdout, Command};
use tokio::sync::watch;

/// Where the AI engine is, as the UI sees it. Everything except live AI works
/// in every state — the library, presenting, the band screens — so `Failed`
/// is a reason to show, not an error to surface as a crash.
#[derive(Clone, Debug, PartialEq, Serialize)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum EngineState {
    Starting,
    Ready { port: u16 },
    /// Never started, or stopped. `reason` is written for the operator.
    Failed { reason: String },
}

/// How long an engine has to stay up before its exit counts as a one-off
/// rather than part of a crash loop.
const STABLE_AFTER: Duration = Duration::from_secs(60);
/// Crashes in a row (each within `STABLE_AFTER` of starting) before giving up.
const MAX_QUICK_CRASHES: u32 = 5;

/// Wait before relaunch attempt `n` (1-based): 1 s, 2 s, 4 s, 8 s, 16 s.
fn restart_delay(n: u32) -> Duration {
    Duration::from_secs(1u64 << n.saturating_sub(1).min(4))
}

/// Start the sidecar and keep `state` current for its whole life — and bring
/// it back if it dies mid-service. With nobody at the laptop, "restart the app
/// to bring AI back" meant no AI for the rest of the service.
///
/// A relaunched engine binds a new port; the UI's connection re-resolves the
/// port on reconnect, notices the change, and restarts the service's session
/// (see `ws.ts` / `recordingClient.ts`). An engine that can't start at all
/// (missing, unsupported macOS) fails once, as before; one that keeps dying
/// soon after starting is given up on after `MAX_QUICK_CRASHES`.
pub async fn run_sidecar(app: AppHandle, db_path: &Path, state: watch::Sender<EngineState>) {
    let mut quick_crashes = 0u32;
    let mut ever_started = false;
    loop {
        let tail = Arc::new(crate::telemetry::EngineTail::default());
        match spawn_sidecar(app.clone(), db_path, tail.clone()).await {
            Ok(Running { port, mut child, mut lines }) => {
                log::info!("sidecar listening on 127.0.0.1:{port}");
                ever_started = true;
                let started = Instant::now();
                let _ = state.send(EngineState::Ready { port });
                while let Ok(Some(line)) = lines.next_line().await {
                    tail.push(&line);
                    log::info!(target: "engine", "{line}");
                }
                let status = child.wait().await;
                log::warn!("sidecar exited: {:?}", status);
                // The engine only exits on its own when the app is going away
                // (it watches our pid), and then this report never leaves.
                // Any exit that gets this far is a crash.
                crate::telemetry::report_engine_exit(
                    &match &status {
                        Ok(s) => s.to_string(),
                        Err(e) => e.to_string(),
                    },
                    &tail,
                );
                if started.elapsed() >= STABLE_AFTER {
                    quick_crashes = 0;
                }
                quick_crashes += 1;
                if quick_crashes > MAX_QUICK_CRASHES {
                    let _ = state.send(EngineState::Failed {
                        reason: format!(
                            "The AI engine keeps stopping ({}), so live AI is off. Presenting \
                             still works by hand; restart the app to try again.",
                            match status {
                                Ok(s) => s.to_string(),
                                Err(e) => e.to_string(),
                            }
                        ),
                    });
                    return;
                }
            }
            Err(err) => {
                log::error!("failed to spawn sidecar: {err:#}");
                // Never came up at all: a relaunch won't change that.
                if !ever_started {
                    let _ = state.send(EngineState::Failed { reason: format!("{err:#}") });
                    return;
                }
                quick_crashes += 1;
                if quick_crashes > MAX_QUICK_CRASHES {
                    let _ = state.send(EngineState::Failed { reason: format!("{err:#}") });
                    return;
                }
            }
        }
        let delay = restart_delay(quick_crashes);
        log::warn!("restarting the AI engine in {delay:?} (attempt {quick_crashes})");
        let _ = state.send(EngineState::Starting);
        tokio::time::sleep(delay).await;
    }
}

/// A sidecar that has announced its port: the process, and the rest of its
/// stdout (forwarded to the log until it exits).
struct Running {
    port: u16,
    child: Child,
    lines: Lines<BufReader<ChildStdout>>,
}

/// Spawns the sidecar and returns once it announces its port on stdout.
/// `db_path` is forwarded so the Python side reads from the same SQLite file
/// Rust writes to — without it, Python falls back to its own `app_data_dir()`
/// which can drift from the Tauri bundle identifier.
async fn spawn_sidecar(
    app: AppHandle,
    db_path: &Path,
    tail: Arc<crate::telemetry::EngineTail>,
) -> Result<Running> {
    let (mut cmd, workdir) = sidecar_command(&app)?;
    // The engine reports its own exceptions (`error_reports.py`), to the
    // same project and release — and only from a build that reports at all.
    // Whether the user allows it, it reads from the settings in `db_path`.
    if let Some(dsn) = crate::telemetry::DSN.filter(|_| crate::telemetry::active_in_this_build()) {
        cmd.env("CHURCH_SLIDES_SENTRY_DSN", dsn)
            .env("CHURCH_SLIDES_RELEASE", crate::telemetry::release())
            .env("CHURCH_SLIDES_ENVIRONMENT", crate::telemetry::environment())
            .env("CHURCH_SLIDES_INSTALL_ID", crate::telemetry::install_id());
        if let Some(dir) = crate::diagnostics::log_dir() {
            cmd.env("CHURCH_SLIDES_LOG_FILE", dir.join("church-slides.log"));
        }
    }
    cmd.arg("--port")
        .arg("0")
        .arg("--db-path")
        .arg(db_path)
        // The sidecar exits on its own when we go away. This is the mechanism
        // that actually works, because the two obvious ones do not:
        //
        // `kill_on_drop` below never fires — the handle is parked in the
        // supervisor loop (`run_sidecar`) and the app exits without dropping
        // it. And even a successful kill would hit the wrong
        // process: in dev this command is `uv run python -m auto_presenter`,
        // so the handle we own is `uv` and the interpreter is a *grandchild*
        // that survives its parent. Neither survives a force-quit at all.
        //
        // Left unfixed this leaked 46 processes over 12 days, three of them
        // holding a loaded Whisper model and burning ~3.5 cores between them —
        // competing with the live pipeline for the CPU that keeps ASR passes
        // inside their budget.
        .arg("--parent-pid")
        .arg(std::process::id().to_string());
    // Speech models in the app's own folder, beside the library, rather than
    // the Hugging Face cache every tool shares: there they outlived the app
    // and nothing tied them to it. The engine moves across any an earlier
    // version left in the shared cache (asr/model_home.py). Development runs
    // keep the shared cache, which the eval scripts read too.
    #[cfg(not(debug_assertions))]
    if let Some(app_dir) = db_path.parent() {
        cmd.arg("--models-dir")
            .arg(app_dir.join("models").join("speech"));
    }
    // Agent-testing verbs (inject_audio, session_snapshot). Compiled out of
    // `tauri build` so a production sidecar never exposes them; the
    // performance suite's `ui-driver` build plays audio through them.
    #[cfg(any(debug_assertions, feature = "ui-driver"))]
    cmd.arg("--dev-tools");
    // No console window flashing up behind the app on Windows.
    #[cfg(windows)]
    cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    let mut child = cmd
        .current_dir(&workdir)
        .stdout(Stdio::piped())
        // Piped into the app's log file: inherited, it went wherever the
        // app's own stderr went — nowhere, for an app opened from Finder.
        .stderr(Stdio::piped())
        .kill_on_drop(true)
        .spawn()
        .with_context(|| {
            format!(
                "The AI engine couldn't start on this computer, so live AI is off. \
                 Everything else works as normal. (Starting it in {} failed.)",
                workdir.display()
            )
        })?;

    if let Some(stderr) = child.stderr.take() {
        tokio::spawn(async move {
            let mut lines = BufReader::new(stderr).lines();
            let mut level = log::Level::Info;
            while let Ok(Some(line)) = lines.next_line().await {
                tail.push(&line);
                level = crate::diagnostics::engine_line_level(&line, level);
                log::log!(target: "engine", level, "{line}");
            }
        });
    }

    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| anyhow!("sidecar stdout missing"))?;
    let mut lines = BufReader::new(stdout).lines();

    // The sidecar prints a JSON line like {"event":"listening","port":54321}
    // as its first stdout output. We block on that line, then forward the
    // rest of stdout to logs in a background task.
    let port = loop {
        let line = lines
            .next_line()
            .await
            .context("reading sidecar stdout")?
            .ok_or_else(|| {
                anyhow!(
                    "The AI engine couldn't start on this computer, so live AI is off. \
                     Everything else works as normal. (It exited during startup — the app's \
                     log has the details.)"
                )
            })?;
        if let Some(p) = parse_listening_line(&line) {
            break p;
        }
        log::info!(target: "engine", "{line}");
    };

    Ok(Running { port, child, lines })
}

/// File name of the frozen sidecar inside the bundled `sidecar/` resource.
const FROZEN_EXE: &str = if cfg!(windows) {
    "auto-presenter-sidecar.exe"
} else {
    "auto-presenter-sidecar"
};

/// The command that starts the sidecar (without its arguments), and the
/// directory to run it in.
fn sidecar_command(app: &AppHandle) -> Result<(Command, PathBuf)> {
    match std::env::var("AUTO_PRESENTER_SIDECAR").ok().as_deref() {
        Some("bundled") => return frozen_command(bundled_sidecar(app)?),
        Some(path) if !path.is_empty() => return frozen_command(PathBuf::from(path)),
        _ => {}
    }
    if cfg!(debug_assertions) {
        dev_command()
    } else {
        frozen_command(bundled_sidecar(app)?)
    }
}

/// Oldest macOS the frozen engine loads on — `MACOS_FLOOR` in
/// scripts/build_sidecar.py, which fails the build if any bundled library
/// needs newer. The app itself runs from 10.15; this is only live AI's floor.
const ENGINE_MACOS_FLOOR: (u32, u32) = (11, 0);

/// Why live AI can't run on this machine at all, if it can't — checked
/// before spawning, so the operator reads "needs macOS 11" rather than an
/// engine that died at load with a dyld error.
fn platform_blocker() -> Option<String> {
    if !cfg!(target_os = "macos") {
        return None;
    }
    let out = std::process::Command::new("sw_vers")
        .arg("-productVersion")
        .output()
        .ok()?;
    let version = String::from_utf8_lossy(&out.stdout).trim().to_string();
    let found = parse_macos_version(&version)?;
    (found < ENGINE_MACOS_FLOOR).then(|| {
        format!(
            "Live AI needs macOS {}.{} or later; this Mac has {version}. \
             Everything else — the library, presenting by hand, band screens — works as normal.",
            ENGINE_MACOS_FLOOR.0, ENGINE_MACOS_FLOOR.1
        )
    })
}

fn parse_macos_version(v: &str) -> Option<(u32, u32)> {
    let mut parts = v.split('.');
    let major = parts.next()?.parse().ok()?;
    let minor = parts.next().and_then(|m| m.parse().ok()).unwrap_or(0);
    Some((major, minor))
}

fn bundled_sidecar(app: &AppHandle) -> Result<PathBuf> {
    if let Some(reason) = platform_blocker() {
        return Err(anyhow!(reason));
    }
    let dir = app
        .path()
        .resource_dir()
        .context("resolving the app's resource directory")?
        .join("sidecar");
    let exe = dir.join(FROZEN_EXE);
    if !exe.is_file() {
        return Err(anyhow!(
            "This copy of the app was built without the AI engine, so live AI is off. \
             Everything else works as normal. (Looked for {}.)",
            exe.display()
        ));
    }
    Ok(exe)
}

fn frozen_command(exe: PathBuf) -> Result<(Command, PathBuf)> {
    let workdir = exe
        .parent()
        .ok_or_else(|| anyhow!("sidecar path has no parent: {}", exe.display()))?
        .to_path_buf();
    Ok((Command::new(exe), workdir))
}

/// `uv run` from the repo, so Python edits apply on the next launch.
/// `CARGO_MANIFEST_DIR` is the compiling machine's source tree — which is why
/// this is only ever the dev path.
fn dev_command() -> Result<(Command, PathBuf)> {
    let sidecar_dir = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .ok_or_else(|| anyhow!("could not find sidecar dir"))?
        .join("sidecar");
    let mut cmd = Command::new("uv");
    cmd.args(["run", "python", "-m", "auto_presenter"]);
    Ok((cmd, sidecar_dir))
}

fn parse_listening_line(line: &str) -> Option<u16> {
    let value: serde_json::Value = serde_json::from_str(line).ok()?;
    if value.get("event")?.as_str()? != "listening" {
        return None;
    }
    value.get("port")?.as_u64().and_then(|p| u16::try_from(p).ok())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn restart_delays_back_off_and_cap() {
        let d: Vec<u64> = (1..=7).map(|n| restart_delay(n).as_secs()).collect();
        assert_eq!(d, vec![1, 2, 4, 8, 16, 16, 16]);
    }

    #[test]
    fn parses_listening_line() {
        assert_eq!(
            parse_listening_line(r#"{"event":"listening","port":54321}"#),
            Some(54321)
        );
    }

    #[test]
    fn engine_state_serialises_as_the_ui_reads_it() {
        // `src/lib/aiEngineStatus.ts` switches on `state`.
        let ready = serde_json::to_value(EngineState::Ready { port: 7 }).unwrap();
        assert_eq!(ready, serde_json::json!({"state": "ready", "port": 7}));
        let failed = serde_json::to_value(EngineState::Failed { reason: "x".into() }).unwrap();
        assert_eq!(failed, serde_json::json!({"state": "failed", "reason": "x"}));
        let starting = serde_json::to_value(EngineState::Starting).unwrap();
        assert_eq!(starting, serde_json::json!({"state": "starting"}));
    }

    #[test]
    fn reads_macos_versions() {
        assert_eq!(parse_macos_version("10.15.7"), Some((10, 15)));
        assert_eq!(parse_macos_version("11.0"), Some((11, 0)));
        assert_eq!(parse_macos_version("26"), Some((26, 0)));
        assert!(parse_macos_version("10.15.7").unwrap() < ENGINE_MACOS_FLOOR);
        assert!(parse_macos_version("11.7.10").unwrap() >= ENGINE_MACOS_FLOOR);
        assert_eq!(parse_macos_version("garbage"), None);
    }

    #[test]
    fn rejects_other_events() {
        assert_eq!(
            parse_listening_line(r#"{"event":"other","port":1}"#),
            None
        );
        assert_eq!(parse_listening_line("not json"), None);
    }
}
