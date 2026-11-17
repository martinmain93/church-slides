"""Talk over a song's slides must not move the screen (`follower.talk`)."""

from __future__ import annotations

from auto_presenter.follower.online import OnlineFollower
from auto_presenter.follower.talk import TalkGate, lyric_pairs
from tests.follower.test_follower import reference

TALK = (
    "another cool feature this has you can copy and paste free text like a whole "
    "sermon like you know thousands of words whatever and it will automatically pick "
    "up all the bible verses from it and make slides for you instantly"
).split()


def _say(gate_or_follower, words, t0: float, step: float = 0.35, lag: float = 3.0):
    push = getattr(gate_or_follower, "push_word", None) or gate_or_follower.push
    for i, w in enumerate(words):
        push(t0 + i * step, t0 + i * step + lag, w)
    return t0 + len(words) * step + lag


def test_lyric_pairs_skip_pairs_of_stop_words():
    pairs = lyric_pairs([["you", "have", "been", "so", "good", "to", "me"]])
    assert ("so", "good") in pairs
    assert ("to", "me") not in pairs


def test_fluent_talk_latches_and_only_the_songs_words_release_it():
    gate = TalkGate(lyric_pairs([["amazing", "grace", "how", "sweet", "the", "sound"]]))
    end = _say(gate, TALK, 0.0)
    assert gate.talking(end)
    # a pause in the conversation is not the song starting
    assert gate.talking(end + 6.0)
    # the song's words are
    end = _say(gate, ["amazing", "grace", "how", "sweet"], end + 6.0, step=0.8)
    assert not gate.talking(end)


def test_a_loop_of_one_misheard_phrase_is_not_talk():
    """Whisper hallucinating on music ("no, no, no, ...") is many words but
    few different ones."""
    gate = TalkGate(lyric_pairs([["amazing", "grace", "how", "sweet"]]))
    end = _say(gate, ["no"] * 40, 0.0, step=0.2)
    assert not gate.talking(end)


def test_talk_over_the_blank_does_not_start_the_song():
    ref = reference()
    f = OnlineFollower(ref, lookahead_s=0.0)
    f.anchor(0, 0.0)
    # words that share lines' vocabulary ("sound", "saved") but are talk
    end = _say(f, [*TALK, "that", "sound", "saved", "us", *TALK], 0.5)
    for t in range(1, int(end) + 1):
        assert f.readout(float(t)).slide == 0


def test_talk_mid_song_holds_the_slide_on_screen():
    ref = reference()
    f = OnlineFollower(ref, lookahead_s=0.0)
    f.anchor(1, 0.0)
    end = _say(f, TALK, 0.5)
    # a scorer step that, on talk, reads as line 2 heard — the live failure
    f.push_scored(end - 4.0, end, ref.scored({(1, 3): -4.0, (2, 5): -2.0}))
    for t in range(int(end) - 3, int(end) + 8):
        assert f.readout(float(t)).slide == 1


def test_straight_back_to_the_slide_just_left_needs_a_clear_majority(monkeypatch):
    """The 2 s lookahead can reach the next slide while the band is finishing
    this one; the next evidence must not flick the screen back and forth."""
    from auto_presenter.follower import online

    f = OnlineFollower(reference(), lookahead_s=0.0)
    f.anchor(1, 0.0)
    f.readout(1.0)
    f.shown = 2  # moved forward at 1.0 s
    f.readout(1.5)
    monkeypatch.setattr(f, "_choose", lambda m, wall: (1, 0.6))
    assert f.readout(2.0).slide == 2  # a bare majority is not enough ...
    monkeypatch.setattr(f, "_choose", lambda m, wall: (1, 0.9))
    assert f.readout(2.5).slide == 1  # ... a clear one is
    monkeypatch.setattr(f, "_choose", lambda m, wall: (1, 0.6))
    assert f.readout(1.5 + online.FLIP_BACK_S + 0.5).slide == 1  # and later, as usual
