# Church Slides sidecar

The Python AI/ASR engine that the Tauri shell spawns at app launch.

## Run directly

```sh
cd sidecar
uv sync
uv run python -m auto_presenter --port 0
```

The sidecar prints a JSON line `{"event":"listening","port":<n>}` on stdout, then
serves WebSocket clients on `ws://127.0.0.1:<n>/`.

## Tests

```sh
uv run pytest
```
