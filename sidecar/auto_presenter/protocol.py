"""Wire protocol shared by the sidecar and the React UI.

Every message has a `kind` discriminator. Client requests carry an `id` that
the server echoes back so the client can correlate replies. Each new message
type goes here; codegen mirrors these into TypeScript.
"""

from __future__ import annotations

import sys
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

# Re-exported so codegen (shared/codegen.py) emits TypeScript interfaces for
# these eval models — the Recording editor wire messages reference them.
from auto_presenter.eval.types import (  # noqa: F401
    DeviationMarker,
    GroundTruthEvent,
    Recording,
    SessionConfig,
    SetlistItemSnapshot,
)


#: A recording's folder name. Requests join it onto the recordings folder, so
#: it must be a plain name: no "..", no path separators, no absolute path.
RecordingId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$", max_length=200)]


class _Base(BaseModel):
    id: int | None = None


# --- Heartbeat -----------------------------------------------------------------


class PingMessage(_Base):
    kind: Literal["ping"] = "ping"


class PongMessage(_Base):
    kind: Literal["pong"] = "pong"
    python_version: str = Field(default_factory=lambda: _python_version_str())
    sidecar_pid: int = Field(default_factory=lambda: _pid())


# --- Errors --------------------------------------------------------------------


class ErrorMessage(_Base):
    kind: Literal["error"] = "error"
    message: str


# --- Slide changes + matcher events --------------------------------------------


class SlideChangeMessage(_Base):
    kind: Literal["slide_change"] = "slide_change"
    item_idx: int
    slide_idx: int
    confidence: float = 1.0
    #: "voice" is a spoken instruction ("go back to verse one") resolved by
    #: `decision.instructions` — distinct from "ai" (continuous fuzzy
    #: tracking) because it carries explicit human intent and is worth
    #: telling apart in the UI and the debug panel.
    source: Literal["ai", "override", "manual", "voice"] = "manual"


class Word(BaseModel):
    text: str
    t_start: float
    t_end: float
    confidence: float = 1.0


class TranscriptPartialMessage(_Base):
    kind: Literal["transcript_partial"] = "transcript_partial"
    words: list[Word]
    is_final: bool


class SetBlankWhenIdleRequest(_Base):
    """Blank the screen when nobody is singing the words on it.

    Off by default. Measured with each recording held out of the detector's own
    fit: it blanks on 1.3% of events during real worship, and catches 95-98% of
    sustained speech within six or seven seconds. Being wrong means a blank
    screen mid-song, which is worse than a stale slide, so the trade is set well
    to the cautious side — and it still belongs to the room.
    """

    kind: Literal["set_blank_when_idle"] = "set_blank_when_idle"
    enabled: bool


class SingingStateMessage(_Base):
    """Whether the words being heard are the words on the screen."""

    kind: Literal["singing_state"] = "singing_state"
    singing: bool
    #: "singing", "silence" or "not_these_words".
    reason: str
    #: Whether the display is being blanked as a result.
    blanking: bool
    #: The smoothed talking score behind that call, 0..1. Shown in the debug
    #: panel so a live test can see how close to the line it is running.
    score: float = 0.0


class SetSpokenInstructionsRequest(_Base):
    """Let a local LLM act on things said between songs — "let's go back to
    verse one" — as a slide change, instead of the tracker trying (and
    failing) to match spoken words against sung lyrics.

    Off by default, and unmeasured on real utterances — the roadmap's own
    gate on this feature is "collect real utterances first," which hasn't
    happened yet. `RuleBasedBackend` measurements are not a substitute; see
    `decision.instructions`.

    Assumes no network: the shipped path is a local model via ollama, run at
    session start alongside Whisper. A worship service is exactly the
    setting where the wifi fails and the failure is public.
    """

    kind: Literal["set_spoken_instructions"] = "set_spoken_instructions"
    enabled: bool


class SpokenInstructionMessage(_Base):
    """One buffered utterance and what, if anything, was done about it.

    Diagnostic — shown in the debug panel so a live test can see what the
    model heard and decided, not just that the slide moved.
    """

    kind: Literal["spoken_instruction"] = "spoken_instruction"
    t_seconds: float
    utterance: str
    #: What the model resolved this to, or empty if nothing was applied —
    #: either it called no tool, or it named a song/section that doesn't
    #: exist in this setlist.
    item_idx: int | None = None
    slide_idx: int | None = None
    applied: bool = False
    #: Filled when the utterance resolved through `search_library`.
    song_id: str | None = None
    title: str | None = None
    score: float | None = None
    #: Filled when the utterance asked for a passage ("off" when it asked to
    #: take one down). `applied` is false when the reference wasn't real.
    scripture_reference: str | None = None
    scripture_display: str | None = None


class ImportLibrarySongMessage(_Base):
    """The sidecar found a library song that is not in tonight's set.

    The matcher has already appended it and jumped. The UI persists the
    same append (`add_setlist_item`) so the operator grid matches, then
    jumps to `(item_idx, slide_idx)`. Spontaneous recovery uses
    `source="ai"` (gated on active mode); a spoken `search_library` uses
    `source="voice"` (always applied).
    """

    kind: Literal["import_library_song"] = "import_library_song"
    song_id: str
    title: str
    item_idx: int
    slide_idx: int
    section_key: str = ""
    score: float = 0.0
    runner_up_score: float = 0.0
    query: str = ""
    reason: Literal["spoken", "spontaneous"] = "spontaneous"
    source: Literal["ai", "voice"] = "ai"


class ShowScriptureMessage(_Base):
    """A spoken request to put a passage on screen, or take it down.

    Carries a reference, not text: the frontend resolves it in whichever
    translation the operator chose, the same way a typed reference is. The
    sidecar has already checked that the reference is real, so a model's
    invented citation never reaches a screen.
    """

    kind: Literal["show_scripture"] = "show_scripture"
    #: Canonical reference ("Jeremiah 29:11"); empty when `display` is "off".
    reference: str = ""
    #: "full" replaces the lyrics; "beside" sits next to them; "off" clears.
    display: Literal["full", "beside", "off"] = "full"
    #: An installed translation the speaker named ("read it from the King
    #: James"), or empty for the one already in use. Naming one switches the
    #: service to it.
    translation: str = ""
    source: Literal["voice"] = "voice"


class LiveScriptureNotification(_Base):
    """UI -> sidecar: the passage a person put on screen changed (or came down).

    So a spoken request can be relative to it — "and the next few verses" with
    Hebrews 11:1-3 up means Hebrews 11:4-6, not the song's next verse slide,
    which is what it meant when the model could not see the passage. A
    notification: the sidecar notes it and does not reply.
    """

    kind: Literal["live_scripture"] = "live_scripture"
    reference: str = ""
    display: Literal["full", "beside", "off"] = "off"


class LibrarySearchMessage(_Base):
    """Diagnostic: one library lookup, whether or not it imported anything."""

    kind: Literal["library_search"] = "library_search"
    t_seconds: float
    reason: Literal["spoken", "spontaneous"] = "spontaneous"
    query: str = ""
    song_id: str | None = None
    title: str | None = None
    score: float = 0.0
    runner_up_score: float = 0.0
    applied: bool = False
    detail: str = ""


class InstructionModelInfo(BaseModel):
    """One entry from `decision.instruction_models.CATALOG`, with runtime
    status the catalog itself can't know (whether it's downloaded, whether
    a key is on file). Embedded in `ListInstructionModelsResponse`, never
    dispatched on its own — no `kind`, same convention as `Word`/`Hypothesis`.
    """

    id: str
    label: str
    model_kind: Literal["local", "hosted"]
    size_mb: int = 0
    provider: str = ""
    note: str = ""
    #: Local models only: whether ollama already has this pulled.
    installed: bool = False
    #: Hosted models only: whether a key is on file (env var or saved).
    has_api_key: bool = False


class ListInstructionModelsRequest(_Base):
    kind: Literal["list_instruction_models"] = "list_instruction_models"


class ListInstructionModelsResponse(_Base):
    kind: Literal["list_instruction_models_response"] = "list_instruction_models_response"
    models: list[InstructionModelInfo]
    active_model_id: str
    #: Whether a local ollama server answered at all — distinct from any
    #: individual model's `installed` flag, since "ollama itself isn't
    #: running" needs a different message than "this one model isn't pulled".
    ollama_available: bool


class SetInstructionModelRequest(_Base):
    """Switch which catalog entry spoken instructions runs on.

    Takes effect immediately if the feature is currently enabled — the
    engine is rebuilt against the new model on the next utterance. Setting
    an uninstalled local model is allowed; it will simply produce no
    decisions (with a logged warning) until it's downloaded.
    """

    kind: Literal["set_instruction_model"] = "set_instruction_model"
    model_id: str


class DownloadInstructionModelRequest(_Base):
    """Pull a local model's weights via ollama. No-op (with an error) if
    `model_id` isn't a local catalog entry, or ollama isn't reachable."""

    kind: Literal["download_instruction_model"] = "download_instruction_model"
    model_id: str


class InstructionModelDownloadProgressMessage(_Base):
    """Broadcast while `download_instruction_model` is in flight."""

    kind: Literal["instruction_model_download_progress"] = "instruction_model_download_progress"
    model_id: str
    bytes_downloaded: int
    bytes_total: int
    fraction: float
    state: Literal["starting", "downloading", "completed", "failed"]
    detail: str = ""


