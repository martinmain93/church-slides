"""Turn a spoken chord instruction into placements on the open song.

Editor-time only. One microphone clip is transcribed, then Groq's
`openai/gpt-oss-120b` is told to carry out the instruction: a progression
repeated across a verse, a different one on the chorus, extra bars held at
the end. A later take in the same editor session is a revision of the
earlier ones. Every placement still has to name a real word. Unlike a
suggestion, this is a direct edit, so every placement that anchors is kept.

Slide edits (rename, split, merge, move, rewrite) are only offered to the
model when the request asks for them; by default a take only charts.

Transcripts and lyrics are logged at DEBUG, never INFO: the recent log goes
into "Save diagnostics", and a song's words don't belong there.
"""

from __future__ import annotations

import base64
import binascii
import copy
import json
import logging
import re
import urllib.error
from dataclasses import dataclass, field

from auto_presenter.api_keys import get_key
from auto_presenter.chords import words
from auto_presenter.chords.suggest import (
    ChordModelBackend,
    MissingKey,
    ModelUnreachable,
    _is_chord,
)
from auto_presenter.http_client import (
    REQUEST_ERRORS,
    open_json_post,
    open_multipart_post,
    read_json,
)
from auto_presenter.protocol import (
    ChordPlacementInfo,
    ChordSuggestLine,
    VoiceChartClear,
    VoiceChartHold,
    VoiceChartRequest,
    VoiceChartResponse,
    VoiceSlideEdit,
)

log = logging.getLogger(__name__)

_NUMBER = re.compile(
    r"^[b#]?(?:[1-7])(?:m|min|maj|dim|aug|sus\d*|add\d*)?(?:/[b#]?[1-7])?$",
    re.IGNORECASE,
)
_MAX_AUDIO_BYTES = 1_500_000
_MAX_PLACEMENTS = 48
_MAX_HISTORY = 8
_SLIDE_OPS = {"rename", "split", "merge", "delete", "duplicate", "move", "lyrics"}
_SECTION_TYPES = {
    "verse",
    "chorus",
    "bridge",
    "tag",
    "intro",
    "outro",
    "pre-chorus",
    "instrumental",
    "other",
}
#: A pause already happened. These last words mean the sentence was cut off.
_DANGLING = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "to",
        "of",
        "from",
        "for",
        "in",
        "on",
        "at",
        "into",
        "with",
        "as",
        "by",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "actually",
        "just",
        "like",
        "called",
        "really",
        "so",
        "if",
        "when",
        "that",
        "this",
        "my",
        "your",
    }
)
#: Same model scripture suggestions already use. Measured there at about
#: 3s per song. `reasoning_effort` is left off: the calls that sent it
#: came back HTTP 400, and the working 120b call does not send it.
VOICE_MODEL = "openai/gpt-oss-120b"
VOICE_TIMEOUT_SECONDS = 25.0

