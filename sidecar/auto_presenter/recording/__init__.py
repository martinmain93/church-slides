"""Live recording — captures audio frames + ground-truth slide changes
to disk during a real service. Phase 5 reads these back via the eval
harness to compute baseline metrics.

A "recording" on disk is a directory:

    <app_data>/recordings/<recording_id>/
      audio.wav          — 16 kHz mono PCM, written incrementally
      recording.json     — Recording metadata + ground-truth timeline

The Recording schema lives in `auto_presenter.eval.types` (Phase 3 ships
the data layer; Phase 4 ships the capture path).
"""
