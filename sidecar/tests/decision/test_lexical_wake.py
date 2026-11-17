"""The wake gate, checked in both directions.

It was validated twice on pure singing, which can only ever show *false*
wakes. It was never shown that a real command clears the bar — and it did not:
traced live, "Go to the chorus" was transcribed perfectly and scored 0.21
against a 0.8 threshold whose ceiling at that word rate was 0.74.

The commands below are real, taken from four recordings by transcribing them
and keeping every event whose words look like an instruction. The negatives
are written here rather than lifted from the goldens so no lyrics live in the
repo; the measured figure over real singing is quoted in the module docstring
of `decision.instructions` and reproduced by `/tmp/wake_eval.py`.
"""

from __future__ import annotations

import pytest

from auto_presenter.decision.instructions import UtteranceBuffer, looks_like_instruction

#: Verbatim from `rec_20260909-110335_370`, `...234330_350`, `...163745_206`
#: and `...160826_034`. The acoustic gate woke for exactly one of these.
REAL_COMMANDS = [
    "Go to the chorus.",
    "Let's go back to verse one.",
    "Go to verse",
    "go back to the first verse.",
    "Let's go to the chorus.",
    "back to the first verse",
]

#: Phrasings a stricter rule would drop. Not in the corpus — recall beyond it
#: is the untested part, and these say which way the filter should lean.
PLAUSIBLE_COMMANDS = [
    "take it from the top",
    "let's do that again",
    "skip the bridge",
    "back to the beginning",
    "Let's bring up Agnus Dei",
    "bring up reckless love",
    # Passage requests. The second has no verb at all — just a citation.
    "turn in your Bibles to Romans 8",
    "Jeremiah 29 11",
    "can we put the scripture up",
]

#: Invented lines in the shape of sung lyrics, deliberately including words a
#: careless filter might catch.
NOT_COMMANDS = [
    "your love it never fails",
    "holy holy is the one",
    "forever and forever more",
    "we are free indeed",
    "morning light will break again",
]


@pytest.mark.parametrize("text", REAL_COMMANDS)
def test_every_real_command_reaches_the_model(text):
    assert looks_like_instruction(text.split()), text


@pytest.mark.parametrize("text", PLAUSIBLE_COMMANDS)
def test_phrasings_outside_the_corpus_also_get_through(text):
    """The filter is a volume control, not a decision. A miss is the only
    error that costs anything here — the model never sees what it drops."""
    assert looks_like_instruction(text.split()), text


@pytest.mark.parametrize("text", NOT_COMMANDS)
def test_ordinary_sung_lines_are_not_sent(text):
    assert not looks_like_instruction(text.split()), text


#: Lines that *do* wake it, and are meant to. The price of covering "sing X",
#: which is the phrasing people reach for and which no amount of cleverness
#: separates from the same word sung. Measured against the library's own 3114
#: slides, `sing` appears in 3.2% of them — the single most expensive word in
#: the vocabulary, taken deliberately.
COSTLY_BUT_KEPT = [
    "we lift our hands and sing",
    "sing of your mercy",
]


@pytest.mark.parametrize("text", COSTLY_BUT_KEPT)
def test_the_price_of_covering_sing(text):
    """Documented, not regretted. A false wake costs one inference the model
    declines; a miss costs the request entirely, and only one of those is
    recoverable."""
    assert looks_like_instruction(text.split()), text


def test_the_gate_asks_nothing_about_length():
    """No minimum words, no minimum duration, no wait for a pause. Each of
    those was a way for a real command to be silently discarded."""
    buf = UtteranceBuffer()
    assert buf.observe(["chorus"], t_seconds=1.0) is not None

    immediate = UtteranceBuffer()
    assert immediate.observe(["go", "back"], t_seconds=0.0) is not None


def test_a_command_still_reaches_the_model_when_singing_never_stops():
    """The old buffer flushed on the falling edge of the talking score, so a
    command with singing straight after it could be held indefinitely."""
    buf = UtteranceBuffer()
    buf.observe(["we", "lift", "our", "hands"], t_seconds=1.0)
    assert buf.observe(["go", "to", "the", "chorus"], t_seconds=2.0) is not None