class SetInstructionModelApiKeyRequest(_Base):
    """Save (or, with an empty string, clear) an API key for a hosted
    provider. Stored in the app data directory — see `api_keys` for
    exactly what that does and doesn't protect against."""

    kind: Literal["set_instruction_model_api_key"] = "set_instruction_model_api_key"
    provider: str
    api_key: str


class ChordCandidatePayload(BaseModel):
    """One of the chords the analyser is choosing between."""

    name: str
    #: Not a calibrated probability — template cosines are squashed for display
    #: so the panel can show which candidates are close. See
    #: `music.features.softmax`.
    confidence: float


class SetMusicAnalysisRequest(_Base):
    """Turn live chord and beat analysis on or off, independently.

    Off by default and purely diagnostic: nothing in the decision path consumes
    it. Measured on the approved goldens, adding this chord feature to the word
    matcher is worth about +1% and sits at the break-even point for detector
    quality — see docs/research/CHORD_DOWNBEAT.md section 7. It is here to be watched.
    """

    kind: Literal["set_music_analysis"] = "set_music_analysis"
    chords: bool
    beats: bool


class MusicAnalysisMessage(_Base):
    """One tick of live chord/beat analysis, about ten a second.

    Carries only the audio levels *since the previous tick*; the panel keeps the
    scrolling window. Sending the whole window each tick would be simpler and
    would put a megabyte a second on the socket.
    """

    kind: Literal["music_analysis"] = "music_analysis"
    #: End of the analysed audio, on the same monotonic clock as transcripts.
    t_end: float
    #: RMS envelope of the new audio only, 50 samples per second.
    levels: list[float]
    #: Best chord now, or null when harmony is off or the room is quiet.
    chord: str | None = None
    candidates: list[ChordCandidatePayload] = []
    #: Beats placed in this tick. Slightly in the past by construction — a beat
    #: cannot be confirmed until the onset envelope has passed it.
    beats: list[float] = []
    bpm: float = 0.0
    chords_enabled: bool = False
    beats_enabled: bool = False
    #: Share of one core, so the panel can show this is not competing with ASR.
    realtime_factor: float = 0.0
    #: Which recogniser produced `chord`: "btc" (the learned model, roughly
    #: twice as accurate) or "template" (the numpy fallback that always ships).
    chord_backend: str = "template"
    #: Why the learned model is not in use, when it is not — "ready",
    #: "model_missing", "runtime_missing" or "load_failed".
    backend_status: str = "model_missing"


class Hypothesis(BaseModel):
    song_id: str
    slide_idx: int
    marginal: float


class BeliefStateMessage(_Base):
    kind: Literal["belief_state"] = "belief_state"
    top_3: list[Hypothesis]
    ess: float
    is_lost: bool


class FlowPositionMessage(_Base):
    """Where continuous mode should hold the lyric column, and how to move it.

    Not a position stream: a short-lived *track* the display follows on its own
    clock until the next one lands, so the column moves smoothly between
    updates that arrive every 1-3 s. Anchored at receipt:

        word(dt) = min(limit, word + rate_wps*dt + correction*min(1, dt/settle_seconds))

    `word` indexes the item's resolved slide sequence, counting tokens slide by
    slide (the leading blank slide contributes none). `jump` asks the display
    to cut rather than scroll — a different song, or an error bigger than a
    screen. See `decision.flow`.
    """

    kind: Literal["flow_position"] = "flow_position"
    item_idx: int
    word: float
    rate_wps: float
    correction: float = 0.0
    settle_seconds: float = 2.0
    limit: float
    jump: bool = False
    confidence: float = 0.0


class EngineTelemetryMessage(_Base):
    """What the engine is *thinking* between commits, for live sanity checks.

    Every other debug surface reports what already happened — the belief, the
    decisions, the transcript. This reports the three quantities that decide
    what happens *next*, so a run that feels wrong can be diagnosed while it is
    still wrong rather than from a trace afterwards.

    The three came out of a measurement session where each was, at some point,
    silently broken: the word rate was being differentiated off a noisy series
    and sat pinned at its floor; the countdown that depends on it was inflated
    about sixfold, so anticipation almost never fired; and the delivery lag was
    an unmeasured estimate that turned out to have a much heavier tail than the
    estimate suggested. None of the three was visible anywhere in the app.

    Broadcast on each belief update. Fields are `None` when the engine has no
    opinion yet — before the first word, or on a slide with nothing to project
    to — which the UI shows as a dash rather than a zero.
    """

    kind: Literal["engine_telemetry"] = "engine_telemetry"

    #: Seconds until the current slide is projected to end. This is the exact
    #: value `LeadTimeScheduler.project()` branched on, not a recomputation.
    seconds_remaining: float | None = None
    #: The lead time it is being compared against, so the countdown can be
    #: read against its own threshold.
    #: The lead the commit rule is actually using right now, in ms, and the
    #: two measurements it is derived from. Reporting only the total would
    #: hide *which* term moved when the timing goes wrong.
    lead_time_ms: float = 0.0
    lead_lag_ms: float = 0.0
    lead_gap_ms: float = 0.0
    target_margin_ms: float = 0.0
    #: Slides between the belief's own slide and the projection's target.
    #: More than one means the lead outlasted a short slide entirely.
    slides_ahead: int = 0
    #: True once the countdown has elapsed *and* song order was predictive
    #: enough to step forward. A small `seconds_remaining` with this false
    #: means the projection was blocked, which looks identical from outside
    #: to the countdown simply not having elapsed yet.
    advancing: bool = False
    #: Slide the projection points at, and progress through the current one.
    target_slide_idx: int | None = None
    position: float | None = None
    words_on_slide: int | None = None

    #: Tempo the countdown is computed from, in words per second.
    rate_wps: float | None = None

    #: Fraction of the belief that says the current slide ends within the lead
    #: time, and the bar it must clear. This is what the commit rule branches
    #: on when the tracker supplies a distribution; `None` means it fell back
    #: to the point estimate. Watching it move is the difference between "the
    #: engine is nearly ready" and "the engine is split down the middle", which
    #: a single countdown cannot show.
    ending_probability: float | None = None
    ending_probability_bar: float | None = None
    #: How many tracker states the belief is spread over for this slide. One
    #: means there is no distribution to reason about.
    states_on_slide: int = 0

    #: Seconds the belief has been pointing at a slide the screen is ignoring,
    #: and the point at which the watchdog overrides the confidence gate.
    #: A rising number here is the display freezing in slow motion.
    stalled_seconds: float | None = None
    stall_after_seconds: float | None = None
    #: Set while another song is being considered but has not yet earned the
    #: higher cross-song bar — the state that used to be a silent song jump.
    pending_item_idx: int | None = None
    pending_item_observations: int = 0

    #: How stale the transcript is when the matcher can first act on it —
    #: sung-time to available-time, over a rolling window of recent words.
    #: Much larger than `PipelineHealthMessage.asr_ms`, which times only the
    #: backend pass and misses the stability margin and the step.
    latency_median_ms: float | None = None
    latency_p90_ms: float | None = None
    latency_samples: int = 0


# --- Audio device enumeration --------------------------------------------------


class AudioInputDeviceInfo(BaseModel):
    index: int
    name: str
    is_default: bool
    max_input_channels: int
    default_samplerate: float


class ListAudioDevicesRequest(_Base):
    kind: Literal["list_audio_devices"] = "list_audio_devices"


class ListAudioDevicesResponse(_Base):
    kind: Literal["list_audio_devices_response"] = "list_audio_devices_response"
    devices: list[AudioInputDeviceInfo]


# --- Live audio session ---------------------------------------------------------


class SetlistItemContent(BaseModel):
    """One setlist item (song) as the matcher sees it.

    `slides` is a list of pre-tokenized word lists — same format as
    `Slide.words` in the library types. Tokenization happens client-side
    via `src/lib/parseSong.ts`, which mirrors Python's
    `library.parser.tokenize` byte-for-byte (enforced by parity tests).

    `slide_sections` is a parallel array of section identifiers
    (e.g. `"verse:1"`, `"chorus:1"`) — one per slide. Phase 6's
    transition priors use this to weight "stay in section", "repeat
    section", and "jump to bridge" moves. Empty when the UI is on a
    pre-Phase-6 protocol; the matcher degrades to slide-index-only
    transitions.

    `title` is only consumed by spoken instructions (`decision.instructions`),
    to describe a song to the model. Nothing before that feature needed a
    song's name — the matcher scores words, not titles. Empty when the UI is
    on an older protocol; the model falls back to `song_id`, which works but
    reads oddly in a prompt.
    """

    item_idx: int
    song_id: str
    slides: list[list[str]]
    slide_sections: list[str] = []
    title: str = ""
    #: A Bible passage read aloud rather than a song sung (docs/done/SERVICE_ITEMS.md
    #: phase 2): its own timing (`spoken_margin_ms`), no score follower, and
    #: never a place the matcher jumps into on its own — the operator puts a
    #: reading up, and the matcher follows it from there.
    spoken: bool = False


class SetlistContent(BaseModel):
    """All items in a setlist, in order, with pre-tokenized slide words.

    Pushed to the sidecar with `StartAudioSessionRequest` so the matcher
    can build an index without reading the SQLite library directly.
    """

    items: list[SetlistItemContent]


