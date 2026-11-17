"""Stand-in for PyAV (`av`) in the frozen engine.

PyAV's wheels bundle an FFmpeg built with `--enable-gpl` (libx264, libx265;
every libav* library reports "GPL version 3 or later"), which a release must
not ship. Nothing in the engine needs it: faster-whisper imports `av` at module
level, but only `faster_whisper.audio.decode_audio` uses it, to decode audio
*files*. The engine always hands Whisper 16 kHz float32 numpy arrays, which
skip decoding entirely.

So `import av` succeeds, and anything that reaches for PyAV fails at once with
a message that says why. sidecar.spec puts this directory first on the bundle's
import path, and packaging/hooks/hook-av.py stops PyInstaller's stock hook from
collecting the real package's libraries.
"""

IS_STUB = True

MESSAGE = (
    "PyAV/FFmpeg is not bundled with Church Slides (its FFmpeg build is "
    "GPL-licensed), so this engine cannot decode audio files. Pass Whisper "
    "a 16 kHz float32 numpy array instead of a path or file object."
)


class AudioFileDecodingUnavailableError(AttributeError):
    """Raised for any use of the stub. An AttributeError subclass so that
    `hasattr(av, ...)` probes answer False instead of blowing up."""


def open(*_args, **_kwargs):
    raise AudioFileDecodingUnavailableError(MESSAGE)


def __getattr__(name: str):
    if name.startswith("__"):
        raise AttributeError(name)
    raise AudioFileDecodingUnavailableError(f"av.{name}: {MESSAGE}")
