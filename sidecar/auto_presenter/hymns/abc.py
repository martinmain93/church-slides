"""Turn an Open Hymnal ABC score into song text: verses, a refrain, line breaks.

The Open Hymnal publishes every hymn as an ABC Plus score. The lyrics live in
two places, and neither is laid out as a hymnal prints them:

- **`w:` lines** — syllables aligned to notes, one line per verse, *per line
  of music*: verse 2's words are the second `w:` line under each music line.
  "A- maz- ing * grace!" is four notes, three syllables of one word, a skipped
  note and a fourth syllable.
- **`W:` lines** — extra verses printed under the score, already broken into
  poetic lines.

Poetic line breaks are not in the `w:` lines at all. They come from the
**metre**: Amazing Grace is 8.6.8.6, so its verse is the first 8 sung
syllables, the next 6, the next 8, the next 6. The score gives syllables
exactly (one per note), the file's `%OHMETRICAL` header (or the site's
metrical index, for older files) gives the metre, and the two together give
the lines. Where the count doesn't add up — a melisma, an elision — the split
falls on the word boundary nearest each target, preferring one after
punctuation.

A **refrain** is written once, under verse 1 only: the other verses have
skipped notes (`*`) there. So it is whatever verse 1 sings beyond the other
verses — or, in scores that repeat it in every verse, the syllables every
verse ends with.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Part of the melody voice's id in the Open Hymnal's scores ("S1V1"); older
#: scores name voices differently, so the first voice to carry music is the
#: fallback.
_MELODY_HINT = "S1V1"

_VOICE_INLINE_RE = re.compile(r"^\[V:\s*([^\]\s]+)[^\]]*\]")
_VOICE_HEADER_RE = re.compile(r"^V:\s*(\S+)")
_LIFE_RE = re.compile(r"\((?:c\.\s*)?(\d{3,4})?\??\s*-\s*(?:c\.\s*)?(\d{3,4})?\??\)")
_VERSE_NO_RE = re.compile(r"^\s*\d+\.\s*")
_PUNCT_END_RE = re.compile(r"[,;:.!?)\u201d\u2019'\"]$")


@dataclass
class Person:
    name: str
    born: int | None = None
    died: int | None = None


@dataclass
class Syllable:
    text: str
    #: True when this syllable ends a word (the next one starts a new word).
    word_end: bool


@dataclass
class AbcHymn:
    """Everything an ABC file says about a hymn, before it becomes slides."""

    title: str
    also_known_as: list[str] = field(default_factory=list)
    credits: list[str] = field(default_factory=list)
    copyright: str = ""
    authors: list[Person] = field(default_factory=list)
    translators: list[Person] = field(default_factory=list)
    metre: list[int] = field(default_factory=list)
    metre_text: str = ""
    topics: list[str] = field(default_factory=list)
    #: Sung verses, as syllables, in verse order.
    sung: list[list[Syllable]] = field(default_factory=list)
    #: Extra verses from `W:` lines, already in poetic lines.
    printed: list[list[str]] = field(default_factory=list)


def decode(data: bytes) -> str:
    """The Open Hymnal's newer files are UTF-8, older ones Latin-1."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    return text.replace("\r\n", "\n").replace("\r", "\n")


#: Author fields that name a tradition, not a person: the text is anonymous.
_TRADITION_RE = re.compile(
    r"^(anonymous|unknown|traditional|anon\.?|"
    r"(?:ancient |early |medieval )?(?:latin|greek|german|english|basque|irish|welsh|"
    r"french|spanish|swedish|danish|norwegian|dutch|bohemian|polish|czech|italian|"
    r"american|african[- ]american|scottish|hebrew|syriac|slovak|finnish|silesian|moravian)"
    r"(?: (?:carol|hymn|folk song|spiritual|traditional|text))?)$",
    re.I,
)
#: "1650 from the Scottish Psalter", "1777 from Landshuter Gesangbuch": a
#: source book, not a person.
_SOURCE_RE = re.compile(r"\d|\b(from|psalter|gesangbuch|hymnal|hymn-?book|book of)\b", re.I)
_CENTURY_RE = re.compile(r"(\d{1,2})(?:st|nd|rd|th)\s+century", re.I)
_YEARS_RE = re.compile(r"(?<![\d/])(\d{3,4})(?![\d])")


