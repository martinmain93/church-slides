"""Stem pictures keep their detectors and take the beat grid from the mix."""

from auto_presenter.music.inspect import STEM_CACHE_VERSION, _with_mix_beats


def test_a_stem_picture_takes_the_mix_beat_grid():
    stem = {
        "version": 2,
        "source": "stems",
        "pitch_backend": "basic_pitch",
        "duration_seconds": 4,
        "onsets": [{"band": "drums", "hz_lo": 0, "hz_hi": 0, "times": [0.0, 0.5, 1.0]}],
        "beats": [0.2],
        "downbeats": [],
    }
    mix = {"beats": [0.0, 0.5, 1.0, 1.5], "downbeats": [0.0, 1.0]}
    out = _with_mix_beats(stem, mix)
    assert out["beats"] == [0.0, 0.5, 1.0, 1.5]
    assert out["downbeats"] == [0.0, 1.0]
    assert out["version"] == STEM_CACHE_VERSION
    assert out["beat_backend"] == "beat_this"
    assert any(event["band"] == "drums" for event in out["rhythm"])
