"""Spoken instructions — "let's go back to verse one" as a slide change.

Today the app hears that sentence and tries to match it against lyrics, which
is the worst possible interpretation: it is not sung, it does not appear on
any slide, and matching it anyway is exactly the *off-book* case
`decision.singing` already detects. This module is what should happen instead
of matching: a single narrow tool call, resolved against the setlist that is
already loaded.

Design follows the roadmap's shape for why a small model fits here better than
most LLM features — the request space is narrow, the tools are few, and it is
**single-step**: one utterance, one action, never a plan. Two action tools
plus an explicit `no_action`: `go_to_slide` for a song already numbered in
the prompt, `search_library` for a title that is not. Tool *selection* is
the harder thing for a 3B model; `no_action` exists because offering only
one action tool made llama3.2:3b call it on 10 of 10 ordinary sentences.

The `go_to_slide` tool takes a **song index and a section key already enumerated in the
prompt**, not free text and not raw slide numbers. Two things this avoids:
asking the model to invent a slide index it cannot see (it has no numeric
view of the setlist, only the text description we hand it), and asking it to
produce a section name it then has to be fuzzy-matched back against ours. It
only ever has to copy a token we already gave it — copying is far more
reliable for a 3B model than generating a well-formed reference from scratch.

`search_library` is the tool for a song **not** in tonight's numbered list
("let's bring up Agnus Dei", "chorus of Reckless Love"). The model never
sees the library; it emits a title (or a distinctive lyric) and this
module resolves it against the phonemic fingerprint index.

`show_scripture` / `hide_scripture` put a Bible passage on screen and take it
down. They are offered only when a verse store is installed. The model writes
the reference ("Jeremiah 29:11") from whatever was said — spoken numbers, a
chapter and verse said the long way, a paraphrase of the verse — and this side
does no interpreting of the speech at all: it only checks that the reference
names a real passage, and does nothing if it doesn't.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import ClassVar, Protocol

from auto_presenter.decision.library_recovery import ImportSong, search_title
from auto_presenter.error_reports import said
from auto_presenter.http_client import REQUEST_ERRORS, open_json_post, read_json
from auto_presenter.index.fingerprint import FingerprintIndex
from auto_presenter.index.types import IndexedSetlist

log = logging.getLogger(__name__)

#: The tool's name and JSON schema, in OpenAI/Ollama function-calling shape.
#: `section_key` is genuinely open-ended text (not an enum) because the valid
#: keys differ per song and are listed in the prompt itself, not in the
#: schema — enumerating them here would mean rebuilding the schema on every
#: setlist change.
TOOL_SPEC: dict = {
    "type": "function",
    "function": {
        "name": "go_to_slide",
        "description": (
            "Move the display to a specific song and section. Use this for "
            "any request to jump to, return to, repeat, or bring up a part "
            "of a song — 'go back to verse one', 'let's do the chorus "
            "again', 'can we start over'. Do NOT call this for anything "
            "that isn't a request to change what's on screen — prayers, "
            "announcements, banter, and ordinary conversation should get no "
            "tool call at all."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "song_index": {
                    "type": ["integer", "null"],
                    "description": (
                        "Index of the song from the numbered list in "
                        "context. Use null to mean the song currently "
                        "displayed."
                    ),
                },
                "section_key": {
                    "type": "string",
                    "description": (
                        "One of the exact section keys listed for that "
                        "song in context, e.g. 'verse:1' or 'chorus:1'. "
                        "Copy it verbatim — do not paraphrase."
                    ),
                },
            },
            "required": ["section_key"],
        },
    },
}

#: Title-first library lookup. Offered alongside `go_to_slide` so the model
#: has a named way to ask for a song that is not in the numbered list,
#: instead of inventing a `song_index` that then fails to resolve. The
#: query is free text because the whole point is spelling-mishap-proof
#: retrieval on *our* side, not asking a 3B model to emit a song id.
SEARCH_LIBRARY_TOOL_SPEC: dict = {
    "type": "function",
    "function": {
        "name": "search_library",
        "description": (
            "Bring up a song that is NOT in tonight's numbered list. Use "
            "this when the speaker names a song that does not appear in "
            "the list, e.g. 'let's bring up Agnus Dei', 'can we go to "
            "the chorus of Reckless Love'. Do NOT use this for a song "
            "that is already numbered — call go_to_slide for those."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "The song title as heard, even if the spelling is "
                        "rough. Distinctive lyrics are acceptable when no "
                        "title was given."
                    ),
                },
                "section_key": {
                    "type": "string",
                    "description": (
                        "Optional section to land on, e.g. 'chorus:1' or "
                        "'verse:1', when the speaker named one. Omit to "
                        "open at the first lyric slide."
                    ),
                },
            },
            "required": ["query"],
        },
    },
}

#: Put a Bible passage on screen. The model writes the reference itself: it
#: hears "Jeremiah twenty-nine eleven" or "John chapter three verse sixteen"
#: and knows what was meant far better than any parser of transcribed speech
#: would, and it can place a passage from a paraphrase ("the plans I have for
#: you verse"). Our side only checks that what it wrote is a real passage —
#: `scripture.parse` + the verse store — so an invented citation does nothing.
SHOW_SCRIPTURE_TOOL_SPEC: dict = {
    "type": "function",
    "function": {
        "name": "show_scripture",
        "description": (
            "Put a Bible passage on screen. Use this when the speaker asks "
            "for a passage to be shown, brought up, or read together — "
            "'bring up Jeremiah 29:11', 'turn in your Bibles to Romans 8', "
            "'can we get John 3:16 on the screen'. Not for a verse merely "
            "quoted or mentioned in passing. A request relative to the passage "
            "on screen ('the next few verses', 'keep going', 'the verse "
            "before') continues from it. If only a book has been named so "
            "far, with no chapter, they are still speaking: call no_action."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reference": {
                    "type": "string",
                    "description": (
                        "One passage in standard form: book, chapter:verse, "
                        "or chapter:first-last — e.g. 'Jeremiah 29:11', "
                        "'1 Corinthians 13:4-7'. Write numbers as digits "
                        "even if they were spoken as words. If only a "
                        "chapter was named, give its first verse."
                    ),
                },
                "display": {
                    "type": "string",
                    "enum": ["full", "beside"],
                    "description": (
                        "'full' (the default) replaces the lyrics; 'beside' "
                        "keeps the lyrics up with the passage next to them. "
                        "Use 'beside' only if they ask for both."
                    ),
                },
                "translation": {
                    "type": "string",
                    "description": (
                        "Only if they name a Bible translation ('read it from "
                        "the King James', 'in the NIV'): its name or "
                        "abbreviation as said. Otherwise leave this out."
                    ),
                },
            },
            "required": ["reference"],
        },
    },
}

HIDE_SCRIPTURE_TOOL_SPEC: dict = {
    "type": "function",
    "function": {
        "name": "hide_scripture",
        "description": (
            "Take the Bible passage off the screen — 'take the verse down', "
            "'you can clear that scripture'."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

#: A second, argument-free tool meaning "do nothing." Measured necessary, not
#: cosmetic: with only `go_to_slide` offered, llama3.2:3b called it on
#: **10 of 10** ordinary sentences with no request in them at all — "amen,
#: come on somebody," "we're going to take up an offering now." Ollama's
#: tool-calling has no strong way to abstain when a single tool is on offer;
#: giving the model an explicit, named way to choose "nothing" fixed it
#: outright — the same 10 sentences dropped to **0 of 10** false fires.
#: See `docs/done/SPOKEN_INSTRUCTIONS.md` for the full measurement.
NO_ACTION_TOOL_SPEC: dict = {
    "type": "function",
    "function": {
        "name": "no_action",
        "description": (
            "Call this when nothing was said that requests changing the "
            "display — prayer, announcements, banter, reactions, ordinary "
            "conversation. This is the common case: most things said "
            "between songs are not a request to change the screen."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


#: "Not finished yet." Requests are heard as they're spoken, and a sentence
#: can sound complete before it is: "switch to the first verse" — of this
#: song, or of the one about to be named? Seen 2026-10-17: acting on it put
#: Reckless Love verse 1 up, two seconds before "...of Build My Life" arrived.
#: A named way to say "wait" lets the model hold off instead of guessing;
#: the caller asks again when more words come.
WAIT_FOR_MORE_TOOL_SPEC: dict = {
    "type": "function",
    "function": {
        "name": "wait_for_more",
        "description": (
            "Call this when a request has started but may not be finished — "
            "it trails off, or names a section without saying which song when "
            "a song name may be about to follow (\"switch to the first verse\" "
            "...). You will be asked again when more is heard. Don't use it for "
            "a complete request: \"go to the chorus\" is complete."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


@dataclass(frozen=True)
class WaitForMore:
    """The model heard the start of a request, not all of it."""


@dataclass(frozen=True)
class ToolCall:
    """One parsed model action, before it is resolved against a setlist."""

    name: str
    song_index: int | None = None
    section_key: str = ""
    query: str = ""
    #: `show_scripture` only: the passage as the model wrote it, and where.
    reference: str = ""
    display: str = "full"
    #: `show_scripture` only: a translation the speaker named, as said.
    translation: str = ""


@dataclass(frozen=True)
class GoToSlide:
    """Move the display to a slide already in tonight's set."""

    item_idx: int
    slide_idx: int
    #: What the model actually said, for the debug panel and logs.
    song_index: int | None
    section_key: str


