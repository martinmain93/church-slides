"""The verse store: one translation on disk, and lookups against it.

One SQLite file per translation, in `<app data>/scripture/`, alongside
`library.db`. Read-only once built. Two ways a translation gets here:

- **Public-domain downloads** from eBible.org: the three offered on a fresh
  install (`TRANSLATIONS`: WEB, KJV, ASV) and any of the ~110 public-domain
  translations in its catalogue (`scripture.catalog`). Downloaded once by
  `ensure_translation`.
- **A church's own file** (`install_imported`): whatever Bible the church
  already has, in any format `scripture.importers` reads. Built on this
  machine and never sent anywhere — see `docs/done/BIBLE_INTEGRATION.md`,
  "Importing a church's own Bible".

**The catalogue is public domain only.** That is the licence, not a limitation
of the implementation: these texts can be redistributed, and NIV/ESV/NLT
cannot be at any price this project would pay (`docs/done/BIBLE_INTEGRATION.md`
§1). A church that holds rights to one of those imports its own copy, and the
credit line it gives is shown with every passage.

Each store holds three tables: `verses` (and its FTS index), `books` (the
canonical USFM code behind each book name, plus the file's own name for the
book) and `meta` (name, abbreviation, credit line, language). Stores built
before `books`/`meta` existed still open: their book names are identified on
the fly and their credit line comes from the catalogue.

The FTS index is what makes "find this lyric in scripture" work offline. It is
also most of the ~15 MB a translation costs on disk, so a display-only
translation could skip it; the build functions take `with_index` for that.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from auto_presenter.config import app_data_dir
from auto_presenter.http_client import open_get
from auto_presenter.scripture import books as canon
from auto_presenter.scripture.references import Reference, format_reference, parse

if TYPE_CHECKING:
    from auto_presenter.scripture.importers import ImportReport

log = logging.getLogger(__name__)

#: Public-domain translations, and where a full copy comes from. One file per
#: translation, one request — the shape an install-time download wants, rather
#: than 1,189 chapter fetches.
TRANSLATIONS: dict[str, "TranslationSpec"] = {}


@dataclass(frozen=True)
class TranslationSpec:
    id: str
    name: str
    url: str
    #: Shown wherever the text is displayed. Public domain still deserves a
    #: credit, and a licensed translation would *require* one.
    attribution: str
    #: What `url` serves: getbible.net's JSON, or an eBible.org USFM zip.
    format: str = "getbible"
    #: Shown beside a passage ("WEB"). Defaults to the id, uppercased.
    abbreviation: str = ""
    #: One line for the catalogue: what this translation is, who it suits.
    description: str = ""


def _register(spec: TranslationSpec) -> TranslationSpec:
    TRANSLATIONS[spec.id] = spec
    return spec


WEB = _register(
    TranslationSpec(
        "web",
        "World English Bible",
        "https://ebible.org/Scriptures/engwebp_usfm.zip",
        "World English Bible (public domain)",
        format="usfm-zip",
        description="Modern English, 66 books.",
    )
)
KJV = _register(
    TranslationSpec(
        "kjv",
        "King James Version",
        "https://ebible.org/Scriptures/eng-kjv2006_usfm.zip",
        "King James Version (public domain)",
        format="usfm-zip",
        description="The 1769 Authorized Version, 66 books.",
    )
)
ASV = _register(
    TranslationSpec(
        "asv",
        "American Standard Version",
        "https://ebible.org/Scriptures/eng-asv_usfm.zip",
        "American Standard Version (public domain)",
        format="usfm-zip",
        description="The 1901 American Standard Version, 66 books.",
    )
)

DEFAULT_TRANSLATION = WEB.id

#: Imported translations get ids of this shape, so they can never collide with
#: a catalogue id the app adds later.
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


@dataclass(frozen=True)
class Verse:
    ref: str
    book: str
    chapter: int
    verse: int
    text: str


@dataclass(frozen=True)
class Passage:
    """A resolved reference: the display text plus the verses behind it."""

    reference: str
    translation: str
    verses: tuple[Verse, ...]

    @property
    def text(self) -> str:
        return " ".join(v.text for v in self.verses)


@dataclass(frozen=True)
class TranslationInfo:
    id: str
    name: str
    abbreviation: str
    attribution: str
    description: str = ""
    language: str = ""
    imported: bool = False
    installed: bool = False


def scripture_dir() -> Path:
    path = app_data_dir() / "scripture"
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path(translation: str) -> Path:
    return scripture_dir() / f"{translation}.db"


def installed_translations() -> list[str]:
    """Every translation on disk: the catalogue's and the church's own."""
    return sorted(p.stem for p in scripture_dir().glob("*.db"))


#: One install of a translation at a time. Installs run concurrently with
#: other requests, and two of the same translation (a double-click, or the
#: Settings page and the setlist editor both asking) shared one `.building`
#: file: the second unlinked it from under the first, or both renamed a
#: half-written store into place. Re-entrant because `ensure_translation`
#: holds it across `build`.
_install_locks: dict[str, threading.RLock] = {}
_install_locks_guard = threading.Lock()


def _install_lock(translation: str) -> threading.RLock:
    with _install_locks_guard:
        return _install_locks.setdefault(translation, threading.RLock())


def ensure_translation(translation: str, *, with_index: bool = True) -> str:
    """Download and build a translation if it isn't there yet. Returns its store id.

    `translation` is a built-in id ("web") or an eBible.org id from the
    public-domain catalogue ("engbsb", or its store id "engbsb").
    """
    spec = TRANSLATIONS.get(translation)
    if spec is not None:
        with _install_lock(translation):
            _ensure_translation_locked(spec, with_index=with_index)
        return translation
    from auto_presenter.scripture import catalog

    entry = catalog.find(translation) or next(
        (e for e in _catalog_or_empty() if e.store_id == translation), None
    )
    if entry is None:
        raise KeyError(f"unknown translation {translation!r}")
    with _install_lock(entry.store_id):
        if not db_path(entry.store_id).exists():
            log.info("downloading %s from eBible.org", entry.title)
            catalog.install(entry, with_index=with_index)
    return entry.store_id


def _catalog_or_empty() -> list:
    from auto_presenter.scripture import catalog

    try:
        return catalog.load_catalog()
    except OSError:
        return []


def _ensure_translation_locked(spec: TranslationSpec, *, with_index: bool) -> Path:
    translation = spec.id
    path = db_path(translation)
    # Checked under the lock: a second install that waited out the first
    # finds the store already there and downloads nothing.
    if path.exists():
        return path
    log.info("downloading %s from %s", spec.name, spec.url)
    # `open_get`, not bare urllib: Bible hosts (getbible.net was the first)
    # answer a default `Python-urllib/3.x` User-Agent with a flat 403, the
    # same bot-blocking that made `http_client` exist in the first place.
    with open_get(spec.url, timeout_seconds=180) as response:
        data = response.read()
    if spec.format == "getbible":
        build(translation, json.loads(data), with_index=with_index)
    else:
        from auto_presenter.scripture.importers import parse_file, plan

        report = plan(parse_file(data, spec.url))
        build_from_report(translation, report, _catalog_meta(spec), with_index=with_index)
    return path


def _catalog_meta(spec: TranslationSpec) -> dict[str, str]:
    return {
        "name": spec.name,
        "abbreviation": spec.abbreviation or spec.id.upper(),
        "attribution": spec.attribution,
        "language": "en",
        "source": spec.url,
    }


def build(translation: str, payload: dict, *, with_index: bool = True) -> int:
    """Write a translation's store from a getbible.net-shaped payload.

    Goes through the same importer as a church's own file, so a catalogue
    translation's books are identified by the same rules.
    """
    from auto_presenter.scripture.importers import ParsedBible, plan
    from auto_presenter.scripture.importers.text_formats import parse_json_data

    parsed = ParsedBible("getbible")
    parse_json_data(payload, parsed)
    spec = TRANSLATIONS.get(translation)
    meta = _catalog_meta(spec) if spec else {"name": translation, "language": "en"}
    return build_from_report(translation, plan(parsed), meta, with_index=with_index)


def build_from_report(
    translation: str,
    report: ImportReport,
    meta: dict[str, str],
    *,
    with_index: bool = True,
) -> int:
    """Write a translation's store from an import plan. Returns the verse count."""
    with _install_lock(translation):
        return _build_locked(translation, report, meta, with_index=with_index)


