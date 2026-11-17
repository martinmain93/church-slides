"""The Open Hymnal Project (openhymnal.org): search it, and fetch one hymn.

Nothing from the Open Hymnal ships inside the app. A church searches it and
adds the hymns it wants; each one is fetched at that moment, from the Open
Hymnal itself. The project places its own work — the compilation, indexes and
data files — in the public domain (its "Copying" page), and states each
hymn's copyright, part by part, in the hymn's own file.

Three pages make the index, fetched together and cached for a week:

    genindex.html   every hymn: title, file name, author, tune, other titles
    alllyrics.html  every hymn's words, credits and copyright line
    people.html     authors and translators, with birth and death years

so search results can say "Public domain" or "Free for worship" before
anything is fetched. Adding a hymn then fetches its ABC score
(`Abc/<name>.abc`), whose `%OHAUTHOR`/`%OHTRANSLATOR` lines are the
authority on who wrote it and when they died.

**Plain HTTP, deliberately.** openhymnal.org's HTTPS certificate doesn't cover
the domain (it fails verification), and the site serves everything over
HTTP. What comes back is hymn text that the operator reads before adding it,
so the exposure is small; it is still a reason to show the preview first.
"""

from __future__ import annotations

import html
import json
import logging
import re
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

from auto_presenter.config import app_data_dir
from auto_presenter.hymns.abc import decode, parse_abc, parse_people, to_song
from auto_presenter.hymns.rights import Contributor, Rights, classify, words_years

log = logging.getLogger(__name__)

BASE_URL = "http://openhymnal.org/"
INDEX_MAX_AGE_SECONDS = 7 * 24 * 3600
#: Bumped when the cached index's shape changes, so an old cache is rebuilt.
INDEX_VERSION = 1

