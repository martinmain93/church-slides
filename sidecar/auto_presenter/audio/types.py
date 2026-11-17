"""Shared audio types.

The whole pipeline operates on 16 kHz mono PCM. That's what faster-whisper
wants natively and it's a common-enough device setting that we can almost
always get it without resampling. When we can't, the capture layer is
responsible for resampling before yielding `AudioFrame`s.

Sample values are normalized `float32` in `[-1.0, 1.0]`.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: Hard-coded sample rate for the entire pipeline.
SAMPLE_RATE = 16_000

#: Default frame length in samples — ~100 ms at 16 kHz.
DEFAULT_FRAME_LENGTH = 1600


@dataclass(frozen=True)
class AudioFrame:
    """A short slice of mono PCM audio with monotonic timestamps.

    `t_start` and `t_end` are seconds, measured against the same monotonic
    clock as the ground-truth events in the eval harness. The capture layer
    is the single source of timestamps so a `Recording` can be replayed at
    real-time speed by simply pacing on `t_start`.
    """

    samples: np.ndarray  # shape (n,), dtype float32, range [-1, 1]
    t_start: float
    t_end: float
    sample_rate: int = SAMPLE_RATE

    def __post_init__(self) -> None:
        if self.samples.ndim != 1:
            raise ValueError(
                f"AudioFrame.samples must be 1-D mono, got shape {self.samples.shape}"
            )
        if self.samples.dtype != np.float32:
            raise ValueError(
                f"AudioFrame.samples must be float32, got {self.samples.dtype}"
            )
        if self.t_end < self.t_start:
            raise ValueError(
                f"AudioFrame t_end ({self.t_end}) < t_start ({self.t_start})"
            )

    @property
    def duration_seconds(self) -> float:
        return self.t_end - self.t_start

    @property
    def n_samples(self) -> int:
        return int(self.samples.shape[0])
