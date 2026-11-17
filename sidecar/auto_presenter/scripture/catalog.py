"""Public-domain Bible translations from eBible.org: search and download.

eBible.org publishes a machine-readable list of every translation it hosts
(`translations.csv`, ~1,550 rows) and a USFM package for each one. This module
offers only the rows whose copyright is **public domain** — 110 translations in
47 languages when this was written, 34 of them English. Another ~1,180 are
free to redistribute under other licences (Creative Commons and the like);
they are left out on purpose, so that everything offered here needs no
attribution terms and no judgement.

A church's licensed translation (NIV, ESV…) never comes from here: that is
"Import a Bible file" (`scripture.importers`).

The list is cached for a week in `<app data>/scripture/`; offline, the cached
copy is used however old it is.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)

CATALOG_URL = "https://ebible.org/Scriptures/translations.csv"
PACKAGE_URL = "https://ebible.org/Scriptures/{id}_usfm.zip"
MAX_AGE_SECONDS = 7 * 24 * 3600
CACHE_VERSION = 1

#: eBible ids whose store has a shorter, older id in this app: the three
#: translations a fresh install offers to download, kept under the ids that
#: existing setlists and settings already name.
STORE_IDS = {"engwebp": "web", "eng-kjv2006": "kjv", "eng-asv": "asv"}
#: Abbreviations eBible's ids don't spell out cleanly.
ABBREVIATIONS = {
    "engwebp": "WEB",
    "engwebpb": "WEB",
    "eng-web": "WEB",
    "engwebu": "WEB",
    "eng-webbe": "WEB",
    "eng-web-c": "WEB",
    "eng-kjv2006": "KJV",
    "eng-kjv": "KJV",
    "engkjvcpb": "KJV",
    "eng-asv": "ASV",
    "engDRA": "DRA",
    "engBBE": "BBE",
    "engDBY": "DBY",
    "enggnv": "GNV",
    "engylt": "YLT",
}

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,40}$")


@dataclass
class CatalogEntry:
    id: str
    title: str
    description: str
    language: str
    language_code: str
    ot_books: int
    nt_books: int
    dc_books: int

    @property
    def store_id(self) -> str:
        return store_id(self.id)

    @property
    def abbreviation(self) -> str:
        return abbreviation(self.id, self.language_code)


def store_id(ebible_id: str) -> str:
    return STORE_IDS.get(ebible_id, ebible_id.lower())


def abbreviation(ebible_id: str, language_code: str = "") -> str:
    if ebible_id in ABBREVIATIONS:
        return ABBREVIATIONS[ebible_id]
    rest = ebible_id
    if language_code and rest.lower().startswith(language_code.lower()):
        rest = rest[len(language_code) :]
    rest = rest.strip("-").replace("-", " ").upper()
    if not rest or rest.isdigit():
        rest = f"{language_code.upper()}{rest}"
    return rest[:12]


def parse_catalog(text: str) -> list[CatalogEntry]:
    """The public-domain, downloadable rows of eBible's `translations.csv`."""
    entries = []
    for row in csv.DictReader(io.StringIO(text.lstrip("\ufeff"))):
        if row.get("downloadable") != "True":
            continue
        if "public domain" not in (row.get("Copyright") or "").lower():
            continue
        if not _ID_RE.match(row.get("translationId") or ""):
            continue
        entries.append(
            CatalogEntry(
                id=row["translationId"],
                title=(row.get("title") or row.get("shortTitle") or row["translationId"]).strip(),
                description=(row.get("description") or "").strip(),
                language=(
                    row.get("languageNameInEnglish") or row.get("languageName") or ""
                ).strip(),
                language_code=(row.get("languageCode") or "").strip(),
                ot_books=_int(row.get("OTbooks")),
                nt_books=_int(row.get("NTbooks")),
                dc_books=_int(row.get("DCbooks")),
            )
        )
    return sorted(entries, key=lambda e: (e.language != "English", e.language, e.title.lower()))


def _int(value: str | None) -> int:
    try:
        return int(value or 0)
    except ValueError:
        return 0


def _cache_path() -> Path:
    from auto_presenter.scripture.store import scripture_dir

    return scripture_dir() / "ebible-catalog.json"


def _fetch(url: str, timeout: float = 60.0) -> bytes:
    from auto_presenter.http_client import open_get

    with open_get(url, timeout_seconds=timeout) as response:
        return response.read()


_lock = threading.Lock()


def load_catalog(*, refresh: bool = False) -> list[CatalogEntry]:
    """The catalogue, from the cache when it is recent. Raises `OSError` only
    when offline with nothing cached."""
    with _lock:
        path = _cache_path()
        cached = _read_cache(path)
        if cached is not None and not refresh and time.time() - cached[1] < MAX_AGE_SECONDS:
            return cached[0]
        try:
            entries = parse_catalog(_fetch(CATALOG_URL).decode("utf-8", errors="replace"))
        except OSError:
            if cached is not None:
                log.info("eBible.org unreachable; using the cached catalogue")
                return cached[0]
            raise
        path.write_text(
            json.dumps(
                {
                    "version": CACHE_VERSION,
                    "fetched": time.time(),
                    "entries": [asdict(e) for e in entries],
                }
            ),
            encoding="utf-8",
        )
        return entries


def _read_cache(path: Path) -> tuple[list[CatalogEntry], float] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != CACHE_VERSION:
            return None
        return [CatalogEntry(**e) for e in data["entries"]], float(data["fetched"])
    except (OSError, ValueError, TypeError, KeyError):
        return None


def search(entries: list[CatalogEntry], query: str) -> list[CatalogEntry]:
    terms = query.lower().split()
    if not terms:
        return entries
    return [
        e
        for e in entries
        if all(
            t in f"{e.title} {e.description} {e.language} {e.abbreviation} {e.id}".lower()
            for t in terms
        )
    ]


def find(ebible_id: str) -> CatalogEntry | None:
    """A catalogue entry by eBible id, from whatever list is at hand."""
    try:
        entries = load_catalog()
    except OSError:
        return None
    return next((e for e in entries if e.id == ebible_id), None)


def install(entry: CatalogEntry, *, with_index: bool = True) -> str:
    """Download a translation's USFM package and build its store. Returns the store id."""
    from auto_presenter.scripture.importers import parse_file, plan
    from auto_presenter.scripture.store import build_from_report

    data = _fetch(PACKAGE_URL.format(id=entry.id), timeout=300)
    report = plan(parse_file(data, f"{entry.id}_usfm.zip"))
    meta = {
        "name": entry.title,
        "abbreviation": entry.abbreviation,
        "attribution": f"{entry.title} (public domain)",
        "language": "en" if entry.language_code == "eng" else entry.language_code,
        "source": PACKAGE_URL.format(id=entry.id),
        "ebible_id": entry.id,
    }
    build_from_report(entry.store_id, report, meta, with_index=with_index)
    return entry.store_id
