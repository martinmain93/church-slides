"""Can this computer run live following well? Measured on it, not guessed.

Live following on a computer without Apple silicon's GPU runs, on the speech
thread, a short-context transcription pass every 1 s and a span-scoring pass
every 2 s (`WhisperBackend._transcribe_short`, `OnnxSpanScorer`), and beside
them the chord analyser and the follower. Measured in real time on the eight
gold recordings (2026-10-15, M1 CPU): small.en short-context transcription
with the fp32 base.en ONNX scorer is as accurate as the Apple-silicon
reference (0.256 against 0.240, two runs each, run-to-run 0.001-0.004) and
ran clean, no audio dropped, in both runs.

So this times that same work here and passes the computer only if the
speech thread's load, at median timings, is within `LOAD_LIMIT`,
which is calibrated on this check's own measurement: the same M1 measures
0.66-0.71 by it (and with the small.en scorer, which overloaded from the
first recording, about 1.4).
The audio is generated noise and the words come from public-domain hymns, so
nothing copyrighted ships and the check is the same on every computer:

* transcription costs the encoder plus a decoding step per token, so the
  decoder is made to produce `TOKENS_PER_PASS` tokens (a sung 6 s window's
  worth) whatever the audio says;
* scoring costs the same for any audio of a given length, given the spans.

A computer that fails can still be offered **Assist** (the operator drives,
the AI points out where the band went): Assist only has to catch a wrong
section, not switch on time, so it runs the cheaper setup `assist_load`
times: a transcription pass every `ASSIST_TRANSCRIBE_EVERY_S`, no span
scorer and no score follower. Below that bar nothing is offered; the
operator is told before a service (cloud tier: a later phase). Apple silicon passes without a
timing run: its reference path was measured on the M1, the slowest of them.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

#: The highest speech-thread load, as this check measures it (median timings,
#: best of `ATTEMPTS`), proven to run live following clean: the M1 CPU
#: measures 0.66-0.71 here (six quiet single checks; full checks 0.67, 0.68,
#: 0.70) and ran the 8 gold recordings in real time twice with no audio
#: dropped (2026-10-15). A little margin above it, not more: nothing above
#: has been shown clean, and the small.en scorer, which overloaded from the
#: first recording, measures well over 1.
LOAD_LIMIT = 0.75
#: The highest speech-thread load for the Assist-only tier (`assist_load`).
#: Under 1.0 the speech work keeps up with no growing backlog; a little
#: margin below it, since this tier has never been run on a computer that
#: measured close to it (Launch Plan field note 6, 2026-10-28).
ASSIST_LOAD_LIMIT = 0.95
#: Seconds of audio between transcription passes in the Assist-only tier.
ASSIST_TRANSCRIBE_EVERY_S = 2.0
#: Tokens a 6 s window of singing decodes to (measured median ~20-28).
TOKENS_PER_PASS = 24
#: The speech model the CPU path runs and this check judges (the frontend's
#: `CPU_PATH_MODEL`): small.en, measured as accurate as Apple silicon's path.
CPU_PATH_MODEL = "small.en"
#: Attempts before a computer is judged too slow, `RETRY_PAUSE_S` apart.
#: Anything else running (the app starting up, another program) only ever
#: adds time, so the best attempt is the truest measure of the computer: on
#: the M1 that measures 0.72 alone, two checks run at once measured 0.94.
ATTEMPTS = 3
RETRY_PAUSE_S = 3.0
#: Timed repeats per step, after one warm-up.
REPEATS = 8
SAMPLE_RATE = 16000
WINDOW_S = 6.0
#: How often each pass runs (seconds of audio between passes).
TRANSCRIBE_EVERY_S = 1.0
SCORE_EVERY_S = 2.0

#: Public-domain hymn texts (all authors died before 1900), for realistic spans.
HYMNS = [
    [
        "amazing grace how sweet the sound that saved a wretch like me",
        "i once was lost but now am found was blind but now i see",
        "twas grace that taught my heart to fear and grace my fears relieved",
        "how precious did that grace appear the hour i first believed",
    ],
    [
        "when peace like a river attendeth my way when sorrows like sea billows roll",
        "whatever my lot thou hast taught me to say it is well it is well with my soul",
        "it is well with my soul it is well it is well with my soul",
    ],
    [
        "holy holy holy lord god almighty early in the morning our song shall rise to thee",
        "holy holy holy merciful and mighty god in three persons blessed trinity",
    ],
    [
        "come thou fount of every blessing tune my heart to sing thy grace",
        "streams of mercy never ceasing call for songs of loudest praise",
        "teach me some melodious sonnet sung by flaming tongues above",
        "praise the mount i'm fixed upon it mount of thy redeeming love",
    ],
]


@dataclass
class Assessment:
    #: "full": live following as accurate as the reference; "assist": too
    #: slow for that, fast enough for Assist (`ASSIST_LOAD_LIMIT`);
    #: "insufficient": too slow for either, so no live AI; "unknown":
    #: couldn't judge (Low Power Mode, a missing model).
    verdict: str
    backend: str
    reason: str
    #: Speech-thread share of real time at median timings (None if not timed);
    #: the p90 timings are kept to show the slow tail.
    speech_load: float | None = None
    transcribe_ms_p90: float | None = None
    score_ms_p90: float | None = None
    #: The chord analyser's ms of work per second of audio (capture thread).
    chord_ms_per_s: float | None = None
    limit: float = LOAD_LIMIT
    #: The Assist-only tier's speech load (`assist_load`), and its limit.
    assist_load: float | None = None
    assist_limit: float = ASSIST_LOAD_LIMIT
    timings: dict[str, list[float]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def check_id() -> str:
    """What a verdict was judged with: change any of it and a saved verdict
    is stale, and the computer is checked again."""
    from auto_presenter.follower.scorer_models import CPU_SCORER

    # v2: median timings (v1 judged on the p90 of fewer repeats); v3: the
    # Assist-only tier, so a computer judged too slow before is judged again
    return (
        f"v3;limit={LOAD_LIMIT};assist={ASSIST_LOAD_LIMIT}@{ASSIST_TRANSCRIBE_EVERY_S};"
        f"tokens={TOKENS_PER_PASS};scorer={CPU_SCORER.name}"
    )


def save(a: Assessment, path: Path) -> None:
    """Keep a verdict that measured something (not "unknown")."""
    if a.verdict == "unknown":
        return
    data = {"check_id": check_id(), **a.to_dict()}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def load(path: Path) -> Assessment | None:
    """The saved verdict, or None when there is none or it is stale."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if data.pop("check_id", None) != check_id():
        return None
    try:
        return Assessment(**data)
    except TypeError:
        return None