def test_singing_right_after_a_command_costs_a_few_inferences():
    """The accepted cost of keeping the window open: words arriving just after
    a trigger are offered too, because that is where the song name usually is
    and the buffer cannot tell them apart. A handful of calls against a
    measured ~54 an hour, and the model declines them."""
    buf = UtteranceBuffer(follow_seconds=5.0)
    assert buf.observe(["go", "to", "the", "chorus"], t_seconds=2.0) is not None
    assert buf.observe(["we", "lift", "our", "hands"], t_seconds=3.0) is not None
    # And it does stop.
    assert buf.observe(["we", "lift", "our", "hands"], t_seconds=30.0) is None


# --- the sentence that got shredded -------------------------------------------
#
# Live, 2026-09-15: "Can you go to the bridge of what a beautiful name?" was
# heard by Whisper as ONE clean 11-word segment and delivered by
# `SlidingWindowWrapper` as four events of 4, 1, 3 and 2 words, because the
# wrapper re-chunks by its stability clock and used to discard the backend's
# segmentation entirely. The instruction path saw fragments and the request
# was never followed.
#
# The fragmentation below is the real one, taken from that recording's
# transcript.
REAL_FRAGMENTS = [
    (9.44, ["Then", "you", "go", "to", "the"]),
    (9.82, ["bridge,"]),
    (11.40, ["of", "what", "a"]),
    (12.12, ["beautiful", "name?"]),
]


def test_the_shredded_sentence_reaches_the_model_whole():
    """What the model is handed has to contain the song name, or the request
    names nothing and is correctly declined."""
    buf = UtteranceBuffer()
    sent = []
    for i, (t, words) in enumerate(REAL_FRAGMENTS):
        last = i == len(REAL_FRAGMENTS) - 1
        u = buf.observe(words, t_seconds=t, ends_segment=last)
        if u:
            sent.append(u)

    assert sent, "the request never reached the model at all"
    whole = [u for u in sent if "beautiful" in u and "go" in u]
    assert whole, f"no utterance carried both the verb and the song name: {sent}"


def test_a_completed_sentence_fires_even_with_no_trigger_in_its_last_piece():
    """The closing fragment is "beautiful name?" — no directive verb, no
    section noun. Firing on the trigger alone could never have caught it."""
    buf = UtteranceBuffer()
    buf.observe(["Then", "you", "go", "to", "the"], t_seconds=1.0)
    buf.observe(["bridge,"], t_seconds=1.4)
    tail = ["beautiful", "name?"]
    assert not looks_like_instruction(tail)
    got = buf.observe(tail, t_seconds=3.0, ends_segment=True)
    assert got is not None and "beautiful" in got


def test_a_sentence_ending_with_no_request_in_it_is_not_sent():
    """A boundary is not a reason on its own — most sentences in a service are
    not requests, and each one sent is an inference."""
    buf = UtteranceBuffer()
    assert buf.observe(["thanks", "everyone"], t_seconds=1.0, ends_segment=True) is None


def test_a_completed_request_does_not_drag_the_next_line_in_with_it():
    """Once whole, it needs no follow-up window; leaving one open would
    re-offer the same request with the next line of singing attached."""
    buf = UtteranceBuffer()
    assert buf.observe(["go", "to", "the", "bridge"], t_seconds=1.0,
                       ends_segment=True) is not None
    assert buf.observe(["we", "lift", "our", "hands"], t_seconds=1.5) is None


def test_a_slow_sentence_that_the_time_window_alone_would_miss():
    """Where the boundary earns its keep.

    `follow_seconds` is a guess at where a sentence ended, and it expires. On
    the recording that prompted this work the fragments arrived inside it, so
    the boundary changed nothing measurable there — both routes delivered the
    whole request. A pause mid-sentence is what separates them: the backend
    still knows the sentence has not ended, and a clock does not.
    """
    buf = UtteranceBuffer(follow_seconds=2.0)
    assert buf.observe(["go", "to", "the", "bridge"], t_seconds=1.0) is not None
    # A long pause — thinking, or the ASR withholding unstable words.
    tail = ["of", "what", "a", "beautiful", "name"]
    assert not looks_like_instruction(tail), "the tail must carry no trigger"

    timed_out = UtteranceBuffer(follow_seconds=2.0)
    timed_out.observe(["go", "to", "the", "bridge"], t_seconds=1.0)
    assert timed_out.observe(tail, t_seconds=9.0) is None, (
        "the time window has expired, so this route drops the song name"
    )

    got = buf.observe(tail, t_seconds=9.0, ends_segment=True)
    assert got is not None and "beautiful" in got
