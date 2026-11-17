"""Debug-only agent-testing surface.

Loaded only when the sidecar is spawned with `--dev-tools`, which the
Tauri shell passes under `#[cfg(debug_assertions)]` and never in a
release build. Production protocol, codegen, and the React client do
not know these messages exist.

The production seams this package hangs off:

- `serve(dev_tools=True)` — dispatcher may import this package
- `restart_with_capture(ws, msg, capture)` — swap FileCapture in for the mic
- `_state.last_start_audio` / `_state.last_slide` — read-only session facts
"""
