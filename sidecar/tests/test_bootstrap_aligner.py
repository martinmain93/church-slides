"""Tests for the repeat-aware aligner in `research/eval/bootstrap_golden.py`.

The script isn't part of the package, so it's loaded by path. It's worth
covering anyway: it decides the ground truth every later eval number is
measured against, and its two hard cases (repeated sections, textually
identical slides) are both silent failures — they produce a plausible-looking
cue list with cues simply missing.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "research" / "eval" / "bootstrap_golden.py"


def _load():
    spec = importlib.util.spec_from_file_location("bootstrap_golden", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # `@dataclass` resolves annotations via sys.modules, so the module has to
    # be registered before it executes.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


bg = _load()


def words(*tokens: str, start: float = 0.0, step: float = 1.0):
    """A transcript of evenly-spaced words."""
    return [
        bg.HypWord(t, start + i * step, start + i * step + 0.5)
        for i, t in enumerate(tokens)
    ]


VERSE = ["long", "before", "the", "dawn", "came"]
CHORUS = ["you", "have", "been", "so", "good"]
BRIDGE = ["there", "is", "no", "shadow", "here"]


def cues(slides, hyp):
    return bg.runs_to_events(bg.decode_slides(hyp, slides), hyp)


def test_emits_a_slide_again_when_its_section_repeats() -> None:
    """The failure that motivated the rewrite: a repeated chorus was silently
    collapsed into one cue by the single-pass aligner."""
    slides = [[], VERSE, CHORUS]
    hyp = words(*VERSE, *CHORUS, *VERSE, *CHORUS)
    assert [s for _t, s in cues(slides, hyp)] == [1, 2, 1, 2]


def test_identical_slides_do_not_freeze_the_decoder() -> None:
    """Two slides with the same words are only separable by how long each is
    held — without that, the decoder parks on the first and emits nothing more."""
    slides = [[], CHORUS, BRIDGE, CHORUS]
    hyp = words(*CHORUS, *BRIDGE, *CHORUS)
    assert [s for _t, s in cues(slides, hyp)] == [1, 2, 3]


def test_cue_times_track_the_first_word_of_each_run() -> None:
    slides = [[], VERSE, CHORUS]
    hyp = words(*VERSE, *CHORUS, step=2.0)
    got = cues(slides, hyp)
    assert got[0][0] == pytest.approx(0.0)
    # The chorus starts at the 6th word -> 5 * 2.0s.
    assert got[1][0] == pytest.approx(10.0)


def test_the_leading_blank_is_never_cued() -> None:
    slides = [[], VERSE, CHORUS]
    hyp = words(*VERSE, *CHORUS)
    assert all(s != 0 for _t, s in cues(slides, hyp))


def test_short_runs_are_dropped_and_the_gap_closes() -> None:
    """A one-word blip mid-slide is decoder noise. Dropping it must also merge
    the two halves back into a single cue, not emit the slide twice."""
    path = [1] * 6 + [2] + [1] * 6 + [3] * 6
    hyp = words(*(["x"] * len(path)))
    kept = bg.runs_to_events(path, hyp, min_run=3)
    assert [s for _t, s in kept] == [1, 3]


def test_degenerate_inputs_do_not_raise() -> None:
    assert bg.runs_to_events([], []) == []
    assert cues([[], VERSE], []) == []
    assert cues([[]], words("anything")) == []


def test_anticipate_pulls_cues_earlier_and_clamps_at_zero() -> None:
    assert bg.anticipate(10.0, 750) == pytest.approx(9.25)
    assert bg.anticipate(0.4, 750) == 0.0
    assert bg.anticipate(10.0, 0) == pytest.approx(10.0)


def test_emission_prefers_the_slide_holding_the_rarer_word() -> None:
    """IDF is what stops a long slide winning purely on word count."""
    common = ["you", "are", "good"]
    distinctive = ["you", "are", "wretch"]
    slides = [[], common, distinctive]
    e = bg.emission_matrix(words("wretch"), slides)
    assert e[0][2] > e[0][1]


# ------------------------------------------------------------- re-timing
#
# Real emissions come from torch + Demucs in a throwaway environment, so these
# build a fake vocal: blank everywhere except the frames where a letter is
# "sung", which is all the CTC aligner looks at.

LABELS = ["-", "|", *"abcdefghijklmnopqrstuvwxyz'"]
FPS = 50.0


def sung(duration: float, *lines: tuple[float, list[str]]):
    """A vocal where each (onset, words) line is sung from `onset`, one letter
    every 2 frames with a blank between."""
    import numpy as np

    frames = np.full((int(duration * FPS), len(LABELS)), -8.0)
    frames[:, 0] = -0.01
    for onset, line in lines:
        f = int(onset * FPS)
        for word in line:
            for ch in word:
                frames[f, :] = -8.0
                frames[f, LABELS.index(ch)] = -0.01
                f += 2
            f += 4
    return bg.VocalEmissions(frames=frames, labels=LABELS, fps=FPS)


def test_ctc_align_places_each_token_on_its_frames() -> None:
    import numpy as np

    seq = [0, 2, 2, 0, 3, 3, 0, 2, 0]  # blank a a blank b b blank a blank
    em = np.full((len(seq), 4), -10.0)
    em[np.arange(len(seq)), seq] = 0.0
    assert bg.ctc_align(em, [2, 3, 2]).tolist() == [[1, 2], [4, 5], [7, 7]]


def test_ctc_align_keeps_a_blank_between_a_doubled_letter() -> None:
    import numpy as np

    seq = [0, 2, 0, 2, 0]
    em = np.full((len(seq), 3), -10.0)
    em[np.arange(len(seq)), seq] = 0.0
    assert bg.ctc_align(em, [2, 2]).tolist() == [[1, 1], [3, 3]]


def test_retime_moves_a_late_cue_onto_the_first_sung_word() -> None:
    """Whisper put the chorus cue 0.6 s late; the vocal says 20.0 s."""
    em = sung(40.0, (10.0, VERSE), (20.0, CHORUS))
    slides = [[], VERSE, CHORUS]
    got = bg.retime_cues([(10.3, 1), (20.6, 2)], slides, em, 40.0)
    assert [s for _t, s in got] == [1, 2]
    assert got[0][0] == pytest.approx(10.0, abs=0.05)
    assert got[1][0] == pytest.approx(20.0, abs=0.05)


def test_retime_never_changes_which_slide_is_cued() -> None:
    em = sung(40.0, (10.0, VERSE), (20.0, CHORUS), (30.0, VERSE))
    slides = [[], VERSE, CHORUS]
    decoded = [(10.2, 1), (20.4, 2), (29.5, 1)]
    got = bg.retime_cues(decoded, slides, em, 40.0)
    assert [s for _t, s in got] == [1, 2, 1]
    assert got[2][0] == pytest.approx(30.0, abs=0.05)


def test_retime_leaves_a_cue_with_nothing_to_align_where_it_was() -> None:
    em = sung(20.0, (5.0, VERSE))
    slides = [[], VERSE]
    got = bg.retime_cues([(1.0, 0), (5.4, 1)], slides, em, 20.0)
    assert got[0] == (1.0, 0)
    assert got[1][0] == pytest.approx(5.0, abs=0.05)


def test_retimed_cues_stay_in_time_order() -> None:
    em = sung(30.0, (10.0, VERSE), (10.5, CHORUS))
    slides = [[], VERSE, CHORUS]
    got = bg.retime_cues([(10.0, 1), (10.4, 2)], slides, em, 30.0)
    assert got[0][0] <= got[1][0]


def test_plan_events_uses_emissions_and_says_so() -> None:
    em = sung(40.0, (10.0, VERSE), (20.0, CHORUS))
    slides = [[], VERSE, CHORUS]
    hyp = words(*VERSE, start=10.4, step=1.0) + words(*CHORUS, start=20.5, step=1.0)
    events, quality = bg.plan_events(
        slides, hyp, 40.0, engine="viterbi", anticipate_ms=0, emissions=em
    )
    assert [e.slide_idx for e in events] == [1, 2]
    assert events[1].t_seconds == pytest.approx(20.0, abs=0.05)
    assert "re-timed" in quality
