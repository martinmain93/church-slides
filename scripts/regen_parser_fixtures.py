"""Regenerate `data/fixtures/parser_cases.expected.json`.

Run after intentional parser behavior changes:

    uv run --project sidecar python scripts/regen_parser_fixtures.py

The committed `parser_cases.expected.json` is what both Python and TS
parser parity tests assert against.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "sidecar"))

from auto_presenter.library.parser import parse_body_text  # noqa: E402

CASES_PATH = REPO_ROOT / "data" / "fixtures" / "parser_cases.json"
EXPECTED_PATH = REPO_ROOT / "data" / "fixtures" / "parser_cases.expected.json"


def main() -> int:
    cases = json.loads(CASES_PATH.read_text())
    out = []
    for case in cases:
        slides, sections = parse_body_text(case["body"], "song1")
        out.append(
            {
                "name": case["name"],
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
                "section_order": [
                    {"type": x.type, "number": x.number} for x in sections
                ],
            }
        )
    EXPECTED_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {EXPECTED_PATH} ({len(out)} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
