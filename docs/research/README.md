# Research

Spikes, literature reviews and dead ends: ideas that were measured and
either parked or rejected. They're kept so that nobody spends a week
rediscovering the same answer. Read the relevant one before proposing an
engine change in its area.

The scripts behind these live in [research/](../../research/) at the repo
root. Some point to `research/dead_ends/`, which wasn't carried into the
public repo.

| Document | Question | Outcome |
| --- | --- | --- |
| [LITERATURE](LITERATURE.md) | What does the field do for following sung lyrics live? | Review; frames the constraints |
| [ASR_ENGINE](ASR_ENGINE.md) | Should a phoneme recogniser replace Whisper? | Keep Whisper |
| [AUDIO_EMISSION](AUDIO_EMISSION.md) | Score candidate slides against the audio directly | Worked, but 2.2x worse |
| [ENCODER_ALIGNMENT](ENCODER_ALIGNMENT.md) | Align Whisper's encoder output to slide text | Scoped, not built: the real problem was the tracker under-committing |
| [LIVE_LATENCY](LIVE_LATENCY.md) | Why does the interface feel laggy? | The delay is speech recognition, not the interface |
| [FAST_CHANNEL](FAST_CHANNEL.md) | A fast streaming recogniser beside Whisper | Fast, but trades accuracy; parked |
| [VOCAL_SEPARATION](VOCAL_SEPARATION.md) | Separate the vocals before recognition (Demucs) | Better words, triple the delay; shelved |
| [MUSICAL_PRIORS](MUSICAL_PRIORS.md) | The original plan for musical signals | Superseded by the spikes below |
| [MUSIC_FINGERPRINTING](MUSIC_FINGERPRINTING.md) | Harmony as a second detection stream | Can't tell neighbouring sections apart (53%); reframed as a speed signal |
| [MUSIC_LITERATURE](MUSIC_LITERATURE.md) | Musical feature literature, filtered by our constraints | Review |
| [MUSIC_MODELS_2026](MUSIC_MODELS_2026.md) | Which beat, downbeat and chord models to use | Review; BTC chosen |
| [CHORD_DOWNBEAT](CHORD_DOWNBEAT.md) | Chord charts as a position signal | +2–3% at best; chords shipped as a diagnostic, later used by the follower |
| [MUSIC_ONLY_MATCHING](MUSIC_ONLY_MATCHING.md) | Find the song and place from music alone | Works offline; led to the score follower |