@dataclass(frozen=True)
class ShowScripture:
    """Put a passage on screen, or take it down (`display == "off"`)."""

    #: Canonical ("Jeremiah 29:11"), already checked against the verse store.
    reference: str
    display: str
    #: A translation the speaker named, as said ("King James"); matched to an
    #: installed one by the caller, which knows what is installed.
    translation: str = ""


@dataclass(frozen=True)
class InstructionItem:
    """One setlist song, as described to the model."""

    item_idx: int
    title: str
    #: Section keys in slide order, de-duplicated (a repeated chorus lists
    #: once). The model picks one of these verbatim.
    section_keys: tuple[str, ...]


@dataclass(frozen=True)
class InstructionContext:
    """Everything the model is told about where we are."""

    items: tuple[InstructionItem, ...]
    current_item_idx: int
    #: The tracker's current best guess at what's being sung right now — not
    #: necessarily what's on screen (the tracker anticipates and can lag),
    #: but the freshest read available. Grounds relative references
    #: ("again", "one more time on that last part") against something,
    #: instead of only ever knowing the song. Empty string when unknown (no
    #: matcher belief yet, or the current slide carries no section key).
    current_section_key: str = ""
    #: Whether a verse store is installed. The scripture tools are offered
    #: only then — a tool that can never succeed is one more way to pick the
    #: wrong tool, which is the error this feature is most prone to.
    scripture: bool = False
    #: The passage a person has on screen, and the last one shown — so "and
    #: the next few verses" can be read against it. Live, with Hebrews
    #: 11:1-3 up and the model unaware of it, that request moved the *song*
    #: to its next verse slides, three times.
    passage_on_screen: str = ""
    passage_last: str = ""


