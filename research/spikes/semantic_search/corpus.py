"""Load the library copy, split songs into slides, and replicate today's keyword search."""
import os
import re
import sqlite3
from pathlib import Path

HERE = Path(__file__).parent
# A library to measure on. The labels in queries.py are for one church's real
# library (326 songs); point this at a copy of yours to rerun them.
DB = Path(os.environ.get("SEMSEARCH_DB", HERE / "library.db"))

# Not songs: loops, announcements, sermon notes, test entries.
NON_SONGS = re.compile(
    r"^(annoucment loop|announcements|welcome loop|loop|sunday_stuff|testing 123|scripture|"
    r"christmas eve message 2018|list of fears.*|sept 2nd 2018|multiply 2019)$",
    re.I,
)


def conn():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def slides_of(body: str) -> list[str]:
    """Blank-line separated blocks, section tags dropped, identical blocks deduped."""
    out, seen = [], set()
    for block in re.split(r"\n\s*\n", body):
        lines = [l.strip() for l in block.splitlines() if l.strip() and not re.match(r"^\[.*\]$", l.strip())]
        if not lines:
            continue
        text = " / ".join(lines)
        key = re.sub(r"\W+", " ", text.lower()).strip()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def load_songs():
    rows = conn().execute("SELECT id, title, body_text FROM songs").fetchall()
    songs = []
    for r in rows:
        if NON_SONGS.match(r["title"].strip()):
            continue
        sl = slides_of(r["body_text"])
        if sl:
            songs.append({"id": r["id"], "title": r["title"], "slides": sl})
    return songs


def _words(t):
    return [w.lower() for w in re.split(r"[^\w]+", t) if w]


def _title_tier(title, terms):
    tw = _words(title)
    joined, wanted = " ".join(tw), " ".join(terms)
    if wanted and joined == wanted:
        return 0
    if wanted and joined.startswith(wanted):
        return 1
    if terms and all(any(w.startswith(t) for w in tw) for t in terms):
        return 2
    return 3


def keyword_search(q: str) -> list[str]:
    """Same as db.rs search_songs: prefix-AND FTS5, bm25(10,1), then title tier."""
    toks = ["".join(ch for ch in raw if ch.isalnum()) for raw in q.split()]
    expr = " ".join(t + "*" for t in toks if t)
    if not expr:
        return []
    rows = conn().execute(
        "SELECT s.id, s.title FROM songs s JOIN songs_fts ON songs_fts.rowid = s.rowid "
        "WHERE songs_fts MATCH ? ORDER BY bm25(songs_fts, 10.0, 1.0)",
        (expr,),
    ).fetchall()
    terms = _words(q)
    rows = sorted(rows, key=lambda r: _title_tier(r["title"], terms))  # stable
    return [r["id"] for r in rows if not NON_SONGS.match(r["title"].strip())]


def title_tier(title, q):
    return _title_tier(title, _words(q))


if __name__ == "__main__":
    s = load_songs()
    n = sum(len(x["slides"]) for x in s)
    print(len(s), "songs", n, "slides")
    print(s[0]["title"], s[0]["slides"][:3])