def _build_locked(
    translation: str, report: ImportReport, meta: dict[str, str], *, with_index: bool
) -> int:
    rows = [
        (f"{planned.name} {chapter}:{verse}", planned.name, chapter, verse, text)
        for planned in report.books
        for chapter, verses in sorted(planned.raw.chapters.items())
        for verse, text in sorted(verses.items())
    ]
    book_rows = [
        (planned.code, planned.name, planned.raw.name, order)
        for order, planned in enumerate(report.books)
    ]
    path = db_path(translation)
    # Build into a temp file and move it into place, so an interrupted
    # download can never leave a half-populated store that looks installed.
    staging = path.with_suffix(".building")
    staging.unlink(missing_ok=True)
    con = sqlite3.connect(staging)
    try:
        con.executescript(
            """
            CREATE TABLE verses(
                ref TEXT PRIMARY KEY, book TEXT NOT NULL, chapter INTEGER NOT NULL,
                verse INTEGER NOT NULL, text TEXT NOT NULL
            );
            CREATE INDEX verses_locator ON verses(book, chapter, verse);
            CREATE TABLE books(
                code TEXT, name TEXT NOT NULL, local_name TEXT NOT NULL, ord INTEGER NOT NULL
            );
            CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """
        )
        # OR IGNORE: a file with the same verse twice (it happens — a
        # duplicated book) keeps the first rather than failing the import.
        con.executemany("INSERT OR IGNORE INTO verses VALUES (?,?,?,?,?)", rows)
        con.executemany("INSERT INTO books VALUES (?,?,?,?)", book_rows)
        con.executemany(
            "INSERT INTO meta VALUES (?,?)", [(k, v) for k, v in meta.items() if v is not None]
        )
        if with_index:
            # The Porter stemmer is English-only: on another language it
            # mangles words rather than helping, so the index stays exact.
            english = meta.get("language", "").lower()[:2] in ("", "en")
            tokenizer = "porter unicode61" if english else "unicode61 remove_diacritics 2"
            con.executescript(
                "CREATE VIRTUAL TABLE verses_fts USING fts5("
                f"ref UNINDEXED, text, tokenize='{tokenizer}');"
            )
            con.execute("INSERT INTO verses_fts(ref, text) SELECT ref, text FROM verses")
        con.commit()
        con.execute("VACUUM")
    except BaseException:
        # Leave nothing behind. The next `build` would overwrite it anyway,
        # but a stale multi-megabyte `.building` file next to the real store
        # is the kind of debris that gets mistaken for a partial install.
        con.close()
        staging.unlink(missing_ok=True)
        raise
    finally:
        con.close()
    staging.replace(path)
    count = len(rows)
    log.info("built %s: %d verses at %s", translation, count, path)
    return count