def build_context(
    setlist: IndexedSetlist, current_item_idx: int, current_slide_idx: int | None = None,
) -> InstructionContext:
    """Describe a setlist the way the model needs to see it.

    Section keys come from `section_index`, which is already how the
    transition prior finds "the chorus" — see `tracker.transitions`. Order
    matches first appearance, not alphabetical, so "verse:1" precedes
    "chorus:1" the way it does in the song.

    `current_slide_idx` is optional because callers exist that don't have a
    live belief to read it from (tests, the harness). Passing it in rather
    than reaching into the setlist ourselves keeps this function pure — the
    server is the one place that knows what the tracker currently believes.
    """
    items = []
    for item in setlist.items:
        # A passage or text item in the service reaches the engine as one
        # wordless slide (`setlistContent.ts`): nothing to go to by voice,
        # and an untitled entry would only make the model guess at it.
        if not any(slide.words for slide in item.slides):
            continue
        seen: dict[str, None] = {}
        for slide in item.slides:
            # Not the leading blank's section: offered it, the model took
            # "go to John 3" to mean the blank before the passage.
            if slide.section_key and slide.words:
                seen.setdefault(slide.section_key, None)
        # Real bug, caught on a real recording: this used to read
        # `item.slides[0].text`, which is always the leading blank slide —
        # every song's "title" in the prompt was an empty string. The model
        # never had a name for anything; it could only ever act on "the
        # current song." Fixed by threading the real title through
        # `SetlistItemContent.title` -> `IndexedItem.title`.
        title = item.title or item.song_id
        items.append(
            InstructionItem(
                item_idx=item.item_idx,
                title=title,
                section_keys=tuple(seen),
            )
        )
    current_section = ""
    if current_slide_idx is not None:
        current_item = setlist.item(current_item_idx)
        current_slide = current_item.slide(current_slide_idx) if current_item else None
        if current_slide is not None:
            current_section = current_slide.section_key
    return InstructionContext(
        items=tuple(items),
        current_item_idx=current_item_idx,
        current_section_key=current_section,
    )


def _first_lyric_slide(item) -> int | None:
    """The first slide with words on it.

    Every song carries a synthetic leading blank (see `slideSequence.ts`), so
    slide 0 is never what anyone means by "the start".
    """
    for i, slide in enumerate(item.slides):
        if slide.words:
            return i
    return None


def resolve(
    setlist: IndexedSetlist, context: InstructionContext,
    song_index: int | None, section_key: str,
) -> GoToSlide | None:
    """Turn the model's tool call into a concrete slide.

    A section can repeat (a chorus sung four times has one `section_key` but
    several slide indices) — we land on the *first*, matching what "go to
    the chorus" means to a person: the start of it, not wherever it last
    happened to be.

    Returns `None` if the model named a song or section that doesn't exist —
    a hallucinated reference should do nothing, not move the screen to slide
    0 or raise.

    No section named means the start of the song, which is what asking for a
    song by name alone means to a person. `search_library` already documented
    that ("omit to open at the first lyric slide") while this path rejected
    it, so the same request behaved differently depending on whether the song
    happened to be in tonight's list.
    """
    item_idx = context.current_item_idx if song_index is None else song_index
    item = setlist.item(item_idx)
    if item is None:
        return None
    if not section_key:
        first = _first_lyric_slide(item)
        if first is None:
            return None
        return GoToSlide(
            item_idx=item_idx, slide_idx=first,
            song_index=song_index, section_key="",
        )
    slides = [i for i in item.section_index.get(section_key, ()) if item.slides[i].words]
    if not slides:
        # A blank's section ("other:0") still means the item: its start.
        if section_key not in item.section_index:
            return None
        return resolve(setlist, context, song_index, "")
    return GoToSlide(
        item_idx=item_idx, slide_idx=slides[0],
        song_index=song_index, section_key=section_key,
    )


def _format_context(context: InstructionContext) -> str:
    lines = ["Songs in tonight's set:"]
    for it in context.items:
        here = "  <- currently displayed" if it.item_idx == context.current_item_idx else ""
        sections = ", ".join(it.section_keys) or "(no sections)"
        lines.append(f"  [{it.item_idx}] {it.title}{here}")
        lines.append(f"      sections: {sections}")
    if context.current_section_key:
        lines.append(
            f"\nRight now the band is believed to be on "
            f"{context.current_section_key!r} of the currently displayed song."
        )
    if context.scripture:
        if context.passage_on_screen:
            lines.append(f"\nA Bible passage is on screen now: {context.passage_on_screen}.")
        elif context.passage_last:
            lines.append(
                f"\nNo Bible passage is on screen; the last one shown was "
                f"{context.passage_last}."
            )
    return "\n".join(lines)


class InstructionBackend(Protocol):
    """Something that can turn one utterance into a tool call, or nothing.

    Never raises on a malformed model response — a backend that can't parse
    its own output returns `None`, the same as "no command was meant." A
    missed command is a person pressing an arrow key; a wrongly-applied one
    is the screen jumping mid-service. The asymmetry is the same one that
    shaped `decision.singing`'s thresholds.

    A backend whose call can fail over a network should also offer
    `decide_strict`, which raises `BackendUnavailable` rather than flattening
    an unreachable server into "no action" — `FallbackBackend` needs that
    distinction to know whether retrying elsewhere is warranted. Optional: a
    backend with no transport cannot be unavailable.
    """

    def decide(
        self, utterance: str, context: InstructionContext
    ) -> ToolCall | None: ...


@dataclass
class RuleBasedBackend:
    """No model — keyword + title matching. Deterministic, for tests and for
    running the harness with no network and no local model installed.

    Not the shipped brain. It exists so `resolve()` and the wiring around it
    are testable without ollama, and as a documented floor: whatever local
    model ships must clear this by a wide margin to be worth the added
    complexity and the download.
    """

    #: word -> the section-key prefix it names.
    SECTION_WORDS: ClassVar[dict[str, str]] = {
        "verse": "verse", "chorus": "chorus", "bridge": "bridge",
        "tag": "tag", "outro": "outro", "intro": "other",
        "beginning": "other",
    }
    NUMBER_WORDS: ClassVar[dict[str, int]] = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "1": 1, "2": 2, "3": 3, "4": 4, "5": 5,
    }

    def decide(self, utterance: str, context: InstructionContext) -> ToolCall | None:
        words = utterance.lower().split()
        song_index = None
        for item in context.items:
            title_words = item.title.lower().split()
            if title_words and title_words[0] in words:
                song_index = item.item_idx
                break

        section_type = next(
            (t for w, t in self.SECTION_WORDS.items() if w in words), None
        )
        if section_type is None:
            return None
        number = next(
            (n for w, n in self.NUMBER_WORDS.items() if w in words), 1
        )
        return ToolCall(
            name="go_to_slide",
            song_index=song_index,
            section_key=f"{section_type}:{number}",
        )


