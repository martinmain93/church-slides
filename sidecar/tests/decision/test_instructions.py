"""Spoken instructions: utterance -> resolved slide, and the wake-buffer that
turns a stream of transcript events into discrete utterances.

`RuleBasedBackend` is exercised here as the deterministic path through
`InstructionEngine`/`resolve` — it is not a claim about what a real model
would do. See `decision.instructions`'s module docstring for why that
question is still open.
"""

from __future__ import annotations

from auto_presenter.decision.instructions import (
    GoToSlide,
    InstructionContext,
    InstructionEngine,
    InstructionItem,
    RuleBasedBackend,
    ShowScripture,
    ToolCall,
    UtteranceBuffer,
    build_context,
    resolve,
)
from auto_presenter.index.library import build_index
from auto_presenter.protocol import SetlistContent, SetlistItemContent


def _setlist(
    items: list[tuple[list[list[str]], list[str]]],
    titles: list[str] | None = None,
):
    titles = titles or [""] * len(items)
    content = SetlistContent(
        items=[
            SetlistItemContent(
                item_idx=i, song_id=f"song{i}", title=title,
                slides=slides, slide_sections=sections,
            )
            for i, ((slides, sections), title) in enumerate(zip(items, titles, strict=True))
        ]
    )
    return build_index(content)


def _mercy_song():
    slides = [
        ["long", "before", "the", "dawn", "came"],
        ["oh", "the", "deep", "unending", "mercy"],
        ["it", "follows", "me", "home"],
        ["oh", "the", "deep", "unending", "mercy"],
    ]
    sections = ["verse:1", "chorus:1", "verse:2", "chorus:1"]
    return _setlist([(slides, sections)])


def _two_songs():
    mercy = (
        [["long", "before", "dawn"], ["oh", "the", "unending", "mercy"]],
        ["verse:1", "chorus:1"],
    )
    surrender = (
        [["all", "to", "jesus"], ["i", "surrender", "all"]],
        ["verse:1", "chorus:1"],
    )
    return _setlist([mercy, surrender])


# --- build_context ------------------------------------------------------------


class TestBuildContext:
    def test_section_keys_are_deduplicated_and_ordered_by_first_appearance(self):
        setlist = _mercy_song()
        ctx = build_context(setlist, current_item_idx=0)
        assert ctx.items[0].section_keys == ("verse:1", "chorus:1", "verse:2")

    def test_current_item_is_recorded(self):
        setlist = _two_songs()
        ctx = build_context(setlist, current_item_idx=1)
        assert ctx.current_item_idx == 1
        assert len(ctx.items) == 2

    def test_a_real_title_is_used_when_the_ui_sent_one(self):
        setlist = _setlist(
            [([["a"]], ["verse:1"])], titles=["Unending Mercy"]
        )
        ctx = build_context(setlist, current_item_idx=0)
        assert ctx.items[0].title == "Unending Mercy"

    def test_title_falls_back_to_song_id_when_the_ui_omitted_it(self):
        """Regression: this used to read the leading blank slide's text,
        which is always empty — every song's title was "" and the model had
        no name for anything. `song_id` is at least a stable identifier
        rather than silently empty."""
        setlist = _mercy_song()  # built with no titles
        ctx = build_context(setlist, current_item_idx=0)
        assert ctx.items[0].title == "song0"
        assert ctx.items[0].title != ""

    def test_current_slide_reports_its_section(self):
        setlist = _mercy_song()
        # slide_idx 1 is the chorus (index 0 is the leading blank).
        ctx = build_context(setlist, current_item_idx=0, current_slide_idx=1)
        assert ctx.current_section_key == "chorus:1"

    def test_no_current_slide_means_no_section_reported(self):
        setlist = _mercy_song()
        ctx = build_context(setlist, current_item_idx=0)
        assert ctx.current_section_key == ""

    def test_an_out_of_range_slide_reports_no_section_rather_than_raising(self):
        setlist = _mercy_song()
        ctx = build_context(setlist, current_item_idx=0, current_slide_idx=99)
        assert ctx.current_section_key == ""


# --- resolve --------------------------------------------------------------