TOOL_SPEC: dict = {
    "type": "function",
    "function": {
        "name": "chart_song",
        "description": (
            "Place the chords from the spoken instruction onto the lyric lines."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "placements": {
                    "type": "array",
                    "description": (
                        "One chord on one word, in the order the progression "
                        "is played. Repeat the pattern as many times as asked."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "line_id": {
                                "type": "string",
                                "description": "Copy line_id from the lyric list.",
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
                                "description": (
                                    "A Nashville number from the instruction "
                                    "(1, 4, 5, 6m) or a chord symbol. An empty "
                                    "string removes the chord on that word."
                                ),
                            },
                        },
                        "required": ["line_id", "word", "word_index", "chord"],
                    },
                },
                "slide_edits": {
                    "type": "array",
                    "description": (
                        "Slide changes. rename, split, merge, delete, duplicate, "
                        "move, or lyrics. Empty when the speaker is only talking "
                        "about chords."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "op": {
                                "type": "string",
                                "enum": [
                                    "rename",
                                    "split",
                                    "merge",
                                    "delete",
                                    "duplicate",
                                    "move",
                                    "lyrics",
                                ],
                            },
                            "section": {
                                "type": "string",
                                "description": "Spoken section, such as 'verse 3' or 'chorus'.",
                            },
                            "slide": {
                                "type": "integer",
                                "description": "One card index from line_id sN. -1 means the whole section.",
                            },
                            "section_type": {
                                "type": "string",
                                "description": "For rename: verse, chorus, bridge, pre-chorus, tag, intro, outro, instrumental.",
                            },
                            "section_number": {"type": "integer"},
                            "after_line": {
                                "type": "integer",
                                "description": "For split: 0-based line to break after.",
                            },
                            "anchor": {
                                "type": "string",
                                "description": "For move: the section to place this one before or after.",
                            },
                            "place": {"type": "string", "enum": ["before", "after"]},
                            "lyrics": {
                                "type": "string",
                                "description": "For lyrics: the slide's new words, lines separated by newlines.",
                            },
                        },
                        "required": ["op"],
                    },
                },
                "clear_lines": {
                    "type": "array",
                    "description": (
                        "Line ids whose chords should all be removed, including "
                        "a hold after the line. Use this when the speaker says "
                        "to remove, clear, or delete the chords on a line or a "
                        "whole section. One entry per line. Do not also return "
                        "those chords as placements."
                    ),
                    "items": {"type": "string"},
                },
                "holds": {
                    "type": "array",
                    "description": (
                        "Extra bars of one chord after a line, when the "
                        "speaker says to hold a chord for extra bars."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "line_id": {"type": "string"},
                            "chord": {"type": "string"},
                            "bars": {
                                "type": "integer",
                                "description": "How many extra bars. 3 means three bars.",
                            },
                        },
                        "required": ["line_id", "chord", "bars"],
                    },
                },
            },
            "required": ["placements", "holds", "clear_lines", "slide_edits"],
        },
    },
}


def tool_spec(slide_edits: bool) -> dict:
    """The tool, without `slide_edits` when the take may only chart."""
    if slide_edits:
        return TOOL_SPEC
    spec = copy.deepcopy(TOOL_SPEC)
    params = spec["function"]["parameters"]
    del params["properties"]["slide_edits"]
    params["required"] = [name for name in params["required"] if name != "slide_edits"]
    return spec


SYSTEM_PROMPT = (
    "You chart a worship song from one spoken instruction. The lyric lines "
    "are listed for you. Place every chord the speaker asked for.\n"
    "A progression named for a section is played across that section's lines, "
    "in order, as many times as they said. '1 4 5 1 twice per verse' on a "
    "four-line verse is eight chords: walk the four lines, then walk them "
    "again, putting the next chord on the next word that does not yet have "
    "one from this instruction. One chord per word. When a line must carry "
    "two chords, use two different words on that line.\n"
    "Each word is followed by its number, like holy[2]. Return that number "
    "as word_index. A repeated word is told apart only by its number.\n"
    "Do this separately for each section they named (every verse, the chorus). "
    "Leave a section alone if they did not mention it.\n"
    "'Hold the 5 at the end for 3 extra bars' is a hold on the last line of "
    "that section: chord 5, bars 3. A hold is not also a placement.\n"
    "Return the number they said (1, 4, 6m) or a chord symbol. Copy line_id "
    "and the word verbatim. Never invent a line.\n"
    "Lines may already show chords as word[n]=symbol. When the speaker says to "
    "finish a section, or to base it on another section, copy that section's "
    "chords in order onto the lines they named. Use the same symbols. If the "
    "source puts two chords on a line, put two chords on a target line of "
    "similar length. If the target is split into shorter lines, continue the "
    "same sequence across those lines. Do not invent a different progression. "
    "Do not turn letter chords into numbers.\n"
    "When the speaker says to remove, clear, or delete chords, put every "
    "line_id of that section in clear_lines. That removes every chord on "
    "the line, including a hold. To remove only one word's chord, return "
    "that word with an empty chord. Leaving a chord out of placements does "
    "not remove it.\n"
    "When earlier instructions appear before the latest one, those takes "
    "already charted the song and the latest instruction is a revision. "
    "Change only what it corrects. Return placements and holds for the "
    "words and holds that change, not the chords that should stay. A chord "
    "you return for a word replaces the chord on that word. A hold you "
    "return for a line replaces the hold on that line. If the new "
    "instruction does not mention a section, leave that section out of "
    "the reply."
)

