"""Engine modules that exist for development and stay out of release builds.

`sidecar.spec` bundles every `auto_presenter` module except these, and lists
them as excludes so nothing can pull one in by importing it. They are the
developer tools behind the Model Inspector and the Golden Recording Editor
(which the app shows only in dev builds) and the eval library the
`research/` harnesses import.

A shipped module may reach one only through a function-level import in a
module named in `LAZY_IMPORTERS`, on a path a release build never takes;
`tests/test_dev_only_boundary.py` holds both rules.
"""

DEV_ONLY_MODULES: tuple[str, ...] = (
    # The Model Inspector's music layers: beats and stems run in hand-made
    # venvs through repo scripts, and pitch is Basic Pitch, whose model the
    # release no longer carries.
    "auto_presenter.music.inspect",
    "auto_presenter.music.layers",
    "auto_presenter.music.rhythm",
    "auto_presenter.music.realtime_stems",
    "auto_presenter.music.beat_this",
    "auto_presenter.music.basic_pitch",
    # The Model Inspector's look-back layer and tracker replay.
    "auto_presenter.learning.inspect",
    "auto_presenter.eval.trace",
    # Eval library used only by research/ harnesses.
    "auto_presenter.eval.compare",
    "auto_presenter.eval.shadow",
    "auto_presenter.eval.visible",
    "auto_presenter.eval.sung_timing",
    # The agent-testing MCP server, and the client it reaches the engine with:
    # nothing launches it from a frozen engine.
    "auto_presenter.dev.mcp",
    "auto_presenter.dev.client",
)

#: Shipped modules allowed a function-level import of a dev-only module, and
#: why release never runs it.
LAZY_IMPORTERS: dict[str, str] = {
    "auto_presenter.server_recordings": (
        "the Model Inspector's requests (inspect_music, replay_recording, "
        "inspect_lookback); the app sends them only from dev builds"
    ),
    "auto_presenter.server_follower": (
        "in-service learning needs Beat This; its tunable is off and hidden, "
        "and the import is guarded, so a release build logs and carries on"
    ),
}
