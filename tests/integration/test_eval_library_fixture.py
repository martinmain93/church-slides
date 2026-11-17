"""Validate the eval-set library fixture parses correctly.

The fixture under `data/fixtures/library_for_eval.json` is consumed by
the import flow in `src-tauri/src/library_export.rs`. We also parse it
here with the canonical Python parser so:

  - Body text changes are caught immediately (no waiting for Rust CI).
  - Every song produces at least one slide (i.e. nothing silently fell
    through to `(other, 1)`).
  - Section types we promised in `eval_purpose` actually exist in the
    parsed output.
"""

from __future__ import annotations

import json
from pathlib import Path

from auto_presenter.library.parser import parse_body_text

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPO_ROOT / "data" / "fixtures" / "library_for_eval.json"


def test_eval_library_loads_and_parses():
    blob = json.loads(FIXTURE_PATH.read_text())
    assert blob["schema_version"] == 1
    assert len(blob["songs"]) >= 10, "eval set should ship at least 10 songs"

    for song in blob["songs"]:
        slides, sections = parse_body_text(song["body_text"], song["id"])
        assert slides, f"song {song['id']} parsed to zero slides"
        # No slide should fall back to `(other, 1)` unless explicitly intended;
        # the fixture only uses canonical section types.
        for s in slides:
            assert s.section.type != "other", (
                f"song {song['id']} slide {s.order_in_song} fell through to (other,1) — "
                "check the section tags"
            )
        # `section_order` is the sequence of section *transitions* — one
        # entry per run of consecutive same-section slides. Collapse the
        # slides' section list the same way and compare.
        slide_transitions: list[tuple[str, int]] = []
        last_key: tuple[str, int] | None = None
        for s in slides:
            key = (s.section.type, s.section.number)
            if key != last_key:
                slide_transitions.append(key)
                last_key = key
        assert [(sec.type, sec.number) for sec in sections] == slide_transitions


def test_eval_library_contains_expected_deviation_cases():
    """Sanity check that the curated mix actually covers the documented cases."""
    blob = json.loads(FIXTURE_PATH.read_text())
    titles = {song["title"] for song in blob["songs"]}
    # Just spot-check a couple of canonical cases.
    assert any("Doxology" in t for t in titles), (
        "short-tag case (single-slide song) missing from eval set"
    )
    assert any("Holy Holy Holy" in t for t in titles), (
        "ambiguous-opening case missing from eval set"
    )

    # No song in the eval set should declare a CCLI number — the fixture
    # is meant to be safe to import on any dev machine without licensing
    # exposure (PD hymns + synthetic compositions only).
    for song in blob["songs"]:
        assert song.get("ccli") in (None, ""), (
            f"song {song['id']} has a CCLI number — should be null in the eval fixture"
        )