#: Shared by every backend below. Extracted to a module constant 2026-09-03
#: when the local-only `OllamaBackend` grew a hosted-provider sibling — one
#: wording, not N copies to keep in sync.
#:
#: Strengthening this to specifically discourage guessing a section on an
#: ambiguous request was tried twice on llama3.2:3b (2026-09-03) — the
#: measured failure was "let's do the second verse one more time" resolving
#: to verse:1 instead of verse:2, a confidently wrong real slide rather than
#: a safe no-op. Both attempts made things worse: adding *any* qualifying
#: caution collapsed command recall from 7/10 to 3/10, then 1/10 — on a 3B
#: model, extra hedging language produces global hesitancy, not selective
#: caution, and it still didn't fix the targeted case. Reverted. The
#: ambiguous-guess failure is real and unresolved; the lever that actually
#: moved it was a better model (`qwen2.5:3b-instruct`, same size, resolves
#: that case correctly with this exact prompt), not more words here.
DEFAULT_SYSTEM_PROMPT = (
    "You control the slide display for a worship service. You hear "
    "everything the leader says between songs. Most of it is not "
    "meant for you: prayer, announcements, banter, reactions. Call "
    "go_to_slide when they ask to change what's on screen and the "
    "song is in tonight's numbered list. Call search_library when they "
    "name a song that is not in that list. Call wait_for_more when a "
    "request has begun but may not be finished. Otherwise call no_action. "
    "Words inside [sung: ...] are lyrics the band was singing, not "
    "something said to you: they are never a request, even when they "
    "contain a song's title — though the leader may also name that song "
    "outside the brackets. "
    "Lines of the songs being sung are not requests, even when they contain "
    "words like 'bring' or 'go'. "
    "You must call exactly one tool every time."
)

#: Appended when the scripture tools are offered.
SCRIPTURE_PROMPT = (
    " Call show_scripture when they ask for a Bible passage to be put on "
    "screen, and hide_scripture when they ask for it to be taken down."
)


def _parse_tool_call(calls: list[dict]) -> ToolCall | None:
    """Extract a `ToolCall` from an OpenAI-shaped tool-call list, or `None`
    if there's nothing usable — never raises.

    Shared by every backend: local and hosted models alike return the same
    `choices[0].message.tool_calls` shape once behind an OpenAI-compatible
    endpoint (verified against ollama, and documented for Groq and Gemini's
    compatibility layer), so this parsing only needs to exist once.
    """
    if not calls:
        # No call at all is the same failure mode `no_action` exists to
        # avoid, just spelled differently — treat it the same way.
        return None
    name = calls[0]["function"].get("name")
    if name == "no_action" or not name:
        return None
    if name == "wait_for_more":
        return ToolCall(name="wait_for_more")
    try:
        args = calls[0]["function"].get("arguments") or {}
        if isinstance(args, str):
            args = json.loads(args) if args.strip() else {}
        if name == "hide_scripture":
            return ToolCall(name="hide_scripture")
        if name == "show_scripture":
            reference = str(args.get("reference") or "").strip()
            if not reference:
                return None
            display = "beside" if args.get("display") == "beside" else "full"
            translation = str(args.get("translation") or "").strip()
            return ToolCall(
                name="show_scripture",
                reference=reference,
                display=display,
                translation=translation,
            )
        if name == "search_library":
            query = str(args.get("query") or "").strip()
            if not query:
                return None
            return ToolCall(
                name="search_library",
                query=query,
                section_key=str(args.get("section_key") or ""),
            )
        if name != "go_to_slide":
            return None
        # `.get`, not `[...]`. The schema marks this required and small models
        # omit it anyway — reasonably, when the request names a song and no
        # section ("can we do <title>"). A bare subscript raised KeyError,
        # which this function's own `except` turned into "unparseable", and a
        # perfectly good request did nothing at all. Measured: three of three
        # runs on one such utterance.
        section_key = args.get("section_key") or ""
        song_index = args.get("song_index")
        # Small models reliably get the *value* right and the *type*
        # wrong — the literal string "null" instead of JSON null shows
        # up often enough (measured on llama3.2:3b) to handle rather
        # than log as a parse failure.
        if isinstance(song_index, str) and song_index.strip().lower() in ("null", "none", ""):
            song_index = None
        return ToolCall(
            name="go_to_slide",
            song_index=int(song_index) if song_index is not None else None,
            section_key=str(section_key),
        )
    except (KeyError, TypeError, ValueError) as exc:
        # «…»: the call echoes what was said (a song name, a search), so error
        # reports strip it; the local log keeps it.
        log.warning("model returned an unparseable tool call: «%s» («%s»)", said(exc), said(calls))
        return None


class BackendUnavailable(Exception):
    """The call did not happen: transport error, HTTP status, or timeout.

    Distinct from a refusal because the two demand opposite responses.
    `decide` returns `None` for both — the model declining, and the request
    never reaching it — and that conflation has produced two wrong
    conclusions in this project already: once a timeout read as "the model
    declined every command", once a fast 503 read as a considered
    `no_action`.

    It is also what decides whether a fallback fires. Retrying a *refusal* on
    a weaker model invites it to invent an action the better one correctly
    declined; retrying an unreachable server is exactly what a fallback is
    for.
    """