class StartAudioSessionRequest(_Base):
    kind: Literal["start_audio_session"] = "start_audio_session"
    device_index: int | None = None
    #: Optional device name. When present, the sidecar prefers this
    #: over `device_index` — names survive reboots and reordering,
    #: indices don't (a USB mic unplugged between sessions makes the
    #: old index point at a different device, sometimes the speakers).
    device_name: str | None = None
    #: "mock" emits canned demo words for piping the UI end-to-end without
    #: a model. "whisper" loads `WhisperBackend.load(model_size)`; first
    #: call downloads the model from Hugging Face (~75 MB for tiny.en,
    #: more for larger sizes). "groq" sends audio to Groq's hosted Whisper
    #: endpoint instead — no download, no local CPU cost, but needs a
    #: network connection and an API key on file (see `api_keys`).
    #:
    #: "mlx" is the same Whisper weights on Apple silicon's GPU via
    #: `asr.mlx_backend`. `recommend_model` now *defaults* to this when
    #: mlx-whisper is installed: CTranslate2 cannot use Metal, and on an M1
    #: a CPU `small.en` pass overruns the 1 s step while MLX `small.en` fits
    #: (336 ms steady-state). Measured on the seven approved goldens with
    #: identical windowing, wrong-slide 0.330 -> 0.276 and median cue delay
    #: 1.4 s -> 0.8 s. The first MLX call compiles Metal kernels (1–5 s);
    #: the session warms the model before capture so that is not on the
    #: sung-word path.
    backend: Literal["mock", "whisper", "groq", "mlx"] = "mock"
    #: faster-whisper model size when `backend="whisper"` (e.g. "tiny.en");
    #: the Groq model name when `backend="groq"` (e.g.
    #: "whisper-large-v3-turbo"). Ignored when `backend="mock"`.
    model_size: str = "tiny.en"
    #: When true, capture audio + slide-change events to a Recording on
    #: disk for later replay through the eval harness.
    record: bool = False
    #: Required when `record=true`; identifies which setlist is being
    #: presented so the eval harness can pair the recording with its
    #: library.
    setlist_id: str | None = None
    #: Per-slide word lists for every item in the setlist. When present,
    #: the sidecar builds a matcher index from this; when None, the
    #: session runs in transcript-only mode (no AI decisions).
    setlist_content: SetlistContent | None = None
    #: Starting AI mode for this session. The session's mode can be
    #: changed mid-flight with `SetAIModeRequest`. Defaults to "shadow"
    #: per PHASE_5.md — decisions are logged but slides are not advanced
    #: until the operator opts in.
    ai_mode: AIMode = "shadow"
    #: Initial engine tunables.
    tunables: ParticleFilterTunables | None = None
    #: What the screen shows as the session starts. Without it the matcher
    #: assumes nothing is displayed, and its first move may land on any song
    #: without clearing the song-change bar. Absent → item 0, slide 0.
    start_item_idx: int | None = None
    start_slide_idx: int = 0
    #: Act on spoken requests this service: the operator's saved choice, or
    #: None to let the engine decide (on when a hosted model's key is on file).
    spoken_instructions: bool | None = None


class StopAudioSessionRequest(_Base):
    kind: Literal["stop_audio_session"] = "stop_audio_session"


class AudioSessionStateMessage(_Base):
    kind: Literal["audio_session_state"] = "audio_session_state"
    state: Literal["starting", "running", "stopping", "stopped", "error"]
    error: str | None = None
    #: Whose session this is: the `setlist_id` the start request carried —
    #: a service's setlist — or None for a transcript-only session (the
    #: debug panel's). The presentation view acts only on its own; the debug
    #: panel's mic failing must not read as the service's AI failing.
    setlist_id: str | None = None
    #: With "running": whether spoken requests are on for this session, so a
    #: switch showing them reads the engine's decision rather than guessing.
    spoken_instructions: bool | None = None


#: "assist": the operator drives; the engine runs free alongside and the
#: assist layer (`decision.assist`) suggests where the band has gone.
AIMode = Literal["off", "shadow", "assist", "active"]
#: "uncertain" is Phase 7: the tracker knows it has lost the song and is
#: re-localizing. It holds the current slide rather than guessing, and the
#: operator needs to see that this is deliberate rather than a hang.
AIState = Literal["idle", "tracking", "following_human", "uncertain"]


class AIStatusEvent(_Base):
    """Server broadcast of the matcher director's current behaviour.

    The status pill in the UI listens for this. `mode` reflects the
    operator's last `off / shadow / active` choice (a sticky preference);
    `state` is the matcher's live activity within that mode. Phase 5
    keeps these orthogonal: e.g., `mode="active", state="following_human"`
    is the post-override grace period before the AI resumes tracking.
    """

    kind: Literal["ai_status"] = "ai_status"
    mode: AIMode
    state: AIState
    last_decision_t_seconds: float | None = None
    last_override_t_seconds: float | None = None


class SlideRef(BaseModel):
    item_idx: int
    slide_idx: int


class AssistSuggestionMessage(_Base):
    """Server broadcast (assist mode): the band is somewhere the screen isn't.

    Sent when a suggestion is raised, and again with the same id when it
    follows the band forward. `tier` "next" is the next slide being due
    (highlight only); "place" is anywhere else (highlight, and a
    notification when the tile isn't in view). See docs/done/AI_ASSISTED_MODE.md.
    """

    kind: Literal["assist_suggestion"] = "assist_suggestion"
    suggestion_id: int
    tier: Literal["next", "place"]
    item_idx: int
    slide_idx: int
    #: Other places the band might be, best first — "Something else".
    alternatives: list[SlideRef] = Field(default_factory=list)


class AssistClearMessage(_Base):
    """Server broadcast (assist mode): a suggestion no longer stands."""

    kind: Literal["assist_clear"] = "assist_clear"
    suggestion_id: int
    reason: str = ""


class AssistDismissRequest(_Base):
    """UI → sidecar: the operator dismissed a suggestion — the screen is right."""

    kind: Literal["assist_dismiss"] = "assist_dismiss"
    suggestion_id: int


class SetAIModeRequest(_Base):
    """UI → sidecar: switch the matcher's mode between off/shadow/active."""

    kind: Literal["set_ai_mode"] = "set_ai_mode"
    mode: AIMode


