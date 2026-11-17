from types import SimpleNamespace

from auto_presenter.decision.plan_gate import PlanGate, content_word
from auto_presenter.index.library import build_index
from auto_presenter.protocol import SetlistContent


def _item(idx: int, *slides: str) -> dict:
    return {"item_idx": idx, "song_id": f"s{idx}", "slides": [t.split() for t in slides]}


def _setlist():
    return build_index(SetlistContent(items=[
        _item(0, "", "the winter now has passed away your love remains the same",
              "for all that you've done our voices rise in thanks to you",
              "jesus we thank you oh how we thank you",
              "the winter now has passed away your love remains the same"),
        _item(1, "", "night and day day and night let our praises rise"),
        _item(2, "", "from the highest of mountains to the floor of the ocean"),
    ]))


def _words(text: str, t0: float, step: float = 0.4):
    return [SimpleNamespace(text=w, t_end=t0 + k * step) for k, w in enumerate(text.split())]


def test_content_words_drop_function_words_and_plurals():
    assert content_word("You've") == ""
    assert content_word("Heights,") == "height"
    assert content_word("Jesus") == "jesu" or content_word("Jesus") == "jesus"
    assert content_word("pour") == "pour"


def test_on_plan_moves_need_no_words_unless_clearly_talk():
    s, g = _setlist(), PlanGate()
    assert g.allows(s, (0, 0), (0, 1), 10.0, talking=0.55)          # blank -> first slide
    assert g.allows(s, (0, 3), (0, 4), 10.0, talking=0.55)          # next slide
    assert g.allows(s, (0, 4), (1, 1), 10.0, talking=0.55)          # next song's start
    assert g.allows(s, (0, 0), (0, 4), 10.0, talking=0.55)          # same words as the planned next slide


def test_off_plan_talk_sharing_a_few_words_is_withheld():
    s, g = _setlist(), PlanGate()
    g.hear(_words("so I think with all that you've all found out already because", 1.0))
    assert not g.allows(s, (0, 0), (0, 2), 6.0)       # skip to "for all that you've done"
    g.hear(_words("we should calculate it on the lowest level not on the highest level", 7.0))
    assert not g.allows(s, (0, 0), (2, 1), 12.0)      # another song, one shared word


def test_off_plan_move_backed_by_the_slides_words_goes():
    s, g = _setlist(), PlanGate()
    g.hear(_words("from the highest of mountains to the floor of the ocean", 1.0))
    assert g.allows(s, (0, 3), (2, 1), 6.0)           # a song further on: its words back it
    g.hear(_words("the winter now has passed away your love remains the same", 20.0))
    assert g.allows(s, (0, 3), (0, 1), 24.0)          # back to verse 1, sung


def test_words_must_be_recent_and_a_fair_share():
    s, g = _setlist(), PlanGate()
    g.hear(_words("from the highest of mountains to the floor of the ocean", 1.0))
    assert not g.allows(s, (0, 3), (2, 1), 30.0)      # heard too long ago
    g = PlanGate()
    g.hear(_words("highest heights depths " + "meeting budget remote control design project finance " * 2, 1.0))
    assert not g.allows(s, (0, 3), (2, 1), 8.0)       # buried in other talk


def test_zero_disables():
    s, g = _setlist(), PlanGate(min_words=0)
    assert g.allows(s, (0, 0), (2, 1), 1.0)


def test_while_singing_off_plan_moves_go_and_clear_talk_holds_planned_ones():
    s, g = _setlist(), PlanGate()
    assert g.allows(s, (0, 3), (0, 2), 10.0, talking=0.2)       # back to the chorus mid-song
    assert not g.allows(s, (0, 3), (2, 1), 10.0, talking=0.0)   # another song: always its words
    assert not g.allows(s, (0, 3), (0, 2), 10.0, talking=0.5)   # same move on talk
    assert g.allows(s, (0, 3), (0, 4), 10.0, talking=0.55)      # planned: lower bar
    assert not g.allows(s, (0, 3), (0, 4), 10.0, talking=0.9)   # planned, but clearly talk
    g.hear(_words("the winter now has passed away your love remains the same", 1.0))
    assert g.allows(s, (0, 3), (0, 4), 6.0, talking=0.9)        # ... unless the words back it


def test_a_playing_band_means_singing_whatever_the_talking_score():
    s, g = _setlist(), PlanGate()
    # sung words that don't fit the song read as talk; the band says otherwise
    assert g.allows(s, (0, 3), (0, 2), 10.0, talking=0.9, band_playing=True)
    assert g.allows(s, (0, 3), (0, 4), 10.0, talking=0.9, band_playing=True)
    # an unplanned jump into another song still needs its words
    assert not g.allows(s, (0, 3), (2, 1), 10.0, talking=0.9, band_playing=True)
