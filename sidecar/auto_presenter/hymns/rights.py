"""Whether a hymn's words are free to use, and where.

The Open Hymnal judges public domain by US law, and says so on its Copying
page. That isn't the whole world: "Great Is Thy Faithfulness" (published 1923)
is public domain in the US, but its author died in 1960, so it is in copyright
in the UK, the EU and Australia until 2030 — and is still licensed there. So
this module draws its own, stricter line, and only for the *words*: slides
show words, so a copyrighted harmonisation or tune doesn't matter here.

    public_domain       free everywhere: published 95+ years ago (the US rule)
                        AND every author and translator died 70+ years ago
                        (the UK/EU/Canada/Australia rule)
    public_domain_us    the Open Hymnal says public domain, but it fails the
                        line above (or a date we'd need is unknown)
    free_for_worship    copyrighted; the writer lets churches reproduce it for
                        worship, unaltered, with the notice kept
    check               anything else — read the notice

Anything that can't be verified falls to the more cautious status, never the
less cautious one. The app never decides for the church: these statuses are
shown before a hymn is added, and the notice travels with the song.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass

PUBLIC_DOMAIN = "public_domain"
PUBLIC_DOMAIN_US = "public_domain_us"
FREE_FOR_WORSHIP = "free_for_worship"
CHECK = "check"

LABELS = {
    PUBLIC_DOMAIN: "Public domain",
    PUBLIC_DOMAIN_US: "Public domain in the US",
    FREE_FOR_WORSHIP: "Free for worship",
    CHECK: "Check the notice",
}

#: Years after publication before a work is public domain in the US.
US_TERM = 95
#: Years after the author's death in most of the rest of the world.
LIFE_TERM = 70
#: Someone who wrote this long ago is safely past `LIFE_TERM`, even when the
#: files don't give their death date. (Writing at 20 and living past 90 would
#: still be well inside it.)
LONG_AGO = 150

_YEAR_RE = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})\b")


@dataclass(frozen=True)
class Contributor:
    name: str
    died: int | None


@dataclass(frozen=True)
class Rights:
    status: str
    #: One sentence the operator reads before adding the hymn.
    explanation: str

    @property
    def label(self) -> str:
        return LABELS[self.status]


def words_years(credits: list[str] | str) -> list[int]:
    """Years in the words and translation credits — not the music's."""
    text = " ".join(credits) if isinstance(credits, list) else credits
    words_part = re.split(r"\b(?:Music|Tune|Setting|Melody)\b\s*(?:and\s+\w+\s*)?:", text)[0]
    years = [int(y) for y in _YEAR_RE.findall(words_part)]
    # "4th Century" dates a text as surely as a year does.
    years += [
        int(c) * 100 for c in re.findall(r"(\d{1,2})(?:st|nd|rd|th)\s+century", words_part, re.I)
    ]
    return years


def classify(
    copyright_line: str,
    contributors: list[Contributor],
    years: list[int],
    *,
    anonymous: bool = False,
    today: _dt.date | None = None,
) -> Rights:
    """Decide the status of a hymn's words. See the module docstring."""
    year = (today or _dt.date.today()).year
    line = " ".join(copyright_line.split()).lower()
    line = line.removeprefix("copyright:").strip()

    words_copyrighted = bool(
        re.search(r"\b(words|lyrics|this hymn)\b[^.]*\bcopyright\b", line)
        or re.search(r"\bcopyright\b[^.]*\b(words|lyrics)\b", line)
        or (line.startswith("copyright") and "public domain" not in line)
    )
    if words_copyrighted:
        if "freely reproduced" in line or "freely distributable" in line:
            return Rights(
                FREE_FOR_WORSHIP,
                "Copyrighted, but the writer lets churches use it in worship as long "
                "as the words aren't changed and the copyright notice is kept.",
            )
        return Rights(CHECK, "Copyrighted. Read the notice before using it.")

    says_public = "public domain" in line
    if not says_public and "freely reproduced" in line:
        # "This hymn (lyrics, music, and setting) may be freely reproduced or
        # used for Christian worship… All other rights reserved."
        return Rights(
            FREE_FOR_WORSHIP,
            "Copyrighted, but the writer lets churches use it in worship as long "
            "as the copyright notice is kept.",
        )
    if not says_public:
        return Rights(CHECK, "The source doesn't state the copyright of the words.")

    newest = max(years) if years else None
    us_ok = newest is not None and newest <= year - US_TERM - 1
    people_ok, unknown = True, []
    for person in contributors:
        if person.died is None:
            unknown.append(person.name)
        elif person.died > year - LIFE_TERM - 1:
            people_ok = False
            reason = (
                f"{person.name} died in {person.died}, so outside the US the words are "
                f"in copyright until {person.died + LIFE_TERM}."
            )
            return Rights(PUBLIC_DOMAIN_US, f"Public domain in the US. {reason}")
    if unknown and not (newest is not None and newest <= year - LONG_AGO):
        return Rights(
            PUBLIC_DOMAIN_US,
            "Public domain in the US. We couldn't confirm when "
            f"{_names(unknown)} died, so we can't say about other countries.",
        )
    if not contributors and not anonymous and not (newest and newest <= year - LONG_AGO):
        return Rights(
            PUBLIC_DOMAIN_US,
            "Public domain in the US. We couldn't confirm who wrote the words, "
            "so we can't say about other countries.",
        )
    if not us_ok:
        return Rights(
            CHECK if newest is None else PUBLIC_DOMAIN_US,
            "We couldn't confirm when the words were published."
            if newest is None
            else f"The Open Hymnal lists these words (from {newest}) as public domain "
            f"in the US; we only call words public domain everywhere once they're "
            f"{US_TERM} years old.",
        )
    if people_ok:
        return Rights(
            PUBLIC_DOMAIN,
            "Free to use anywhere: the words are old enough, and their writers "
            "died long enough ago, to be public domain everywhere.",
        )
    return Rights(CHECK, "Read the notice before using it.")  # pragma: no cover


def _names(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