def assessment_spans() -> list[str]:
    from auto_presenter.follower.scorer import spans_for

    spans: set[str] = set()
    for hymn in HYMNS:
        spans.update(spans_for([line.split() for line in hymn]).values())
    return sorted(spans)


def noise(seconds: float, seed: int = 0) -> np.ndarray:
    """Quiet, steady noise: the same input on every computer."""
    rng = np.random.default_rng(seed)
    return (0.05 * rng.standard_normal(int(seconds * SAMPLE_RATE))).astype(np.float32)


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def _p90(xs: list[float]) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, int(0.9 * len(s)))]


def time_transcription(model, pad_s: float) -> list[float]:
    """ms per short-context pass, decoding `TOKENS_PER_PASS` tokens: the work
    `WhisperBackend._transcribe_short` does for a sung window."""
    from faster_whisper.tokenizer import Tokenizer

    tok = Tokenizer(
        model.hf_tokenizer, model.model.is_multilingual, task="transcribe", language="en"
    )
    prompt = [*tok.sot_sequence, tok.no_timestamps]
    audio = np.concatenate([noise(WINDOW_S), np.zeros(int(pad_s * SAMPLE_RATE), np.float32)])
    n_frames = round(len(audio) / SAMPLE_RATE * 100)

    def one() -> None:
        enc = model.encode(model.feature_extractor(audio)[:, :n_frames])
        # end-of-text suppressed: the decoder runs a full window's tokens
        res = model.model.generate(
            enc, [prompt], beam_size=1, max_length=len(prompt) + TOKENS_PER_PASS,
            suppress_tokens=[-1, tok.eot], suppress_blank=False,
        )[0]
        toks = [t for t in res.sequences_ids[0] if t < tok.eot]
        model.find_alignment(tok, [toks], enc, n_frames)

    one()
    out = []
    for _ in range(REPEATS):
        t = time.perf_counter()
        one()
        out.append((time.perf_counter() - t) * 1000)
    return out


def time_scoring(scorer) -> list[float]:
    spans = assessment_spans()
    audio = noise(WINDOW_S, seed=1)
    scorer.score(audio, spans)
    out = []
    for _ in range(REPEATS):
        t = time.perf_counter()
        scorer.score(audio, spans)
        out.append((time.perf_counter() - t) * 1000)
    return out


def time_chords(seconds: float = 6.0) -> float:
    """The session chord analyser's ms of work per second of audio."""
    from auto_presenter.audio.types import AudioFrame
    from auto_presenter.server_music import _new_analyser

    analyser = _new_analyser(True, True)
    audio = noise(seconds + 12.0, seed=2)  # BTC needs ~11 s before it predicts
    step = SAMPLE_RATE // 10
    spent = 0.0
    timed_s = 0.0
    for i in range(0, len(audio) - step, step):
        frame = AudioFrame(samples=audio[i : i + step], t_start=i / SAMPLE_RATE,
                           t_end=(i + step) / SAMPLE_RATE)
        t = time.perf_counter()
        analyser.feed(frame)
        if i >= 12 * SAMPLE_RATE:
            spent += time.perf_counter() - t
            timed_s += step / SAMPLE_RATE
    return 1000 * spent / timed_s if timed_s else 0.0