_SLIDE_EDIT_PROMPT = (
    "You can also edit slides. Use slide_edits and leave placements empty "
    "when they are not talking about chords. rename changes the section "
    "they named (verse 3 is actually a chorus: op rename, section 'verse 3', "
    "section_type chorus, section_number 1) on every slide in it. split "
    "breaks one slide after after_line, a 0-based line on that slide. merge "
    "joins the consecutive slides of a section into one. delete removes the "
    "section. duplicate copies it to just after itself. move places the "
    "section before or after anchor. lyrics replaces one slide's words, and "
    "only when they dictate the new words. slide is the card index from "
    "line_id sN, or -1 for the whole section. Never edit a section they did "
    "not name."
)


def system_prompt(slide_edits: bool) -> str:
    return SYSTEM_PROMPT + ("\n" + _SLIDE_EDIT_PROMPT if slide_edits else "")


def transcribe_clip(audio: bytes, *, mime: str) -> str:
    """One Groq Whisper call. Raises MissingKey or ModelUnreachable."""
    key = get_key("groq")
    if not key:
        raise MissingKey("groq")
    if not audio:
        raise ModelUnreachable()
    if len(audio) > _MAX_AUDIO_BYTES:
        raise ModelUnreachable()
    filename, content_type = audio_upload_name(mime)
    log.debug("voice chart upload %s (%s)", filename, content_type)
    fields = {
        "model": "whisper-large-v3-turbo",
        "response_format": "json",
        "language": "en",
        "prompt": (
            "Worship chord chart. Verse, chorus, remove chords, clear chords, "
            "Nashville numbers, progression, bars."
        ),
    }
    try:
        with open_multipart_post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            fields,
            file_field="file",
            filename=filename,
            file_content_type=content_type,
            file_bytes=audio,
            api_key=key,
            timeout_seconds=20.0,
        ) as resp:
            data = read_json(resp)
    except REQUEST_ERRORS as exc:
        _log_failure("transcription", exc)
        raise ModelUnreachable()
    text = str(data.get("text", "")).strip()
    log.debug("voice chart heard (%d bytes): %s", len(audio), text or "(empty)")
    if not text:
        raise ModelUnreachable()
    return text


@dataclass
class RawChart:
    """What the model returned, before any of it is checked."""

    placements: list = field(default_factory=list)
    holds: list = field(default_factory=list)
    clear_lines: list = field(default_factory=list)
    slide_edits: list = field(default_factory=list)


def _as_list(value: object) -> list:
    return value if isinstance(value, list) else []