@dataclass
class GenericLLMBackend:
    """Calls any OpenAI-compatible tool-calling endpoint — local or hosted.

    One class instead of one-per-provider because the wire format is the
    same once you're behind `/chat/completions`: ollama, Groq, and Gemini's
    OpenAI-compatibility layer all accept the same request shape and return
    `choices[0].message.tool_calls` in the same shape. Verified directly
    against ollama's `/v1/chat/completions` (2026-09-03); Groq and Gemini
    are implemented to the same documented contract but not yet verified
    end-to-end — see `decision.instruction_models` for provider entries and
    `docs/done/SPOKEN_INSTRUCTIONS.md` for what's confirmed vs. assumed.

    Never raises: connection failure, timeout, auth failure, or a response
    that doesn't parse as the tool schema all return `None`. A missed
    command gets repeated; the backend should never be the reason the whole
    feature crashes.
    """

    base_url: str
    model: str
    #: `None` for a local server with no auth (ollama). Hosted providers
    #: reject the request with 401 if this is missing or wrong, which
    #: surfaces as a `None` decision + a logged warning, same as any other
    #: failure — there is no separate "bad key" signal today.
    api_key: str | None = None
    timeout_seconds: float = 8.0
    system_prompt: str = DEFAULT_SYSTEM_PROMPT

    def decide(self, utterance: str, context: InstructionContext) -> ToolCall | None:
        """Never raises — the transcript handler relies on that."""
        try:
            return self.decide_strict(utterance, context)
        except BackendUnavailable:
            return None

    def decide_strict(
        self, utterance: str, context: InstructionContext
    ) -> ToolCall | None:
        """As `decide`, but raises `BackendUnavailable` when the call itself
        failed, rather than flattening that into "no action"."""
        tools = [TOOL_SPEC, SEARCH_LIBRARY_TOOL_SPEC]
        system_prompt = self.system_prompt
        if context.scripture:
            tools += [SHOW_SCRIPTURE_TOOL_SPEC, HIDE_SCRIPTURE_TOOL_SPEC]
            system_prompt += SCRIPTURE_PROMPT
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": f'{_format_context(context)}\n\nHeard: "{utterance}"',
                },
            ],
            "tools": [*tools, WAIT_FOR_MORE_TOOL_SPEC, NO_ACTION_TOOL_SPEC],
            "stream": False,
            # Deterministic, matching the same fix already shipped for
            # Whisper (`whisper_backend.py`'s beam_size=1, temperature=0).
            # This is structured single-answer extraction, not open-ended
            # generation — sampling only adds variance no one wants here.
            "temperature": 0.0,
        }
        try:
            with open_json_post(
                f"{self.base_url.rstrip('/')}/chat/completions",
                payload,
                api_key=self.api_key,
                timeout_seconds=self.timeout_seconds,
            ) as resp:
                body = read_json(resp)
        except REQUEST_ERRORS as exc:
            # Includes a body that is not JSON at all. `read_json` raises
            # `JSONDecodeError` there, which is a `ValueError` and so slipped
            # past the old URLError/TimeoutError/OSError list — see
            # `http_client.REQUEST_ERRORS`. The docstring above promises this
            # never raises, and the transcript handler relies on it.
            log.warning("model call to %s failed: %s", self.base_url, exc)
            raise BackendUnavailable(str(exc)) from exc

        try:
            calls = body["choices"][0]["message"].get("tool_calls") or []
        except (KeyError, IndexError, TypeError) as exc:
            log.warning("model response from %s had no usable choice: %s («%s»)",
                       self.base_url, type(exc).__name__, said(body))
            return None
        return _parse_tool_call(calls)


@dataclass
class OllamaBackend:
    """`GenericLLMBackend` pointed at a local ollama server.

    Kept as a distinct, named entry point — most callers want "the local
    model," not "an OpenAI-compatible endpoint at this particular URL" —
    but it is a thin convenience over the same generic implementation now
    that ollama's `/v1/chat/completions` endpoint is confirmed to behave
    identically to its native `/api/chat`. `host` is the bare origin
    (`http://localhost:11434`), not the `/v1` API root.
    """

    model: str
    host: str = "http://localhost:11434"
    timeout_seconds: float = 8.0
    system_prompt: str = DEFAULT_SYSTEM_PROMPT

    def _inner(self) -> GenericLLMBackend:
        return GenericLLMBackend(
            base_url=f"{self.host}/v1",
            model=self.model,
            timeout_seconds=self.timeout_seconds,
            system_prompt=self.system_prompt,
        )

    def decide(self, utterance: str, context: InstructionContext) -> ToolCall | None:
        return self._inner().decide(utterance, context)

    def decide_strict(
        self, utterance: str, context: InstructionContext
    ) -> ToolCall | None:
        return self._inner().decide_strict(utterance, context)


@dataclass
class FallbackBackend:
    """Ask a better model first; ask the on-device one if the call fails.

    This is what makes a hosted default defensible. The catalog's standing
    objection is sound — a worship service is exactly where the wifi fails and
    the failure is public — and it argues against *depending* on a network,
    not against using one. With a fallback, losing the connection costs
    accuracy, not the feature.

    Measured 2026-09-17 over 15 utterances: the hosted 20B scored 15/15 at
    ~0.5 s a call against the shipped local 7B's 14/15 at 1.8-2.8 s, and got
    right the one case no local model of any size managed — a request whose
    opening word the ASR had mis-heard. Better *and* faster, because the cost
    is a network round trip rather than local compute.

    Only a `BackendUnavailable` falls through. A refusal does not: re-asking a
    weaker model to second-guess a considered `no_action` invites it to invent
    an action the better one correctly declined, and most of what this feature
    hears is not a request.
    """

    primary: InstructionBackend
    secondary: InstructionBackend

    def decide(self, utterance: str, context: InstructionContext) -> ToolCall | None:
        strict = getattr(self.primary, "decide_strict", None)
        if strict is None:
            # A backend with no transport to fail (the rule-based one) cannot
            # be unavailable, so there is nothing to fall back from.
            return self.primary.decide(utterance, context)
        try:
            return strict(utterance, context)
        except BackendUnavailable as exc:
            log.warning("primary instruction model unavailable (%s); using fallback", exc)
            return self.secondary.decide(utterance, context)