class ParticleFilterTunables(BaseModel):
    """Live-tunable engine parameters, and the engine's kill switches.

    The name is historical (it began as the Phase 6 particle filter's knobs,
    which went with it on 2026-10-19; settings saved with them still load).
    The debug panel's Tunables tab sends `SetTunablesRequest` whenever a
    value changes; the sidecar applies it to the running engine and
    broadcasts a `TunablesUpdatedMessage` to all clients so multiple debug
    panels stay in sync.
    """

    #: How early a slide should land, in milliseconds. Positive puts it up
    #: before the line is sung, negative after. 0 aims at the line itself.
    #:
    #: This replaced `lead_time_ms`, which looked like a preference and was
    #: not one. The lead that lands a slide on the beat is
    #: `delivery_lag + gap_to_next_update + this`, and the first two are
    #: measurements that move continuously — 3.92 s summed at the median over
    #: the goldens, 7.13 s at p90. A fixed lead is wrong by however far those
    #: have drifted since it was chosen. The scheduler now derives it; see
    #: `decision.scheduler.LeadTimeSchedulerConfig.target_margin_ms`.
    target_margin_ms: int = 0
    marginal_threshold: float = 0.4
    #: A move the service plan does not predict (not the next slide, not the
    #: next song's start) needs this many distinct content words of the target
    #: slide heard in the last few seconds (`decision.plan_gate`). 0 turns the
    #: gate off. Not shown in the Tunables panel.
    off_plan_min_words: int = 3
    #: Follow songs that have a lead sheet with the score follower
    #: (`auto_presenter.follower`). On by default; the kill switch, not a
    #: setting — the Tunables panel does not show it.
    score_follower: bool = True
    #: Follow a song with no lead sheet by its library slides alone
    #: (`follower.spec.library_spec`) instead of leaving it to the matcher.
    #: Real time, 4 held-out recordings: 0.243 wrong-slide vs production
    #: 0.373, better on all 4 (docs/done/LEARNED_REFERENCES.md, round 6). Kill
    #: switch; not shown in the Tunables panel.
    score_follower_without_sheet: bool = True
    #: Run the score follower without Apple silicon's speech engine (an Intel
    #: Mac, a Windows PC, or Groq's online speech): words and chords, no span
    #: scorer, which needs MLX. Off until measured on the processor path
    #: (research/eval/run_follower_eval.py --backend whisper). Not shown.
    score_follower_cpu: bool = False
    #: Run as a computer the machine check put in the Assist-only tier
    #: (`server_follower.assist_tier`): short-context speech every 2 s, no
    #: follower, no "next slide is due" pulse, Assist only on a slower
    #: computer. For development and evals (research/eval/
    #: record_engine_tapes.py --assist-tier); not shown.
    assist_tier: bool = False
    #: faster-whisper (the CPU speech engine) encodes each window as it is,
    #: plus a little silence, instead of padded to 30 s: about 3x faster a
    #: pass, which lets small.en keep up on a processor
    #: (`WhisperBackend._transcribe_short`, scripts/bench_whisper.py). Off
    #: until the real-time gold eval says the words are as good. Not shown.
    asr_short_context: bool = False
    #: Apple silicon: transcribe with Qwen3-ASR 0.6B streamed incrementally
    #: (`asr.qwen_backend`) instead of Whisper small.en's sliding window. The
    #: follower still scores with Whisper. Needs the sidecar's `qwen` extra;
    #: without it, or if Qwen fails to load, the session runs on Whisper. On:
    #: it beat Whisper on the real-time gold eval, 0.249 vs 0.272 wrong-slide
    #: (docs/research/QWEN3_ASR_IMPROVEMENTS.md). The kill switch; not shown.
    asr_qwen_streaming: bool = True
    #: Research: with `backend="whisper"`, send speech to an online
    #: OpenAI-compatible endpoint (`asr.hosted_backend`), with Whisper on the
    #: processor as the fallback. The endpoint, model and token come from
    #: AUTO_PRESENTER_HOSTED_SPEECH_{URL,MODEL,TOKEN} until the app hands
    #: them over (docs/roadmap/HOSTED_SPEECH.md). Not shown.
    asr_hosted: bool = False
    #: Research: with `backend="whisper"`, stream speech to the streaming Qwen
    #: engine on a GPU server (`asr.remote_backend`, speech-server/),
    #: Whisper on the processor as the fallback.
    #: AUTO_PRESENTER_REMOTE_SPEECH_{URL,TOKEN} until the app hands them over.
    asr_remote: bool = False
    #: Let a key or tempo change in the music (`music.change_watch`) lower the
    #: bar for a song change the words propose. The kill switch, like
    #: `score_follower`; not shown in the Tunables panel.
    music_change_watch: bool = True
    #: Listen for the other setlist songs' opening lines (`follower.openings`)
    #: and change song when one is clearly sung. Kill switch; not shown.
    song_openings_watch: bool = True
    #: How much clearer (gain and lead, `follower.openings.MID_SONG_NUDGE`) a
    #: heard line must be to enter another song anywhere but its first sung
    #: slide. Not shown in the Tunables panel.
    openings_mid_song_nudge: float = 0.5
    #: With no band playing (`music.band`), a song change the opening-line
    #: watch proposes needs a second hearing. Kill switch; not shown.
    openings_confirm_without_band: bool = True
    #: Blank the screen when an AI song change is then not heard (mean opening-
    #: watch gain for the new song below this over its probation). 0 disables.
    song_probation_min_gain: float = 1.2
    #: Learn how this church plays each song from its services
    #: (`auto_presenter.learning`): collection only, nothing reaches the
    #: screen. Kill switch; not shown.
    learned_collect: bool = True
    #: Lay what past services taught about a song (its order, and its line
    #: timing where the library has none) on the score follower's reference.
    #: Kill switch; not shown.
    learned_use: bool = True
    #: Learn each library-followed song's rhythm and chords during the
    #: service (`follower.learn`): a look-back behind live writes what it is
    #: sure of into the live follower's reference, lent to slides with the
    #: same metre. Needs Beat This (dev installs only: not in the frozen app,
    #: weights licence unchecked). Real time, library follower: hymns gain
    #: (I Surrender All 0.294 -> 0.232, Since Your Love 0.261 -> 0.123), a
    #: medley's tag vamp loses (0.220 -> 0.269); see docs/done/LEARNED_REFERENCES.md
    #: round 7. Not shown in the Tunables panel.
    in_service_learning: bool = False
    #: Counted loops in the score follower's reference (`follower.reference.
    #: LOOPS`): a section the band repeats (identical slides in a row, a tag
    #: closing a slide) is followed pass by pass, expecting even counts.
    #: Real time, 4 recordings, library slides: 0.222 -> 0.176 wrong-slide
    #: (King Of My Heart 0.275 -> 0.131). Kill switch; not shown.
    counted_loops: bool = True
    #: The next-line check (`follower.next_line`): hears the band start a new
    #: line about a second after its first word, from Whisper's encoder and a
    #: small phone model, instead of waiting ~3 s for the words. MLX small.en
    #: only. Replayed on 53 held-out public recordings: changes more than 2 s
    #: late or missed 315 -> 126, wrong-slide seconds -31%
    #: (docs/done/NEXT_LINE_CHECK.md). Kill switch; not shown.
    next_line_check: bool = True
    #: Follow a Bible passage as it is read aloud (docs/done/SERVICE_ITEMS.md
    #: phase 2). LibriVox KJV readings, real time (MLX small.en): 0.07
    #: wrong-slide clean, 0.27 with simulated asides. On by default since
    #: 2026-10-29. The kill switch, not a setting; not shown. Off, a passage's
    #: slides keep their place but carry no words, so the AI holds on it.
    #: (Named afresh: `read_along`, its off-by-default predecessor, is still
    #: in settings saved before, and would have kept it off there.)
    follow_readings: bool = True
    #: How early a reading's slide lands, on top of `target_margin_ms`. 0:
    #: the derived lead (measured recognition delay + update gap) is right for
    #: speech too. It shipped at -3000 from a sweep scored on the *spoken*
    #: clock, which leaves that delay out — so it cancelled it, and live every
    #: slide came ~4 s after the reader started it (2026-10-29 recording).
    #: Replayed on the availability clock, 0 puts each slide up 0.8-4.1 s
    #: before the reader reaches it (median 1.3 s).
    spoken_margin_ms: int = 0


class SetTunablesRequest(_Base):
    kind: Literal["set_tunables"] = "set_tunables"
    tunables: ParticleFilterTunables


class TunablesUpdatedMessage(_Base):
    kind: Literal["tunables_updated"] = "tunables_updated"
    tunables: ParticleFilterTunables


class UpdateSetlistContentRequest(_Base):
    """UI → sidecar: the operator edited the running setlist mid-service.

    Rebuilds the matcher's indexed setlist from the fresh content and
    re-anchors the engine + director to `current_item_idx` so tracking
    continues from where the operator is rather than restarting from the
    first slide.
    """

    kind: Literal["update_setlist_content"] = "update_setlist_content"
    setlist_content: SetlistContent
    current_item_idx: int = 0


class SetlistContentUpdatedMessage(_Base):
    """Server ack that the matcher re-indexed (or why it didn't)."""

    kind: Literal["setlist_content_updated"] = "setlist_content_updated"
    item_count: int
    applied: bool
    detail: str = ""


class SlideScore(BaseModel):
    """One slide's fuzzy-match score in the current belief snapshot."""

    item_idx: int
    slide_idx: int
    score: float


class PipelineHealthMessage(_Base):
    """How well capture -> ASR is keeping up with the room.

    Broadcast every couple of seconds while a session runs. Without it,
    overload is invisible: the capture queue quietly discards audio, the
    matcher sees a transcript full of holes, and the operator has no way to
    tell a struggling pipeline from a broken one.
    """

    kind: Literal["pipeline_health"] = "pipeline_health"
    #: Seconds between audio happening and the pipeline getting to it.
    lag_seconds: float = 0.0
    #: Cumulative audio chunks the capture queue had to discard.
    dropped_chunks: int = 0
    healthy: bool = True
    #: Cost, so a change that makes the pipeline more expensive is visible
    #: while it is happening rather than inferred from the fans.
    cpu_percent: float = 0.0
    rss_mb: float = 0.0
    #: Mean wall time of one ASR pass / one matcher update in the window.
    asr_ms: float = 0.0
    matcher_ms: float = 0.0
    #: The online speech service (Groq) is refusing requests — its rate limit
    #: — so the AI hears nothing while everything else looks healthy.
    asr_refused: bool = False
    #: The score follower's span scorer (it shares the GPU queue with ASR):
    #: median ms of its recent passes and how many spans the last one scored
    #: (its own song's plus the other songs' openings). 0 when not running.
    scorer_ms: float = 0.0
    scorer_spans: int = 0
    #: The next-line check's own thread (`follower.next_line`): median ms of
    #: its recent checks (encoder + phone model + decision; due every 0.25 s)
    #: and how many ticks it skipped this session for being behind.
    next_line_ms: float = 0.0
    next_line_skipped: int = 0
    #: The music follower and the chord analyser, on the capture thread
    #: (`pipeline.stage_cost`): ms of work per second of audio (100 = 10% of
    #: a core) and the p95 of one frame. 0 when not running. On an Intel Mac
    #: or a Windows PC they share the processor with speech recognition.
    follower_ms_per_s: float = 0.0
    follower_frame_ms_p95: float = 0.0
    chord_ms_per_s: float = 0.0
    chord_frame_ms_p95: float = 0.0
    #: The computer's power state, reported only while the pipeline is behind
    #: (`pipeline.power`): Low Power Mode, or a battery near empty, slows the
    #: engine several-fold even on a fast machine — and even once plugged in.
    low_power_mode: bool = False
    #: None when not behind, or there is no battery.
    battery_percent: int | None = None
    on_battery: bool = False


class AudioLevelMessage(_Base):
    """Server broadcast: one VU-meter reading from the active session.

    Emitted at the audio capture's frame rate (~10 Hz for 100 ms frames).
    UI clamps `rms_dbfs` to a sensible floor (-60 dB) for display.
    `peak` is the absolute peak sample in [0, 1] — useful for spotting
    clipping that the RMS smooths over.
    """

    kind: Literal["audio_level"] = "audio_level"
    t_seconds: float
    rms_dbfs: float
    peak: float


class ModelDownloadProgressMessage(_Base):
    """Server broadcast: progress for a Whisper model download / load."""

    kind: Literal["model_download_progress"] = "model_download_progress"
    model_size: str
    bytes_downloaded: int
    bytes_total: int
    fraction: float
    state: Literal["starting", "downloading", "completed", "failed"]
    detail: str = ""


class RecommendModelRequest(_Base):
    kind: Literal["recommend_model"] = "recommend_model"