class VoiceChartBackend:
    """Groq gpt-oss-120b, asked to carry out one instruction."""

    def __init__(self, inner: ChordModelBackend | None = None) -> None:
        self._inner = inner or ChordModelBackend(
            model=VOICE_MODEL,
            timeout_seconds=VOICE_TIMEOUT_SECONDS,
        )

    def request(
        self,
        song_key: str,
        lines: list[ChordSuggestLine],
        instruction: str,
        history: list | None = None,
        slide_edits: bool = False,
    ) -> RawChart:
        key = get_key(self._inner.provider)
        if not key:
            raise MissingKey(self._inner.provider)
        prior = list(history or [])[-_MAX_HISTORY:]
        log.info(
            "voice chart asking %s lines=%d earlier=%d slide_edits=%s",
            self._inner.model,
            len(lines),
            len(prior),
            slide_edits,
        )
        log.debug("voice chart instruction: %s", instruction.strip()[:500])
        payload = {
            "model": self._inner.model,
            "temperature": 0,
            "messages": voice_messages(song_key, lines, instruction, prior, slide_edits),
            "tools": [tool_spec(slide_edits)],
            "tool_choice": {"type": "function", "function": {"name": "chart_song"}},
        }
        try:
            response = open_json_post(
                f"{self._inner.base_url}/chat/completions",
                payload,
                api_key=key,
                timeout_seconds=self._inner.timeout_seconds,
            )
            data = read_json(response)
        except REQUEST_ERRORS as exc:
            _log_failure("model", exc)
            raise ModelUnreachable()
        try:
            message = data["choices"][0]["message"]
            calls = message.get("tool_calls") or []
            if not calls:
                log.info("voice chart model returned no tool call")
                log.debug("voice chart model message: %s", _clip(message))
                return RawChart()
            raw_args = calls[0]["function"]["arguments"]
            log.debug("voice chart model tool args: %s", _clip(raw_args))
            args = json.loads(raw_args)
            raw = RawChart(
                placements=_as_list(args.get("placements")),
                holds=_as_list(args.get("holds")),
                clear_lines=_as_list(args.get("clear_lines")),
                slide_edits=_as_list(args.get("slide_edits")) if slide_edits else [],
            )
            log.info(
                "voice chart model returned %d placements, %d holds, %d clears, %d slide edits",
                len(raw.placements),
                len(raw.holds),
                len(raw.clear_lines),
                len(raw.slide_edits),
            )
            return raw
        except (KeyError, IndexError, TypeError, AttributeError, json.JSONDecodeError):
            log.exception("voice chart unexpected response shape")
            return RawChart()


def chart(
    song_key: str,
    lines: list[ChordSuggestLine],
    instruction: str,
    backend: VoiceChartBackend | None = None,
    history: list | None = None,
    slide_edits: bool = False,
) -> tuple[list[ChordPlacementInfo], list[VoiceChartHold], list[VoiceChartClear], list[VoiceSlideEdit], str | None]:
    """Verified placements, holds, clears, slide edits, and an error. Never raises."""
    instruction = instruction.strip()
    if not instruction:
        log.info("voice chart skipped: empty instruction")
        return [], [], [], [], "Didn't catch that"
    if not lines:
        log.info("voice chart skipped: no lyric lines")
        return [], [], [], [], "This song has no lyrics to chart"
    try:
        raw = (backend or VoiceChartBackend()).request(
            song_key, lines, instruction, list(history or []), slide_edits
        )
    except MissingKey as err:
        log.info("voice chart skipped: no %s API key", err.provider)
        return [], [], [], [], f"No {err.provider} API key saved"
    except ModelUnreachable:
        return [], [], [], [], "Chord model could not be reached"
    placements, holds, clears = verify_voice(lines, raw.placements, raw.holds, raw.clear_lines)
    edits = verify_edits(raw.slide_edits) if slide_edits else []
    log.info(
        "voice chart kept %d/%d placements, %d/%d holds, %d/%d clears, %d slide edits",
        len(placements),
        len(raw.placements),
        len(holds),
        len(raw.holds),
        len(clears),
        len(raw.clear_lines),
        len(edits),
    )
    for edit in edits:
        log.debug("voice chart edit %s section=%r slide=%s type=%s", edit.op, edit.section, edit.slide, edit.section_type)
    for item in placements:
        log.debug("voice chart place %s %s[%d] -> %s", item.line_id, item.word, item.word_index, item.chord)
    for item in holds:
        log.debug("voice chart hold %s %s x%d", item.line_id, item.chord, item.bars)
    for item in clears:
        log.debug("voice chart clear %s %s", item.line_id, item.word or "(whole line)")
    return placements, holds, clears, edits, None