@dataclass
class InstructionEngine:
    """Ties a backend to a live setlist: utterance in, a slide action or
    nothing out."""

    backend: InstructionBackend

    def decide(
        self, utterance: str, setlist: IndexedSetlist, current_item_idx: int,
        current_slide_idx: int | None = None,
        library: FingerprintIndex | None = None,
        scripture: Callable[[str], str | None] | None = None,
        passage_on_screen: str = "",
        passage_last: str = "",
    ) -> GoToSlide | ImportSong | ShowScripture | WaitForMore | None:
        """`scripture` turns a reference into its canonical form, or `None`
        if it isn't a real passage. Without one, the scripture tools are not
        offered at all."""
        context = build_context(setlist, current_item_idx, current_slide_idx)
        if scripture is not None:
            context = replace(
                context, scripture=True,
                passage_on_screen=passage_on_screen, passage_last=passage_last,
            )
        called = self.backend.decide(mark_sung(utterance, setlist), context)
        if called is None:
            return None
        if called.name == "wait_for_more":
            return WaitForMore()
        if called.name == "hide_scripture":
            return ShowScripture(reference="", display="off")
        if called.name == "show_scripture":
            canonical = scripture(called.reference) if scripture is not None else None
            if canonical is None:
                # A misheard or invented citation. Showing nothing is the
                # whole safety property: a wrong verse in front of a
                # congregation is worse than a missed one.
                log.info("show_scripture «%s» is not a real passage", said(called.reference))
                return None
            return ShowScripture(
                reference=canonical, display=called.display, translation=called.translation
            )
        if called.name == "search_library":
            if library is None or not library.songs:
                log.info("search_library «%s» — no library index loaded", said(called.query))
                return None
            found = search_title(
                library, called.query, setlist=setlist, section_key=called.section_key,
            )
            if found is None:
                log.info("search_library «%s» matched nothing clearly", said(called.query))
                return None
            if found.already_in_setlist and found.existing_item_idx is not None:
                # Resolved against the setlist's own copy of the song, not the
                # library's: the setlist's starts with a blank slide, so the
                # library's slide numbers land one early — "can we go to Agnus
                # Dei" put up the blank before its first line (live, 2026-10-15).
                return resolve(
                    setlist, context, found.existing_item_idx, called.section_key,
                ) or GoToSlide(
                    item_idx=found.existing_item_idx,
                    slide_idx=found.slide_idx + 1,
                    song_index=found.existing_item_idx,
                    section_key=found.section_key,
                )
            return found
        result = resolve(setlist, context, called.song_index, called.section_key)
        if result is None:
            log.info(
                "spoken instruction named song=%r section=«%s», no such slide",
                called.song_index, said(called.section_key),
            )
        return result


#: How many words in a row must appear, in order, in one song's lyrics for
#: them to count as sung. A request names a song by its title, and titles are
#: lyrics too ("sing What a Beautiful Name"), so a run that is exactly a
#: setlist title is never marked; anything else this long is the band.
SUNG_RUN_WORDS = 4

_TOKEN = re.compile(r"[a-z0-9']+")


def _tokens(text: str) -> list[str]:
    """Lowercase words, with British -our spelt -or: Whisper writes "Savior"
    where the song sheet says "Saviour". Applied to both sides alike, so a
    word it mangles ("devour") still matches itself."""
    return [
        t[:-3] + "or" if len(t) >= 6 and t.endswith("our") else t
        for t in _TOKEN.findall(text.lower().replace("\u2019", "'"))
    ]


def mark_sung(utterance: str, setlist: IndexedSetlist) -> str:
    """Bracket the stretches of `utterance` that are lyrics being sung.

    The transcript the model sees is everything heard lately, and between
    songs that's speech and singing mixed. Seen 2026-10-17: "...my blessed
    Saviour, I surrender all. Now let's sing." went to I Surrender All —
    the title was in the *sung* line, and "now let's sing" named nothing.
    Words that run `SUNG_RUN_WORDS`+ long through one song's lyrics are
    wrapped as `[sung: ...]`, which the prompt says is never a request.
    Marked, not removed: the model still knows what song the band is on.
    """
    words = utterance.split()
    if len(words) < SUNG_RUN_WORDS:
        return utterance
    toks = [(_tokens(w) or [""])[0] for w in words]
    grams = _lyric_ngrams(setlist)
    n = SUNG_RUN_WORDS
    sung = [False] * len(words)
    for i in range(len(words) - n + 1):
        if tuple(toks[i : i + n]) in grams:
            for j in range(i, i + n):
                sung[j] = True
    if not any(sung):
        return utterance
    out: list[str] = []
    i = 0
    while i < len(words):
        if not sung[i]:
            out.append(words[i])
            i += 1
            continue
        j = i
        while j < len(words) and sung[j]:
            j += 1
        run = words[i:j]
        if tuple(toks[i:j]) in _titles(setlist):
            out.extend(run)  # a song named, not sung
        else:
            out.append("[sung: " + " ".join(run) + "]")
        i = j
    return " ".join(out)


def _titles(setlist: IndexedSetlist) -> set[tuple[str, ...]]:
    return {tuple(_tokens(item.title)) for item in setlist.items if item.title}


def unsung_part(utterance: str, setlist: IndexedSetlist) -> str:
    """`utterance` with its sung stretches taken out — what was *said*."""
    return re.sub(r"\[sung: [^\]]*\]", " ", mark_sung(utterance, setlist)).strip()


_NGRAM_CACHE: dict[int, tuple[IndexedSetlist, frozenset]] = {}


