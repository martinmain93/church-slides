"""The books of the Bible: one canonical identity, many spellings.

Every Bible format names its books differently — OSIS writes `Exod`, USFM
`EXO`, Zefania `bnumber="2"`, OpenSong whatever the translator called it
("Éxodo", "Exodus", "2. Mojžišova"). A verse store built from one of them has
to land on the same identity as one built from another, or "Exodus 20:3"
resolves in one translation and not the next. The identity is the USFM book
code: it is what Paratext, eBible.org and the Digital Bible Library use, and
it already has codes for every deuterocanonical book a Catholic or Orthodox
Bible carries.

Three ways in, in the order an importer should trust them:

    by_code      a format that states the code (USFM, USX, USFX, OSIS)
    identify     a name or abbreviation, in English (alias table below)
    match_shape  chapter and verse counts, for names in any other language

`match_shape` is what lets a Slovak or Spanish file import without a name
table per language: Genesis has 50 chapters of 31, 25, 24, 26... verses in any
language, and very few books share a shape. Multi-language *display* is a
future feature; recognising the books is what this module guarantees now.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from auto_presenter.scripture._versification import VERSE_COUNTS


@dataclass(frozen=True)
class Book:
    code: str
    osis: str
    name: str
    #: "ot", "nt", or "dc" (deuterocanon / apocrypha).
    section: str
    #: Extra spellings, lowercased, without punctuation. The name, the code
    #: and the OSIS id are always accepted and needn't be listed.
    aliases: tuple[str, ...] = field(default=())

    @property
    def verse_counts(self) -> tuple[int, ...]:
        """Verses per chapter in the reference versification (may be empty)."""
        return VERSE_COUNTS.get(self.code, ())


_B = Book

#: Protestant canon order first, then the deuterocanon in Zefania's order where
#: it has one. The order is the order books appear in reports and the order
#: `match_shape` prefers when two books have similar shapes.
BOOKS: tuple[Book, ...] = (
    _B("GEN", "Gen", "Genesis", "ot", ("gn", "ge")),
    _B("EXO", "Exod", "Exodus", "ot", ("ex", "exo")),
    _B("LEV", "Lev", "Leviticus", "ot", ("lv", "le")),
    _B("NUM", "Num", "Numbers", "ot", ("nm", "nu")),
    _B("DEU", "Deut", "Deuteronomy", "ot", ("dt", "de")),
    _B("JOS", "Josh", "Joshua", "ot", ("jos", "jsh")),
    _B("JDG", "Judg", "Judges", "ot", ("jg", "jdgs")),
    _B("RUT", "Ruth", "Ruth", "ot", ("ru", "rth")),
    _B("1SA", "1Sam", "1 Samuel", "ot", ("1 sam", "1 sa", "1 kingdoms")),
    _B("2SA", "2Sam", "2 Samuel", "ot", ("2 sam", "2 sa", "2 kingdoms")),
    _B("1KI", "1Kgs", "1 Kings", "ot", ("1 kgs", "1 ki", "3 kingdoms")),
    _B("2KI", "2Kgs", "2 Kings", "ot", ("2 kgs", "2 ki", "4 kingdoms")),
    _B("1CH", "1Chr", "1 Chronicles", "ot", ("1 chr", "1 chron", "1 paralipomenon")),
    _B("2CH", "2Chr", "2 Chronicles", "ot", ("2 chr", "2 chron", "2 paralipomenon")),
    _B("EZR", "Ezra", "Ezra", "ot", ("ezr", "1 esdras vulgate")),
    _B("NEH", "Neh", "Nehemiah", "ot", ("ne",)),
    _B("EST", "Esth", "Esther", "ot", ("est", "es")),
    _B("JOB", "Job", "Job", "ot", ("jb",)),
    _B("PSA", "Ps", "Psalms", "ot", ("psalm", "pss", "psa", "psm", "pslm")),
    _B("PRO", "Prov", "Proverbs", "ot", ("pr", "prv")),
    _B("ECC", "Eccl", "Ecclesiastes", "ot", ("ecc", "eccles", "qoheleth")),
    _B(
        "SNG",
        "Song",
        "Song of Solomon",
        "ot",
        ("song of songs", "canticles", "canticle of canticles", "sos", "song", "sol"),
    ),
    _B("ISA", "Isa", "Isaiah", "ot", ("is",)),
    _B("JER", "Jer", "Jeremiah", "ot", ("je", "jr")),
    _B("LAM", "Lam", "Lamentations", "ot", ("la",)),
    _B("EZK", "Ezek", "Ezekiel", "ot", ("eze", "ezk")),
    _B("DAN", "Dan", "Daniel", "ot", ("da", "dn")),
    _B("HOS", "Hos", "Hosea", "ot", ("ho",)),
    _B("JOL", "Joel", "Joel", "ot", ("jl", "joe")),
    _B("AMO", "Amos", "Amos", "ot", ("am",)),
    _B("OBA", "Obad", "Obadiah", "ot", ("ob", "obd")),
    _B("JON", "Jonah", "Jonah", "ot", ("jnh", "jon")),
    _B("MIC", "Mic", "Micah", "ot", ("mi",)),
    _B("NAM", "Nah", "Nahum", "ot", ("na",)),
    _B("HAB", "Hab", "Habakkuk", "ot", ("hb",)),
    _B("ZEP", "Zeph", "Zephaniah", "ot", ("zep", "zp")),
    _B("HAG", "Hag", "Haggai", "ot", ("hg",)),
    _B("ZEC", "Zech", "Zechariah", "ot", ("zec", "zc")),
    _B("MAL", "Mal", "Malachi", "ot", ("ml",)),
    _B("MAT", "Matt", "Matthew", "nt", ("mt", "mat")),
    _B("MRK", "Mark", "Mark", "nt", ("mk", "mr", "mrk", "mar")),
    _B("LUK", "Luke", "Luke", "nt", ("lk", "lu")),
    _B("JHN", "John", "John", "nt", ("jn", "jhn", "joh")),
    _B("ACT", "Acts", "Acts", "nt", ("ac", "acts of the apostles")),
    _B("ROM", "Rom", "Romans", "nt", ("ro", "rm")),
    _B("1CO", "1Cor", "1 Corinthians", "nt", ("1 cor", "1 co")),
    _B("2CO", "2Cor", "2 Corinthians", "nt", ("2 cor", "2 co")),
    _B("GAL", "Gal", "Galatians", "nt", ("ga",)),
    _B("EPH", "Eph", "Ephesians", "nt", ("ephes",)),
    _B("PHP", "Phil", "Philippians", "nt", ("php", "pp", "phi")),
    _B("COL", "Col", "Colossians", "nt", ("colos",)),
    _B("1TH", "1Thess", "1 Thessalonians", "nt", ("1 thess", "1 th")),
    _B("2TH", "2Thess", "2 Thessalonians", "nt", ("2 thess", "2 th")),
    _B("1TI", "1Tim", "1 Timothy", "nt", ("1 tim", "1 ti")),
    _B("2TI", "2Tim", "2 Timothy", "nt", ("2 tim", "2 ti")),
    _B("TIT", "Titus", "Titus", "nt", ("tit",)),
    _B("PHM", "Phlm", "Philemon", "nt", ("phm", "philem")),
    _B("HEB", "Heb", "Hebrews", "nt", ()),
    _B("JAS", "Jas", "James", "nt", ("jm", "jam")),
    _B("1PE", "1Pet", "1 Peter", "nt", ("1 pet", "1 pe", "1 pt")),
    _B("2PE", "2Pet", "2 Peter", "nt", ("2 pet", "2 pe", "2 pt")),
    _B("1JN", "1John", "1 John", "nt", ("1 jn", "1 jhn", "1 jo")),
    _B("2JN", "2John", "2 John", "nt", ("2 jn", "2 jhn", "2 jo")),
    _B("3JN", "3John", "3 John", "nt", ("3 jn", "3 jhn", "3 jo")),
    _B("JUD", "Jude", "Jude", "nt", ("jud", "jd", "jde")),
    _B("REV", "Rev", "Revelation", "nt", ("revelations", "re", "apocalypse", "the revelation")),
    # Deuterocanon. Zefania numbers these 67+ in this order (verified against
    # real files: Judith 67, Wisdom 68, Tobit 69, ... Baruch 71).
    _B("JDT", "Jdt", "Judith", "dc", ("jdth",)),
    _B("WIS", "Wis", "Wisdom", "dc", ("wisdom of solomon", "wis of sol", "book of wisdom")),
    _B("TOB", "Tob", "Tobit", "dc", ("tobias", "tb")),
    _B("SIR", "Sir", "Sirach", "dc", ("ecclesiasticus", "ben sira", "ecclus")),
    _B("BAR", "Bar", "Baruch", "dc", ()),
    _B("1MA", "1Macc", "1 Maccabees", "dc", ("1 macc", "1 mac", "1 ma")),
    _B("2MA", "2Macc", "2 Maccabees", "dc", ("2 macc", "2 mac", "2 ma")),
    # Names a reference can be typed with: no brackets ("Daniel (Greek)" is
    # USFM's own label, accepted as an alias).
    _B(
        "DAG",
        "AddDan",
        "Greek Daniel",
        "dc",
        ("daniel (greek)", "additions to daniel", "adddan", "daniel greek"),
    ),
    _B(
        "ESG",
        "AddEsth",
        "Greek Esther",
        "dc",
        ("esther (greek)", "additions to esther", "addesth", "esther greek", "esthgr"),
    ),
    _B("MAN", "PrMan", "Prayer of Manasseh", "dc", ("prayer of manasses", "prman")),
    _B("3MA", "3Macc", "3 Maccabees", "dc", ("3 macc", "3 mac")),
    _B("4MA", "4Macc", "4 Maccabees", "dc", ("4 macc", "4 mac")),
    _B(
        "LJE",
        "EpJer",
        "Letter of Jeremiah",
        "dc",
        ("epistle of jeremiah", "epjer", "epistle of jeremy"),
    ),
    _B("1ES", "1Esd", "1 Esdras", "dc", ("1 esd", "3 esdras")),
    _B("2ES", "2Esd", "2 Esdras", "dc", ("2 esd", "4 esdras")),
    _B("ODA", "Odes", "Odes", "dc", ()),
    _B("PSS", "PssSol", "Psalms of Solomon", "dc", ("psssol",)),
    _B("LAO", "EpLao", "Laodiceans", "dc", ("epistle to the laodiceans",)),
    _B(
        "S3Y",
        "PrAzar",
        "Song of the Three Young Men",
        "dc",
        ("prayer of azariah", "song of three youths", "song of the three"),
    ),
    _B("SUS", "Sus", "Susanna", "dc", ()),
    _B("BEL", "Bel", "Bel and the Dragon", "dc", ("bel and dragon",)),
    _B("PS2", "AddPs", "Psalm 151", "dc", ("psalm 151", "ps151")),
)

BY_CODE: dict[str, Book] = {b.code: b for b in BOOKS}
BY_OSIS: dict[str, Book] = {b.osis.lower(): b for b in BOOKS}

#: The 66 books of the Protestant canon, in order. Zefania numbers these 1-66
#: and every other format agrees on their identity.
PROTESTANT: tuple[Book, ...] = tuple(b for b in BOOKS if b.section != "dc")

#: Zefania's numbering. 1-66 is universal; 67+ was verified on real files for
#: the books listed, and is only used when the file's own name agrees.
ZEFANIA_NUMBERS: dict[int, str] = {
    **{i + 1: b.code for i, b in enumerate(PROTESTANT)},
    67: "JDT",
    68: "WIS",
    69: "TOB",
    70: "SIR",
    71: "BAR",
    72: "1MA",
    73: "2MA",
    74: "DAG",
    75: "ESG",
    76: "MAN",
    77: "3MA",
    78: "4MA",
    79: "LJE",
    80: "1ES",
    81: "2ES",
    82: "ODA",
    83: "PSS",
    84: "LAO",
}

#: Codes that appear in USFM/USX packages but are not scripture: front matter,
#: glossaries, introductions. Skipped silently rather than reported as unknown.
NON_SCRIPTURE_CODES = frozenset(
    "FRT BAK OTH INT CNC GLO TDX NDX TOB_INTRO XXA XXB XXC XXD XXE XXF XXG".split()
)

_ORDINALS = {
    "i": "1",
    "ii": "2",
    "iii": "3",
    "iv": "4",
    "first": "1",
    "second": "2",
    "third": "3",
    "fourth": "4",
    "1st": "1",
    "2nd": "2",
    "3rd": "3",
    "4th": "4",
}

_SPLIT_RE = re.compile(r"[\s._\-]+")


def normalize_name(text: str) -> str:
    """Lowercase, drop punctuation, and spell a leading ordinal as a digit.

    "I John", "First John", "1st John", "1. John", "1John" and "1 john" all
    come out as "1 john".
    """
    text = text.strip().lower().replace("’", "'")
    text = re.sub(r"^(\d)(?!st|nd|rd|th)(?=[^\d\s.])", r"\1 ", text)  # "1john" -> "1 john"
    parts = [p for p in _SPLIT_RE.split(text) if p]
    if len(parts) > 1 and parts[0] in _ORDINALS:
        parts[0] = _ORDINALS[parts[0]]
    return " ".join(parts)


def _build_aliases() -> dict[str, str]:
    table: dict[str, str] = {}
    for book in BOOKS:
        for spelling in (book.name, book.code, book.osis, *book.aliases):
            table.setdefault(normalize_name(spelling), book.code)
    return table


_ALIASES = _build_aliases()
#: Compact keys ("1cor", "songofsolomon") for prefix matching.
_COMPACT = {
    b.code: [k.replace(" ", "") for k, code in _ALIASES.items() if code == b.code] for b in BOOKS
}


def by_code(code: str) -> Book | None:
    """A book from a USFM code or an OSIS id (case-insensitive)."""
    code = code.strip()
    return BY_CODE.get(code.upper()) or BY_OSIS.get(code.lower())


def identify(name: str, *, prefix: bool = False) -> Book | None:
    """A book from an English name, abbreviation, USFM code or OSIS id.

    With `prefix`, also accepts an unambiguous prefix of any spelling — what an
    operator types ("jer", "1 cor", "phili"). Ambiguous prefixes ("phil" could
    be Philippians or Philemon by prefix — though "phil" is also Philippians'
    OSIS id, so exact matches win first) return None rather than guessing,
    because guessing would put the wrong passage on a screen.
    """
    key = normalize_name(name)
    if not key:
        return None
    code = _ALIASES.get(key)
    if code:
        return BY_CODE[code]
    if not prefix:
        return None
    compact = key.replace(" ", "")
    if len(compact) < 2:
        return None
    hits = {c for c, keys in _COMPACT.items() if any(k.startswith(compact) for k in keys)}
    return BY_CODE[hits.pop()] if len(hits) == 1 else None


def shape_similarity(found: list[int], book: Book) -> float:
    """How alike a book's verses-per-chapter are to `found`'s, 0..1.

    Three signals, because versifications genuinely differ: Catholic Bibles
    number Malachi in 3 chapters (4 in Protestant ones), carry Esther with its
    Greek additions as 16 chapters, and count Sirach's verses differently
    throughout. Chapter-by-chapter agreement is the strong signal (off by one
    or two counts partly — where a superscription is a verse, where a chapter
    break falls); the chapter count and the total verse count keep a book
    recognisable when its chapter breaks have moved.
    """
    expected = book.verse_counts
    if not expected or not found:
        return 0.0
    per_chapter = 0.0
    for a, b in zip(found, expected, strict=False):
        diff = abs(a - b)
        per_chapter += 1.0 if diff == 0 else 0.6 if diff <= 2 else 0.0
    per_chapter /= max(len(found), len(expected))
    chapters = min(len(found), len(expected)) / max(len(found), len(expected))
    total_a, total_b = sum(found), sum(expected)
    totals = min(total_a, total_b) / max(total_a, total_b) if max(total_a, total_b) else 0.0
    return 0.5 * per_chapter + 0.25 * chapters + 0.25 * totals


def match_shape(
    found: list[int],
    *,
    exclude: set[str] | frozenset[str] = frozenset(),
    after: int = -1,
    name: str = "",
    threshold: float = 0.6,
) -> Book | None:
    """The book whose chapter/verse shape best fits `found`, or None.

    `after` is the canon index of the previous book identified in the file.
    Books nearly always come in order, so the next unidentified book in canon
    order gets a real preference — that is what tells 3 John (one chapter of
    14 or 15 verses, depending on the Bible) from the Prayer of Manasseh (one
    chapter of 15) — and a slightly lower bar, since it is where a book with a
    reworked versification usually turns out to be.

    A leading number in the file's own name ("3. Jánov", "1 Kings") means the
    same in every language, so it restricts the candidates to books with that
    number.
    """
    first = normalize_name(name).split(" ", 1)[0] if name else ""
    numbered = first if first in ("1", "2", "3", "4") else ""
    next_index = next((i for i, b in enumerate(BOOKS) if i > after and b.code not in exclude), -1)
    best: tuple[float, Book] | None = None
    for index, book in enumerate(BOOKS):
        if book.code in exclude:
            continue
        if numbered and not book.name.startswith(numbered):
            continue
        score = shape_similarity(found, book)
        is_next = index == next_index
        if score < threshold - (0.1 if is_next else 0.0):
            continue
        score += 0.3 if is_next else 0.05 if index > after else 0.0
        if best is None or score > best[0]:
            best = (score, book)
    return best[1] if best else None


def canon_index(code: str) -> int:
    for index, book in enumerate(BOOKS):
        if book.code == code:
            return index
    return -1