class RecommendModelResponse(_Base):
    kind: Literal["recommend_model_response"] = "recommend_model_response"
    model_size: str
    device: str
    compute_type: str
    reason: str
    has_cuda: bool
    has_mps: bool
    cpu_count: int
    total_ram_gb: float | None = None
    #: "whisper" (faster-whisper / CPU or CUDA) or "mlx" (Apple GPU).
    #: The live session start request takes this as `backend`.
    backend: Literal["whisper", "mlx"] = "whisper"
    #: Whether the recommended model is already on disk. When false, starting
    #: AI needs an internet connection once to fetch `download_mb`; when true,
    #: it works fully offline.
    model_downloaded: bool = False
    #: Approximate size of that first-time download.
    download_mb: int = 0


class AssessMachineRequest(_Base):
    """Can this computer run live following at full accuracy? Timed on it
    (`asr.machine_check`): ~10-30 s of CPU work off Apple silicon."""

    kind: Literal["assess_machine"] = "assess_machine"
    #: Run the check again even when a current verdict is saved (Settings).
    rerun: bool = False


class AssessMachineResponse(_Base):
    kind: Literal["assess_machine_response"] = "assess_machine_response"
    #: "full": live AI as accurate as the reference; "assist": too slow for
    #: that, but Assist is offered (the operator drives, the AI points out
    #: where the band went); "insufficient": too slow for either, so live AI
    #: isn't offered; "unknown": couldn't judge yet (a model still to
    #: download, Low Power Mode).
    verdict: Literal["full", "assist", "insufficient", "unknown"]
    #: "mlx" (Apple silicon's GPU) or "whisper" (the CPU path).
    backend: str
    #: For the operator, in plain words.
    reason: str
    #: The speech thread's share of real time at slow-pass timings, and the
    #: limit it was held to (None when nothing was timed).
    speech_load: float | None = None
    limit: float = 0.0
    transcribe_ms_p90: float | None = None
    score_ms_p90: float | None = None
    chord_ms_per_s: float | None = None
    #: The Assist-only tier's speech load and limit (None when not timed).
    assist_load: float | None = None
    assist_limit: float = 0.0


class ModelStatusRequest(_Base):
    """Is the speech model a live session would use already on this machine?

    Asked for the backend/model the session will *actually* start with, which
    is the operator's choice when they made one — not necessarily the
    recommendation.
    """

    kind: Literal["model_status"] = "model_status"
    backend: Literal["whisper", "mlx", "groq"]
    model_size: str


class ModelStatusResponse(_Base):
    kind: Literal["model_status_response"] = "model_status_response"
    backend: Literal["whisper", "mlx", "groq"]
    model_size: str
    #: On disk, so live AI works with no internet. Always false for "groq",
    #: which never downloads anything.
    downloaded: bool
    #: Approximate first-time download when not `downloaded` (0 for groq).
    download_mb: int
    #: True for "groq": hosted transcription needs internet all service long.
    needs_internet_while_running: bool


class PrepareModelRequest(_Base):
    """Download the speech model now, so the next service works offline.

    The same download a first service start would do, but chosen by the
    operator ahead of time — at home on Saturday rather than in a building
    with no signal on Sunday. Progress arrives as `model_download_progress`.
    """

    kind: Literal["prepare_model"] = "prepare_model"
    backend: Literal["whisper", "mlx", "groq"]
    model_size: str


class PrepareModelResponse(_Base):
    kind: Literal["prepare_model_response"] = "prepare_model_response"
    downloaded: bool


# --- Recording -----------------------------------------------------------------


class RecordSlideChangeNotification(_Base):
    """UI → sidecar: notify the active recording that a slide changed."""

    kind: Literal["record_slide_change"] = "record_slide_change"
    item_idx: int
    slide_idx: int
    #: "voice" is distinct from "override" on purpose: both are human intent,
    #: but one arrived through the microphone and the other through a hand on
    #: the keyboard, and telling them apart is the whole question when a
    #: spoken request appears not to have been followed.
    source: Literal["manual", "override", "ai", "voice"] = "manual"


class RecordingSummary(BaseModel):
    """Lightweight metadata for the Recordings list UI."""

    id: str
    setlist_id: str
    started_at_ms: int
    duration_seconds: float
    ground_truth_count: int
    #: Songs in the recording's setlist snapshot, in order. The UI resolves
    #: these to titles against the library (which lives behind Tauri).
    song_ids: list[str] = Field(default_factory=list)
    #: A `golden.json` exists — it may still be machine-estimated.
    has_golden: bool = False
    #: A human has signed the timeline off in the editor.
    golden_approved: bool = False


class ListRecordingsRequest(_Base):
    kind: Literal["list_recordings"] = "list_recordings"


class ListRecordingsResponse(_Base):
    kind: Literal["list_recordings_response"] = "list_recordings_response"
    recordings: list[RecordingSummary]


class DeleteRecordingRequest(_Base):
    kind: Literal["delete_recording"] = "delete_recording"
    recording_id: RecordingId


class DeleteRecordingResponse(_Base):
    kind: Literal["delete_recording_response"] = "delete_recording_response"
    deleted: bool


class ExportRecordingRequest(_Base):
    kind: Literal["export_recording"] = "export_recording"
    recording_id: RecordingId


class ExportRecordingResponse(_Base):
    kind: Literal["export_recording_response"] = "export_recording_response"
    #: Filesystem path of the produced zip file; UI surfaces in a toast
    #: so the user can locate it in Finder / Explorer.
    zip_path: str | None = None
    error: str | None = None


class RecordingFinalizedMessage(_Base):
    """Server broadcast when a recording session has been finalized."""

    kind: Literal["recording_finalized"] = "recording_finalized"
    recording_id: str
    duration_seconds: float
    ground_truth_count: int


# --- Golden Recording Editor ---------------------------------------------------


class LoadRecordingRequest(_Base):
    kind: Literal["load_recording"] = "load_recording"
    recording_id: RecordingId


class LoadRecordingResponse(_Base):
    kind: Literal["load_recording_response"] = "load_recording_response"
    #: Base recording (from recording.json). None when the id was not found.
    recording: Recording | None = None
    #: Human-corrected ground truth if a golden.json exists; else None and the
    #: editor seeds from `recording.ground_truth`.
    golden_ground_truth: list[GroundTruthEvent] | None = None
    #: Whether that golden annotation carries a human sign-off.
    golden_approved: bool = False
    error: str | None = None


class LoadRecordingAudioRequest(_Base):
    kind: Literal["load_recording_audio"] = "load_recording_audio"
    recording_id: RecordingId
    #: "drums", "bass", "other", or "vocals". Empty loads the original mix.
    stem: str = ""


class LoadRecordingAudioResponse(_Base):
    kind: Literal["load_recording_audio_response"] = "load_recording_audio_response"
    #: base64-encoded WAV bytes; None on error.
    audio_base64: str | None = None
    sample_rate: int = 0
    duration_seconds: float = 0.0
    error: str | None = None


class TraceSampleMsg(BaseModel):
    """One instant of matcher state, for the model inspector."""

    t_seconds: float
    item_idx: int
    slide_idx: int
    marginal: float
    ess: float
    evidence: float
    mode_position: float
    mode_rate: float
    is_lost: bool
    displayed_slide: int | None = None
    top_3: list[SlideScore] = Field(default_factory=list)
    words: list[str] = Field(default_factory=list)


class TraceDecisionMsg(BaseModel):
    """A slide the matcher committed to, and when."""

    t_seconds: float
    item_idx: int
    slide_idx: int


class ReplayRecordingRequest(_Base):
    """Run the matcher over a stored recording and return what it thought."""

    kind: Literal["replay_recording"] = "replay_recording"
    recording_id: RecordingId
    #: Matches `ParticleFilterTunables.target_margin_ms`, so a replay
    #: schedules commits the way the app does. Its predecessor sat at 300
    #: while production ran 1500, which made the inspector's commit markers
    #: describe a scheduler nobody uses — the same drift
    #: `decision.production` exists to prevent.
    target_margin_ms: int = 0


class ReplayRecordingResponse(_Base):
    kind: Literal["replay_recording_response"] = "replay_recording_response"
    samples: list[TraceSampleMsg] = Field(default_factory=list)
    decisions: list[TraceDecisionMsg] = Field(default_factory=list)
    notes: str = ""
    error: str | None = None


class SaveGoldenRequest(_Base):
    kind: Literal["save_golden"] = "save_golden"
    recording_id: RecordingId
    ground_truth: list[GroundTruthEvent]
    notes: str = ""
    #: Human sign-off. False saves progress without claiming the timeline
    #: is finished.
    approved: bool = False


class SaveGoldenResponse(_Base):
    kind: Literal["save_golden_response"] = "save_golden_response"
    saved: bool = False
    error: str | None = None


class MusicChordSpanMsg(BaseModel):
    """A run of one chord label on the inspector timeline."""

    name: str
    t_start: float
    t_end: float


class MusicPulseMsg(BaseModel):
    """Local tempo at one instant. The estimate is allowed to move."""

    t_seconds: float
    bpm: float
    lock: float


class MusicOnsetBandMsg(BaseModel):
    """Onset times inside one frequency band."""

    band: str
    hz_lo: float
    hz_hi: float
    times: list[float] = Field(default_factory=list)


class MusicPitchMsg(BaseModel):
    """One point on the Basic Pitch contour."""

    t_seconds: float
    midi: float
    confidence: float


class MusicNoteMsg(BaseModel):
    """A note span from Basic Pitch's note head."""

    t_start: float
    t_end: float
    midi: int
    confidence: float


class MusicRhythmEventMsg(BaseModel):
    """One onset, measured against the beat that contains it.

    `kind` is the slot it belongs to. `show` is the drawing rule: quarters
    and eighths are always shown when they are close, and sixteenths and
    triplets only when that pattern holds for two bars.
    """

    band: str
    t: float
    phase: float
    kind: str
    error_ms: float
    bar_index: int
    show: bool = False


