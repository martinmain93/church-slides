"""Read a Bible file a church already has, in whichever format it came in.

    parse_file(data, file_name)  ->  ParsedBible   (format detected from content)
    plan(parsed)                 ->  ImportReport  (books identified, problems listed)

Formats: USFM (one file, several, or a zip — eBible.org's packages), USX and
Digital Bible Library bundles, USFX, OSIS, Zefania, OpenSong, verse-per-line
text, CSV/TSV, and JSON. Detection looks at the content, never only the file
extension: OpenSong Bibles usually have no extension at all, and a `.xml` could
be any of five formats.

What is deliberately *not* here: SWORD modules (every reader is GPL, which
this app can't ship) and the encrypted formats of commercial presentation
software (ProPresenter, EasyWorship), whose licences don't transfer anyway.

Licensing is the church's responsibility, and the import screen says so; this
package only reads. See `docs/done/BIBLE_INTEGRATION.md`, "Importing a church's own
Bible".
"""

from __future__ import annotations

import io
import re
import zipfile
from html import unescape
from pathlib import PurePosixPath

from auto_presenter.scripture.importers.model import BibleImportError, ParsedBible
from auto_presenter.scripture.importers.plan import ImportReport, PlannedBook, plan
from auto_presenter.scripture.importers.text_formats import (
    parse_json,
    parse_table,
    parse_verse_lines,
)
from auto_presenter.scripture.importers.usfm import parse_usfm
from auto_presenter.scripture.importers.xml_formats import (
    PARSERS,
    local,
    parse_tree,
    parse_usx,
    read_dbl_metadata,
)

__all__ = [
    "BibleImportError",
    "ImportReport",
    "ParsedBible",
    "PlannedBook",
    "parse_file",
    "plan",
]

#: Big enough for any real Bible (the largest OSIS with Strong's numbers is
#: ~30 MB; zipped USFM packages are ~3 MB); small enough that a wrong file
#: (a video, a disk image) is refused before it's read into memory.
MAX_BYTES = 80 * 1024 * 1024

_USFM_EXT = (".usfm", ".sfm", ".ptx", ".txt")
_USFM_START_RE = re.compile(r"^\s*\\id\s+[A-Z0-9]{3}", re.MULTILINE)
_ROOT_RE = re.compile(rb"<(?![?!])([A-Za-z_][\w.:-]*)")


def parse_file(data: bytes, file_name: str = "") -> ParsedBible:
    if len(data) > MAX_BYTES:
        raise BibleImportError("This file is too large to be a Bible.")
    if data[:4] == b"PK\x03\x04":
        return _parse_zip(data)
    return _parse_single(data, file_name)


def _parse_zip(data: bytes) -> ParsedBible:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as err:
        raise BibleImportError("This zip file is damaged.") from err
    names = [
        n
        for n in archive.namelist()
        if not n.endswith("/") and not PurePosixPath(n).name.startswith(".") and "__MACOSX" not in n
    ]
    total = sum(archive.getinfo(n).file_size for n in names)
    if total > MAX_BYTES * 2:
        raise BibleImportError("This zip unpacks to more than any Bible would.")

    def read(name: str) -> bytes:
        return archive.read(name)

    usx = sorted(n for n in names if n.lower().endswith(".usx"))
    if usx:
        bible = ParsedBible("USX")
        for name in usx:
            parse_usx(parse_tree(read(name)), bible)
        meta = next((n for n in names if PurePosixPath(n).name.lower() == "metadata.xml"), None)
        if meta:
            read_dbl_metadata(parse_tree(read(meta)), bible)
        return bible

    usfm = sorted(
        n
        for n in names
        if n.lower().endswith(_USFM_EXT) and _USFM_START_RE.search(_decode(read(n))[:2000])
    )
    if usfm:
        bible = ParsedBible("USFM")
        for name in usfm:
            parse_usfm(_decode(read(name)), bible)
        about = next((n for n in names if PurePosixPath(n).name.lower() == "copr.htm"), None)
        if about:
            _read_ebible_about(_decode(read(about)), bible)
        return bible

    # One Bible file inside the zip (eBible's VPL package also carries an
    # .sql and an .xml copy; prefer the formats we read most faithfully).
    for suffix in (".xml", ".osis", ".zef", ".txt", ".csv", ".tsv", ".json"):
        candidates = sorted(
            (n for n in names if n.lower().endswith(suffix)),
            key=lambda n: -archive.getinfo(n).file_size,
        )
        for name in candidates:
            try:
                return _parse_single(read(name), name)
            except BibleImportError:
                continue
    raise BibleImportError("Nothing in this zip looks like a Bible.")


def _parse_single(data: bytes, file_name: str) -> ParsedBible:
    head = data[:4096].lstrip(b"\xef\xbb\xbf \t\r\n")
    if head.startswith(b"<"):
        return _parse_xml(data)

    text = _decode(data)
    stripped = text.lstrip("\ufeff \t\r\n")
    if _USFM_START_RE.match(stripped):
        bible = ParsedBible("USFM")
        # Some single-file USFM Bibles concatenate every book.
        for chunk in re.split(r"(?m)^(?=\\id\s)", stripped):
            if chunk.strip():
                parse_usfm(chunk, bible)
        return bible
    if stripped.startswith(("{", "[")):
        bible = ParsedBible("JSON")
        parse_json(stripped, bible)
        return bible

    bible = ParsedBible("Verse per line")
    if parse_verse_lines(stripped, bible):
        return bible
    bible = ParsedBible("CSV" if not file_name.lower().endswith(".tsv") else "TSV")
    if parse_table(stripped, bible):
        return bible
    raise BibleImportError(
        "This doesn't look like a Bible file we can read. Supported: USFM, USX, "
        "USFX, OSIS, Zefania and OpenSong XML, verse-per-line text, CSV/TSV, and JSON."
    )


def _parse_xml(data: bytes) -> ParsedBible:
    root = parse_tree(data)
    tag = local(root.tag).lower()
    if tag not in PARSERS:
        match = _ROOT_RE.search(data[:4096])
        found = match.group(1).decode(errors="replace") if match else tag
        raise BibleImportError(
            f"This XML file (<{found}>) isn't a Bible format we recognise. "
            "Supported XML: OSIS, USX, USFX, Zefania and OpenSong."
        )
    name, parser = PARSERS[tag]
    bible = ParsedBible(name)
    parser(root, bible)
    return bible


_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def _read_ebible_about(html: str, bible: ParsedBible) -> None:
    """eBible.org packages carry `copr.htm`: the title, and the rights line."""
    title = _TITLE_RE.search(html)
    if title:
        bible.title = " ".join(unescape(title.group(1)).split())
    for line in unescape(_TAG_RE.sub("\n", html)).splitlines():
        line = " ".join(line.split())
        if re.match(r"(?i)^(public domain|copyright|©|\(c\))", line):
            bible.rights = line
            break
    language = re.search(r"Language:\s*([^<\n]+)", html)
    if language:
        bible.language = language.group(1).strip()


def _decode(data: bytes) -> str:
    """UTF-8 (with or without BOM), UTF-16 by BOM, else Windows-1252."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")
