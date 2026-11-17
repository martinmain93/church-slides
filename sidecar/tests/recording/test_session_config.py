"""A recording has to be able to say how it was configured.

Without this a live trace cannot be analysed on the variables being tuned. A
session reported as "lead time 3000, fast-mode on" could not be confirmed from
its own recording, so a diagnosis had to guess at the configuration it was
diagnosing — and when a replay disagrees with the session it replays, that
guess is exactly where the answer lives.
"""

from __future__ import annotations

import json

from auto_presenter.eval.types import Recording, SessionConfig
from auto_presenter.recording.sink import RecordingSink, new_recording_id


def _sink(tmp_path, config=None):
    s = RecordingSink(
        recording_id=new_recording_id(),
        output_dir=tmp_path / "rec",
        setlist_id="setlist-1",
        config=config,
    )
    s.open()
    return s


def test_config_round_trips_to_disk(tmp_path):
    config = SessionConfig(
        asr_backend="mlx",
        asr_model="small.en",
        ai_mode="active",
        tunables={"lead_time_ms": 3000.0, "marginal_threshold": 0.4},
        window_seconds=6.0,
        step_seconds=1.0,
        stable_margin_seconds=1.5,
    )
    sink = _sink(tmp_path, config)
    sink.close()

    written = json.loads((tmp_path / "rec" / "recording.json").read_text())
    back = Recording.model_validate(written)
    assert back.config is not None
    assert back.config.asr_backend == "mlx"
    # The knob whose live value could not be recovered, which is the point.
    assert back.config.tunables["lead_time_ms"] == 3000.0
    assert back.config.stable_margin_seconds == 1.5


def test_recordings_without_a_config_still_load(tmp_path):
    """Every recording on disk before this predates the field. They have to
    keep replaying, or the golden set is lost."""
    sink = _sink(tmp_path)
    sink.close()

    written = json.loads((tmp_path / "rec" / "recording.json").read_text())
    assert Recording.model_validate(written).config is None

    del written["config"]
    assert Recording.model_validate(written).config is None


def test_tunables_are_a_plain_mapping_not_a_fixed_schema():
    """Adding a knob must not invalidate every recording already written, so
    this is a dict rather than a typed model."""
    c = SessionConfig(tunables={"lead_time_ms": 1500.0, "a_knob_added_later": 7.0})
    assert c.tunables["a_knob_added_later"] == 7.0


def test_a_display_log_records_what_moved_the_screen(tmp_path):
    """`source` used to be accepted and dropped, so a display log could say
    *that* the screen changed and never *why*.

    "Was that the matcher, a spoken request, or the operator's hand?" is the
    first question of every live diagnosis, and three separate investigations
    stalled on not being able to answer it from the recording.
    """
    sink = _sink(tmp_path)
    sink.record_slide_change(item_idx=0, slide_idx=1, source="ai")
    sink.record_slide_change(item_idx=4, slide_idx=1, source="voice")
    sink.record_slide_change(item_idx=0, slide_idx=1, source="manual")
    sink.close()

    written = json.loads((tmp_path / "rec" / "recording.json").read_text())
    got = [e.source for e in Recording.model_validate(written).ground_truth]
    assert got == ["ai", "voice", "manual"]


def test_display_logs_written_before_this_still_load(tmp_path):
    """Every golden predates the field."""
    sink = _sink(tmp_path)
    sink.record_slide_change(item_idx=0, slide_idx=1, source="ai")
    sink.close()
    written = json.loads((tmp_path / "rec" / "recording.json").read_text())
    for e in written["ground_truth"]:
        del e["source"]
    back = Recording.model_validate(written)
    assert back.ground_truth[0].source is None


def test_recordings_with_the_removed_fast_channel_fields_still_load():
    """Recordings written before the fast channel left the app (2026-10-19)
    carry `fast_channel` and `fast_feeds_matcher`; they must still load."""
    config = SessionConfig.model_validate(
        {"asr_backend": "mlx", "fast_channel": True, "fast_feeds_matcher": False}
    )
    assert config.asr_backend == "mlx"