class MusicBarPatternMsg(BaseModel):
    """The rhythmic pattern of one band across one bar.

    This is the unit a later fingerprint can compare: which pattern, and
    whether a fine pattern was steady enough to trust.
    """

    band: str
    bar_index: int
    t_start: float
    t_end: float
    pattern: str
    stable: bool = False
    onset_count: int = 0
    on_grid: int = 0


class InspectMusicRequest(_Base):
    """Analyse a recording for the inspector's music panel.

    Offline. Beat This and Basic Pitch run here, not on the live audio
    thread. The reply is cached beside the recording.

    `source` "mix" reads the recording. "stems" separates it first and
    runs beats and downbeats on the mix, chords on the harmonic stem,
    and pitch on vocals.
    """

    kind: Literal["inspect_music"] = "inspect_music"
    recording_id: RecordingId
    source: Literal["mix", "stems"] = "mix"


class InspectMusicResponse(_Base):
    kind: Literal["inspect_music_response"] = "inspect_music_response"
    duration_seconds: float = 0
    beats: list[float] = Field(default_factory=list)
    downbeats: list[float] = Field(default_factory=list)
    #: "beat_this", or why it is not (missing, failed, timeout).
    beat_backend: str = ""
    chords: list[MusicChordSpanMsg] = Field(default_factory=list)
    #: "btc" or "template".
    chord_backend: str = ""
    pulse: list[MusicPulseMsg] = Field(default_factory=list)
    onsets: list[MusicOnsetBandMsg] = Field(default_factory=list)
    pitch: list[MusicPitchMsg] = Field(default_factory=list)
    notes: list[MusicNoteMsg] = Field(default_factory=list)
    #: "basic_pitch", or why it is not.
    pitch_backend: str = ""
    #: "mix" reads the recording. "stems" ran the detectors on separated audio.
    source: str = "mix"
    #: "separating" while stems are still being split, "analysing" after that.
    stage: str = ""
    #: Onsets measured against the beat grid. The drawing reads `show`.
    rhythm: list[MusicRhythmEventMsg] = Field(default_factory=list)
    #: One pattern per band per bar. The fingerprint reads this, not the pixels.
    rhythm_bars: list[MusicBarPatternMsg] = Field(default_factory=list)
    #: True while later stretches of the file are still being analysed.
    #: Each partial is the prefix so far, not a delta, and the last message
    #: has partial false.
    partial: bool = False
    #: Time, in seconds, through which pitch, chords and onsets are filled in.
    through_seconds: float = 0
    error: str | None = None


# --- Scripture -----------------------------------------------------------------
#
# Nothing here reaches the matcher or the audio pipeline. The sidecar's role in
# the Bible feature is the two things the frontend cannot do for itself: hold a
# whole translation, and talk to a model. Song↔scripture links live in SQLite
# on the Rust side and never pass through here.


class ScriptureVerse(BaseModel):
    """One verse. Embedded, never dispatched — same convention as `Word`."""

    ref: str
    text: str


class ScripturePassage(BaseModel):
    """A resolved reference: what goes on a screen."""

    reference: str
    translation: str
    verses: list[ScriptureVerse]
    text: str
    #: Credit line for the translation. Public-domain texts still deserve one,
    #: and a licensed translation would require it.
    attribution: str = ""
    #: Shown beside the reference ("NIV"). Empty from an older sidecar, in
    #: which case the translation id, uppercased, stands in.
    abbreviation: str = ""
    #: The section's heading when the passage is a whole section ("Jesus and
    #: Nicodemus") — set only by `passage_contexts`.
    heading: str | None = None


class ScriptureTranslationInfo(BaseModel):
    id: str
    name: str
    attribution: str
    #: Whether the verse store for this translation is on disk yet.
    installed: bool = False
    abbreviation: str = ""
    #: One line for the catalogue ("Modern English, 66 books.").
    description: str = ""
    #: Imported from a church's own file, rather than the public-domain
    #: catalogue. Only these can't be downloaded again once removed.
    imported: bool = False
    language: str = ""


class ResolvePassagesRequest(_Base):
    kind: Literal["resolve_passages"] = "resolve_passages"
    references: list[str]
    translation: str = ""


class ResolvePassagesResponse(_Base):
    kind: Literal["resolve_passages_response"] = "resolve_passages_response"
    passages: list[ScripturePassage]
    #: Reference -> why it could not be resolved. Returned rather than raised
    #: because one bad reference in a setlist must not cost the other twenty.
    errors: dict[str, str] = {}
    #: Reference as asked -> the canonical reference of the passage it found.
    #: A passage is keyed by its canonical form ("Jeremiah 29:11", en dash in
    #: ranges), which is rarely how anyone types it ("jer 29:11", "1-3"), so
    #: a caller holding what was typed needs this to find its passage.
    resolved: dict[str, str] = {}


class PassageContextsRequest(_Base):
    """Wider passages around one verse (`scripture.sections`)."""

    kind: Literal["passage_contexts"] = "passage_contexts"
    reference: str
    translation: str = ""


class PassageContextsResponse(_Base):
    kind: Literal["passage_contexts_response"] = "passage_contexts_response"
    #: Narrowest first; empty for anything but a single real verse.
    passages: list[ScripturePassage]


class FindScriptureReferencesRequest(_Base):
    """The passages in a message the pastor sent (`scripture.extract`)."""

    kind: Literal["find_scripture_references"] = "find_scripture_references"
    text: str
    translation: str = ""


class FindScriptureReferencesResponse(_Base):
    kind: Literal["find_scripture_references_response"] = "find_scripture_references_response"
    #: Each passage found, once, in the order the message gives them.
    passages: list[ScripturePassage]
    #: References a model named that the installed Bible doesn't hold.
    #: Shown so the operator can fix a typo, never shown on a screen.
    unresolved: list[str] = []
    #: Whether a model read the message, or each phrase was tried as it stood.
    used_model: bool = False


class SearchScriptureRequest(_Base):
    kind: Literal["search_scripture"] = "search_scripture"
    text: str
    translation: str = ""
    limit: int = 8


class SearchScriptureResponse(_Base):
    kind: Literal["search_scripture_response"] = "search_scripture_response"
    passages: list[ScripturePassage]


class ListScriptureTranslationsRequest(_Base):
    kind: Literal["list_scripture_translations"] = "list_scripture_translations"


class ListScriptureTranslationsResponse(_Base):
    kind: Literal["list_scripture_translations_response"] = (
        "list_scripture_translations_response"
    )
    translations: list[ScriptureTranslationInfo]
    default_translation: str


class InstallScriptureTranslationRequest(_Base):
    """Download a public-domain translation: a built-in id ("web") or an
    eBible.org catalogue id ("engbsb")."""

    kind: Literal["install_scripture_translation"] = "install_scripture_translation"
    translation: str


class InstallScriptureTranslationResponse(_Base):
    kind: Literal["install_scripture_translation_response"] = (
        "install_scripture_translation_response"
    )
    #: The id it was installed under — what settings and lookups use. For a
    #: catalogue id this can differ from the request ("eng-kjv2006" -> "kjv").
    translation: str
    installed: bool = False
    error: str | None = None


class ScriptureCatalogEntry(BaseModel):
    """A public-domain translation eBible.org offers for download."""

    #: eBible.org's id: what `InstallScriptureTranslationRequest` takes.
    id: str
    #: The id it is (or would be) installed under.
    store_id: str
    title: str
    abbreviation: str = ""
    description: str = ""
    language: str = ""
    language_code: str = ""
    ot_books: int = 0
    nt_books: int = 0
    dc_books: int = 0
    installed: bool = False


class SearchScriptureCatalogRequest(_Base):
    kind: Literal["search_scripture_catalog"] = "search_scripture_catalog"
    #: Empty lists every translation (English first).
    query: str = ""
    refresh: bool = False


class SearchScriptureCatalogResponse(_Base):
    kind: Literal["search_scripture_catalog_response"] = "search_scripture_catalog_response"
    entries: list[ScriptureCatalogEntry] = []
    error: str | None = None


class RemoveScriptureTranslationRequest(_Base):
    kind: Literal["remove_scripture_translation"] = "remove_scripture_translation"
    translation: str


class RemoveScriptureTranslationResponse(_Base):
    kind: Literal["remove_scripture_translation_response"] = (
        "remove_scripture_translation_response"
    )
    translation: str
    removed: bool = False
    error: str | None = None


class ScriptureImportBook(BaseModel):
    """One book of an import, as the review screen lists it."""

    #: Canonical USFM code, or empty when the book wasn't recognised.
    code: str = ""
    #: The name the verse store will use (English, when recognised).
    name: str
    #: The name the file gave it ("Genezis").
    source_name: str
    #: How it was recognised: code, number, name, shape — or "" if it wasn't.
    matched_by: str = ""
    chapters: int
    verses: int
    #: "ot", "nt", "dc", or "" when unrecognised.
    section: str = ""


class ScriptureImportReport(BaseModel):
    """What a Bible file holds, shown for review before anything is installed."""

    format: str
    #: Suggestions read from the file, for the operator to accept or edit.
    title: str = ""
    abbreviation: str = ""
    rights: str = ""
    language: str = ""
    verse_count: int
    books: list[ScriptureImportBook]
    #: Books of the 66 absent from a testament the file does have.
    missing: list[str] = []
    unmatched: list[str] = []
    deuterocanon: list[str] = []
    #: "Psalms: 1,200 of 2,461 verses" — books far shorter than expected.
    incomplete: list[str] = []
    merged_verses: int = 0
    empty_verses: int = 0
    warnings: list[str] = []


