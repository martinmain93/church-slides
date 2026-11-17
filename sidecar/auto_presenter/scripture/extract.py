"""Find the Bible passages in a message the pastor sent.

"Sermon: John 3:1-21, also Rom 5:8 and Eph 2:8-9" pasted into the Scripture
tab becomes three passages to add to the service (`docs/done/SERVICE_ITEMS.md`).

The model writes the references when one is available; our side only checks
them against the verse store, which is the same split as `show_scripture` and
the reason a made-up reference can never reach a screen. Without a model (no
key, offline) each phrase of the message is offered to the reference parser
as it stands — the parser decides, nothing here pattern-matches prose. A
human approves the result either way, so a missed passage costs a retype and
a wrong one costs nothing.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterable

from auto_presenter.http_client import REQUEST_ERRORS, open_json_post, read_json
from auto_presenter.scripture.references import ReferenceError

log = logging.getLogger(__name__)

#: Phrases end at line breaks, semicolons, commas and joining words. Only
#: used to cut the message into pieces for the parser to accept or refuse.
_PHRASE_BREAKS = re.compile(r"\n|;|,|\s+(?:and|also|then|plus)\s+|\s+&\s+", re.IGNORECASE)

#: Every run of up to this many words in a phrase is offered, longest first
#: from each start, so words around a reference ("Sermon: John 3:1-21. Thanks!")
#: fall away. The longest book name with a range ("Song of Solomon 2:1-3") is
#: four words; six leaves room for spacing like "1 Cor 13 : 4".
_MAX_SPAN_WORDS = 6
#: A phrase longer than this is prose, and only its start is read.
_MAX_PHRASE_WORDS = 40

#: "Psalm 23" means the whole chapter. The parser wants verses, and the
#: store clamps a range to the verses that exist, so a whole chapter is asked
#: for as 1 to this.
WHOLE_CHAPTER_LAST_VERSE = 999
_CHAPTER_ONLY = re.compile(r"^(.*\D)\s*(\d+)\s*$")

EXTRACT_TOOL = {
    "type": "function",
    "function": {
        "name": "propose_passages",
        "description": "The Bible passages the message asks to show, in the order it gives them.",
        "parameters": {
            "type": "object",
            "properties": {
                "references": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "One passage per entry, as a reference like \"John 3:1-21\" or "
                        "\"Romans 5:8\". A whole chapter is \"Psalm 23\"."
                    ),
                }
            },
            "required": ["references"],
        },
    },
}

EXTRACT_PROMPT = (
    "A pastor has sent the church's tech team a message about Sunday's service. "
    "List every Bible passage in it that should go on screen, using propose_passages. "
    "One passage per entry; split \"John 3:16, 18\" into \"John 3:16\" and \"John 3:18\". "
    "Write book names in full. Never add a passage the message doesn't mention. "
    "If it mentions none, call propose_passages with an empty list."
)


def read_phrases(text: str, resolve: Callable[[str], object]) -> list[object]:
    """The passages in `text`, read phrase by phrase, each once, in order.

    Within a phrase, each run of words from a start is offered longest
    first; one the store accepts is taken and reading resumes after it, so
    "1 John 3:16" is never also "John 3:16", and two references a single-line
    box ran together ("Psalm 23:1-3 Reading: John 3:16") are both found.
    `resolve` raises `ReferenceError` for anything that isn't a real passage.
    """
    found: list[object] = []
    seen: set[str] = set()
    for phrase in _PHRASE_BREAKS.split(text):
        words = phrase.split()[:_MAX_PHRASE_WORDS]
        start = 0
        while start < len(words):
            hit = None
            for end in range(min(len(words), start + _MAX_SPAN_WORDS), start, -1):
                span = " ".join(words[start:end]).strip(" .:-!?()\"'")
                passage = _first_resolved(span, resolve) if span else None
                if passage is not None:
                    hit = (passage, end)
                    break
            if hit is None:
                start += 1
                continue
            passage, start = hit
            _keep(passage, found, seen)
    return found


def resolve_named(named: Iterable[str], resolve: Callable[[str], object]) -> list[object]:
    """Each reference a model named that the store accepts, once, in order."""
    found: list[object] = []
    seen: set[str] = set()
    for ref in named:
        passage = _first_resolved(ref, resolve)
        if passage is not None:
            _keep(passage, found, seen)
    return found


def chapter_or_passage(candidate: str) -> list[str]:
    """The forms to try for one candidate: as written, then as a whole chapter."""
    tries = [candidate]
    if ":" not in candidate and _CHAPTER_ONLY.match(candidate):
        tries.append(f"{candidate}:1-{WHOLE_CHAPTER_LAST_VERSE}")
    return tries


def _keep(passage: object, found: list[object], seen: set[str]) -> None:
    key = getattr(passage, "reference", None) or str(passage)
    if key not in seen:
        seen.add(key)
        found.append(passage)


def _first_resolved(candidate: str, resolve: Callable[[str], object]) -> object | None:
    for form in chapter_or_passage(candidate):
        try:
            return resolve(form)
        except ReferenceError:
            continue
    return None


def ask_model(
    text: str, *, base_url: str, model: str, api_key: str | None, timeout_seconds: float = 15.0
) -> list[str] | None:
    """The references a model reads in `text`, or None if it couldn't be asked.

    None (no answer) and [] (the model found nothing) are different: only the
    first falls back to reading the message phrase by phrase.
    """
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": EXTRACT_PROMPT},
            {"role": "user", "content": text},
        ],
        "tools": [EXTRACT_TOOL],
        # Never forced: forcing a tool makes "none here" unsayable for some
        # providers (docs/done/BIBLE_INTEGRATION.md §4).
        "tool_choice": "auto",
        "stream": False,
        "temperature": 0.0,
    }
    try:
        with open_json_post(
            f"{base_url.rstrip('/')}/chat/completions",
            payload,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
        ) as resp:
            body = read_json(resp)
    except REQUEST_ERRORS as exc:
        log.warning("reference extraction call failed: %s", exc)
        return None
    try:
        calls = body["choices"][0]["message"].get("tool_calls") or []
    except (KeyError, IndexError, TypeError):
        return None
    for call in calls:
        fn = call.get("function") or {}
        if fn.get("name") != "propose_passages":
            continue
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            return None
        refs = args.get("references")
        if isinstance(refs, list):
            return [r.strip() for r in refs if isinstance(r, str) and r.strip()]
    # No tool call at all: the model answered in prose, which says nothing
    # usable about the passages — treat it as not asked.
    return None
