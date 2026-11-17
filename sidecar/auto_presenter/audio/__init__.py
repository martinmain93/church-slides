"""Audio capture + processing for the AI engine.

The data types and the capture interface land in Phase 4 Slice 1A so the
eval harness can replay recorded WAVs and the ASR backend has a concrete
input shape to consume. Live microphone capture, vocal separation, and
streaming Whisper land in subsequent slices (and Phase 9).
"""