class PreviewScriptureImportRequest(_Base):
    """Read a Bible file and report on it. Installs nothing.

    The file travels as base64 over the local socket (a Bible is 3-30 MB —
    hence the raised message limit in `server.serve`). The sidecar keeps the
    parsed result for a short while under `token`, so confirming the import
    doesn't send the file a second time.
    """

    kind: Literal["preview_scripture_import"] = "preview_scripture_import"
    file_name: str = ""
    data_base64: str


class PreviewScriptureImportResponse(_Base):
    kind: Literal["preview_scripture_import_response"] = "preview_scripture_import_response"
    token: str = ""
    report: ScriptureImportReport | None = None
    #: Why the file couldn't be read. User-facing.
    error: str | None = None


class CommitScriptureImportRequest(_Base):
    kind: Literal["commit_scripture_import"] = "commit_scripture_import"
    token: str
    name: str
    abbreviation: str
    #: The credit line shown with every passage. Required: a licensed
    #: translation's terms nearly always demand one.
    attribution: str
    #: The operator confirmed the church has the right to use this text.
    #: Refused without it — the import screen can't be skipped past.
    rights_confirmed: bool = False


class CommitScriptureImportResponse(_Base):
    kind: Literal["commit_scripture_import_response"] = "commit_scripture_import_response"
    #: The new translation's id, on success.
    translation: str | None = None
    error: str | None = None


class ChordSuggestLine(BaseModel):
    """One lyric line, as the chord model needs to see it.

    `line_id` is chosen by the editor (`s0-l2`) so two copies of the same
    lyric can be told apart. `role` is `target` when the line still has a
    word without a chord.

    Words are counted the same way on both sides (`chords/words.py` and
    `lineWords.ts`): whitespace-separated, and only tokens with a letter or
    digit. `chorded_word_indexes` is which of those carry a chord, parallel
    to `chorded_words` and `chords`, so "Holy, holy, holy" keeps its three
    words apart.
    """

    line_id: str
    section: str
    line: str
    chords: list[str] = []
    chorded_words: list[str] = []
    chorded_word_indexes: list[int] = []
    role: Literal["charted", "target"] = "target"


class ChordPlacementInfo(BaseModel):
    """A chord the gate has already anchored to a real word on a target line.

    `word_index` is the word's place on the line (see `ChordSuggestLine`);
    `word` is its text, kept as a cross-check."""

    line_id: str
    word: str
    word_index: int = -1
    chord: str


class SuggestChordsRequest(_Base):
    kind: Literal["suggest_chords"] = "suggest_chords"
    song_key: str = ""
    lines: list[ChordSuggestLine] = []


class SuggestChordsResponse(_Base):
    kind: Literal["suggest_chords_response"] = "suggest_chords_response"
    placements: list[ChordPlacementInfo] = []
    #: True when every placement is one verified phrase and Tab should take them together.
    phrase: bool = False
    #: Set when the request could not be made. An empty `placements` with no
    #: error means the model looked and declined to guess.
    error: str | None = None


class VoiceChartHold(BaseModel):
    """Extra bars of one chord after a lyric line ("hold the 5 for 3 bars")."""

    line_id: str
    chord: str
    bars: int = 1


class VoiceChartTurn(BaseModel):
    """One earlier take in this editing session.

    The next recording is a revision of these, not a fresh chart. `applied`
    is the short list of chords that take actually wrote."""

    transcript: str = ""
    applied: str = ""


class VoiceChartRequest(_Base):
    """A spoken chord instruction for the song open in the editor.

    `audio_base64` is one short microphone clip (webm or wav). The sidecar
    transcribes it, then asks the chord model to place those chords on
    `lines`. `instruction` skips transcription — used when the words are
    already known. `history` is the earlier takes from this same open song,
    newest last, so a correction can revise them. `slide_edits` lets the
    instruction rename, split, merge, move or rewrite slides as well; off,
    it only charts."""

    kind: Literal["voice_chart"] = "voice_chart"
    song_key: str = ""
    lines: list[ChordSuggestLine] = []
    audio_base64: str = ""
    audio_mime: str = "audio/webm"
    instruction: str = ""
    history: list[VoiceChartTurn] = []
    #: The speaker stopped the recording. A pause mid-session leaves this false
    #: so an unfinished sentence is not applied yet.
    final: bool = False
    slide_edits: bool = False


class VoiceChartClear(BaseModel):
    """Chords to remove. A blank `word` clears the whole line, including a hold."""

    line_id: str
    word: str = ""
    word_index: int = -1


class VoiceSlideEdit(BaseModel):
    """One structural edit to the song's slides.

    `section` is the spoken name ("verse 3"). `slide` is a single card index
    from the line ids (`s2` is slide 2); -1 means every slide in the section.
    `rename` uses `section_type` and `section_number`. `split` breaks a slide
    after `after_line`. `move` places the section `before` or `after` `anchor`.
    `lyrics` replaces one slide's words."""

    op: Literal["rename", "split", "merge", "delete", "duplicate", "move", "lyrics"]
    section: str = ""
    slide: int = -1
    section_type: str = ""
    section_number: int = 0
    after_line: int = -1
    anchor: str = ""
    place: Literal["before", "after"] = "before"
    lyrics: str = ""


class VoiceChartResponse(_Base):
    kind: Literal["voice_chart_response"] = "voice_chart_response"
    transcript: str = ""
    placements: list[ChordPlacementInfo] = []
    holds: list[VoiceChartHold] = []
    clears: list[VoiceChartClear] = []
    slide_edits: list[VoiceSlideEdit] = []
    #: False when a pause arrived before the sentence finished. The editor
    #: keeps the audio and waits for the rest.
    complete: bool = True
    #: Set when the clip could not be heard or the model could not be asked.
    #: Empty placements and clears with no error means the words didn't land.
    error: str | None = None


# --- Hymns from the Open Hymnal -------------------------------------------------
#
# Searched and fetched on demand, at the church's request; nothing from the
# Open Hymnal ships in the app. See `hymns/openhymnal.py` and `hymns/rights.py`.

HymnStatus = Literal["public_domain", "public_domain_us", "free_for_worship", "check"]


class HymnSummary(BaseModel):
    """One search result. Its status comes from the site's indexes; the
    hymn's own file (fetched by `GetHymnRequest`) has the final word."""

    hymn_id: str
    title: str
    also_known_as: list[str] = []
    author: str = ""
    tune: str = ""
    first_line: str = ""
    status: HymnStatus = "check"
    status_label: str = ""
    explanation: str = ""


class HymnDetail(BaseModel):
    """A hymn converted to song text, ready to add to the library."""

    hymn_id: str
    title: str
    body_text: str
    slide_order: str = ""
    author: str = ""
    #: The notice that travels with the song. For "free for worship" hymns
    #: the writer's terms require it on every copy.
    copyright: str = ""
    status: HymnStatus = "check"
    status_label: str = ""
    explanation: str = ""
    source_url: str = ""


class SearchHymnsRequest(_Base):
    kind: Literal["search_hymns"] = "search_hymns"
    #: Empty lists every hymn, alphabetically.
    query: str = ""
    #: Fetch the Open Hymnal's index again even if the cached copy is recent.
    refresh: bool = False


class SearchHymnsResponse(_Base):
    kind: Literal["search_hymns_response"] = "search_hymns_response"
    results: list[HymnSummary] = []
    #: Unix seconds the index was fetched from the Open Hymnal.
    fetched_at: float | None = None
    error: str | None = None


class GetHymnRequest(_Base):
    kind: Literal["get_hymn"] = "get_hymn"
    hymn_id: str


class GetHymnResponse(_Base):
    kind: Literal["get_hymn_response"] = "get_hymn_response"
    hymn: HymnDetail | None = None
    error: str | None = None


# --- Library search -------------------------------------------------------------


class SemanticSearchHit(_Base):
    song_id: str
    #: Similarity, roughly 0.7-0.9 for this model. Only the order means
    #: anything; the UI merges by rank, never by this number.
    score: float
    #: The passage (blank-line block, lines joined with " / ") that matched
    #: best: shown under the song as why it came up.
    passage: str


class SemanticSearchRequest(_Base):
    """Rank the library's songs by meaning against `texts` (averaged).

    The UI parses the query first (`src/lib/smartSearch`): filters like a key
    or "not played lately" never come here, and an occasion arrives as its
    lyric-like phrases ("He is risen from the grave", ...)."""

    kind: Literal["semantic_search"] = "semantic_search"
    texts: list[str]
    limit: int = 50


class SemanticSearchResponse(_Base):
    kind: Literal["semantic_search_response"] = "semantic_search_response"
    #: `indexing`: the library's first index is being built (seconds); ask
    #: again. `unavailable`: no model in this engine; use keywords only.
    status: Literal["ready", "indexing", "unavailable"]
    hits: list[SemanticSearchHit] = []


# --- Lead sheets ----------------------------------------------------------------


class ParseLeadSheetRequest(_Base):
    """Is this PDF a lead sheet, and if so what does it say?

    The importer sends every PDF here before treating it as a chord chart:
    a notation PDF becomes a reference on its song, anything else carries on
    to the chart reader."""

    kind: Literal["parse_lead_sheet"] = "parse_lead_sheet"
    pdf_base64: str
    file_name: str = ""


