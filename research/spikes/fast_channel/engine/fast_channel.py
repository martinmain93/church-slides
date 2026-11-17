"""A low-latency second listener, for watching rather than deciding.

Whisper delivers a word 2-3 s after it is sung, which is why the app catches up
instead of anticipating. This runs a streaming model alongside it that delivers
a word in about 0.4 s for a fifteenth of the compute.

**It does not drive any decision.** Measured on the six approved goldens it
transcribes sung worship audio far worse than Whisper (word error 0.85 against
0.22) and tracking on it alone is markedly worse (wrong-slide 0.748 against
0.484), so wiring it into the matcher would make the app worse. See
docs/research/FAST_CHANNEL.md.

It is here because those measurements are all full-mix band recordings, and live
input may be vocals-forward, a different mic, a different room. Broadcasting what
it hears next to what Whisper hears is how that gets judged on real input instead
of on six files.

Optional by construction: `sherpa_onnx` is not a dependency of the sidecar, and
everything degrades to "unavailable" when it or the model directory is absent.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000

#: Audio handed to the recogniser per call, in samples (50 ms).
#:
#: Capture delivers 100 ms frames; these are split in half before feeding. The
#: model buffers to its own internal chunk size either way, so this changes
#: *when* a word surfaces, not what is heard — measured across 50-500 ms chunks,
#: word count was identical at 402 and WER identical at 0.87, while the delay
#: before a word appeared ran 0.38 s to 0.60 s. Smaller is therefore free
#: latency; 50 ms costs 0.0202 realtime factor against 0.0191 at 100 ms.
FEED_SAMPLES = 800

#: Why the channel is not running, for the panel to explain rather than just
#: showing a blank. "Not configured" is the state most users will be in, and it
#: is a setup step rather than a fault.
STATUS_READY = "ready"
#: Model is on disk but the recogniser is not loaded. Session start probes
#: to this and leaves the CPU free until the debug tab asks to listen.
STATUS_IDLE = "idle"
STATUS_NO_RUNTIME = "runtime_missing"
STATUS_NO_MODEL = "model_missing"
STATUS_FAILED = "load_failed"


def default_model_dir() -> Path:
    """Where `research/spikes/fast_channel/setup_fast_channel.py` puts the model.

    Beside the library and the recordings, so it survives a rebuild and needs no
    configuration. `AUTO_PRESENTER_FAST_MODEL` overrides it for experiments.
    """
    from auto_presenter.config import app_data_dir

    root = app_data_dir() / "models" / "fast-channel"
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if child.is_dir() and _model_stem(child) is not None:
                return child
    return root


@dataclass
class FastWord:
    """One word, with both clocks that matter."""

    text: str
    #: When it was sung, on the audio clock.
    t_start: float
    #: When we found out, on the same clock. The difference is the whole point.
    t_emitted: float

    @property
    def latency(self) -> float:
        return max(0.0, self.t_emitted - self.t_start)


@dataclass
class FastChannel:
    """Wraps a sherpa-onnx streaming recogniser, or degrades to a no-op."""

    model_dir: Path
    num_threads: int = 2
    _recognizer: object | None = field(default=None, init=False, repr=False)
    _stream: object | None = field(default=None, init=False, repr=False)
    _seen: int = field(default=0, init=False)
    _consumed: float = field(default=0.0, init=False)
    _pending: str = field(default="", init=False)
    _pending_t: float | None = field(default=None, init=False)
    _cpu_seconds: float = field(default=0.0, init=False)
    #: Why it is not running, if it is not. See the STATUS_* constants.
    status: str = field(default=STATUS_NO_MODEL, init=False)

    @property
    def available(self) -> bool:
        return self._recognizer is not None

    def probe(self) -> str:
        """Set `status` without loading the recogniser.

        Session start used to call `start()` unconditionally whenever the
        model was installed, which put sherpa-onnx on every capture frame
        of a live service — about 2% of a core, sharing the same four
        performance cores Whisper already saturates. Probe is the cheap
        "is it even there?" check so the debug panel can explain itself
        without paying that cost.
        """
        try:
            import sherpa_onnx  # noqa: F401
        except ImportError:
            self.status = STATUS_NO_RUNTIME
            return self.status
        if _model_stem(self.model_dir) is None:
            self.status = STATUS_NO_MODEL
            return self.status
        if self._recognizer is None:
            self.status = STATUS_IDLE
        return self.status

    def start(self) -> bool:
        """Load the model. Returns False if unavailable, which is not an error."""
        try:
            import sherpa_onnx
        except ImportError:
            self.status = STATUS_NO_RUNTIME
            log.info("fast channel: sherpa-onnx not installed — run "
                     "research/spikes/fast_channel/setup_fast_channel.py")
            return False
        stem = _model_stem(self.model_dir)
        if stem is None:
            self.status = STATUS_NO_MODEL
            log.info("fast channel: no model under %s — run "
                     "research/spikes/fast_channel/setup_fast_channel.py", self.model_dir)
            return False
        try:
            self._recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
                tokens=str(self.model_dir / "tokens.txt"),
                encoder=str(self.model_dir / f"encoder-{stem}.int8.onnx"),
                decoder=str(self.model_dir / f"decoder-{stem}.int8.onnx"),
                joiner=str(self.model_dir / f"joiner-{stem}.int8.onnx"),
                num_threads=self.num_threads,
                provider="cpu",
                enable_endpoint_detection=False,
                decoding_method="greedy_search",
            )
        except Exception:
            self.status = STATUS_FAILED
            log.exception("fast channel: failed to load %s", self.model_dir)
            return False
        self._stream = self._recognizer.create_stream()
        self._seen = 0
        self._consumed = 0.0
        self.status = STATUS_READY
        return True

    def feed(self, samples: np.ndarray) -> list[FastWord]:
        """Push audio, get back whatever words became available because of it."""
        if self._recognizer is None or self._stream is None:
            return []
        t0 = time.monotonic()
        audio = samples.astype(np.float32)
        self._consumed += len(audio) / SAMPLE_RATE
        # Split the capture frame so results surface sooner. See FEED_SAMPLES.
        for i in range(0, len(audio), FEED_SAMPLES):
            self._stream.accept_waveform(SAMPLE_RATE, audio[i:i + FEED_SAMPLES])
            while self._recognizer.is_ready(self._stream):
                self._recognizer.decode_stream(self._stream)
        payload = json.loads(self._recognizer.get_result_as_json_string(self._stream))
        self._cpu_seconds += time.monotonic() - t0

        tokens = payload.get("tokens", [])
        stamps = payload.get("timestamps", [])
        out: list[FastWord] = []
        while len(tokens) > self._seen:
            i = self._seen
            self._seen += 1
            token = tokens[i]
            stamp = stamps[i] if i < len(stamps) else None
            # This model's vocabulary marks a word start with a LEADING SPACE,
            # not the "▁" some sentencepiece models use. Splitting on "▁" yields
            # one enormous word and no usable timing at all.
            if token.startswith(" "):
                if self._pending.strip() and self._pending_t is not None:
                    out.append(FastWord(self._pending.strip(), self._pending_t,
                                        self._consumed))
                self._pending, self._pending_t = token, stamp
            else:
                self._pending += token
                if self._pending_t is None:
                    self._pending_t = stamp
        return out

    @property
    def realtime_factor(self) -> float:
        return self._cpu_seconds / self._consumed if self._consumed else 0.0


def _model_stem(model_dir: Path) -> str | None:
    """Model files are named `encoder-<stem>.int8.onnx`; find <stem>."""
    if not model_dir.is_dir():
        return None
    for p in sorted(model_dir.glob("encoder-*.int8.onnx")):
        return p.name[len("encoder-"):-len(".int8.onnx")]
    return None