class TestResolve:
    def test_a_repeated_section_lands_on_its_first_occurrence(self):
        """Chorus is sung twice (slides 1 and 3); 'the chorus' means the
        start of it, not wherever it last happened to be."""
        setlist = _mercy_song()
        ctx = build_context(setlist, current_item_idx=0)
        result = resolve(setlist, ctx, song_index=None, section_key="chorus:1")
        assert result == GoToSlide(
            item_idx=0, slide_idx=1, song_index=None, section_key="chorus:1"
        )

    def test_null_song_index_means_the_current_song(self):
        setlist = _two_songs()
        ctx = build_context(setlist, current_item_idx=1)
        result = resolve(setlist, ctx, song_index=None, section_key="verse:1")
        assert result is not None
        assert result.item_idx == 1

    def test_an_explicit_song_index_overrides_current(self):
        setlist = _two_songs()
        ctx = build_context(setlist, current_item_idx=1)
        result = resolve(setlist, ctx, song_index=0, section_key="chorus:1")
        assert result is not None
        assert result.item_idx == 0

    def test_a_hallucinated_section_resolves_to_nothing(self):
        """A model naming a section that doesn't exist must not move the
        screen to slide 0 or raise — nothing happening is the safe failure."""
        setlist = _mercy_song()
        ctx = build_context(setlist, current_item_idx=0)
        assert resolve(setlist, ctx, song_index=None, section_key="bridge:1") is None

    def test_a_hallucinated_song_index_resolves_to_nothing(self):
        setlist = _mercy_song()
        ctx = build_context(setlist, current_item_idx=0)
        assert resolve(setlist, ctx, song_index=5, section_key="verse:1") is None


# --- RuleBasedBackend + InstructionEngine ----------------------------------


class TestRuleBasedBackend:
    def test_names_a_section_by_type_and_number(self):
        backend = RuleBasedBackend()
        ctx = InstructionContext(
            items=(InstructionItem(0, "Unending Mercy", ("verse:1", "chorus:1")),),
            current_item_idx=0,
        )
        assert backend.decide("let's go back to verse one", ctx) == ToolCall(
            name="go_to_slide", song_index=None, section_key="verse:1"
        )

    def test_defaults_to_number_one_when_none_is_said(self):
        backend = RuleBasedBackend()
        ctx = InstructionContext(
            items=(InstructionItem(0, "Unending Mercy", ("verse:1", "chorus:1")),),
            current_item_idx=0,
        )
        assert backend.decide("can we do the chorus again", ctx) == ToolCall(
            name="go_to_slide", song_index=None, section_key="chorus:1"
        )

    def test_ordinary_conversation_calls_nothing(self):
        backend = RuleBasedBackend()
        ctx = InstructionContext(
            items=(InstructionItem(0, "Unending Mercy", ("verse:1",)),),
            current_item_idx=0,
        )
        assert backend.decide("let's pray together this morning", ctx) is None

    def test_names_a_song_by_its_first_title_word(self):
        backend = RuleBasedBackend()
        ctx = InstructionContext(
            items=(
                InstructionItem(0, "Unending Mercy", ("verse:1",)),
                InstructionItem(1, "Surrender All", ("verse:1",)),
            ),
            current_item_idx=0,
        )
        assert backend.decide("let's do surrender verse one", ctx) == ToolCall(
            name="go_to_slide", song_index=1, section_key="verse:1"
        )