class ParseLeadSheetResponse(_Base):
    kind: Literal["parse_lead_sheet_response"] = "parse_lead_sheet_response"
    is_lead_sheet: bool
    #: Why it is not one (or why it could not be read), for the import log.
    reason: str = ""
    title: str | None = None
    ccli: str | None = None
    key: str | None = None
    copyright: str | None = None
    bar_count: int = 0
    #: Bars whose notes do not add up to the metre. Kept, not rejected: a
    #: sheet with a few unreadable bars still places the rest.
    problem_bars: int = 0
    sections: list[str] = []
    reader_version: int = 0
    #: The parsed score (melody, chords, lyrics, roadmap), stored verbatim.
    score_json: str = ""


class InspectLookbackRequest(_Base):
    """Run the learning look-back over a stored recording (Model inspector)."""

    kind: Literal["inspect_lookback"] = "inspect_lookback"
    recording_id: RecordingId
    #: Where the words come from: "live" (what the session heard), "asr"
    #: (streamed Whisper, cached after the first run), "cached" (whole-file
    #: Whisper), "golden" (a perfect transcript: the ceiling), "auto".
    source: Literal["auto", "live", "asr", "cached", "golden"] = "auto"


class SlideProb(BaseModel):
    slide: int
    p: float


class LookbackWordView(BaseModel):
    t: float
    text: str
    #: Where the look-back placed the word: its slide and word index there.
    slide: int
    pos: int
    #: Posterior of that slide for this word, and the top three.
    p: float
    top: list[SlideProb]


class LookbackPassView(BaseModel):
    slide: int
    t_start: float
    t_end: float
    confidence: float
    n_words: int
    #: The golden's slide over most of the pass, and whether the golden showed
    #: the pass's slide at all during it (identical-text slides count as one).
    golden_slide: int | None
    agrees: bool | None


class LookbackItemView(BaseModel):
    item_idx: int
    title: str
    heard_words: int
    lookback_ms: float
    passes: list[LookbackPassView]
    words: list[LookbackWordView]
    #: What a live session would store for this performance.
    led_by: str
    weight: float
    agreement: float | None
    mean_confidence: float
    #: Confident line changes (repeats collapsed) against the golden's.
    lookback_sequence: list[int]
    golden_sequence: list[int]
    precision: float | None
    recall: float | None
    median_offset_s: float | None


class InspectLookbackResponse(_Base):
    kind: Literal["inspect_lookback_response"] = "inspect_lookback_response"
    source: str = ""
    sources_available: list[str] = Field(default_factory=list)
    #: What the look-back is judged against: "approved" (a human signed the
    #: golden off), "estimated" (a golden nobody has approved yet) or "shown"
    #: (no golden: what the screen showed live, the AI's choices perhaps).
    golden_kind: str = "shown"
    items: list[LookbackItemView] = Field(default_factory=list)
    error: str | None = None


# --- Learning (debug panel) ----------------------------------------------------


class GetLearningStateRequest(_Base):
    """What the learning observer has heard this session and what past
    services taught about this setlist's songs (`auto_presenter.learning`)."""

    kind: Literal["get_learning_state"] = "get_learning_state"


class LearnedPassView(BaseModel):
    slide: int
    #: Seconds after the performance's first heard word.
    t_start: float
    confidence: float


class OperatorMoveView(BaseModel):
    t: float
    slide: int
    source: str


class PerformanceView(BaseModel):
    id: str
    started_at_ms: int
    provenance: str
    led_by: str
    weight: float
    agreement: float | None
    mean_confidence: float | None
    heard_words: int
    final: bool
    passes: list[LearnedPassView]
    operator: list[OperatorMoveView]


class TransitionEvidenceView(BaseModel):
    """One service's line change from this line (`profile.TransitionEvidence`)."""

    to_slide: int
    performance_id: str
    started_at_ms: int
    led_by: str
    #: What it counts for, after decay.
    weight: float
    #: Seconds into that performance.
    t: float
    conf_from: float
    conf_to: float
    operator_agrees: bool | None


class LearnedTransitionView(BaseModel):
    """From one line: where library order goes, and where services went."""

    from_slide: int
    library_next: int | None
    learned_next: int | None
    #: Weighted times services went `learned_next` from here, and the share
    #: of the order prior the learned successors now take.
    count: float
    share: float
    #: Every next line services went to, with its weighted count (`p`).
    successors: list[SlideProb]
    evidence: list[TransitionEvidenceView]
    #: The follower's next-line probabilities from this line: as it runs
    #: (with what was learned), and with the learning taken out.
    prior: list[SlideProb]
    base_prior: list[SlideProb]


class LearnedSongView(BaseModel):
    item_idx: int
    song_id: str
    title: str
    #: The follower's reference this session ("lead_sheet+learned", ...), or
    #: None when the matcher follows the song.
    follower_source: str | None
    #: This session's performance so far (look-back), when one exists.
    live: PerformanceView | None
    live_heard_words: int
    lookback_ms: float
    performances: list[PerformanceView]
    used: int
    n_eff: float
    seconds_per_word: float | None
    timed_lines: int
    order: list[LearnedTransitionView]
    #: Each slide's words, joined (to say which line a number means).
    slide_text: list[str]


class GetLearningStateResponse(_Base):
    kind: Literal["get_learning_state_response"] = "get_learning_state_response"
    collecting: bool
    provenance: str | None
    audio_now: float
    passes_run: int
    store: str | None
    songs: list[LearnedSongView]


class DeletePerformanceRequest(_Base):
    """Forget one past performance (a wedding medley, a rehearsal)."""

    kind: Literal["delete_performance"] = "delete_performance"
    performance_id: str


# --- Discriminated unions ------------------------------------------------------

ClientMessage = Annotated[
    Union[
        PingMessage,
        GetLearningStateRequest,
        DeletePerformanceRequest,
        InspectLookbackRequest,
        ListAudioDevicesRequest,
        StartAudioSessionRequest,
        StopAudioSessionRequest,
        RecordSlideChangeNotification,
        LiveScriptureNotification,
        ListRecordingsRequest,
        DeleteRecordingRequest,
        ExportRecordingRequest,
        LoadRecordingRequest,
        LoadRecordingAudioRequest,
        ReplayRecordingRequest,
        SaveGoldenRequest,
        InspectMusicRequest,
        RecommendModelRequest,
        AssessMachineRequest,
        ModelStatusRequest,
        PrepareModelRequest,
        SetAIModeRequest,
        AssistDismissRequest,
        SetBlankWhenIdleRequest,
        SetMusicAnalysisRequest,
        SetSpokenInstructionsRequest,
        SetTunablesRequest,
        UpdateSetlistContentRequest,
        ListInstructionModelsRequest,
        SetInstructionModelRequest,
        DownloadInstructionModelRequest,
        SetInstructionModelApiKeyRequest,
        ResolvePassagesRequest,
        FindScriptureReferencesRequest,
        PassageContextsRequest,
        SearchScriptureRequest,
        ListScriptureTranslationsRequest,
        InstallScriptureTranslationRequest,
        RemoveScriptureTranslationRequest,
        SearchScriptureCatalogRequest,
        PreviewScriptureImportRequest,
        CommitScriptureImportRequest,
        SuggestChordsRequest,
        VoiceChartRequest,
        SearchHymnsRequest,
        GetHymnRequest,
        SemanticSearchRequest,
        ParseLeadSheetRequest,
    ],
    Field(discriminator="kind"),
]

ServerMessage = Annotated[
    Union[
        PongMessage,
        GetLearningStateResponse,
        InspectLookbackResponse,
        ErrorMessage,
        SlideChangeMessage,
        TranscriptPartialMessage,
        MusicAnalysisMessage,
        SingingStateMessage,
        SpokenInstructionMessage,
        ImportLibrarySongMessage,
        ShowScriptureMessage,
        LibrarySearchMessage,
        BeliefStateMessage,
        EngineTelemetryMessage,
        FlowPositionMessage,
        ListAudioDevicesResponse,
        AudioSessionStateMessage,
        AudioLevelMessage,
        PipelineHealthMessage,
        AIStatusEvent,
        AssistSuggestionMessage,
        AssistClearMessage,
        TunablesUpdatedMessage,
        SetlistContentUpdatedMessage,
        ModelDownloadProgressMessage,
        RecommendModelResponse,
        AssessMachineResponse,
        ModelStatusResponse,
        PrepareModelResponse,
        ListRecordingsResponse,
        DeleteRecordingResponse,
        ExportRecordingResponse,
        LoadRecordingResponse,
        LoadRecordingAudioResponse,
        ReplayRecordingResponse,
        SaveGoldenResponse,
        InspectMusicResponse,
        RecordingFinalizedMessage,
        ListInstructionModelsResponse,
        InstructionModelDownloadProgressMessage,
        ResolvePassagesResponse,
        FindScriptureReferencesResponse,
        PassageContextsResponse,
        SearchScriptureResponse,
        ListScriptureTranslationsResponse,
        InstallScriptureTranslationResponse,
        RemoveScriptureTranslationResponse,
        SearchScriptureCatalogResponse,
        PreviewScriptureImportResponse,
        CommitScriptureImportResponse,
        SuggestChordsResponse,
        VoiceChartResponse,
        SearchHymnsResponse,
        GetHymnResponse,
        SemanticSearchResponse,
        ParseLeadSheetResponse,
    ],
    Field(discriminator="kind"),
]


def _python_version_str() -> str:
    v = sys.version_info
    return f"{v.major}.{v.minor}.{v.micro}"


def _pid() -> int:
    import os

    return os.getpid()