def parse_people(value: str) -> list[Person]:
    """`Newton, John (1725-1807);Clayton, David L.` -> people with life dates.

    Tolerant of how the files actually write dates — "(1181/1182-1226)",
    "(circa 387-493)", "(18th Century)", "Georgson, 1863-1938" — because a date
    it can't read makes a hymn's status more cautious, not less. A tradition
    ("German", "anonymous") is not a person and is left out.
    """
    people = []
    for part in value.split(";"):
        part = part.strip()
        if not part or part.lower() in ("none", "unknown"):
            continue
        name = re.sub(r"\([^)]*\)", "", part)
        name = re.sub(r",?\s*(?:c\.|circa)?\s*\d{3,4}\s*[-–]\s*\d{3,4}", "", name)
        name = " ".join(name.split()).strip(" ,")
        if "," in name:
            last, first = name.split(",", 1)
            name = f"{first.strip()} {last.strip()}"
        if not name or _TRADITION_RE.match(name) or _SOURCE_RE.search(name):
            continue
        dates = re.findall(r"\(([^)]*)\)", part) or [part]
        years = [int(y) for y in _YEARS_RE.findall(" ".join(dates))]
        century = _CENTURY_RE.search(part)
        died = max(years) if len(years) >= 2 else None
        if died is None and century:
            died = int(century.group(1)) * 100
        born = min(years) if len(years) >= 2 else None
        people.append(Person(name=name, born=born, died=died))
    return people


def parse_metre(value: str) -> list[int]:
    """`8 6 8 6` -> [8, 6, 8, 6]; `8 7 8 7 D` doubles; `Irregular` -> []."""
    numbers = [int(n) for n in re.findall(r"\b\d+\b", value)]
    if re.search(r"\bD\b", value):
        numbers = numbers * 2
    return numbers


def tokenize_lyrics(line: str) -> list[Syllable]:
    """One `w:` line -> its syllables, one per note (skips and holds dropped)."""
    out: list[Syllable] = []
    current = ""

    def emit(word_end: bool) -> None:
        nonlocal current
        if current.strip():
            out.append(Syllable(current.strip(), word_end))
        current = ""

    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line):
            current += line[i + 1]  # an escaped character, e.g. a literal hyphen
            i += 2
            continue
        if ch == "~":
            current += " "  # several words on one note
        elif ch == "-":
            emit(word_end=False)
        elif ch in " \t*_|":
            emit(word_end=True)
        else:
            current += ch
        i += 1
    emit(word_end=True)

    # "1.~A-" puts the verse number on the first note; it isn't a word.
    if out:
        out[0].text = _VERSE_NO_RE.sub("", out[0].text)
        if not out[0].text:
            out.pop(0)
    return out


def parse_abc(text: str) -> AbcHymn:
    lines = text.split("\n")
    hymn = AbcHymn(title="")
    melody: str | None = None
    voice: str | None = None
    #: w: lines under the current melody line, in order (verse 1, 2, ...).
    block: list[str] = []
    sung: dict[int, list[Syllable]] = {}
    printed: list[list[str]] = [[]]
    in_tune = False

    def flush() -> None:
        for index, w in enumerate(block):
            sung.setdefault(index, []).extend(tokenize_lyrics(w))
        block.clear()

    for raw in lines:
        line = raw.rstrip()
        if line.startswith("%OH"):
            key, _, value = line[3:].partition(" ")
            value = value.strip()
            if key == "AUTHOR":
                hymn.authors = parse_people(value)
            elif key == "TRANSLATOR":
                hymn.translators = parse_people(value)
            elif key == "METRICAL":
                hymn.metre_text = value
                hymn.metre = parse_metre(value)
            elif key == "TOPICS":
                hymn.topics = re.findall(r"\{([^}\[]+?)\s*(?:\[[^\]]*\])?\}", value)
            continue
        if line.startswith("%"):
            continue
        if line.startswith("X:"):
            in_tune = True
            continue
        if not in_tune:
            continue
        if line.startswith("T:"):
            title = line[2:].strip()
            aka = re.match(r"^\(?(?:also known as|see also)\s+(.*?)\)?$", title, re.I)
            if aka:
                hymn.also_known_as.append(aka.group(1).strip("'\" "))
            elif not hymn.title:
                hymn.title = title
            else:
                hymn.also_known_as.append(title)
            continue
        if line.startswith("C:"):
            credit = line[2:].strip()
            if credit.lower().startswith("copyright:") or hymn.copyright:
                hymn.copyright = f"{hymn.copyright} {credit}".strip()
            else:
                hymn.credits.append(credit)
            continue
        if line.startswith("W:"):
            verse_line = line[2:].strip()
            if not verse_line:
                if printed[-1]:
                    printed.append([])
                continue
            if _VERSE_NO_RE.match(verse_line) and printed[-1]:
                printed.append([])
            printed[-1].append(_VERSE_NO_RE.sub("", verse_line).strip())
            continue
        if line.startswith("w:"):
            if voice is None or voice == melody:
                block.append(line[2:])
            continue
        header = _VOICE_HEADER_RE.match(line)
        if header:
            voice = header.group(1)
            continue
        inline = _VOICE_INLINE_RE.match(line)
        if inline or (line and not re.match(r"^[A-Za-z]:", line)):
            # A line of music. The w: lines that follow belong to it.
            flush()
            if inline:
                voice = inline.group(1)
            if melody is None and voice is not None and _has_notes(line):
                melody = voice
            if melody is not None and voice is not None and _MELODY_HINT in voice:
                melody = voice
    flush()

    hymn.sung = [sung[i] for i in sorted(sung) if sung[i]]
    hymn.printed = [v for v in printed if v]
    return hymn


