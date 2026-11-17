"""The chord-suggestion gate, with no network."""

from auto_presenter.chords.suggest import _prompt, verify
from auto_presenter.chords.words import line_words
from auto_presenter.protocol import ChordSuggestLine


def line(line_id: str, text: str, role: str = "target", chorded: list[str] | None = None) -> ChordSuggestLine:
    tokens = line_words(text)
    indexes = [tokens.index(word) for word in chorded or []]
    return ChordSuggestLine(
        line_id=line_id,
        section="verse 1",
        line=text,
        role=role,  # type: ignore[arg-type]
        chorded_words=chorded or [],
        chorded_word_indexes=indexes,
    )


def test_phrase_survives_only_when_every_placement_anchors():
    lines = [
        line("s1-l0", "Blessed be Your name"),
        line("s1-l1", "When I'm found in the desert place"),
        line("s1-l2", "Though I walk through the wilderness"),
        line("s2-l0", "Every blessing You pour out"),
    ]
    raw = [
        {"line_id": "s1-l0", "word": "Blessed", "chord": "A"},
        {"line_id": "s1-l1", "word": "found", "chord": "D"},
        {"line_id": "s1-l2", "word": "walk", "chord": "F#m"},
        {"line_id": "s2-l0", "word": "blessing", "chord": "E"},
    ]
    kept, phrase = verify(lines, raw, phrase=True)
    assert phrase is True
    assert [item["chord"] for item in kept] == ["A", "D", "F#m", "E"]


def test_a_bad_placement_collapses_a_phrase_to_one_chord():
    lines = [line("s0-l3", "Blessed be Your name")]
    raw = [
        {"line_id": "s0-l3", "word": "Blessed", "chord": "E"},
        {"line_id": "s0-l3", "word": "imaginary", "chord": "A"},
    ]
    kept, phrase = verify(lines, raw, phrase=True)
    assert phrase is False
    assert kept == [{"line_id": "s0-l3", "word": "Blessed", "word_index": 0, "chord": "E"}]


def test_unsure_reply_shows_only_the_first_good_chord():
    lines = [
        line("s1-l0", "Blessed be Your name"),
        line("s1-l1", "When I'm found in the desert place"),
    ]
    raw = [
        {"line_id": "s1-l0", "word": "Blessed", "chord": "A"},
        {"line_id": "s1-l1", "word": "found", "chord": "D"},
    ]
    kept, phrase = verify(lines, raw, phrase=False)
    assert phrase is False
    assert len(kept) == 1
    assert kept[0]["chord"] == "A"


def test_restating_a_chord_that_is_already_there_does_not_collapse_the_phrase():
    lines = [
        line("s1-l1", "When I'm found", chorded=["found"]),
        line("s1-l2", "in the desert place"),
        line("s2-l0", "Though I walk"),
    ]
    raw = [
        {"line_id": "s1-l1", "word": "found", "chord": "Em"},
        {"line_id": "s1-l2", "word": "place", "chord": "C"},
        {"line_id": "s2-l0", "word": "walk", "chord": "G"},
    ]
    kept, phrase = verify(lines, raw, phrase=True)
    assert phrase is True
    assert [item["chord"] for item in kept] == ["C", "G"]


def test_rejects_lyric_words_charted_lines_and_words_that_already_have_a_chord():
    lines = [
        line("s0-l0", "Blessed be Your name", role="charted", chorded=["Blessed"]),
        line("s0-l3", "Blessed be Your name", chorded=["name"]),
    ]
    raw = [
        {"line_id": "s0-l0", "word": "be", "chord": "A"},
        {"line_id": "s0-l3", "word": "name", "chord": "E"},
        {"line_id": "s0-l3", "word": "Blessed", "chord": "not-a-chord"},
        {"line_id": "missing", "word": "Blessed", "chord": "A"},
    ]
    kept, phrase = verify(lines, raw, phrase=True)
    assert kept == []
    assert phrase is False


def test_a_repeated_word_is_addressed_by_its_number():
    lines = [
        ChordSuggestLine(
            line_id="s0-l0",
            section="verse 1",
            line="Holy, holy, holy",
            chords=["D"],
            chorded_words=["Holy,"],
            chorded_word_indexes=[0],
        )
    ]
    raw = [
        {"line_id": "s0-l0", "word": "holy,", "word_index": 1, "chord": "G"},
        {"line_id": "s0-l0", "word": "holy", "word_index": 2, "chord": "A"},
    ]
    kept, phrase = verify(lines, raw, phrase=True)
    assert phrase is True
    assert [(item["word_index"], item["chord"]) for item in kept] == [(1, "G"), (2, "A")]


def test_a_missing_number_falls_back_to_the_next_unchorded_copy():
    lines = [
        ChordSuggestLine(
            line_id="s0-l0",
            section="verse 1",
            line="Holy, holy, holy",
            chorded_word_indexes=[0],
        )
    ]
    kept, _ = verify(lines, [{"line_id": "s0-l0", "word": "holy", "chord": "G"}], phrase=False)
    assert kept[0]["word_index"] == 1


def test_the_prompt_numbers_every_word():
    text = _prompt("D", [line("s0-l0", "Holy, holy, holy")])
    assert "Holy,[0] holy,[1] holy[2]" in text
