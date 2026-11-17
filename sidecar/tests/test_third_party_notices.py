"""The licence rules behind THIRD-PARTY-NOTICES.txt: which text a package
gets, and which licences stop a build until someone decides."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "third_party_notices.py"


def _load():
    spec = importlib.util.spec_from_file_location("third_party_notices", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


tpn = _load()
Package = tpn.Package


def test_or_takes_the_preferred_alternative_and_and_takes_all():
    assert tpn.spdx_ids("MIT OR Apache-2.0") == ["MIT"]
    assert tpn.spdx_ids("Apache-2.0/MIT") == ["MIT"]
    assert tpn.spdx_ids("Zlib OR Apache-2.0 OR MIT") == ["MIT"]
    assert tpn.spdx_ids("MIT AND BSD-3-Clause") == ["MIT", "BSD-3-Clause"]


def test_dual_licensed_package_ships_only_the_elected_text(tmp_path: Path):
    files = [tmp_path / n for n in ("LICENSE-APACHE", "LICENSE-MIT", "NOTICE", "COPYRIGHT")]
    kept = tpn.elected_files(files, "MIT OR Apache-2.0")
    assert [f.name for f in kept] == ["LICENSE-MIT", "NOTICE", "COPYRIGHT"]
    # Not an OR: everything stays.
    assert tpn.elected_files(files, "Apache-2.0") == files


def test_missing_text_gets_the_standard_one_with_the_authors():
    pkg = Package("rust", "objc2", "0.6.4", "MIT")
    tpn.fill_missing(pkg, "Mads Marquart")
    assert len(pkg.texts) == 1
    assert pkg.texts[0].startswith("MIT License\n\nCopyright (c) Mads Marquart")
    assert "standard text" in pkg.notes[0]


def test_copyleft_is_flagged_unless_a_permissive_alternative_exists():
    assert tpn.flagged(Package("python", "x", license="GPL-3.0-only"))
    assert tpn.flagged(Package("python", "x", license="LGPL-2.1-or-later"))
    assert tpn.flagged(Package("python", "x", license="CC-BY-NC-4.0"))
    # r-efi's shape: we take Apache, so its LGPL text doesn't matter.
    gpl_text = "GNU LESSER GENERAL PUBLIC LICENSE\n   Version 2.1, February 1999"
    assert tpn.flagged(Package("rust", "r-efi", license="MIT OR Apache-2.0 OR LGPL-2.1-or-later",
                               texts=[gpl_text])) is None
    # A permissive declaration that bundles GPL'd code (numpy's libquadmath).
    assert tpn.flagged(Package("python", "numpy", license="BSD-3-Clause", texts=[gpl_text]))
    # Mentioning the GPL in passing isn't a GPL text.
    assert tpn.flagged(Package("npm", "y", license="MIT",
                               texts=["Unlike the GNU General Public License, this ..."])) is None


def test_unreviewed_flag_fails_and_open_one_warns(tmp_path: Path, monkeypatch):
    (tmp_path / "reviewed.json").write_text(json.dumps({"packages": {
        "python:numpy": {"status": "open", "note": "decide about libquadmath"},
        "runtime:PyInstaller bootloader": {"status": "accepted", "note": "exception"},
    }}))
    monkeypatch.setattr(tpn, "NOTICES", tmp_path)
    warnings, errors = tpn.review([
        Package("python", "numpy", license="GPL-3.0"),
        Package("runtime", "PyInstaller bootloader", license="GPL-2.0-or-later"),
        Package("python", "newthing", license="AGPL-3.0"),
        Package("python", "fine", license="MIT"),
    ])
    assert len(warnings) == 1 and "libquadmath" in warnings[0]
    assert len(errors) == 1 and errors[0].startswith("python:newthing")


def test_reviewed_file_is_valid():
    data = json.loads((tpn.NOTICES / "reviewed.json").read_text())
    for key, decision in data["packages"].items():
        assert ":" in key
        assert decision["status"] in ("accepted", "open")
        assert decision["note"]


def test_identical_texts_print_once_with_every_user():
    mit = "MIT License\n\nCopyright (c) A\n\nPermission is hereby granted"
    out = tpn.render(
        [Package("npm", "a", "1", "MIT", texts=[mit]),
         Package("npm", "b", "2", "MIT", texts=[mit.replace("\n\n", "\n  \n")])],
        "aarch64-apple-darwin", "0.0.9", None,
    )
    assert out.count("Permission is hereby granted") == 1
    assert "[1] Used by: a 1, b 2" in out
    assert "no AI engine" in out
