# Cross-component integration tests

Tests here wire multiple subsystems together end-to-end — recording →
replay → ASR → metrics, for example. They live outside `sidecar/tests/`
because they cross the eval-harness / recording / ASR boundaries that
each unit-test suite owns separately.

Run them from the repo root:

```bash
uv run --project sidecar pytest tests/integration
```

The shared `conftest.py` adds `sidecar/` to `sys.path`, so test modules
import `auto_presenter.*` the same way unit tests do.
