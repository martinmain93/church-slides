#!/usr/bin/env python3
"""Find song lyrics in a source tree, by checking it against a real library.

Any run of 7 words that appears in a lyric line of a song in the library is a
hit. Songs marked public domain are skipped. Run it on the public export
before publishing; hits are listed by song and by file, for a person to judge
(a quoted song title or a line from a hymn the library doesn't mark public
domain is fine). Runs already reviewed and allowed are in lyrics_allow.txt.

    python3 scripts/public_release/scan_lyrics.py TREE [--db LIBRARY.db]

The library defaults to the app's own (macOS path). It's opened read-only.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

N = 7
TEXT_SUFFIXES = {
    ".md", ".txt", ".py", ".ts", ".tsx", ".js", ".mjs", ".rs", ".json", ".toml",
    ".yml", ".yaml", ".html", ".css", ".sql", ".sh", ".swift", ".csv", ".xml", "",
}  # fmt: skip
DEFAULT_DB = Path.home() / "Library/Application Support/com.churchslides.app/library.db"
ALLOW = Path(__file__).with_name("lyrics_allow.txt")


def words(text: str) -> list[str]:
    # Escaped line breaks in code and JSON strings ("...\nAll my life...")
    # would otherwise glue two words together and hide the line.
    text = re.sub(r"\\[nrt]", " ", text)
    return re.findall(r"[a-z0-9']+", text.lower().replace("’", "'"))


def shingles(ws: list[str]) -> set[tuple[str, ...]]:
    return {tuple(ws[i : i + N]) for i in range(len(ws) - N + 1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tree", type=Path)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    a = ap.parse_args()
    if not a.db.is_file():
        print(f"no library at {a.db}", file=sys.stderr)
        return 2

    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    index: dict[tuple[str, ...], str] = {}
    skipped = 0
    for title, body, copyright_ in con.execute("select title, body_text, coalesce(copyright,'') from songs"):
        if "public domain" in copyright_.lower():
            skipped += 1
            continue
        for line in (body or "").splitlines():
            if line.strip().startswith("["):
                continue
            for s in shingles(words(line)):
                index.setdefault(s, title)
    # Each allowed line is a phrase; every 7-word run inside it is allowed.
    allowed = {
        s
        for line in ALLOW.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
        for s in shingles(words(line))
    }
    for s in allowed:
        index.pop(s, None)
    print(f"{len(index)} lyric shingles from the library ({skipped} public-domain songs skipped, "
          f"{len(allowed)} reviewed runs allowed)")

    by_song: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for path in sorted(a.tree.rglob("*")):
        if not path.is_file() or ".git" in path.parts or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for s in shingles(words(text)):
            if s in index:
                by_song[index[s]][str(path.relative_to(a.tree))].add(" ".join(s))

    if not by_song:
        print("no lyrics found")
        return 0
    for song, files in sorted(by_song.items()):
        print(f"\n{song}:")
        for f, hits in sorted(files.items()):
            print(f"  {f} ({len(hits)} runs): \"{sorted(hits)[0]}\"")
    return 1


if __name__ == "__main__":
    sys.exit(main())