def speech_load(transcribe_ms: list[float], score_ms: list[float]) -> float:
    """The speech thread's share of real time: a transcription pass every
    `TRANSCRIBE_EVERY_S` and a scoring pass every `SCORE_EVERY_S`, at their
    median cost. The median, not the slow tail: keeping up is a matter of
    sustained cost (the speech pipeline paces itself around an occasional
    slow pass), and with a handful of repeats the p90 is just the slowest
    one. On the M1, one 498 ms pass among ~413 ms ones moved a p90 load from
    0.67 to 0.78 and flipped a verdict between two checks a minute apart."""
    return _median(transcribe_ms) / (TRANSCRIBE_EVERY_S * 1000) + _median(score_ms) / (
        SCORE_EVERY_S * 1000
    )


def assist_load(transcribe_ms: list[float]) -> float:
    """The speech thread's share of real time in the Assist-only tier: a
    transcription pass every `ASSIST_TRANSCRIBE_EVERY_S`, nothing else on
    that thread (no span scorer, no score follower)."""
    return _median(transcribe_ms) / (ASSIST_TRANSCRIBE_EVERY_S * 1000)


def assess_cpu(model, scorer, pad_s: float) -> Assessment:
    """Time the CPU path (`model`: a faster-whisper WhisperModel; `scorer`: an
    `OnnxSpanScorer`) and judge it against `LOAD_LIMIT`."""
    from auto_presenter.pipeline.power import read_power_state

    power = read_power_state()
    if power.low_power_mode or power.battery_low:
        return Assessment(
            "unknown", "whisper",
            "This computer is saving power (Low Power Mode or a nearly flat battery), "
            "which slows it several-fold. Plug it in, turn Low Power Mode off, and check again.",
        )
    best: tuple[float, list[float], list[float]] | None = None
    for attempt in range(ATTEMPTS):
        if attempt:
            time.sleep(RETRY_PAUSE_S)
        tr = time_transcription(model, pad_s)
        sc = time_scoring(scorer)
        load = speech_load(tr, sc)
        if best is None or load < best[0]:
            best = (load, tr, sc)
        if load <= LOAD_LIMIT:
            break
        log.info(
            "machine check attempt %d: speech load %.2f, over %.2f", attempt + 1, load, LOAD_LIMIT
        )
    load, tr, sc = best
    chords = time_chords()
    a_load = assist_load(tr)
    if load <= LOAD_LIMIT:
        verdict = "full"
        reason = (
            f"Keeps up with live following: {load:.0%} of real time on the speech work "
            f"(the limit is {LOAD_LIMIT:.0%})."
        )
    elif a_load <= ASSIST_LOAD_LIMIT:
        verdict = "assist"
        reason = (
            f"Too slow for the AI to change slides itself ({load:.0%} of real time, the "
            f"limit is {LOAD_LIMIT:.0%}), but fast enough for Assist: you change the "
            "slides and the AI points out where the band has gone."
        )
    else:
        verdict = "insufficient"
        reason = (
            f"Too slow for live AI: even Assist's lighter speech work needs {a_load:.0%} of "
            f"real time here, and above {ASSIST_LOAD_LIMIT:.0%} it falls behind the band."
        )
    a = Assessment(
        verdict, "whisper", reason, round(load, 3),
        round(_p90(tr), 1), round(_p90(sc), 1), round(chords, 1),
        assist_load=round(a_load, 3),
        timings={"transcribe_ms": [round(x, 1) for x in tr], "score_ms": [round(x, 1) for x in sc]},
    )
    log.info("machine check: %s (speech load %.2f, assist load %.2f, transcribe p90 %.0f ms, "
             "score p90 %.0f ms, chords %.0f ms/s)", a.verdict, load, a_load,
             a.transcribe_ms_p90, a.score_ms_p90, chords)
    return a


def assess(onnx_dir: Path | None, whisper_model_size: str = CPU_PATH_MODEL) -> Assessment:
    """This computer's verdict. Apple silicon (MLX) passes on the M1's
    measurement; elsewhere the CPU path is timed, if its models are here."""
    from auto_presenter.asr.mlx_backend import MLXWhisperBackend

    if MLXWhisperBackend.is_available():
        return Assessment(
            "full", "mlx",
            "Apple silicon: live following runs on the graphics chip, measured on the M1.",
        )
    from auto_presenter.follower.onnx_scorer import OnnxSpanScorer, available

    if onnx_dir is None or not available(onnx_dir):
        return Assessment(
            "unknown", "whisper", "The AI's scoring model isn't on this computer yet."
        )
    from faster_whisper import WhisperModel

    from auto_presenter.asr.whisper_backend import SHORT_CONTEXT_PAD_S

    try:
        model = WhisperModel(whisper_model_size, device="cpu", compute_type="int8",
                             local_files_only=True)
    except Exception:
        return Assessment("unknown", "whisper", "The AI's speech model isn't on this computer yet.")
    return assess_cpu(model, OnnxSpanScorer(onnx_dir), SHORT_CONTEXT_PAD_S)
