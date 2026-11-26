"""The catalog of models spoken instructions can run on.

Deprecated (2026-11-23): Groq's free tier can't carry a church's use and
paid tiers aren't available, so Groq integrations are on their way out.
The Groq entries stay until spoken requests have another home.

Two kinds, both behind the same `GenericLLMBackend` (see
`decision.instructions`) now that ollama, Groq, and Gemini's
OpenAI-compatibility layer all speak the same `/chat/completions` shape:

- **local** — downloaded once via ollama, runs on-device, no network at
  service time. The three measured 2026-09-03 (`docs/done/SPOKEN_INSTRUCTIONS.md`):
  a 3B/3B/7B spread trading latency for accuracy.
- **hosted** — a provider's API, needs a key, needs a network. Now the
  shipped default, *paired with a local fallback* — see `DEFAULT_MODEL_ID`
  below for the measurement and for why the standing objection is answered
  rather than ignored.

Adding a model means adding one entry here — nothing else needs to change
to support it, local or hosted, as long as it speaks OpenAI-style tool
calling.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class InstructionModelSpec:
    """One catalog entry."""

    id: str
    label: str
    kind: Literal["local", "hosted"]
    #: The exact model string sent to the endpoint. Usually equal to `id`
    #: for local models; hosted providers sometimes use a different name
    #: (Groq, Gemini) than the catalog's own id.
    model_name: str
    #: ollama's pull name, local only. Usually the same as `model_name`.
    pull_name: str = ""
    #: Approximate download size, local only — sizes the progress bar
    #: before the first byte arrives; see `ModelManager`'s Whisper
    #: equivalent for the same reasoning.
    size_mb: int = 0
    #: Hosted only: which provider's API this hits, and the base URL of
    #: its OpenAI-compatible endpoint.
    provider: str = ""
    base_url: str = ""
    #: One line shown in the picker — what you get and what it costs.
    note: str = ""


CATALOG: tuple[InstructionModelSpec, ...] = (
    InstructionModelSpec(
        id="llama3.2:3b",
        label="Llama 3.2 (3B)",
        kind="local",
        model_name="llama3.2:3b",
        pull_name="llama3.2:3b",
        size_mb=2000,
        note="Fastest measured (~0.6-1.2s/call). 7/10 on the synthetic command set.",
    ),
    InstructionModelSpec(
        id="qwen2.5:3b-instruct",
        label="Qwen 2.5 Instruct (3B)",
        kind="local",
        model_name="qwen2.5:3b-instruct",
        pull_name="qwen2.5:3b-instruct",
        size_mb=1900,
        note="Shipped default. Same size and speed as Llama 3.2 3B, 8/10 on the "
             "synthetic command set — the more accurate choice at this size.",
    ),
    InstructionModelSpec(
        id="qwen2.5:7b-instruct",
        label="Qwen 2.5 Instruct (7B)",
        kind="local",
        model_name="qwen2.5:7b-instruct",
        pull_name="qwen2.5:7b-instruct",
        size_mb=4700,
        note="Most accurate measured (9/10), ~1.7-3.3s/call, ~4.5GB RAM while loaded. "
             "Worth it if the machine can carry it.",
    ),
    InstructionModelSpec(
        # `llama-3.1-8b-instant` (this entry's id, kept for stability) was
        # retired from Groq's lineup between when this catalog was written
        # and when a key first became available to test it — the actual
        # `model_name` below points at its replacement. Model availability
        # on hosted providers moves under you; check `GET /v1/models` with
        # the live key before assuming a hardcoded name still resolves.
        id="groq-llama-3.1-8b-instant",
        label="GPT-OSS 20B (Groq)",
        kind="hosted",
        model_name="openai/gpt-oss-20b",
        provider="groq",
        base_url="https://api.groq.com/openai/v1",
        # Measured 2026-09-03 against a real key: 9/10 on the synthetic
        # command set and 10/10 correctly silent (the only model tested
        # with a *complete*, non-rate-limited sample on both). Also the
        # fastest by far — 0.2-0.7s/call, Groq's whole selling point.
        note="Free tier: 30 req/min, 500K tokens/day. Fastest and most reliable "
             "measured (9/10 commands, 10/10 silent, 0.2-0.7s/call) — the best "
             "result of any model tried, local or hosted. Needs a network "
             "connection at service time and a Groq API key.",
    ),
    InstructionModelSpec(
        id="gemini-flash",
        label="Gemini Flash (Google)",
        kind="hosted",
        model_name="gemini-3.5-flash",
        provider="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        # Measured 2026-09-03 against a real key: 9/10 on the synthetic
        # command set (tied for best of everything measured, local or
        # hosted) and 5/5 correctly silent on the non-command calls that
        # completed. The free tier's real throughput is tighter than its
        # documented 15 req/min — half the non-command calls hit 429s even
        # paced at one request per 4.5s; needs a bigger safety margin than
        # the docs suggest. See `docs/done/SPOKEN_INSTRUCTIONS.md`.
        note="Free tier: 15 req/min, 1M tokens/day (measured: leave more headroom "
             "than that — 429s showed up well under the documented limit). Needs a "
             "network connection at service time and a Gemini API key. The most "
             "accurate model measured so far, local or hosted.",
    ),
)


def find_model(model_id: str) -> InstructionModelSpec | None:
    return next((m for m in CATALOG if m.id == model_id), None)


#: What runs if nothing has been configured yet.
#: The shipped default: hosted, with `FALLBACK_MODEL_ID` behind it.
#:
#: This catalog previously refused to default to a hosted model, on the
#: grounds that a worship service is exactly where the wifi fails and the
#: failure is public. That reasoning is sound and it argues against
#: *depending* on a network, not against using one — so the default changed
#: only once there was a fallback. Losing the connection now costs accuracy,
#: not the feature. See `decision.instructions.FallbackBackend`.
#:
#: Measured 2026-09-17 over 15 utterances drawn from a real recording,
#: failures counted from log records rather than inferred from a `None`
#: return:
#:
#:     llama3.2:3b           11.0/15    ~2 s / call
#:     qwen2.5:3b-instruct   13.0/15    ~2 s / call
#:     qwen2.5:7b-instruct   14.0/15    1.8-2.8 s / call
#:     openai/gpt-oss-20b    15.0/15    0.5 s / call   <- this
#:
#: Better *and* faster, because the cost is a network round trip rather than
#: local compute. It is also the only model of any size that got right a
#: request whose opening word the ASR had mis-heard ("When you go to the
#: bridge of <title>") — every local model read that as the current song's
#: bridge and dropped the title.
#:
#: Gemini stays unmeasured: 8 of 15 calls returned 503 or timed out, which is
#: infrastructure rather than capability.
DEFAULT_MODEL_ID = "groq-llama-3.1-8b-instant"

#: What runs when the hosted call fails, or when no key was ever entered.
#:
#: The smallest of the measured local models rather than the most accurate
#: (7B scored 14/15 against this one's 13/15) because a fallback is only
#: reached when the network already has, and 4.7 GB of weights is a poor
#: thing to require for a path most users will never hit. `_build_local_fallback`
#: checks it is actually installed before offering it.
FALLBACK_MODEL_ID = "qwen2.5:3b-instruct"