class TestInstructionEngineEndToEnd:
    def test_a_real_request_moves_to_the_right_slide(self):
        setlist = _mercy_song()
        engine = InstructionEngine(backend=RuleBasedBackend())
        result = engine.decide(
            "let's go back to verse one", setlist, current_item_idx=0
        )
        assert result == GoToSlide(0, 0, song_index=None, section_key="verse:1")

    def test_banter_produces_no_action(self):
        setlist = _mercy_song()
        engine = InstructionEngine(backend=RuleBasedBackend())
        assert engine.decide("amen, come on somebody", setlist, current_item_idx=0) is None

    def test_search_library_imports_when_the_index_has_a_clear_title_hit(self):
        from auto_presenter.decision.instructions import ToolCall
        from auto_presenter.decision.library_recovery import ImportSong
        from auto_presenter.index.fingerprint import FingerprintIndex, catalog_song_from_body

        class SearchAgnus:
            def decide(self, utterance, context):
                return ToolCall(name="search_library", query="Agnus Dei", section_key="chorus:1")

        library = FingerprintIndex.from_songs([
            catalog_song_from_body(
                "agnus", "Agnus Dei", "[chorus]\nHoly holy are you Lord\n"
            )
        ])
        setlist = _mercy_song()
        engine = InstructionEngine(backend=SearchAgnus())
        result = engine.decide(
            "bring up agnus dei", setlist, current_item_idx=0, library=library
        )
        assert isinstance(result, ImportSong)
        assert result.song_id == "agnus"
        assert result.section_key == "chorus:1"

    def test_search_library_of_a_setlist_song_becomes_go_to_slide(self):
        from auto_presenter.decision.instructions import ToolCall
        from auto_presenter.index.fingerprint import FingerprintIndex, catalog_song_from_body

        class SearchMercy:
            def decide(self, utterance, context):
                return ToolCall(name="search_library", query="Unending Mercy")

        library = FingerprintIndex.from_songs([
            catalog_song_from_body(
                "song0", "Unending Mercy",
                "[verse 1]\nLong before dawn\n\n[chorus]\nOh the unending mercy\n",
            )
        ])
        setlist = _mercy_song()
        engine = InstructionEngine(backend=SearchMercy())
        result = engine.decide(
            "bring up unending mercy", setlist, current_item_idx=0, library=library
        )
        assert isinstance(result, GoToSlide)
        assert result.item_idx == 0
        """The backend named something real-shaped, but this setlist has no
        such section — must not raise or fall back to a slide."""

        class NamesNonexistentSection:
            def decide(self, utterance, context):
                return ToolCall(name="go_to_slide", song_index=None, section_key="bridge:1")

        setlist = _mercy_song()
        engine = InstructionEngine(backend=NamesNonexistentSection())
        assert engine.decide("go to the bridge", setlist, current_item_idx=0) is None


def test_a_setlist_song_asked_for_by_name_opens_on_its_first_line_not_the_blank():
    """Live, 2026-10-15: "can we go to Agnus Dei" put up the blank slide that
    leads every song in a setlist — the library's slide numbers, which have
    no blank, were used as the setlist's."""
    from auto_presenter.index.fingerprint import FingerprintIndex, catalog_song_from_body

    class SearchAgnus:
        def decide(self, utterance, context):
            return ToolCall(name="search_library", query="Agnus Dei")

    library = FingerprintIndex.from_songs([
        catalog_song_from_body("song1", "Agnus Dei", "[verse 1]\nAlleluia for the Lord\n"),
    ])
    setlist = _setlist(
        [([["x"]], ["verse:1"]), ([[], ["alleluia", "for", "the", "lord"]], ["", "verse:1"])],
        titles=["Other", "Agnus Dei"],
    )
    result = InstructionEngine(backend=SearchAgnus()).decide(
        "can we go to agnes day", setlist, current_item_idx=0, library=library
    )
    assert result == GoToSlide(1, 1, song_index=1, section_key="")


def test_a_reading_in_the_service_is_left_out_of_the_prompt():
    """A passage or text item reaches the engine as one wordless slide; the
    model is offered the songs either side, under their own positions."""
    setlist = _setlist(
        [
            ([[], ["amazing", "grace"]], ["other:0", "verse:1"]),
            ([[]], ["other:0"]),
            ([[], ["how", "great"]], ["other:0", "verse:1"]),
        ],
        titles=["Amazing Grace", "", "How Great"],
    )
    context = build_context(setlist, 0)
    assert [(i.item_idx, i.title) for i in context.items] == [(0, "Amazing Grace"), (2, "How Great")]


def test_the_passage_on_screen_is_in_the_prompt():
    """So "and the next few verses" continues it instead of moving the song."""
    from dataclasses import replace

    from auto_presenter.decision.instructions import _format_context

    context = replace(build_context(_mercy_song(), 0), scripture=True)
    up = replace(context, passage_on_screen="Hebrews 11:1–3", passage_last="Hebrews 11:1–3")
    assert "on screen now: Hebrews 11:1–3" in _format_context(up)
    down = replace(context, passage_last="Hebrews 11:1–3")
    assert "last one shown was Hebrews 11:1–3" in _format_context(down)
    assert "Hebrews" not in _format_context(replace(up, scripture=False))


class _Says:
    """A backend that makes one fixed call, and records what it was offered."""

    def __init__(self, call):
        self.call = call
        self.offered_scripture = None

    def decide(self, utterance, context):
        self.offered_scripture = context.scripture
        return self.call


