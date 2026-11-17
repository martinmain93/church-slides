"""Nothing GPL ships in the engine: packages known to be GPL must not be
imported by the app's own code (the frozen build also checks FFmpeg)."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "auto_presenter"
#: GPL packages that were (or could easily be) pulled in, with what replaced them
GPL = {
    "cmudict": "auto_presenter.index.phonemic.load_cmudict (its data is BSD-style)",
    "av": "nothing: the engine passes Whisper arrays, never files",
}


def test_the_app_never_imports_a_gpl_package():
    found = []
    for path in PACKAGE.rglob("*.py"):
        if path.name == "self_check.py":
            continue  # imports `av` on purpose: to check it is the stub
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            for name in names:
                if name.split(".")[0] in GPL:
                    found.append(f"{path.relative_to(PACKAGE)}: {name}")
    assert not found, found


def test_the_pronouncing_dictionary_loads_from_its_bundled_data():
    from auto_presenter.index.phonemic import CMUDICT, load_cmudict

    assert CMUDICT.exists() and (CMUDICT.parent / "cmudict.LICENSE").exists()
    d = load_cmudict()
    assert d["amazing"] == [["AH0", "M", "EY1", "Z", "IH0", "NG"]]
    assert len(d["a"]) == 2  # "a(2)" is a variant of "a"