def _lyric_ngrams(setlist: IndexedSetlist) -> frozenset:
    """Every `SUNG_RUN_WORDS`-word run in each song, across slide breaks.
    Cached per setlist object (a reindex makes a new one)."""
    hit = _NGRAM_CACHE.get(id(setlist))
    if hit is not None and hit[0] is setlist:
        return hit[1]
    n = SUNG_RUN_WORDS
    grams: set[tuple[str, ...]] = set()
    for item in setlist.items:
        toks = [t for slide in item.slides for w in slide.words for t in _tokens(w)]
        grams.update(tuple(toks[i : i + n]) for i in range(len(toks) - n + 1))
    frozen = frozenset(grams)
    _NGRAM_CACHE.clear()  # one live setlist at a time
    _NGRAM_CACHE[id(setlist)] = (setlist, frozen)
    return frozen


#: Words that make an utterance worth showing the model.
#:
#: Deliberately loose, and deliberately a *union* rather than a conjunction:
#: this is a volume filter, not a decision. Anything matching costs one local
#: inference; anything not matching is never seen at all, so the only error
#: that matters here is a miss.
#:
#: Measured over the corpus in `tests/decision/test_lexical_wake.py`: the
#: directive verbs alone catch 6 of 7 real commands with 9 false wakes across
#: 46 minutes of singing, the section nouns alone catch 7 of 7 with 1, and the
#: union catches 7 of 7. That is ~13 calls an hour against the ~1890 an
#: unfiltered stream would produce.
#: "again" is deliberately absent: it is ordinary in lyrics and adds nothing,
#: since every command using it carries a stronger word too ("go again",
#: "let's do that again").
#:
#: Extended 2026-09-15 for *library* requests, which are a different intent
#: with different verbs. The list was fit to within-song commands ("go to the
#: chorus") and reused for "bring up Agnus Dei" without being re-derived, so
#: "sing X", "can we do X", "put on X", "switch to X" and "pull up X" were all
#: dropped before the model saw them.
#:
#: Cost measured against the library's own 3114 sung slides, since the gate
#: fires per transcript event and a word ordinary in lyrics wakes constantly.
#: `sing` is the expensive one at 3.2% of slides — worship lyrics are full of
#: it — and is kept anyway, because it is the phrasing people actually use.
#: `find`, `want` and `open` are left out: comparable cost (0.8-1.2%) and much
#: weaker as request openers. The free ones (switch, pull, pick, try, cue,
#: queue, grab, previous — 0% of slides) cost nothing to carry.
_DIRECTIVE_WORDS = frozenset(
    """go goes going gone jump jumping skip skipping move moving back
    return returning repeat repeating replay rewind restart start
    starting stop begin beginning play take takes let lets bring
    bringing sing sings singing do does put switch pull turn pick
    try cue queue show grab next previous""".split()
)
_SECTION_WORDS = frozenset(
    """chorus choruses verse verses bridge refrain intro introduction outro
    tag prechorus pre-chorus ending end beginning start top""".split()
)
#: Extended 2026-10-15 for passage requests, whose one reliable word is the
#: book's name: "Jeremiah 29:11" may come with no verb at all. Measured
#: against the library's 2867 sung slides, every one of these is under 0.1%
#: except "hebrews" (1.3%), "romans" (0.9%), "joel" (0.7%), "corinthians"
#: (0.4%) and "philippians" (0.2%) — citation and credit lines printed on lyric
#: sheets, which nobody sings. The one that *is* sung is "kings" (0.5%, "King
#: of kings"), still far cheaper than "sing" at 3.2%. The real cost is a sermon that cites books in
#: passing; each costs one call, and the model declines it.
#: Numbered books are covered by their name ("corinthians"), and multi-word
#: ones by their distinctive word ("solomon").
_SCRIPTURE_WORDS = frozenset(
    """scripture scriptures bible bibles passage chapter
    genesis exodus leviticus numbers deuteronomy joshua judges ruth samuel
    kings chronicles ezra nehemiah esther job psalm psalms proverbs
    ecclesiastes solomon isaiah jeremiah lamentations ezekiel daniel hosea
    joel amos obadiah jonah micah nahum habakkuk zephaniah haggai zechariah
    malachi matthew mark luke john acts romans corinthians galatians
    ephesians philippians colossians thessalonians timothy titus philemon
    hebrews james peter jude revelation""".split()
)
_INSTRUCTION_WORDS = _DIRECTIVE_WORDS | _SECTION_WORDS | _SCRIPTURE_WORDS


def looks_like_instruction(words: Sequence[str]) -> bool:
    """Could this plausibly be someone telling the app what to do?

    Word-level rather than a regex over the joined text, so punctuation and
    casing from the ASR cannot change the answer. Apostrophes are dropped
    rather than trimmed, so a contraction matches its root — "let's" is the
    opening of a great many instructions and none of them survive if it does
    not.
    """
    return any(
        w.strip(".,!?;:\"").replace("'", "").lower() in _INSTRUCTION_WORDS
        for w in words
    )


