"""Score the spoken-request model on written utterances, scripture included.

Each case is a line shaped the way Whisper writes it ("Jeremiah 29 11", "First
Corinthians 13, 4 through 7"), sent to the real model with the real prompt and
tools, and the call it makes is checked against what a person meant. Passage
references are compared after canonicalizing through the installed verse
store, so "Jer 29:11" and "Jeremiah 29:11" agree.

This is the typed-utterance benchmark, not a live one. It says whether the
model picks the right tool and writes the right reference; it says nothing
about whether the wake filter lets the sentence through, or whether the ASR
transcribes it — those need real recordings.

A failed call (network, 429, timeout) is counted as a failure *of the call*,
separately, never as a refusal: `decide()` returning None for both has caused
wrong conclusions in this project twice.

    uv run --project sidecar python research/eval/bench_spoken_requests.py
    uv run --project sidecar python research/eval/bench_spoken_requests.py --model qwen2.5:3b-instruct
    uv run --project sidecar python research/eval/bench_spoken_requests.py --no-scripture   # old tool set
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sidecar"))

from auto_presenter.decision.instructions import (  # noqa: E402
    BackendUnavailable,
    ToolCall,
    build_context,
)
from auto_presenter.index.library import build_index  # noqa: E402
from auto_presenter.protocol import SetlistContent, SetlistItemContent  # noqa: E402
from auto_presenter.server_instructions import (  # noqa: E402
    _build_instruction_backend,
    _scripture_checker,
)
from auto_presenter.decision.instruction_models import DEFAULT_MODEL_ID  # noqa: E402

SECTIONS = ["verse:1", "chorus:1", "verse:2", "chorus:1", "bridge:1", "chorus:1"]
SETLIST = ["Reckless Love", "What A Beautiful Name", "Way Maker"]
#: The song on screen when each line is heard.
CURRENT = 0


@dataclass(frozen=True)
class Case:
    said: str
    #: ("go", song_index or None for the current song, section_key)
    #: ("search", a word the query must contain)
    #: ("show", canonical reference, display)
    #: ("show_from", the passage must start here, e.g. "Hebrews 11:4")
    #: ("hide",)  ("none",)  ("wait",) — wait_for_more
    #: ("either",) — genuinely ambiguous; reported, not scored.
    want: tuple
    scripture: bool = False
    #: The passage on screen when it was said ("" for none).
    on_screen: str = ""


CASES = [
    # --- song commands (the old benchmark's shape) ---------------------------
    Case("Go to the chorus.", ("go", None, "chorus:1")),
    Case("Let's go back to verse one.", ("go", None, "verse:1")),
    Case("Let's do the second verse one more time.", ("go", None, "verse:2")),
    Case("Can you go to the bridge of What a Beautiful Name?", ("go", 1, "bridge:1")),
    Case("When you go to the bridge of what a beautiful name.", ("go", 1, "bridge:1")),
    Case("Let's go to Way Maker.", ("go", 2, "")),
    Case("Take it from the top.", ("go", None, "verse:1")),
    Case("Let's bring up Agnus Dei.", ("search", "agnus")),
    Case("Can we sing 10,000 Reasons?", ("search", "reasons")),
    # --- passages ---------------------------------------------------------
    Case("Bring up Jeremiah 29:11.", ("show", "Jeremiah 29:11", "full"), True),
    Case("Can we get Jeremiah 29 11 on the screen?", ("show", "Jeremiah 29:11", "full"), True),
    Case("Turn in your Bibles to Romans chapter 8 verse 28.", ("show", "Romans 8:28", "full"), True),
    Case("Let's read together from Psalm 23, verses 1 through 4.", ("show", "Psalm 23:1–4", "full"), True),
    Case("John 3 16, put that up please.", ("show", "John 3:16", "full"), True),
    Case("Put up First Corinthians 13, 4 through 7.", ("show", "1 Corinthians 13:4–7", "full"), True),
    Case("Could we have the verse about the plans I have for you up on the screen?", ("show", "Jeremiah 29:11", "full"), True),
    Case("Put Philippians 4:13 up next to the lyrics.", ("show", "Philippians 4:13", "beside"), True),
    Case("Okay, you can take the verse down now.", ("hide",), True),
    Case("Take the scripture off the screen.", ("hide",), True),
    # --- not requests -------------------------------------------------------
    Case("Amen, come on somebody.", ("none",)),
    Case("We're going to take up an offering now.", ("none",)),
    Case("Turn to your neighbor and say hello.", ("none",)),
    Case("Jeremiah wrote this to people in exile, people who had lost everything.", ("none",), True),
    Case("The word of the Lord. Thanks be to God.", ("none",), True),
    # --- half-said requests: wait_for_more (2026-10-17) -----------------------
    Case("We're gonna switch to the first verse of", ("wait",)),
    Case("Okay let's go to", ("wait",)),
    Case("Now bring up the lyrics to", ("wait",)),
    # ...and the same requests finished: no waiting on a complete one.
    Case("We're gonna switch to the first verse of What a Beautiful Name.", ("go", 1, "verse:1")),
    Case("We're gonna switch to the first verse of Build My Life.", ("search", "build")),
    # --- sung lyrics in the context, as `mark_sung` brackets them -------------
    Case("[sung: what a beautiful name you have given to us] Okay, now let's sing.", ("none",)),
    Case("[sung: what a beautiful name you have given to us] Let's go to Way Maker.", ("go", 2, "")),
    Case("Let's all stand and sing What a Beautiful Name.", ("go", 1, "")),
    # --- verbatim from the 2026-10-15 live set (rec_20261015-235203_678) -------
    Case("Can we bring up Jeremiah", ("none",), True),
    Case("Can we bring up Jeremiah 2911?", ("show", "Jeremiah 29:11", "full"), True),
    Case("Let me bring up John 3 .16.", ("show", "John 3:16", "full"), True),
    Case("How about the first few verses of Hebrews 11?", ("show_from", "Hebrews 11:1"), True),
    Case("And then the next few verses", ("show_from", "Hebrews 11:4"), True, "Hebrews 11:1–3"),
    Case("And then the next few verses after that", ("show_from", "Hebrews 11:7"), True,
         "Hebrews 11:4–6"),
    Case("the next few verses in Hebrews.", ("show_from", "Hebrews 11:4"), True, "Hebrews 11:1–3"),
    Case("Hebrews", ("none",), True, "Hebrews 11:1–3"),
    Case("Hebrews 11, 4 to 6.", ("show", "Hebrews 11:4–6", "full"), True, "Hebrews 11:1–3"),
    Case("Now can we go to Agnes Day?", ("search", "agn"), True),
    Case("are. Yes, they are. Let's bring up building my life.", ("search", "build"), True),
    Case("You're worthy of every song we could ever sing", ("none",), True),
    Case("Worthy is all the praise we could ever bring every...", ("none",), True),
    # --- ambiguous: a preacher citing a verse mid-sermon ---------------------
    Case("As Paul says in Romans 8 28, all things work together for good.", ("either",), True),
    Case("Look at verse 12 with me.", ("either",), True),
]


def _setlist():
    content = SetlistContent(
        items=[
            SetlistItemContent(
                item_idx=i, song_id=f"song{i}", title=title,
                slides=[["la"] * 4 for _ in SECTIONS], slide_sections=SECTIONS,
            )
            for i, title in enumerate(SETLIST)
        ]
    )
    return build_index(content)


def _describe(call: ToolCall | None) -> str:
    if call is None:
        return "no_action"
    if call.name == "go_to_slide":
        return f"go_to_slide({call.song_index}, {call.section_key!r})"
    if call.name == "search_library":
        return f"search_library({call.query!r})"
    if call.name == "show_scripture":
        return f"show_scripture({call.reference!r}, {call.display})"
    return call.name


def _score(case: Case, call: ToolCall | None, check) -> bool | None:
    kind = case.want[0]
    if kind == "either":
        return None
    if kind == "none":
        return call is None
    if kind == "wait":
        return call is not None and call.name == "wait_for_more"
    if call is None:
        return False
    if kind == "go":
        _, song, section = case.want
        if call.name != "go_to_slide":
            return False
        same_song = (call.song_index if call.song_index is not None else CURRENT) == (
            song if song is not None else CURRENT
        )
        # "Way Maker" with no section means its start; either "" or verse:1 lands there.
        same_section = call.section_key == section or (section == "" and call.section_key in ("", "verse:1"))
        return same_song and same_section
    if kind == "search":
        return call.name == "search_library" and case.want[1] in call.query.lower()
    if kind == "show":
        _, ref, display = case.want
        return (
            call.name == "show_scripture"
            and check is not None
            and check(call.reference) == ref
            and call.display == display
        )
    if kind == "show_from":
        canonical = check(call.reference) if check is not None else None
        return call.name == "show_scripture" and canonical is not None and (
            canonical == case.want[1] or canonical.startswith(case.want[1] + "–")
        )
    if kind == "hide":
        return call.name == "hide_scripture"
    raise ValueError(kind)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL_ID, help="instruction model catalog id")
    ap.add_argument("--no-scripture", action="store_true", help="offer only the old tools")
    ap.add_argument("--pace", type=float, default=2.5, help="seconds between calls (Groq free tier: 30/min)")
    ap.add_argument("--retries", type=int, default=3, help="retries after a 429, 20 s apart")
    args = ap.parse_args()

    backend = _build_instruction_backend(args.model)
    if backend is None:
        print(f"could not build {args.model} (no key on file?)")
        return 2
    # Score the model asked for, not its fallback: a fallback answering would
    # make a network failure look like the primary model's accuracy.
    backend = getattr(backend, "primary", backend)
    strict = getattr(backend, "decide_strict", backend.decide)

    check = None if args.no_scripture else _scripture_checker()
    if not args.no_scripture and check is None:
        print("no verse store installed — install a translation first")
        return 2
    cases = [c for c in CASES if not (args.no_scripture and c.scripture)]
    base = build_context(_setlist(), CURRENT)
    context = replace(base, scripture=check is not None)

    right = wrong = failed = 0
    latencies = []
    for case in cases:
        call = None
        for attempt in range(args.retries + 1):
            t0 = time.monotonic()
            try:
                call = strict(case.said, replace(context, passage_on_screen=case.on_screen))
                break
            except BackendUnavailable as exc:
                # A hosted free tier's rate limit (429) is a wait, not an answer.
                if "429" in str(exc) and attempt < args.retries:
                    time.sleep(20.0)
                    continue
                failed += 1
                print(f"  CALL FAILED  {case.said!r}: {exc}")
                call = BackendUnavailable
                break
        if call is BackendUnavailable:
            time.sleep(args.pace)
            continue
        latencies.append(time.monotonic() - t0)
        verdict = _score(case, call, check)
        mark = {True: "ok   ", False: "WRONG", None: "  ?  "}[verdict]
        right += verdict is True
        wrong += verdict is False
        shown = f"  [on screen: {case.on_screen}]" if case.on_screen else ""
        print(f"  {mark}  {case.said!r}{shown}\n         -> {_describe(call)}   (wanted {case.want})")
        time.sleep(args.pace)

    scored = right + wrong
    latencies.sort()
    median = latencies[len(latencies) // 2] if latencies else float("nan")
    print(
        f"\n{args.model}{' (old tools)' if args.no_scripture else ''}: "
        f"{right}/{scored} right, {failed} calls failed, median {median:.2f} s/call"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