_ROW_RE = re.compile(
    r"new_window\('Lyrics/([^']+)\.html'\)\">(.*?)</a>.*?"
    r'class="author">(.*?)</p>.*?class="tune">(.*?)</p>',
    re.S,
)
_SEE_RE = re.compile(r'<p class="western">(.*?)\s+see\s+(.*?)</p>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_ANON_RE = re.compile(r"\b(unknown|traditional|anonymous|latin|greek|german hymn)\b", re.I)


@dataclass
class HymnEntry:
    """One hymn in the search index."""

    id: str
    title: str
    also_known_as: list[str] = field(default_factory=list)
    author: str = ""
    tune: str = ""
    first_line: str = ""
    words_credit: str = ""
    copyright: str = ""
    status: str = ""
    status_label: str = ""
    explanation: str = ""


@dataclass
class HymnSong:
    """A hymn ready to become a song in the library."""

    id: str
    title: str
    body_text: str
    slide_order: str
    author: str
    copyright: str
    status: str
    status_label: str
    explanation: str
    source_url: str


def _text(fragment: str) -> str:
    return " ".join(html.unescape(_TAG_RE.sub(" ", fragment)).split())


def normalize_title(title: str) -> str:
    """For matching one index's titles against another's."""
    title = re.sub(r"\(.*?\)", "", title)
    title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


# --- Parsing the three index pages -----------------------------------------


def parse_genindex(page: str) -> list[HymnEntry]:
    entries: dict[str, HymnEntry] = {}
    for slug, title, author, tune in _ROW_RE.findall(page):
        if slug in entries:
            continue
        entries[slug] = HymnEntry(
            id=slug, title=_text(title), author=_text(author), tune=_text(tune)
        )
    by_title = {normalize_title(e.title): e for e in entries.values()}
    for alias, target in _SEE_RE.findall(page):
        entry = by_title.get(normalize_title(_text(target)))
        if entry is not None:
            entry.also_known_as.append(_text(alias))
    return list(entries.values())


@dataclass
class _LyricsInfo:
    first_line: str
    words_credit: str
    copyright: str
    also_known_as: list[str]


def parse_alllyrics(page: str) -> dict[str, _LyricsInfo]:
    """Normalized title -> first line, words credit, copyright line."""
    out: dict[str, _LyricsInfo] = {}
    for block in re.split(r"<h3>", page)[1:]:
        head, _, body = block.partition("</h3>")
        title = _text(head)
        aka = re.findall(r"also known as (.*?)\)?$", title)
        aka_list = [a.strip() for a in re.split(r"\s+or\s+", aka[0])] if aka else []
        verses = re.findall(r"<p>\s*1\.\s*(.*?)</p>", body, re.S)
        first = _text(verses[0]) if verses else ""
        credits_html = (re.findall(r"<i>(.*?)</i>", body, re.S) or [""])[0]
        credits = [_text(c) for c in re.split(r"<br\s*/?>", credits_html)]
        words = " ".join(c for c in credits if re.match(r"(?i)(words|translat|text)", c))
        cr = next((c for c in credits if c.lower().startswith("copyright")), "")
        out[normalize_title(title)] = _LyricsInfo(
            first_line=_first_line(first),
            words_credit=words,
            copyright=cr,
            also_known_as=aka_list,
        )
    return out


def _first_line(verse: str) -> str:
    """The opening of verse 1, up to its first punctuation."""
    match = re.match(r"(.{12,70}?[,;:.!?])\s", verse + " ")
    return (match.group(1) if match else verse[:60]).rstrip(",;:")


def parse_people_index(page: str) -> dict[str, list[tuple[Contributor, str]]]:
    """Normalized title -> [(person, role)] for authors and translators."""
    text = html.unescape(_TAG_RE.sub("\n", page))
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    out: dict[str, list[tuple[Contributor, str]]] = {}
    person: Contributor | None = None
    for index, line in enumerate(lines):
        if re.search(r"\((?:c\.\s*)?\d{0,4}\??\s*-\s*(?:c\.\s*)?\d{0,4}\??\)$", line):
            parsed = parse_people(line)
            person = Contributor(parsed[0].name, parsed[0].died) if parsed else None
            continue
        role = re.match(r"^\(as (.+)\)$", line)
        if role and person is not None and index > 0 and role.group(1) in ("author", "translator"):
            out.setdefault(normalize_title(lines[index - 1]), []).append((person, role.group(1)))
    return out


def build_index(genindex: str, alllyrics: str, people: str) -> list[HymnEntry]:
    entries = parse_genindex(genindex)
    lyrics = parse_alllyrics(alllyrics)
    contributors = parse_people_index(people)
    for entry in entries:
        key = normalize_title(entry.title)
        info = lyrics.get(key)
        if info is not None:
            entry.first_line = info.first_line
            entry.words_credit = info.words_credit
            entry.copyright = info.copyright
            for aka in info.also_known_as:
                if aka not in entry.also_known_as:
                    entry.also_known_as.append(aka)
        people = [p for p, _ in contributors.get(key, [])]
        rights = classify(
            entry.copyright,
            people,
            words_years(entry.words_credit),
            anonymous=bool(_ANON_RE.search(entry.words_credit)),
        )
        entry.status, entry.status_label, entry.explanation = (
            rights.status,
            rights.label,
            rights.explanation,
        )
    return sorted(entries, key=lambda e: normalize_title(e.title))


# --- Fetching, with a cache ------------------------------------------------


def _cache_path() -> Path:
    path = app_data_dir() / "hymns"
    path.mkdir(parents=True, exist_ok=True)
    return path / "openhymnal-index.json"


def _fetch(path: str, timeout: float = 30.0) -> bytes:
    from auto_presenter.http_client import open_get

    with open_get(BASE_URL + path, timeout_seconds=timeout) as response:
        return response.read()


_index_lock = threading.Lock()


def load_index(*, refresh: bool = False) -> tuple[list[HymnEntry], float]:
    """The search index and when it was fetched. Fetches when stale or asked.

    Offline with a cache: the cache, however old — searching last month's list
    beats searching nothing. Offline without one: raises `OSError`.
    """
    with _index_lock:
        path = _cache_path()
        cached = _read_cache(path)
        fresh = cached is not None and time.time() - cached[1] < INDEX_MAX_AGE_SECONDS
        if cached is not None and fresh and not refresh:
            return cached
        try:
            pages = [decode(_fetch(p)) for p in ("genindex.html", "alllyrics.html", "people.html")]
            entries = build_index(*pages)
        except OSError:
            if cached is not None:
                log.info("Open Hymnal unreachable; using the cached index")
                return cached
            raise
        fetched = time.time()
        path.write_text(
            json.dumps(
                {
                    "version": INDEX_VERSION,
                    "fetched": fetched,
                    "entries": [asdict(e) for e in entries],
                }
            ),
            encoding="utf-8",
        )
        return entries, fetched


def _read_cache(path: Path) -> tuple[list[HymnEntry], float] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != INDEX_VERSION:
            return None
        return [HymnEntry(**e) for e in data["entries"]], float(data["fetched"])
    except (OSError, ValueError, TypeError, KeyError):
        return None


def search(entries: list[HymnEntry], query: str, limit: int = 400) -> list[HymnEntry]:
    """Title, other titles, first line, author and tune; every word must match."""
    terms = normalize_title(query).split()
    if not terms:
        return entries[:limit]
    scored = []
    for entry in entries:
        title = normalize_title(" ".join([entry.title, *entry.also_known_as]))
        rest = normalize_title(
            " ".join([entry.first_line, entry.author, entry.tune, entry.words_credit])
        )
        haystack = f"{title} {rest}"
        if all(t in haystack for t in terms):
            # A title that starts with the query, then any title match, then
            # first lines, authors and tunes; alphabetical within each.
            rank = (
                0
                if title.startswith(" ".join(terms))
                else 1
                if all(t in title for t in terms)
                else 2
            )
            scored.append((rank, entry))
    scored.sort(key=lambda pair: pair[0])
    return [entry for _, entry in scored[:limit]]


_SLUG_RE = re.compile(r"^[A-Za-z0-9_\-']+$")


def fetch_hymn(hymn_id: str) -> HymnSong:
    """Fetch one hymn's score and turn it into a song. Raises `ValueError`/`OSError`."""
    if not _SLUG_RE.match(hymn_id):
        raise ValueError(f"not an Open Hymnal hymn: {hymn_id!r}")
    data = _fetch(f"Abc/{hymn_id}.abc")
    return hymn_to_song(hymn_id, decode(data))


def hymn_to_song(hymn_id: str, abc_text: str) -> HymnSong:
    hymn = parse_abc(abc_text)
    song = to_song(hymn)
    if not song.body_text.strip() or not hymn.title:
        raise ValueError("This hymn's file has no words we can read.")
    people = [Contributor(p.name, p.died) for p in hymn.authors + hymn.translators]
    words = [c for c in hymn.credits if re.match(r"(?i)(words|translat|text)", c)]
    rights: Rights = classify(
        hymn.copyright,
        people,
        words_years(words),
        anonymous=bool(_ANON_RE.search(" ".join(words))),
    )
    return HymnSong(
        id=hymn_id,
        title=hymn.title,
        body_text=song.body_text,
        slide_order=song.slide_order,
        author=", ".join(p.name for p in hymn.authors) or _author_from_credit(words),
        copyright=_notice(words, hymn.copyright, rights),
        status=rights.status,
        status_label=rights.label,
        explanation=rights.explanation,
        source_url=f"{BASE_URL}Lyrics/{hymn_id}.html",
    )


def _author_from_credit(words: list[str]) -> str:
    if not words:
        return ""
    match = re.match(r"(?i)words:\s*([^,.;]+)", words[0])
    return match.group(1).strip() if match else ""


def _notice(words: list[str], copyright_line: str, rights: Rights) -> str:
    """What the song's copyright field says.

    Public domain: the credit, and that it is public domain (and where, when
    it's only the US). Anything else: the notice exactly as the source gives
    it — "free for worship" texts require it on every copy.
    """
    credit = " ".join(" ".join(words).split())
    # One C: line often credits the words and then the music; keep the words.
    credit = re.split(r"\s+(?:Music|Tune|Setting|Melody)\b[^:]*:", credit)[0].strip()
    line = " ".join(copyright_line.split())
    line = re.sub(r"(?i)^copyright:\s*", "", line)
    line = re.sub(r"\s*This score is a part of the Open Hymnal Project.*$", "", line).strip()
    if rights.status == "public_domain":
        return f"{credit} Public domain.".strip()
    if rights.status == "public_domain_us":
        # Not the source's bare "public domain": that would travel with the
        # song to a church where it isn't.
        return f"{credit} Public domain in the US.".strip()
    return f"{credit} {line}".strip()