@dataclass
class UtteranceBuffer:
    """Decides which transcript events are worth an inference call.

    **Lexically triggered, because the acoustic signal cannot do this job.**
    The previous gate scored each event with `decision.singing.talking_score`
    — word rate and match quality — and required 0.8. Traced live on
    2026-09-09, "Go to the chorus" was transcribed perfectly and scored
    **0.21**, and the *ceiling* at its word rate was 0.74: no evidence value
    could have woken it. `talking_score` weights word rate most heavily
    against a mean of 1.65 w/s and measures it over a fixed 4 s window, so a
    short command spoken into a gap is divided by the surrounding silence and
    reads as *slow*. Speaking clearly into a pause — the natural way to give
    an instruction — is what made the gate refuse it.

    Removing the rate term was measured and is **worse**: 0 of 7 commands at
    any threshold with a tolerable false-wake rate, because the two acoustic
    features do not separate at all. The 7 real commands sit at match
    evidence 0.11-0.15 while pure singing has a median of 0.28 with 35% under
    0.2, and they span word rates 0.75-2.25 straight through the singing
    distribution.

    The words themselves do separate, and we already have them.

    **Nothing here is a threshold.** No minimum word count, no minimum
    duration, no wait for a pause. A single word matching is enough, because
    the model — measured 7/7 on commands and 0/30 on sung lines — is a far
    better judge than any rule this class could hold, and a false wake costs
    one inference.

    What it will *not* do is let the model see everything: at one event every
    1.9 s against 1.8-2.8 s per inference, an unfiltered stream is a queue
    that grows without bound. This is the cheapest filter that keeps the
    arrival rate under the service rate.
    """

    #: How much recent speech travels with a triggering event.
    #:
    #: Commands split across transcript events — the live trace caught "Go"
    #: and "to the chorus." in separate ones — so sending only the matching
    #: event would hand the model half a sentence. This is context, not a
    #: gate: it never withholds anything, it only decides how much company the
    #: trigger arrives with.
    context_seconds: float = 8.0
    #: How long to keep offering new words after a trigger.
    #:
    #: **A request arrives verb-first, and the verb is the trigger.** "Let's
    #: go to Agnus Dei" split as "Let's go" then "to Agnus Dei": the first
    #: half fired, the model quite rightly declined a sentence naming nothing,
    #: and the half carrying the song name contained no trigger word of its
    #: own — so it never fired, and the completed phrase never existed
    #: anywhere the model could see it. Reported live as having to say it
    #: three times; the attempt that worked was the one the ASR happened to
    #: emit whole.
    #:
    #: So a trigger opens a window rather than firing once. Each event inside
    #: it re-offers the accumulated context, and the model sees the request
    #: grow into a complete one. Stale calls are already handled —
    #: `server_instructions` supersedes an in-flight decision when a newer
    #: utterance is dispatched, which is exactly this case.
    #:
    #: The cost is a handful of extra inferences per command, against a
    #: measured ~54 an hour; the alternative is the request never arriving.
    follow_seconds: float = 5.0

    _recent: list[tuple[float, str]] = field(default_factory=list, init=False)
    _listening_until: float | None = field(default=None, init=False)

    def observe(
        self,
        words: Sequence[str],
        t_seconds: float,
        *,
        ends_segment: bool = False,
    ) -> str | None:
        """Fold in one transcript event. Returns an utterance to act on, if any.

        Three ways to fire, in order of how much they can be trusted:

        1. `ends_segment` with a trigger somewhere in the context — the
           backend says a *sentence* just finished, and it contains a request.
           This is the real signal, and the only one that is not a guess.
        2. A trigger in this event's own words — fires immediately, because
           latency matters and the sentence may already be complete.
        3. Any words inside `follow_seconds` of a trigger — the fallback for
           backends that report no segmentation at all.

        (2) and (3) exist because a request arrives verb-first: the half
        naming the song carries no trigger word of its own, so firing only on
        a trigger meant the model saw "Let's go" and nothing after.
        """
        self._recent.extend((t_seconds, w) for w in words)
        cutoff = t_seconds - self.context_seconds
        self._recent = [(t, w) for t, w in self._recent if t >= cutoff]

        triggered = looks_like_instruction(words)
        context = [w for _t, w in self._recent]
        sentence_done = (
            ends_segment and bool(words) and looks_like_instruction(context)
        )
        following = (
            bool(words)
            and self._listening_until is not None
            and t_seconds <= self._listening_until
        )
        if not (triggered or sentence_done or following):
            return None

        # A completed sentence needs no follow-up: it is already whole, and
        # leaving the window open would re-offer it with the next line of
        # singing attached. Only a trigger opens (or extends) the window:
        # extending it on every word heard inside it kept it open for as long
        # as anyone went on singing — a model call every few seconds, which a
        # hosted free tier's rate limit then turned away (2026-10-17).
        if sentence_done:
            self._listening_until = None
        elif triggered:
            self._listening_until = t_seconds + self.follow_seconds
        return " ".join(context)

    def reset(self) -> None:
        self._recent.clear()
        self._listening_until = None

    def consume(self) -> None:
        """A request was acted on: forget its words, so a later trigger
        doesn't hand the model the same request again with new words
        attached, and stop following it."""
        self.reset()

    def keep_listening(self, t_seconds: float) -> None:
        """The model asked to hear more: keep offering new words for another
        `follow_seconds`, trigger or not (the rest of a request — "...of
        Build My Life" — usually has no trigger word of its own)."""
        self._listening_until = t_seconds + self.follow_seconds


@dataclass
class SentenceGate:
    """Hold a request until it's finished being said, then let it through.

    `UtteranceBuffer` offers the request again every time it grows, and each
    offer used to go straight to the model and be acted on. Seen 2026-10-17:
    "We're gonna switch to the first verse" put Reckless Love verse 1 on
    screen, and "...of build my life" moved it again two seconds later. This
    keeps only the newest offer and releases it when the sentence is over:

      - the transcriber marks the end of a sentence (`ends_segment`);
      - nothing new for `pause_s` (the speaker stopped);
      - or `max_wait_s` after the first offer, because a request is often
        followed straight away by singing, which never pauses.

    It also caps the model calls (one per sentence, not one per growing
    fragment), which is what kept a hosted free tier's rate limit from
    turning requests away. The clock is the caller's (wall time), checked by
    `poll` between transcript events — nothing arrives while someone pauses.
    """

    pause_s: float = 1.2
    max_wait_s: float = 4.0

    _held: str | None = field(default=None, init=False)
    _since: float | None = field(default=None, init=False)
    _last: float | None = field(default=None, init=False)

    @property
    def holding(self) -> bool:
        return self._held is not None

    def offer(self, utterance: str, now: float, *, ends_segment: bool = False) -> str | None:
        if self._since is None:
            self._since = now
        self._held = utterance
        self._last = now
        return self._release() if ends_segment else None

    def poll(self, now: float) -> str | None:
        if self._held is None or self._last is None or self._since is None:
            return None
        if now - self._last >= self.pause_s or now - self._since >= self.max_wait_s:
            return self._release()
        return None

    def reset(self) -> None:
        self._held = self._since = self._last = None

    def _release(self) -> str | None:
        held = self._held
        self.reset()
        return held