def verify_voice(
    lines: list[ChordSuggestLine],
    raw_placements: list[dict],
    raw_holds: list[dict],
    raw_clears: list | None = None,
) -> tuple[list[ChordPlacementInfo], list[VoiceChartHold], list[VoiceChartClear]]:
    """Keep every placement, hold, and clear that names a real line."""
    by_id = {line.line_id: line for line in lines}
    placements: list[ChordPlacementInfo] = []
    clears: list[VoiceChartClear] = []
    seen: set[tuple[str, int]] = set()
    for item in raw_placements:
        if len(placements) >= _MAX_PLACEMENTS:
            break
        if not isinstance(item, dict):
            continue
        line_id = str(item.get("line_id", "")).strip()
        word = str(item.get("word", "")).strip()
        chord = _clean_chord(str(item.get("chord", "")))
        line = by_id.get(line_id)
        if line is None:
            continue
        if not chord:
            # A clear means a word that has a chord: prefer a chorded copy.
            chorded = set(line.chorded_word_indexes)
            unchorded = set(range(len(words.line_words(line.line)))) - chorded
            found = words.resolve(line.line, word, item.get("word_index", -1), avoid=unchorded)
            if found is not None:
                clears.append(VoiceChartClear(line_id=line_id, word=found[1], word_index=found[0]))
            continue
        if not _placeable(chord):
            continue
        taken = {index for lid, index in seen if lid == line_id}
        found = words.resolve(line.line, word, item.get("word_index", -1), avoid=taken)
        if found is None or (line_id, found[0]) in seen:
            continue
        index, token = found
        seen.add((line_id, index))
        placements.append(
            ChordPlacementInfo(line_id=line_id, word=token, word_index=index, chord=chord)
        )

    cleared_lines: set[str] = set()
    for item in raw_clears or []:
        line_id = str(item).strip()
        if line_id not in by_id or line_id in cleared_lines:
            continue
        cleared_lines.add(line_id)
        clears.append(VoiceChartClear(line_id=line_id, word=""))

    holds: list[VoiceChartHold] = []
    for item in raw_holds:
        if not isinstance(item, dict):
            continue
        line_id = str(item.get("line_id", "")).strip()
        chord = _clean_chord(str(item.get("chord", "")))
        if line_id not in by_id or not _placeable(chord):
            continue
        try:
            bars = int(item.get("bars", 1))
        except (TypeError, ValueError):
            continue
        bars = max(1, min(bars, 8))
        holds.append(VoiceChartHold(line_id=line_id, chord=chord, bars=bars))
    return placements, holds, clears


def instruction_is_complete(text: str) -> bool:
    """True when a paused utterance is a finished instruction, not a fragment.

    The caller has already waited through a silence. A trailing word such as
    "actually" or "the" means they stopped in the middle of the sentence.
    """
    words = re.findall(r"[A-Za-z0-9']+", text)
    if len(words) < 3:
        return False
    return words[-1].lower() not in _DANGLING


def verify_edits(raw: list) -> list[VoiceSlideEdit]:
    """Keep slide edits that name an operation and a section or a slide."""
    edits: list[VoiceSlideEdit] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        op = str(item.get("op", "")).strip().lower()
        if op not in _SLIDE_OPS:
            continue
        section = str(item.get("section", "")).strip()
        try:
            slide = int(item.get("slide", -1))
        except (TypeError, ValueError):
            slide = -1
        if not section and slide < 0:
            continue
        section_type = str(item.get("section_type", "")).strip().lower().replace(" ", "-")
        if section_type == "prechorus":
            section_type = "pre-chorus"
        if section_type and section_type not in _SECTION_TYPES:
            section_type = ""
        if op == "rename" and not section_type:
            continue
        try:
            number = int(item.get("section_number", 0) or 0)
        except (TypeError, ValueError):
            number = 0
        try:
            after_line = int(item.get("after_line", -1))
        except (TypeError, ValueError):
            after_line = -1
        place = str(item.get("place", "before")).strip().lower()
        if place not in ("before", "after"):
            place = "before"
        lyrics = str(item.get("lyrics", "")).strip()
        if op == "lyrics" and not lyrics:
            continue
        edits.append(
            VoiceSlideEdit(
                op=op,  # type: ignore[arg-type]
                section=section,
                slide=slide,
                section_type=section_type,
                section_number=max(0, number),
                after_line=after_line,
                anchor=str(item.get("anchor", "")).strip(),
                place=place,  # type: ignore[arg-type]
                lyrics=lyrics,
            )
        )
    return edits


