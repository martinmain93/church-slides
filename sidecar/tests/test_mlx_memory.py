"""The MLX buffer cache is capped, and released when a session ends.

Uncapped, MLX kept every freed Metal buffer: the sidecar's GPU memory grew
through a service (cache 2.2 GB after 300 passes, ~4 GB live after twenty
minutes) while the model's own memory stayed flat. See
docs/done/PERF_AUDIT_2026-09-26.md.
"""

from __future__ import annotations

import sys
import types

import pytest

from auto_presenter.asr import mlx_backend


class FakeMx(types.ModuleType):
    def __init__(self) -> None:
        super().__init__("mlx.core")
        self.cache_limit: int | None = None
        self.cleared = 0

    def set_cache_limit(self, n: int) -> int:
        self.cache_limit = n
        return 0

    def clear_cache(self) -> None:
        self.cleared += 1


@pytest.fixture
def fake_mx(monkeypatch: pytest.MonkeyPatch) -> FakeMx:
    mx = FakeMx()
    pkg = types.ModuleType("mlx")
    pkg.core = mx  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mlx", pkg)
    monkeypatch.setitem(sys.modules, "mlx.core", mx)
    monkeypatch.setattr(mlx_backend, "_memory_configured", False)
    return mx


def test_caps_the_cache_once(fake_mx: FakeMx) -> None:
    mlx_backend._configure_mlx_memory()
    assert fake_mx.cache_limit == mlx_backend.MLX_CACHE_LIMIT_BYTES
    fake_mx.cache_limit = None
    mlx_backend._configure_mlx_memory()
    assert fake_mx.cache_limit is None  # not re-applied every pass


def test_release_clears_the_cache(fake_mx: FakeMx) -> None:
    mlx_backend.release_cache()
    assert fake_mx.cleared == 1


def test_release_does_not_import_mlx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "mlx.core", raising=False)
    mlx_backend.release_cache()
    assert "mlx.core" not in sys.modules
