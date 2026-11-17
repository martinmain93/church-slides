"""Onsets land on a beat grid as quarters, eighths, sixteenths, or triplets."""

from auto_presenter.music.rhythm import describe_rhythm

# 120 BPM. A beat is half a second, a bar is two seconds.
BEATS = [i * 0.5 for i in range(9)]
DOWNS = [0.0, 2.0, 4.0]


def _shown(events, band):
    return [e for e in events if e.band == band and e.show]


def test_beats_on_the_grid_are_quarters():
    events, bars = describe_rhythm(BEATS, DOWNS, {"kick": BEATS[:-1]})
    kick = [b for b in bars if b.band == "kick"]
    assert kick
    assert {b.pattern for b in kick} == {"quarter"}
    assert _shown(events, "kick")
    assert {e.kind for e in _shown(events, "kick")} == {"quarter"}


def test_offbeats_are_eighths():
    times = [i * 0.25 for i in range(16)]
    events, bars = describe_rhythm(BEATS, DOWNS, {"strum": times})
    assert {b.pattern for b in bars} == {"eighth"}
    off = [e for e in _shown(events, "strum") if abs(e.phase - 0.5) < 0.05]
    assert off
    assert {e.kind for e in off} == {"eighth"}


def test_one_bar_of_sixteenths_hides_the_off_sixteenth_slots():
    times = [i * 0.125 for i in range(16)]
    events, bars = describe_rhythm(BEATS, DOWNS, {"air": times})
    first = next(b for b in bars if b.bar_index == 0)
    assert first.pattern == "sixteenth"
    assert first.stable is False
    fine = [e for e in events if abs(e.phase - 0.25) < 0.05 or abs(e.phase - 0.75) < 0.05]
    assert fine
    assert not any(e.show for e in fine)


def test_two_bars_of_sixteenths_are_shown():
    times = [i * 0.125 for i in range(32)]
    events, bars = describe_rhythm(BEATS, DOWNS, {"air": times})
    assert {b.pattern for b in bars} == {"sixteenth"}
    assert all(b.stable for b in bars)
    shown = _shown(events, "air")
    assert shown
    assert {e.kind for e in shown} == {"sixteenth"}


def test_two_bars_of_triplets_are_shown():
    times = []
    for beat in BEATS[:-1]:
        times.extend([beat, beat + 0.5 / 3, beat + 1.0 / 3])
    events, bars = describe_rhythm(BEATS, DOWNS, {"body": times})
    assert {b.pattern for b in bars} == {"triplet"}
    assert all(b.stable for b in bars)
    assert {e.kind for e in _shown(events, "body")} == {"triplet"}
