# Inputs: a talkback channel, a talk button, and audio from other devices

**Status:** ideas for discussion, not designed or built. Order TBD.
**Date:** 2026-11-19

Today the engine hears one input device, mixed down to mono
(`sidecar/auto_presenter/audio/capture.py`). Everything it acts on, sung
words and spoken commands alike, comes through that one mix.

These ideas give each input one fixed job:

| Role | What it's for | Where it can come from |
| --- | --- | --- |
| **Music** | Following the song (today's input) | This computer, or a joined device |
| **Talkback** | Commands only, never lyric matching | A channel on this computer, or a phone's talk button |

**Keep the setup simple.** Settings get one Inputs list showing what's
connected, each input with a role and a sensible default. No routing
matrix and no per-channel tuning.

## 1. Music director talkback channel

In many churches the music director (MD) gives cues on a separate
talkback mic that the room doesn't hear: "go to the bridge", "do that verse
again", "two more times". If one input channel is marked as talkback,
everything on it goes straight to the commands agent and skips the lyric
matcher.

**Why it's worth doing:**

- **Spoken commands through the main mix barely work today.** The
  acoustic wake gate can't pass a short command: 4 words top out at 0.74
  against a 0.8 bar, and removing the speaking-rate term made it worse
  ([diagnosis](../done/SPOKEN_INSTRUCTIONS.md)). On a talkback channel,
  anything spoken is a likely command, so there's no gate to clear.
- **The volume is low enough to send everything to the model.** Sending all
  sung audio to the decision model can't keep up (measured: events arrive
  faster than the model answers). Talkback only carries speech when the MD
  talks.
- **Commands stay out of the lyrics.** "Go to the bridge" never reaches the
  lyric matcher.
- **MD cues give early warning.** "Two more times", "chorus again" and
  "watch me on the outro" are what a human operator listens for. They tell
  us what comes next before anyone sings it, and lateness is most of the
  remaining error. The commands agent should be able to answer "expect
  this next" as well as "jump now".

**What it needs:**

- Choosing a channel on a multi-channel interface (for example "input 3 is
  talkback"), or a second input device. Capture mixes everything down to
  mono today.
- On the talkback channel, speech detection runs first. Speech recognition
  runs only while someone is talking, so it's cheap.
- The commands agent must ignore band chatter that isn't for the slides:
  counting in ("1, 2, 3, 4"), "watch me", tuning talk. The hosted model
  handles this well. The offline fallback (qwen2.5:3b) scored only 20/45
  on the current decision bench, so offline talkback needs a better local
  model or a stricter prompt.
- **Setting:** "MD talkback: none / input N", off by default.

**Open questions:** how much stage noise and singing the talkback mic picks
up in a real room. Whether "expect next" cues should pre-arm the on-rails
anticipation or only bias the follower.

## 2. Talk button on a phone

A talk button on the leader or band page. The MD presses it and speaks into
their phone. Pressing it shows they mean it, so the wake-gate question
doesn't arise. For churches without a talkback mic, it's idea 1 with no
audio-desk setup.

**What it needs:**

- It reuses the band page and its device pairing (invite QR, network
  socket, online relay). The phone records only while the button is held
  and sends the clip to the slide computer, which treats it as talkback.
- Clips are short and only sent on purpose, so it works over the online
  relay as well as the local network.
- If the phone drops off or a clip is lost, nothing happens. The service
  never stops.

**Open questions:** whether to transcribe on the phone or on the slide
computer. Whether to show what was heard ("Heard: go to the bridge") on the
button page so the MD knows it landed.

## 3. Audio from another device as the music input

A phone or iPad near the stage, or one plugged into the sound desk, streams
its audio to the slide computer and becomes the music input. This helps when
the slide computer sits at the back of the room and hears the band poorly.

**What it needs:**

- The band page captures the mic or a plugged-in input and streams it to the
  slide computer. Prefer the local network, since sending continuous audio
  through the online relay costs bandwidth.
- Wi-Fi jitter and clock drift between the two devices: the engine needs a
  jitter buffer, and has to treat the stream's timestamps carefully, since
  timing feeds the follower.
- If the stream drops, fall back silently to the local mic. The service
  never stops.
- Audio leaves the device it was recorded on, so `site/privacy.html` needs
  updating in the same change.

**Open questions:** whether a phone in the room actually hears better than
the slide computer's own mic. Measure on a real service before building
the full version. Battery and screen-lock behaviour on iOS when the band
page runs in the background.

## Considered and dropped: MIDI keyboard chords

The idea: take MIDI from the keyboard player, on the slide computer or
through an iPad, for instant, near-perfect chords. Dropped on 2026-11-19:

- Chords barely help pick the right slide. Even perfect chords added only
  2–3% in the chord spike.
- The existing audio chord detector (BTC) is already ~70% right online, at
  ~0.75 s lag.
- MIDI brings real complexity: voicings, inversions, pads, the sustain
  pedal, riffs, device pairing, and no Web MIDI in the app's WebView or in
  iOS Safari.

What would reopen it: evidence that chords give position within a slide or
time-to-end sooner than words. That hasn't been measured. Test it first with
perfect chords taken from the charts.
