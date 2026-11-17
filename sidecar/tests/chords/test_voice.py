"""Voice charting keeps every anchored chord from a spoken instruction."""

from auto_presenter.chords.voice import (
    VOICE_MODEL,
    RawChart,
    VoiceChartBackend,
    run_voice_chart,
    tool_spec,
    audio_upload_name,
    chart,
    instruction_is_complete,
    verify_edits,
    verify_voice,
    voice_messages,
)
from auto_presenter.protocol import ChordSuggestLine, VoiceChartRequest, VoiceChartTurn


def line(line_id: str, text: str, section: str = "verse 1") -> ChordSuggestLine:
    return ChordSuggestLine(line_id=line_id, section=section, line=text, role="target")


VERSES = [
    line("s0-l0", "Blessed be Your name"),
    line("s0-l1", "In the land where the barley grows"),
    line("s0-l2", "Where the quiet waters flow"),
    line("s0-l3", "Blessed be Your name"),
]
CHORUS = [
    line("s1-l0", "Every morning You are near", section="chorus"),
    line("s1-l1", "Turn my heart to praise", section="chorus"),
]


def test_a_spoken_progression_keeps_every_chord_and_the_held_bars():
    raw = [
        {"line_id": "s0-l0", "word": "Blessed", "chord": "1"},
        {"line_id": "s0-l1", "word": "In", "chord": "4"},
        {"line_id": "s0-l2", "word": "Where", "chord": "5"},
        {"line_id": "s0-l3", "word": "Blessed", "chord": "1"},
        {"line_id": "s1-l0", "word": "Every", "chord": "6m"},
        {"line_id": "s1-l1", "word": "Turn", "chord": "4"},
        {"line_id": "s9-l0", "word": "missing", "chord": "1"},
    ]
    holds = [{"line_id": "s1-l1", "chord": "5", "bars": 3}, {"line_id": "nope", "chord": "5", "bars": 3}]
    placements, kept_holds, clears = verify_voice(VERSES + CHORUS, raw, holds)
    assert [item.chord for item in placements] == ["1", "4", "5", "1", "6m", "4"]
    assert [(hold.line_id, hold.chord, hold.bars) for hold in kept_holds] == [("s1-l1", "5", 3)]
    assert clears == []


def test_chart_asks_the_model_and_returns_what_anchored():
    class Backend:
        def request(self, song_key, lines, instruction, history=None, slide_edits=False):
            assert song_key == "G"
            assert "twice per verse" in instruction
            assert history == []
            assert any(line.line_id == "s0-l0" for line in lines)
            return RawChart(
                placements=[
                    {"line_id": "s0-l0", "word": "Blessed", "chord": "1"},
                    {"line_id": "s0-l1", "word": "land", "chord": "4"},
                ],
                holds=[{"line_id": "s1-l1", "chord": "5", "bars": 3}],
            )

    placements, holds, clears, edits, error = chart("G", VERSES + CHORUS, "1 4 5 1 twice per verse", Backend())
    assert error is None
    assert [item.word for item in placements] == ["Blessed", "land"]
    assert holds[0].bars == 3
    assert clears == []
    assert edits == []


def test_an_empty_instruction_is_not_sent_to_the_model():
    placements, holds, clears, edits, error = chart("G", VERSES, "   ", backend=None)
    assert placements == []
    assert holds == []
    assert clears == []
    assert edits == []
    assert error == "Didn't catch that"


def test_mp4_audio_is_uploaded_as_mp4():
    assert audio_upload_name("audio/mp4; codecs=mp4a.40.2") == ("audio.mp4", "audio/mp4")
    assert audio_upload_name("audio/webm;codecs=opus") == ("audio.webm", "audio/webm")


def test_removing_chords_keeps_a_cleared_line_and_a_cleared_word():
    class Backend:
        def request(self, song_key, lines, instruction, history=None, slide_edits=False):
            return RawChart(
                placements=[{"line_id": "s0-l0", "word": "Blessed", "chord": ""}],
                clear_lines=["s0-l1", "s9-l0"],
            )

    placements, holds, clears, edits, error = chart("G", VERSES, "Remove all chords from verse 1", Backend())
    assert error is None
    assert placements == []
    assert holds == []
    assert [(item.line_id, item.word) for item in clears] == [("s0-l0", "Blessed"), ("s0-l1", "")]
    assert edits == []


def test_a_pause_holds_an_unfinished_sentence():
    assert instruction_is_complete("Verse 3 is actually") is False
    assert instruction_is_complete("Verse 3 is actually a chorus") is True
    assert instruction_is_complete("Remove all chords from verse 2 and verse 3") is True
    assert instruction_is_complete("Delete verse 3") is True
    assert instruction_is_complete("the chorus") is False


