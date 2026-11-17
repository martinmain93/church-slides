"""Long-body parser perf guard for the canonical Python parser.

The Python parser runs once on save (not per keystroke), so the budget
is more generous than the TS-side guard. This test fires only if
something regresses by an order of magnitude.
"""

from __future__ import annotations

import time

from auto_presenter.library.parser import parse_body_text


def _build_long_body(slides: int) -> str:
    sections = ["verse 1", "chorus", "verse 2", "bridge"]
    out: list[str] = []
    for i in range(slides):
        out.append(f"[{sections[i % len(sections)]}]")
        for line in range(4):
            out.append(f"Slide {i} line {line} with some lyrics that vary a little")
        out.append("")
    return "\n".join(out)


def test_long_body_parses_quickly():
    body = _build_long_body(300)  # ~1500 lines
    assert body.count("\n") > 1500

    start = time.perf_counter()
    slides, _sections = parse_body_text(body, "song1")
    elapsed = time.perf_counter() - start

    assert len(slides) >= 300
    assert elapsed < 0.5, f"parser took {elapsed * 1000:.1f}ms"