def _has_notes(line: str) -> bool:
    body = _VOICE_INLINE_RE.sub("", line)
    body = re.sub(r'"[^"]*"|![^!]*!|\[[A-Za-z]:[^\]]*\]', "", body)
    return bool(re.search(r"[A-Ga-g]", body))


# --- From syllables to lines ------------------------------------------------


def _words(syllables: list[Syllable]) -> list[tuple[str, int]]:
    """Syllables -> words, each with how many syllables (notes) it took."""
    words: list[tuple[str, int]] = []
    text, count = "", 0
    for s in syllables:
        text += s.text
        count += 1
        if s.word_end:
            words.append((text, count))
            text, count = "", 0
    if text:
        words.append((text, count))
    # A word ending in an escaped hyphen is either half of a compound
    # ("life\\- im- part- ing") or a dash ("day\\- Je- sus, my Lord"). The
    # score writes both the same way; the next word's capital tells them apart.
    joined: list[tuple[str, int]] = []
    for text, count in words:
        if joined and joined[-1][0].endswith("-"):
            prev, prev_count = joined.pop()
            if _CAPITAL_RE.match(text):
                joined.append((prev[:-1] + "\u2014", prev_count))
            else:
                text, count = prev + text, prev_count + count
        joined.append((text, count))
    return joined


#: How much a break after punctuation, or before a capitalised word, is worth
#: against landing a syllable away from where the metre says a line ends.
_PUNCT_BONUS = 1.5
_CAPITAL_BONUS = 1.0
_CAPITAL_RE = re.compile(r"^[\"'“‘(]?[A-Z]")


def split_lines(syllables: list[Syllable], metre: list[int]) -> list[str]:
    """Break a verse into poetic lines.

    With a metre, the line ends are where its syllable counts say, nudged to a
    nearby word boundary that reads as a line end (after punctuation, before a
    capital) when the count is off — the best set of breaks is chosen as a
    whole, so one early miss doesn't push every later line out of place.
    Without one, lines end at punctuation.
    """
    words = _words(syllables)
    if not words:
        return []
    total = sum(n for _, n in words)
    if not metre:
        return _split_at_punctuation(words)
    pattern = list(metre)
    while sum(pattern) < total * 0.75 and len(pattern) < 64:
        pattern += metre  # a verse that sings the metre twice (an implied D)
    scale = total / sum(pattern)
    targets, acc = [], 0.0
    for n in pattern[:-1]:
        acc += n * scale
        targets.append(acc)

    # positions[i]: syllables up to and including word i; reward for a break there.
    positions, rewards = [], []
    position = 0
    for index, (text, n) in enumerate(words):
        position += n
        positions.append(position)
        nxt = words[index + 1][0] if index + 1 < len(words) else ""
        rewards.append(
            (_PUNCT_BONUS if _PUNCT_END_RE.search(text) else 0.0)
            + (_CAPITAL_BONUS if _CAPITAL_RE.match(nxt) and nxt != "I" else 0.0)
        )

    # Best increasing assignment of a word boundary to each target (DP).
    candidates = range(len(words) - 1)
    inf = float("inf")
    cost = [[inf] * len(words) for _ in targets]
    back = [[-1] * len(words) for _ in targets]
    for t, target in enumerate(targets):
        for i in candidates:
            here = abs(positions[i] - target) - rewards[i]
            if t == 0:
                cost[t][i] = here
                continue
            best_prev = min(
                ((cost[t - 1][j], j) for j in range(i) if cost[t - 1][j] < inf),
                default=(inf, -1),
            )
            if best_prev[0] < inf:
                cost[t][i] = best_prev[0] + here
                back[t][i] = best_prev[1]
    if not targets:
        return [" ".join(w for w, _ in words)]
    last = min(candidates, key=lambda i: cost[-1][i], default=None)
    if last is None or cost[-1][last] == inf:
        return _split_at_punctuation(words)
    cuts = [last]
    for t in range(len(targets) - 1, 0, -1):
        cuts.append(back[t][cuts[-1]])
    cuts.reverse()
    return _join(words, cuts)


