from __future__ import annotations

from auto_presenter.dev.lockfile import read_lockfile, remove_lockfile, write_lockfile


def test_lockfile_roundtrip(tmp_path, monkeypatch):
    import auto_presenter.dev.lockfile as lock

    monkeypatch.setattr(lock, "_LOCK_DIR", tmp_path)
    monkeypatch.setattr(lock, "_LOCK_PATH", tmp_path / "sidecar.json")
    assert read_lockfile() is None
    write_lockfile(12345)
    data = read_lockfile()
    assert data is not None
    assert data["port"] == 12345
    assert "pid" in data
    remove_lockfile()
    assert read_lockfile() is None