def decode_audio(audio_base64: str) -> bytes | None:
    if not audio_base64.strip():
        return b""
    try:
        return base64.b64decode(audio_base64, validate=False)
    except (binascii.Error, ValueError):
        return None


def run_voice_chart(msg: VoiceChartRequest) -> VoiceChartResponse:
    """Transcribe (unless the words came typed), then chart. Blocking; never raises."""
    history = list(msg.history or [])
    log.info(
        "voice chart start: key=%r lines=%d audio_chars=%d mime=%s history=%d typed=%s",
        msg.song_key,
        len(msg.lines),
        len(msg.audio_base64 or ""),
        msg.audio_mime,
        len(history),
        bool((msg.instruction or "").strip()),
    )
    for index, turn in enumerate(history[-_MAX_HISTORY:], start=1):
        log.debug(
            "voice chart earlier %d: %s | placed: %s",
            index,
            _clip(turn.transcript, 300),
            _clip(turn.applied, 300),
        )

    def reply(transcript: str = "", *, error: str | None = None, complete: bool = True) -> VoiceChartResponse:
        return VoiceChartResponse(id=msg.id, transcript=transcript, error=error, complete=complete)

    instruction = (msg.instruction or "").strip()
    if not instruction:
        audio = decode_audio(msg.audio_base64 or "")
        if audio is None:
            log.info("voice chart audio could not be decoded")
            return reply(error="Couldn't read that recording")
        if not audio:
            log.info("voice chart audio was empty")
            return reply(error="Didn't catch that")
        log.info("voice chart transcribing %d bytes as %s", len(audio), msg.audio_mime)
        try:
            instruction = transcribe_clip(audio, mime=msg.audio_mime or "audio/webm")
        except MissingKey as err:
            log.info("voice chart skipped: no %s API key", err.provider)
            return reply(error=f"No {err.provider} API key saved")
        except ModelUnreachable:
            return reply(error="Couldn't transcribe that recording")
    if not msg.final and not instruction_is_complete(instruction):
        log.info("voice chart holding for the rest of the sentence")
        return reply(instruction, complete=False)
    placements, holds, clears, edits, error = chart(
        msg.song_key, list(msg.lines), instruction, history=history, slide_edits=msg.slide_edits
    )
    log.info(
        "voice chart done: placements=%d holds=%d clears=%d edits=%d error=%r",
        len(placements),
        len(holds),
        len(clears),
        len(edits),
        error,
    )
    return VoiceChartResponse(
        id=msg.id,
        transcript=instruction,
        placements=placements,
        holds=holds,
        clears=clears,
        slide_edits=edits,
        complete=True,
        error=error,
    )