def new_translation_id(abbreviation: str) -> str:
    """An id for an imported translation, unique on this machine.

    Prefixed `my-` so it can never collide with a catalogue id, now or after
    the app adds one: an imported "KJV" becomes `my-kjv`, and a second one
    `my-kjv-2`.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", abbreviation.lower()).strip("-")[:30] or "bible"
    base = f"my-{slug}"
    taken = set(installed_translations()) | set(TRANSLATIONS)
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def install_imported(
    report: ImportReport,
    *,
    name: str,
    abbreviation: str,
    attribution: str,
    source_file: str = "",
) -> str:
    """Build a church's own Bible into a store. Returns its new id."""
    translation = new_translation_id(abbreviation)
    meta = {
        "name": name.strip(),
        "abbreviation": abbreviation.strip(),
        "attribution": attribution.strip(),
        "language": _language_code(report.language),
        "imported": "1",
        "format": report.format,
        "source": source_file,
    }
    build_from_report(translation, report, meta)
    return translation


def _language_code(language: str) -> str:
    """'ENG', 'en', 'English', 'und' -> a best-effort two-letter-ish code."""
    value = language.strip().lower()
    if value in ("", "und", "unknown"):
        return ""
    return {"eng": "en", "english": "en"}.get(value, value)


def remove_translation(translation: str) -> bool:
    """Delete a translation's store. True if there was one."""
    if not _ID_RE.match(translation):
        raise ValueError(f"not a translation id: {translation!r}")
    with _install_lock(translation):
        path = db_path(translation)
        if not path.exists():
            return False
        path.unlink()
        log.info("removed translation %s", translation)
        return True


def translation_info(translation: str) -> TranslationInfo:
    """Name, abbreviation and credit for a translation, installed or not."""
    spec = TRANSLATIONS.get(translation)
    meta: dict[str, str] = {}
    installed = db_path(translation).exists()
    if installed:
        meta = _read_meta(db_path(translation))
    name = meta.get("name") or (spec.name if spec else translation)
    return TranslationInfo(
        id=translation,
        name=name,
        abbreviation=meta.get("abbreviation")
        or (spec.abbreviation if spec and spec.abbreviation else translation.upper()),
        attribution=meta.get("attribution") or (spec.attribution if spec else name),
        description=spec.description if spec else "",
        language=meta.get("language", "en" if spec else ""),
        imported=meta.get("imported") == "1",
        installed=installed,
    )