def test_rename_verse_to_chorus_is_kept():
    edits = verify_edits(
        [{"op": "rename", "section": "verse 3", "section_type": "chorus", "section_number": 1, "slide": -1}]
    )
    assert len(edits) == 1
    assert edits[0].op == "rename"
    assert edits[0].section == "verse 3"
    assert edits[0].section_type == "chorus"
    assert verify_edits([{"op": "rename", "section": "verse 3"}]) == []


def test_a_copy_instruction_is_shown_the_chords_already_on_the_song():
    from auto_presenter.chords.voice import SYSTEM_PROMPT

    lines = [
        ChordSuggestLine(
            line_id="s0-l0",
            section="verse 1",
            line="Blessed be Your name",
            chords=["G", "D"],
            chorded_words=["Blessed", "be"],
            chorded_word_indexes=[0, 1],
            role="charted",
        ),
        ChordSuggestLine(line_id="s1-l0", section="verse 2", line="Blessed be Your name", role="target"),
    ]
    text = voice_messages("", lines, "Finish verse 2 and 3 based on the chords from verse 1")[-1]["content"]
    assert "Blessed[0]=G, be[1]=D" in text
    assert "s1-l0 | verse 2 | Blessed[0] be[1] Your[2] name[3] | (no chords)" in text
    assert "return Nashville" not in text
    assert "Do not convert them to numbers" in text
    assert "copy" in SYSTEM_PROMPT.lower()


def test_voice_chart_uses_gpt_oss_120b():
    backend = VoiceChartBackend()
    assert backend._inner.model == VOICE_MODEL == "openai/gpt-oss-120b"


def test_a_later_take_is_sent_as_a_revision_of_the_earlier_ones():
    history = [
        VoiceChartTurn(
            transcript="The verses are 1 4 5 1 twice",
            applied="s0-l0 Blessed=1; s0-l1 In=4",
        )
    ]
    seen: dict = {}

    class Backend:
        def request(self, song_key, lines, instruction, history=None, slide_edits=False):
            seen["messages"] = voice_messages(song_key, lines, instruction, history)
            return RawChart()

    chart("G", VERSES, "No, hold the 4 on the chorus", Backend(), history)
    messages = seen["messages"]
    assert messages[1]["content"].startswith("Earlier instruction:")
    assert "1 4 5 1" in messages[1]["content"]
    assert messages[2]["role"] == "assistant"
    assert "Blessed=1" in messages[2]["content"]
    assert messages[-1]["content"].startswith("This is a revision")
    assert "hold the 4" in messages[-1]["content"]


def test_each_holy_gets_its_own_chord():
    lines = [line("s0-l0", "Holy, holy, holy")]
    raw = [
        {"line_id": "s0-l0", "word": "Holy", "word_index": 0, "chord": "1"},
        {"line_id": "s0-l0", "word": "holy", "word_index": 1, "chord": "4"},
        {"line_id": "s0-l0", "word": "holy", "word_index": 2, "chord": "5"},
    ]
    placements, _, _ = verify_voice(lines, raw, [])
    assert [(item.word_index, item.chord) for item in placements] == [(0, "1"), (1, "4"), (2, "5")]


def test_slide_edits_are_offered_only_when_asked_for():
    assert "slide_edits" not in tool_spec(False)["function"]["parameters"]["properties"]
    assert "slide_edits" in tool_spec(True)["function"]["parameters"]["properties"]
    assert "You can also edit slides" not in voice_messages("G", VERSES, "x")[0]["content"]
    assert "You can also edit slides" in voice_messages("G", VERSES, "x", slide_edits=True)[0]["content"]

    class Backend:
        def request(self, song_key, lines, instruction, history=None, slide_edits=False):
            return RawChart(slide_edits=[{"op": "delete", "section": "verse 1"}])

    *_, edits, _ = chart("G", VERSES, "delete verse 1", Backend())
    assert edits == []
    *_, edits, _ = chart("G", VERSES, "delete verse 1", Backend(), slide_edits=True)
    assert [edit.op for edit in edits] == ["delete"]


def test_a_typed_instruction_skips_transcription_and_holds_a_fragment():
    reply = run_voice_chart(VoiceChartRequest(id=7, lines=VERSES, instruction="Verse 3 is actually"))
    assert reply.id == 7
    assert reply.complete is False
    assert reply.transcript == "Verse 3 is actually"