def voice_messages(
    song_key: str,
    lines: list[ChordSuggestLine],
    instruction: str,
    history: list | None = None,
    slide_edits: bool = False,
) -> list[dict[str, str]]:
    """System prompt, then each earlier take, then this instruction.

    A non-empty history marks the latest user message as a revision so the
    model changes the chart instead of charting the song from scratch.
    """
    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt(slide_edits)}]
    prior = []
    for turn in list(history or [])[-_MAX_HISTORY:]:
        transcript = str(getattr(turn, "transcript", "") or "").strip()
        if not transcript:
            continue
        applied = str(getattr(turn, "applied", "") or "").strip() or "(nothing placed)"
        prior.append((transcript, applied))
    for transcript, applied in prior:
        messages.append({"role": "user", "content": f"Earlier instruction:\n{transcript}"})
        messages.append({"role": "assistant", "content": f"Placed:\n{applied}"})
    latest = _prompt(song_key, lines, instruction)
    notes = []
    if prior:
        notes.append(
            "This is a revision of the chart from the earlier instructions. "
            "Apply only the change. Leave every chord the speaker is not "
            "correcting where it is, and do not repeat those chords."
        )
    if any(line.chords for line in lines):
        notes.append(
            "Chords already on a line are written after it. If the instruction "
            "says to finish a section, or to base it on another section, copy "
            "that section's chords in order onto the section they named. Use "
            "the same symbols. Do not invent a new progression and do not turn "
            "those symbols into numbers. Return every chord the target section "
            "should end up with, including ones that replace a wrong chord. "
            "To remove chords, list those line ids in clear_lines. Omitting a "
            "chord does not delete it."
        )
    if notes:
        latest = "\n".join(notes) + "\n\n" + latest
    messages.append({"role": "user", "content": latest})
    return messages


def _chord_note(line: ChordSuggestLine) -> str:
    """`Blessed[0]=G, name[3]=D` — one entry per chord, by word number."""
    chords = [str(chord) for chord in line.chords]
    if not chords:
        return ""
    indexes = list(line.chorded_word_indexes)
    if len(indexes) == len(chords):
        tokens = words.line_words(line.line)
        return ", ".join(
            f"{tokens[i] if 0 <= i < len(tokens) else '?'}[{i}]={chord}"
            for i, chord in zip(indexes, chords)
        )
    return " ".join(chords)


def _prompt(song_key: str, lines: list[ChordSuggestLine], instruction: str) -> str:
    rows = []
    for line in lines:
        note = _chord_note(line) or "(no chords)"
        rows.append(f"{line.line_id} | {line.section} | {words.numbered(line.line)} | {note}")
    if song_key.strip():
        key = song_key.strip()
    elif any(line.chords for line in lines):
        key = (
            "unknown. Copy the chord symbols already written on the lines. "
            "Do not convert them to numbers."
        )
    else:
        key = "unknown — return Nashville numbers"
    return f"Key: {key}\n\nSpoken instruction:\n{instruction.strip()}\n\nLines:\n" + "\n".join(rows)


def audio_upload_name(mime: str) -> tuple[str, str]:
    """Filename and content type Whisper should see.

    The browser records AAC inside an MP4 container. Naming that file
    `.webm` makes the decoder drop the start of the clip.
    """
    content_type = (mime or "").split(";")[0].strip().lower() or "audio/webm"
    if "wav" in content_type:
        return "audio.wav", content_type
    if "mp4" in content_type or "m4a" in content_type or "aac" in content_type:
        return "audio.mp4", "audio/mp4"
    if "ogg" in content_type:
        return "audio.ogg", content_type
    return "audio.webm", content_type


def _clip(value: object, limit: int = 2000) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _log_failure(step: str, exc: BaseException) -> None:
    """One searchable line, including Groq's response body on an HTTP error."""
    if isinstance(exc, urllib.error.HTTPError):
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        # Groq's error body can quote the request, so it stays out of error reports.
        log.error("voice chart %s HTTP %s: «%s»", step, exc.code, _clip(body, 4000))
        return
    log.exception("voice chart %s failed", step)


def _clean_chord(symbol: str) -> str:
    return symbol.strip().replace("♯", "#").replace("♭", "b")


def _placeable(symbol: str) -> bool:
    return bool(symbol) and (bool(_is_chord(symbol)) or bool(_NUMBER.match(symbol)))