class TestScripture:
    def _known(self, reference):
        return {"jeremiah 29:11": "Jeremiah 29:11"}.get(reference.lower())

    def test_a_real_reference_comes_back_canonical(self):
        backend = _Says(ToolCall(name="show_scripture", reference="jeremiah 29:11"))
        engine = InstructionEngine(backend=backend)
        result = engine.decide(
            "bring up jeremiah 29 11", _mercy_song(), 0, scripture=self._known
        )
        assert result == ShowScripture(reference="Jeremiah 29:11", display="full")
        assert backend.offered_scripture is True

    def test_an_invented_reference_does_nothing(self):
        """The safety property: a citation that isn't real never reaches a screen."""
        engine = InstructionEngine(
            backend=_Says(ToolCall(name="show_scripture", reference="Hezekiah 4:2"))
        )
        assert engine.decide("x", _mercy_song(), 0, scripture=self._known) is None

    def test_without_a_verse_store_the_tools_are_not_offered(self):
        backend = _Says(ToolCall(name="show_scripture", reference="jeremiah 29:11"))
        engine = InstructionEngine(backend=backend)
        assert engine.decide("x", _mercy_song(), 0) is None
        assert backend.offered_scripture is False

    def test_a_named_translation_is_passed_through_as_said(self):
        """Matched to an installed translation by the server, which knows
        what is installed; the engine only carries it."""
        from auto_presenter.decision.instructions import _parse_tool_call

        call = _parse_tool_call([
            {
                "function": {
                    "name": "show_scripture",
                    "arguments": '{"reference":"John 3:16","translation":"King James"}',
                }
            }
        ])
        assert call is not None and call.translation == "King James"
        engine = InstructionEngine(
            backend=_Says(
                ToolCall(name="show_scripture", reference="jeremiah 29:11", translation="KJV")
            )
        )
        result = engine.decide("x", _mercy_song(), 0, scripture=self._known)
        assert result == ShowScripture(reference="Jeremiah 29:11", display="full", translation="KJV")

    def test_hide_is_a_scripture_action_with_no_reference(self):
        engine = InstructionEngine(backend=_Says(ToolCall(name="hide_scripture")))
        result = engine.decide("take it down", _mercy_song(), 0, scripture=self._known)
        assert result == ShowScripture(reference="", display="off")


# --- UtteranceBuffer --------------------------------------------------------


class TestUtteranceBuffer:
    """The gate is lexical now. See `UtteranceBuffer` for why the acoustic one
    could not work: "Go to the chorus" scored 0.21 against a 0.8 bar with a
    *ceiling* of 0.74, and removing the offending term measured worse."""

    def test_ordinary_singing_is_never_sent(self):
        buf = UtteranceBuffer()
        assert buf.observe(["shine", "on", "us"], t_seconds=1.0) is None
        assert buf.observe(["for", "ever", "more"], t_seconds=2.0) is None

    def test_a_command_is_sent(self):
        buf = UtteranceBuffer()
        assert buf.observe(["Go", "to", "the", "chorus."], t_seconds=1.0) is not None

    def test_a_single_word_is_enough(self):
        """No minimum length. One word is a cheap inference and the model is a
        better judge than any rule this class could hold."""
        buf = UtteranceBuffer()
        assert buf.observe(["chorus"], t_seconds=1.0) == "chorus"

    def test_it_does_not_wait_for_a_pause(self):
        """The old buffer flushed on a falling edge, so a command with singing
        straight after it might never be handed over at all."""
        buf = UtteranceBuffer()
        flushed = buf.observe(["back", "to", "verse", "one"], t_seconds=1.0)
        assert flushed is not None

    def test_a_command_split_across_events_arrives_whole(self):
        """The live trace caught "Go" and "to the chorus." in separate
        transcript events. Sending only the triggering event would hand the
        model half a sentence."""
        buf = UtteranceBuffer()
        assert buf.observe(["Go"], t_seconds=1.0) == "Go"
        assert buf.observe(["to", "the", "chorus."], t_seconds=2.0) == "Go to the chorus."

    def test_context_carries_words_that_did_not_trigger(self):
        buf = UtteranceBuffer()
        buf.observe(["okay"], t_seconds=1.0)
        assert buf.observe(["everyone", "chorus"], t_seconds=2.0) == "okay everyone chorus"

    def test_context_is_bounded_by_time(self):
        buf = UtteranceBuffer(context_seconds=5.0)
        buf.observe(["ancient", "history"], t_seconds=1.0)
        assert buf.observe(["chorus"], t_seconds=30.0) == "chorus"

    def test_a_trigger_keeps_listening_so_a_split_request_completes(self):
        """A request arrives verb-first and the verb is the trigger, so the
        half naming the song carries no trigger word of its own. Firing once
        meant the model only ever saw "Let's go"."""
        buf = UtteranceBuffer()
        assert buf.observe(["Let's", "go"], t_seconds=1.0) == "Let's go"
        assert buf.observe(["to", "Agnus", "Dei"], t_seconds=2.0) == (
            "Let's go to Agnus Dei"
        )

    def test_the_window_closes(self):
        buf = UtteranceBuffer(follow_seconds=5.0)
        assert buf.observe(["chorus"], t_seconds=1.0) is not None
        assert buf.observe(["shine", "on", "us"], t_seconds=20.0) is None

    def test_punctuation_and_casing_cannot_change_the_answer(self):
        buf = UtteranceBuffer()
        assert buf.observe(["CHORUS!"], t_seconds=1.0) is not None

    def test_reset_forgets_everything(self):
        buf = UtteranceBuffer()
        buf.observe(["okay"], t_seconds=1.0)
        buf.reset()
        assert buf.observe(["chorus"], t_seconds=2.0) == "chorus"



