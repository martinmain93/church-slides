"""Suggest the next chord, or a whole phrase, and refuse what does not anchor.

Editor-time only. One short call after a chord is placed — never during a
service. The model may return a single next chord or, when it is sure, every
chord in the next phrase (including a phrase that runs from one verse into
the next). A local gate then keeps only placements whose chord parses and
whose word is actually on the target line. A phrase is shown whole only when
every placement in it survives; one failure drops the run back to a single
chord, or to nothing.

`gpt-oss-20b` on Groq is the measured fast model (`decision.instruction_models`).
Reasoning is pinned to low so the reply lands between clicks instead of
spending seconds thinking.

Groq's free tier allows 8,000 tokens a minute on this model, output
included. A call is ~1,500 prompt tokens for a 24-line window, and low
reasoning still sometimes writes 2,000 tokens, so output is capped: a reply
cut off before its tool call is a decline, not an error. The editor keeps
its own token budget (`chordModel.ts`) and backs off on `RATE_LIMITED`.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
from dataclasses import dataclass

from auto_presenter.api_keys import endpoint
from auto_presenter.chords import words
from auto_presenter.http_client import REQUEST_ERRORS, open_json_post, read_json
from auto_presenter.protocol import ChordPlacementInfo, ChordSuggestLine

log = logging.getLogger(__name__)

# Root plus a permissive quality suffix. Lyric words ("Blessed", "name") fail
# because letters outside the quality vocabulary cannot fill the suffix.
#: Sent back when Groq answers 429, so the editor can wait before asking again.
RATE_LIMITED = "Chord suggestions are busy; try again in a minute"
MAX_COMPLETION_TOKENS = 1024

_CHORD = re.compile(
    r"^[A-G][#b♯♭]?"
    r"(?:maj|ma|min|dim|aug|sus|add|no|alt|omit|m|M|°|ø|\+|-|Δ|\^|\d|[#b♯♭(),])*"
    r"(?:/[A-G][#b♯♭]?)?$"
)

TOOL_SPEC: dict = {
    "type": "function",
    "function": {
        "name": "suggest_chords",
        "description": (
            "Place chords on lyric lines that do not have them yet. "
            "Return nothing if you are not sure."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "phrase": {
                    "type": "boolean",
                    "description": (
                        "True only when every placement is one musical phrase "
                        "and you are sure of all of them. False when you are "
                        "sure of only the single next chord."
                    ),
                },
                "placements": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "line_id": {
                                "type": "string",
                                "description": "Copy line_id from the lyric list. Never invent one.",
                            },
                            "word": {
                                "type": "string",
                                "description": "One word from that line, copied verbatim.",
                            },
                            "word_index": {
                                "type": "integer",
                                "description": "The number shown after that word, such as 2 for holy[2].",
                            },
                            "chord": {
                                "type": "string",
                                "description": "One chord symbol, such as A, D/F#, or F#m.",
                            },
                        },
                        "required": ["line_id", "word", "word_index", "chord"],
                    },
                },
            },
            "required": ["phrase", "placements"],
        },
    },
}

SYSTEM_PROMPT = (
    "You chart worship songs. You are given lines that already have chords and "
    "lines that still need them. Return only chords you are sure of.\n"
    "A repeated lyric is not automatically the same chord. A title line that "
    "opens a verse and the same title line that closes it are often different "
    "chords — the last one is the turnaround. Do not copy the opening chord "
    "onto that closing line while the verse is still being charted.\n"
    "Read the meter. One harmonic phrase may start in a verse and finish in "
    "the next. When you are sure of that whole phrase, return every chord in "
    "it and set phrase to true. When you are sure of only the next chord, "
    "return that one chord and set phrase to false. When you are not sure, "
    "return no placements.\n"
    "Each word is followed by its number, like holy[2]. Copy line_id, the "
    "word and its number from the list you were given; a repeated word is "
    "told apart only by its number. One chord symbol per placement. Never "
    "invent a line. Never return a word whose number is listed as chorded."
)


@dataclass(frozen=True)
class ChordModelBackend:
    provider: str = "groq"
    base_url: str = "https://api.groq.com/openai/v1"
    model: str = "openai/gpt-oss-20b"
    timeout_seconds: float = 8.0

    def request(self, song_key: str, lines: list[ChordSuggestLine]) -> tuple[list[dict], bool]:
        """Raw placements and the model's phrase flag. `([], False)` on failure."""
        where = endpoint(self.provider, self.base_url)
        if where is None:
            log.warning("no API key saved for %s; cannot suggest chords", self.provider)
            raise MissingKey(self.provider)
        base_url, key = where
        payload = {
            "model": self.model,
            "temperature": 0,
            "reasoning_effort": "low",
            "max_completion_tokens": MAX_COMPLETION_TOKENS,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _prompt(song_key, lines)},
            ],
            "tools": [TOOL_SPEC],
            "tool_choice": "auto",
        }
        try:
            response = open_json_post(
                f"{base_url}/chat/completions",
                payload,
                api_key=key,
                timeout_seconds=self.timeout_seconds,
            )
            data = read_json(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                log.info("chord suggestion rate-limited by %s", self.provider)
                raise RateLimited() from exc
            log.exception("chord suggestion request failed")
            raise ModelUnreachable() from exc
        except REQUEST_ERRORS as exc:
            log.exception("chord suggestion request failed")
            raise ModelUnreachable() from exc
        try:
            calls = data["choices"][0]["message"].get("tool_calls") or []
            if not calls:
                return [], False
            args = json.loads(calls[0]["function"]["arguments"])
            placements = args.get("placements") or []
            if not isinstance(placements, list):
                return [], False
            return placements, bool(args.get("phrase"))
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            log.exception("unexpected chord suggestion response shape")
            return [], False


class MissingKey(Exception):
    def __init__(self, provider: str) -> None:
        super().__init__(provider)
        self.provider = provider


class ModelUnreachable(Exception):
    pass


class RateLimited(ModelUnreachable):
    pass


DEFAULT_BACKEND = ChordModelBackend()


def suggest(
    song_key: str,
    lines: list[ChordSuggestLine],
    backend: ChordModelBackend = DEFAULT_BACKEND,
) -> tuple[list[ChordPlacementInfo], bool, str | None]:
    """Verified placements, whether they are one phrase, and an error string.

    Never raises. An empty list with no error means the model looked and
    found nothing it would stand behind.
    """
    try:
        raw, phrase = backend.request(song_key, lines)
    except MissingKey as err:
        return [], False, f"No {err.provider} API key saved"
    except RateLimited:
        return [], False, RATE_LIMITED
    except ModelUnreachable:
        return [], False, "Chord model could not be reached"
    kept, show_phrase = verify(lines, raw, phrase)
    return [
        ChordPlacementInfo(
            line_id=item["line_id"],
            word=item["word"],
            word_index=item["word_index"],
            chord=item["chord"],
        )
        for item in kept
    ], show_phrase, None


def verify(
    lines: list[ChordSuggestLine],
    raw: list[dict],
    phrase: bool,
) -> tuple[list[dict], bool]:
    """Keep placements that name a real unchorded word. A phrase survives only intact."""
    by_id = {line.line_id: line for line in lines}
    verified: list[dict] = []
    seen: set[tuple[str, int]] = set()
    failed = False
    for item in raw:
        if not isinstance(item, dict):
            failed = True
            continue
        line_id = str(item.get("line_id", "")).strip()
        chord = str(item.get("chord", "")).strip()
        line = by_id.get(line_id)
        if line is None or line.role != "target" or not _is_chord(chord):
            failed = True
            continue
        chorded = set(line.chorded_word_indexes)
        taken = chorded | {index for lid, index in seen if lid == line_id}
        found = words.resolve(
            line.line, str(item.get("word", "")), item.get("word_index", -1), avoid=taken
        )
        if found is None:
            failed = True
            continue
        index, token = found
        # Restating a chord that is already on the word is noise, not a miss.
        # It must not throw away the rest of a phrase.
        if index in taken:
            continue
        seen.add((line_id, index))
        verified.append({"line_id": line_id, "word": token, "word_index": index, "chord": chord})
        if len(verified) >= 16:
            break

    if not verified:
        return [], False
    if phrase and not failed and len(verified) >= 2:
        return verified, True
    return verified[:1], False


def _prompt(song_key: str, lines: list[ChordSuggestLine]) -> str:
    rows = []
    for line in lines:
        chords = ", ".join(line.chords) if line.chords else "none"
        taken = (
            ", ".join(str(index) for index in line.chorded_word_indexes)
            if line.chorded_word_indexes
            else "none"
        )
        rows.append(
            f"{line.line_id} | {line.role} | {line.section} | {words.numbered(line.line)} "
            f"| chords: {chords} | word numbers already chorded: {taken}"
        )
    key = song_key.strip() or "unknown"
    return f"Key: {key}\n\n" + "\n".join(rows)


def _is_chord(symbol: str) -> bool:
    return bool(symbol) and bool(_CHORD.match(symbol))