def _split_at_punctuation(words: list[tuple[str, int]]) -> list[str]:
    """No metre: a line ends at punctuation, once it is long enough to be one."""
    cuts, length = [], 0
    for index, (text, n) in enumerate(words[:-1]):
        length += n
        if (_PUNCT_END_RE.search(text) and length >= 4) or length >= 12:
            cuts.append(index)
            length = 0
    return _join(words, cuts)


def _join(words: list[tuple[str, int]], cuts: list[int]) -> list[str]:
    lines, start = [], 0
    for cut in [*cuts, len(words) - 1]:
        lines.append(" ".join(w for w, _ in words[start : cut + 1]))
        start = cut + 1
    return [line for line in lines if line]


@dataclass
class Song:
    title: str
    body_text: str
    slide_order: str
    verses: int
    has_refrain: bool


def to_song(hymn: AbcHymn) -> Song:
    """Verses and refrain as the app's song text (`docs/SONG_FORMAT.md`)."""
    verses_syl = list(hymn.sung)
    refrain_syl: list[Syllable] = []

    if len(verses_syl) > 1:
        others = [len(v) for v in verses_syl[1:]]
        typical = max(set(others), key=others.count)
        if len(verses_syl[0]) > typical + 3:
            # The refrain is written once, under verse 1.
            refrain_syl = verses_syl[0][typical:]
            verses_syl[0] = verses_syl[0][:typical]
        else:
            refrain_syl, verses_syl = _common_refrain(verses_syl, hymn.metre)

    verse_metre, refrain_metre = _split_metre(hymn.metre, verses_syl, refrain_syl)
    verses = [split_lines(v, verse_metre) for v in verses_syl]
    verses += hymn.printed
    refrain = split_lines(refrain_syl, refrain_metre) if refrain_syl else []

    parts: list[str] = []
    order: list[str] = []
    for number, lines in enumerate(verses, 1):
        if not lines:
            continue
        parts.append(f"[verse {number}]\n" + _slides(lines))
        order.append(f"V{number}")
        if refrain:
            order.append("C")
    if refrain:
        parts.insert(1, "[chorus]\n" + _slides(refrain))
    return Song(
        title=hymn.title,
        body_text="\n\n".join(parts) + "\n",
        slide_order=", ".join(order) if refrain else "",
        verses=len(verses),
        has_refrain=bool(refrain),
    )


def _common_refrain(
    verses: list[list[Syllable]], metre: list[int]
) -> tuple[list[Syllable], list[list[Syllable]]]:
    """A refrain printed in full under every verse: their shared ending.

    Only when the shared ending is whole lines of the metre, and at least two
    of them. A litany hymn ends every verse with the same line ("Hear our
    penitential cry!"); that line belongs to each verse, and splitting part of
    it off as a chorus would put half a sentence on its own slide.
    """

    def key(s: Syllable) -> str:
        return re.sub(r"[^\w]", "", s.text.lower())

    shortest = min(len(v) for v in verses)
    common = 0
    while common < shortest and len({key(v[-1 - common]) for v in verses}) == 1:
        common += 1
    tails = {sum(metre[-k:]) for k in range(2, len(metre))} if metre else set()
    if (metre and common not in tails) or (not metre and common < 12):
        return [], verses
    return verses[0][-common:], [v[:-common] for v in verses]


def _split_metre(
    metre: list[int], verses: list[list[Syllable]], refrain: list[Syllable]
) -> tuple[list[int], list[int]]:
    """Which part of the metre is the verse's and which the refrain's."""
    if not metre or not refrain or not verses:
        return metre, []
    verse_len = len(verses[0])
    acc = 0
    for index, n in enumerate(metre):
        acc += n
        if acc >= verse_len - 1:
            if index + 1 < len(metre):
                return metre[: index + 1], metre[index + 1 :]
            break
    return metre, []


def _slides(lines: list[str]) -> str:
    """A verse's lines as slides: four lines a slide, six as three and three."""
    if len(lines) <= 4:
        groups = [lines]
    elif len(lines) == 6:
        groups = [lines[:3], lines[3:]]
    else:
        groups = [lines[i : i + 4] for i in range(0, len(lines), 4)]
    return "\n\n".join("\n".join(group) for group in groups)
