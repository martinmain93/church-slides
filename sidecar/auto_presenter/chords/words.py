"""The words of one lyric line, counted the same way the editor counts them.

A chord is addressed by its word's place on the line, not by the word's
text: "Holy, holy, holy" has three different places for a chord. Both sides
split on whitespace and skip tokens with no letter or digit (a lone dash),
so index 2 here is index 2 in `src/lib/chords/lineWords.ts`.
"""

from __future__ import annotations

import re

_TOKEN = re.compile(r"\S+")


def norm(word: str) -> str:
    return re.sub(r"[^a-z0-9']", "", word.lower())


def line_words(line: str) -> list[str]:
    """The line's words, as written, in order."""
    return [token for token in _TOKEN.findall(line) if norm(token)]


def numbered(line: str) -> str:
    """`Holy[0] holy[1] holy[2]` — how the model is shown a line."""
    return " ".join(f"{word}[{index}]" for index, word in enumerate(line_words(line)))


def resolve(
    line: str,
    word: str,
    index: object = -1,
    *,
    avoid: set[int] | frozenset[int] = frozenset(),
) -> tuple[int, str] | None:
    """The `(index, word)` a placement means, or None if it names no word.

    The index wins when its word matches the text. A missing or wrong index
    falls back to the first matching word not in `avoid`, then to the first
    matching word at all.
    """
    words = line_words(line)
    want = norm(word)
    try:
        at = int(index)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        at = -1
    if 0 <= at < len(words) and (not want or norm(words[at]) == want):
        return at, words[at]
    if not want:
        return None
    matches = [i for i, token in enumerate(words) if norm(token) == want]
    for i in matches:
        if i not in avoid:
            return i, words[i]
    if matches:
        return matches[0], words[matches[0]]
    return None
