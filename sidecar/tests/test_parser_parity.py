"""Python-side parity test against the golden fixture JSON.

The TS-side mirror (`src/__tests__/parseSong.test.ts`) reads the same
JSON. If parser behavior changes intentionally, regenerate via:

    uv run --project sidecar python scripts/regen_parser_fixtures.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from auto_presenter.library.parser import parse_body_text


REPO_ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = REPO_ROOT / "data" / "fixtures" / "parser_cases.json"
EXPECTED_PATH = REPO_ROOT / "data" / "fixtures" / "parser_cases.expected.json"


def _cases() -> list[tuple[dict, dict]]:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    expected_by_name = {e["name"]: e for e in json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))}
    return [(c, expected_by_name[c["name"]]) for c in cases]


@pytest.mark.parametrize(("case", "expected"), _cases(), ids=lambda x: x.get("name", ""))
def test_python_matches_golden(case: dict, expected: dict) -> None:
    slides, sections = parse_body_text(case["body"], "song1")
    actual = {
        "slides": [
            {
                "section": {"type": s.section.type, "number": s.section.number},
                "order_in_song": s.order_in_song,
                "order_in_section": s.order_in_section,
                "lyrics": s.lyrics,
                "words": s.words,
            }
            for s in slides
        ],
        "section_order": [{"type": x.type, "number": x.number} for x in sections],
    }
    assert actual == {
        "slides": expected["slides"],
        "section_order": expected["section_order"],
    }