def _setlist_with_sections():
    """Two songs, each with the synthetic leading blank a real setlist has."""
    first = ([[], ["alpha", "bravo"], ["charlie", "delta"]],
             ["other:0", "verse:1", "chorus:1"])
    second = ([[], ["echo", "foxtrot"], ["golf", "hotel"]],
              ["other:0", "verse:1", "chorus:1"])
    return _setlist([first, second], titles=["First Song", "Second Song"])


class TestSectionlessRequests:
    """Naming a song and no section.

    The schema marks `section_key` required and small models omit it anyway —
    reasonably, for "can we do <title>". A bare `args["section_key"]` raised
    KeyError, which the parser's own `except` turned into "unparseable", and
    the request did nothing at all. Measured three of three runs on one
    utterance.
    """

    def test_a_call_with_no_section_is_not_discarded(self):
        from auto_presenter.decision.instructions import _parse_tool_call

        call = _parse_tool_call([
            {"function": {"name": "go_to_slide", "arguments": '{"song_index":4}'}}
        ])
        assert call is not None, "a song named with no section is still a request"
        assert call.song_index == 4
        assert call.section_key == ""

    def test_no_section_means_the_start_of_the_song(self):
        setlist = _setlist_with_sections()
        ctx = build_context(setlist, current_item_idx=0, current_slide_idx=1)
        got = resolve(setlist, ctx, song_index=1, section_key="")
        assert got is not None
        assert got.item_idx == 1
        # Not slide 0: every song carries a synthetic leading blank.
        assert got.slide_idx == 1

    def test_both_paths_agree_on_what_no_section_means(self):
        """`search_library` already documented "omit to open at the first lyric
        slide" while this path rejected it, so the same request behaved
        differently depending on whether the song was in tonight's list."""
        setlist = _setlist_with_sections()
        ctx = build_context(setlist, current_item_idx=0, current_slide_idx=1)
        assert resolve(setlist, ctx, song_index=1, section_key="") is not None

    def test_a_hallucinated_section_still_does_nothing(self):
        setlist = _setlist_with_sections()
        ctx = build_context(setlist, current_item_idx=0, current_slide_idx=1)
        assert resolve(setlist, ctx, song_index=0, section_key="verse:99") is None

    def test_the_leading_blank_is_not_offered_as_a_section(self):
        setlist = _setlist_with_sections()
        ctx = build_context(setlist, current_item_idx=0, current_slide_idx=1)
        assert ctx.items[1].section_keys == ("verse:1", "chorus:1")

    def test_asking_for_the_blank_lands_on_the_first_words(self):
        """"Let's go to John 3" came back as the blank's section, and put the
        reading up on the blank before its first verse (2026-10-29)."""
        setlist = _setlist_with_sections()
        ctx = build_context(setlist, current_item_idx=0, current_slide_idx=1)
        got = resolve(setlist, ctx, song_index=1, section_key="other:0")
        assert got is not None
        assert (got.item_idx, got.slide_idx) == (1, 1)


# --- SentenceGate, mark_sung, wait_for_more (2026-10-17) ---------------------