def match_translation(said: str) -> str | None:
    """An installed translation from how someone named it, or None.

    "King James", "the KJV", "N.I.V.", "World English Bible": matched against
    each installed translation's id, abbreviation and name, ignoring case,
    punctuation and a leading "the". Only an unambiguous match counts — a
    wrong translation is a smaller error than a wrong verse, but still one.
    """

    def norm(text: str) -> str:
        text = re.sub(r"[^a-z0-9 ]+", "", text.lower())
        text = re.sub(r"^(the )", "", " ".join(text.split()))
        return re.sub(r" (version|translation|bible)$", "", text)

    key = norm(said)
    if not key:
        return None
    installed = [translation_info(t) for t in installed_translations()]
    exact = [
        t.id
        for t in installed
        if key in {norm(t.id), norm(t.abbreviation), norm(t.name), norm(t.abbreviation).replace(" ", "")}
        or key.replace(" ", "") == norm(t.abbreviation).replace(" ", "")
    ]
    if len(set(exact)) == 1:
        return exact[0]
    partial = [t.id for t in installed if key in norm(t.name)]
    return partial[0] if len(set(partial)) == 1 else None


def all_translations() -> list[TranslationInfo]:
    """The catalogue (installed or not), then every imported translation."""
    ids = list(TRANSLATIONS) + [t for t in installed_translations() if t not in TRANSLATIONS]
    return [translation_info(t) for t in ids]


def _read_meta(path: Path) -> dict[str, str]:
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return dict(con.execute("SELECT key, value FROM meta").fetchall())
        finally:
            con.close()
    except sqlite3.Error:
        # A store from before `meta` existed. The catalogue entry covers it.
        return {}


#: Words too common to carry a scripture signal. Trimmed against the user's own
#: library: worship lyrics are heavy on second-person address, and leaving
#: "you"/"your" in makes every psalm a match for every song.
_STOP = frozenset(
    "the a an and or but of to in on for with is are was were be been am i you "
    "he she it we they my your his her our their me him them this that as at by "
    "from not no so if then there here all any some just oh yeah when what who "
    "how why do did done have has had will would can could shall should may "
    "might come came go went let lets".split()
)

#: Words in any script (a Spanish or Russian translation searches too).
_WORD_RE = re.compile(r"[^\W\d_][\w']*")


