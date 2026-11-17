"""Shared-opening slides, and the words that tell them apart."""

from __future__ import annotations

from auto_presenter.index.confusion import find_confusion_groups

SURRENDER = [
    (0, tuple("all to jesus i surrender all to him i freely give".split())),
    (1, tuple("all to jesus i surrender humbly at his feet i bow".split())),
    (2, tuple("all to jesus i surrender lord i give myself to thee".split())),
]


def test_slides_sharing_an_opening_are_grouped():
    groups = find_confusion_groups(SURRENDER, item_idx=0)
    assert len(groups) == 1
    assert groups[0].slide_idxs == (0, 1, 2)


def test_the_divergence_point_is_where_they_stop_agreeing():
    """'all to jesus i surrender' is five words; word 5 is the first split."""
    (g,) = find_confusion_groups(SURRENDER, item_idx=0)
    assert g.divergence_idx == 5


def test_a_word_from_the_shared_opening_is_never_a_signature():
    """The regression that made this feature actively harmful.

    "Jesus" is in every member's opening *and* recurs later in one of them.
    Judging ownership only over each member's continuation made it look like
    that member's private word, so the shared opening line itself triggered a
    confident branch to the wrong verse.
    """
    slides = list(SURRENDER)
    # Give slide 1 a second "jesus" past the divergence point.
    slides[1] = (1, tuple("all to jesus i surrender humbly jesus at his feet".split()))
    (g,) = find_confusion_groups(slides, item_idx=0)
    assert g.identify("jesus") is None
    assert g.identify("Jesus") is None


def test_a_unique_later_word_identifies_its_slide():
    (g,) = find_confusion_groups(SURRENDER, item_idx=0)
    assert g.identify("freely") == 0
    assert g.identify("humbly") == 1
    assert g.identify("myself") == 2


def test_signatures_survive_being_misheard():
    """Phonetic, because the discriminating word is the one carrying the
    information and therefore the one worth mishearing."""
    (g,) = find_confusion_groups(SURRENDER, item_idx=0)
    assert g.identify("humblee") == 1
    assert g.identify("freeley") == 0


def test_a_word_shared_by_two_members_identifies_nothing():
    (g,) = find_confusion_groups(SURRENDER, item_idx=0)
    # "give" appears in slides 0 and 2 — it cannot settle anything.
    assert g.identify("give") is None


def test_identical_slides_are_a_repeat_not_a_confusion():
    """Showing either copy displays exactly the right words, so there is
    nothing to disambiguate and no group to build."""
    same = tuple("holy holy holy lord god almighty".split())
    assert find_confusion_groups([(0, same), (1, same)], item_idx=0) == []


def test_slides_that_merely_share_two_words_are_left_alone():
    """Two words is grammar; three is a shared line."""
    slides = [
        (0, tuple("i will praise the lord forever".split())),
        (1, tuple("i will sing of mercy now".split())),
    ]
    assert find_confusion_groups(slides, item_idx=0) == []


def test_short_slides_are_skipped():
    assert find_confusion_groups([(0, ("amen",)), (1, ("amen",))], item_idx=0) == []