class TestSentenceGate:
    def test_holds_a_growing_request_and_lets_the_finished_one_through(self):
        from auto_presenter.decision.instructions import SentenceGate

        gate = SentenceGate(pause_s=1.2, max_wait_s=4.0)
        assert gate.offer("we're gonna switch to the first verse", 0.0) is None
        assert gate.poll(0.5) is None
        got = gate.offer("we're gonna switch to the first verse of build my life", 0.9,
                         ends_segment=True)
        assert got == "we're gonna switch to the first verse of build my life"
        assert not gate.holding

    def test_a_pause_releases_it(self):
        from auto_presenter.decision.instructions import SentenceGate

        gate = SentenceGate(pause_s=1.2, max_wait_s=4.0)
        gate.offer("go to the chorus", 0.0)
        assert gate.poll(1.1) is None
        assert gate.poll(1.2) == "go to the chorus"

    def test_singing_straight_after_still_gets_it_through(self):
        from auto_presenter.decision.instructions import SentenceGate

        gate = SentenceGate(pause_s=1.2, max_wait_s=4.0)
        for k, t in enumerate([0.0, 1.0, 2.0, 3.0]):
            assert gate.offer(f"go to the chorus {'la ' * k}", t) is None
        assert gate.poll(4.0) == "go to the chorus la la la "


def _service_setlist():
    content = SetlistContent(items=[
        SetlistItemContent(item_idx=0, song_id="a", title="I Surrender All", slides=[
            [], "all to thee my blessed saviour i surrender all".split(),
            "i surrender all i surrender all".split(),
        ]),
        SetlistItemContent(item_idx=1, song_id="b", title="Morning Light", slides=[
            [], "morning light upon the hills".split(),
            "morning light upon the hills we wake and sing".split(),
        ]),
    ])
    return build_index(content)


class TestMarkSung:
    """The lines from 2026-10-17's service, as Whisper wrote them."""

    def test_the_sung_line_before_a_request_is_marked(self):
        from auto_presenter.decision.instructions import mark_sung

        assert mark_sung("Savior, I surrender all. Now let's sing.", _service_setlist()) == (
            "[sung: Savior, I surrender all.] Now let's sing."
        )

    def test_a_request_naming_a_song_is_left_alone(self):
        from auto_presenter.decision.instructions import mark_sung

        for said in ("Now bring up the lyrics to I surrender all.",
                     "let's all stand and sing. Morning light."):
            assert mark_sung(said, _service_setlist()) == said

    def test_real_singing_is_marked(self):
        from auto_presenter.decision.instructions import mark_sung

        assert mark_sung("morning light upon the hills we wake and sing", _service_setlist()) == (
            "[sung: morning light upon the hills we wake and sing]"
        )


def test_wait_for_more_comes_back_as_its_own_answer():
    from auto_presenter.decision.instructions import (
        InstructionEngine,
        ToolCall,
        WaitForMore,
        _parse_tool_call,
    )

    assert _parse_tool_call([{"function": {"name": "wait_for_more", "arguments": "{}"}}]) == (
        ToolCall(name="wait_for_more")
    )

    class Waits:
        def decide(self, utterance, context):
            return ToolCall(name="wait_for_more")

    got = InstructionEngine(backend=Waits()).decide("switch to the first verse", _service_setlist(), 0)
    assert isinstance(got, WaitForMore)


def test_words_heard_after_a_trigger_dont_keep_the_window_open():
    # Only a trigger extends it: singing straight on after a request used to
    # keep it open — and the model asked — for as long as the song went on.
    buf = UtteranceBuffer(follow_seconds=5.0)
    assert buf.observe(["go", "to", "the", "chorus"], t_seconds=0.0) is not None
    for t in (2.0, 4.0):
        assert buf.observe(["la", "la", "la"], t_seconds=t) is not None
    assert buf.observe(["la", "la", "la"], t_seconds=6.0) is None


def test_a_trigger_word_inside_a_sung_line_is_not_a_request():
    from auto_presenter.decision.instructions import looks_like_instruction, unsung_part

    setlist = build_index(SetlistContent(items=[SetlistItemContent(
        item_idx=0, song_id="r", title="Unending Mercy",
        slides=[[], "long before the dawn your people started singing".split()],
    )]))
    sung = "long before the dawn your people started singing"
    assert looks_like_instruction(sung.split())  # "singing" alone would wake it
    assert not looks_like_instruction(unsung_part(sung, setlist).split())
    said = "Start singing. Start singing. Long before the dawn"
    assert looks_like_instruction(unsung_part(said, setlist).split())