class VerseStore:
    """Read-only access to one translation.

    Opened per request rather than held open: lookups are rare (setlist load,
    an editor search) and a long-lived handle on a file the installer may
    replace buys nothing.
    """

    def __init__(self, translation: str = DEFAULT_TRANSLATION) -> None:
        self.translation = translation
        self.path = db_path(translation)
        if not self.path.exists():
            raise FileNotFoundError(
                f"no {translation!r} verse store at {self.path} — not installed yet"
            )
        self._con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        self._books: list[str] | None = None
        self._by_code: dict[str, str] | None = None
        self._info: TranslationInfo | None = None

    def close(self) -> None:
        self._con.close()

    def __enter__(self) -> "VerseStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def info(self) -> TranslationInfo:
        if self._info is None:
            self._info = translation_info(self.translation)
        return self._info

    @property
    def attribution(self) -> str:
        return self.info.attribution

    @property
    def abbreviation(self) -> str:
        return self.info.abbreviation

    def resolve(self, reference: str | Reference) -> Passage:
        """Look up a reference. Raises `ReferenceError` if it isn't real.

        "Isn't real" covers both malformed strings and well-formed references
        to verses that don't exist (`Psalm 23:47`), because from the display's
        point of view they are the same failure: nothing to show, and a caller
        that must not fall back to showing something else.
        """
        ref = reference if isinstance(reference, Reference) else parse(reference)
        rows = self._con.execute(
            "SELECT ref, book, chapter, verse, text FROM verses "
            "WHERE lower(book)=? AND chapter=? AND verse BETWEEN ? AND ? "
            "ORDER BY verse",
            (self._book(ref.book), ref.chapter, ref.first_verse, ref.last_verse),
        ).fetchall()
        if not rows:
            from auto_presenter.scripture.references import ReferenceError

            raise ReferenceError(f"{ref.display} is not in the {self.abbreviation}")
        # A translation that merges verses ("1-2" as one) stores the same text
        # under each; a passage spanning them shows it once.
        verses: list[Verse] = []
        for row in rows:
            if verses and verses[-1].text == row[4]:
                continue
            verses.append(Verse(*row))
        return Passage(
            # Built from the store's own book name rather than the caller's
            # spelling, so a verse reads the same however it was reached —
            # typed, searched for, or suggested.
            reference=format_reference(rows[0][1], ref.chapter, ref.first_verse, rows[-1][3]),
            translation=self.translation,
            verses=tuple(verses),
        )

    def _book(self, typed: str) -> str:
        """The store's name for a book, from however much of it was typed.

        Through the canonical book table first, so every spelling of a book
        reaches it in every translation — "Song of Songs", "Canticles" and
        "Song of Solomon" are one book whichever of them the store was built
        with. Then, for a book the table doesn't know (an imported file's
        unrecognised book), by the store's own names.

        An operator types "jer" or "1 cor", not "jeremiah". A book is found by
        a prefix of its name with spaces ignored, but only when exactly one
        book starts that way — "phil" could be Philippians or Philemon, and
        guessing between them would put the wrong passage on a screen. An
        unmatched or ambiguous name comes back as typed, so the lookup fails
        the ordinary way and says which reference it couldn't find.
        """
        if self._books is None:
            self._books = [
                row[0].lower()
                for row in self._con.execute("SELECT DISTINCT book FROM verses")
            ]
            self._by_code = self._book_codes()
        book = canon.identify(typed, prefix=True)
        if book is not None and self._by_code and book.code in self._by_code:
            return self._by_code[book.code]
        if typed in self._books:
            return typed
        key = typed.replace(" ", "")
        found = [b for b in self._books if b.replace(" ", "").startswith(key)]
        return found[0] if len(found) == 1 else typed

    def book_code(self, name: str) -> str | None:
        """The canonical USFM code of one of this store's books, by its name."""
        self._book(name)  # loads the store's book names on first use
        for code, stored in (self._by_code or {}).items():
            if stored == name.lower():
                return code
        book = canon.identify(name)
        return book.code if book else None

    def _book_codes(self) -> dict[str, str]:
        """Canonical code -> this store's (lowercased) book name."""
        try:
            rows = self._con.execute("SELECT code, name FROM books WHERE code IS NOT NULL")
            mapping = {code: name.lower() for code, name in rows}
            if mapping:
                return mapping
        except sqlite3.OperationalError:
            pass  # a store from before the `books` table
        mapping = {}
        for name in self._books or []:
            book = canon.identify(name)
            if book is not None:
                mapping.setdefault(book.code, name)
        return mapping

    def search(self, text: str, limit: int = 8) -> list[Passage]:
        """BM25 over the verse text. Finds quotations; blind to paraphrase.

        This is the editor's "where is this line from?" box, not the
        suggester — measured on the user's library, it lands the verse at rank
        1 when a song quotes it nearly verbatim and misses entirely when the
        song merely alludes. See `docs/done/BIBLE_INTEGRATION.md` §4.
        """
        words = [w.replace('"', "") for w in _WORD_RE.findall(text.lower())]
        terms: dict[str, None] = {}
        for word in words:
            if len(word) > 2 and word not in _STOP:
                terms.setdefault(word, None)
        if not terms:
            return []
        # Tightest first: the words as a phrase, then verses holding every
        # word, then any of them. With "any" alone, "for God so loved" ranked
        # 1 John 4 (which says "love" over and over) above John 3:16.
        tiers = [
            '"' + " ".join(w for w in words[:60] if w) + '"',
            " AND ".join(f'"{t}"' for t in list(terms)[:60]),
            " OR ".join(f'"{t}"' for t in list(terms)[:60]),
        ]
        rows: list[tuple] = []
        try:
            for query in dict.fromkeys(tiers):
                if len(rows) >= limit:
                    break
                got = {r[0] for r in rows}
                rows += [
                    r
                    for r in self._con.execute(
                        "SELECT v.ref, v.book, v.chapter, v.verse, v.text "
                        "FROM verses_fts f JOIN verses v ON v.ref = f.ref "
                        "WHERE verses_fts MATCH ? ORDER BY bm25(verses_fts) LIMIT ?",
                        (query, limit),
                    ).fetchall()
                    if r[0] not in got
                ]
            rows = rows[:limit]
        except sqlite3.OperationalError:
            # No FTS table (a display-only translation), or a query the
            # tokenizer choked on. Searching is a convenience; failing it
            # should never take down the caller.
            log.debug("verse search unavailable for %s", self.translation, exc_info=True)
            return []
        # Built from the row, not by re-parsing its reference: a book the
        # canonical table doesn't know may have a name no reference parser
        # would accept, and search must still find its verses.
        passages: list[Passage] = []
        seen: set[str] = set()
        for row in rows:
            verse = Verse(*row)
            if verse.text in seen:
                continue  # the same merged verse, found under each number
            seen.add(verse.text)
            passages.append(
                Passage(
                    reference=format_reference(verse.book, verse.chapter, verse.verse, verse.verse),
                    translation=self.translation,
                    verses=(verse,),
                )
            )
        return passages
