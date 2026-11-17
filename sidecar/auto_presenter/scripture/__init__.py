"""Scripture: Bibles to install or import, and references that resolve.

Wholly self-contained and entirely optional. Nothing in the matcher, the
tracker, the scheduler or the audio pipeline imports this package, and nothing
here imports them — the only wiring is `server_scripture`'s handlers, which do
nothing until asked. Turning the feature off is therefore not a code path, it
is the absence of one.

Two pieces, in dependency order:

    references   parse "Psalm 103:1-5"; refuse anything else
    store        one translation per SQLite file; lookup + search

(Passages attached to songs, with their line anchors and model-suggested
verses, were removed on 2026-10-19: research/dead_ends/song_scripture.)

`docs/done/BIBLE_INTEGRATION.md` is the design and the measurements behind it.
"""

from auto_presenter.scripture.references import (
    Reference,
    ReferenceError,
    parse,
    split_compound,
)
from auto_presenter.scripture.store import (
    DEFAULT_TRANSLATION,
    TRANSLATIONS,
    Passage,
    TranslationSpec,
    Verse,
    VerseStore,
    build,
    db_path,
    ensure_translation,
    installed_translations,
    match_translation,
)

__all__ = [
    "DEFAULT_TRANSLATION",
    "TRANSLATIONS",
    "Passage",
    "Reference",
    "ReferenceError",
    "TranslationSpec",
    "Verse",
    "VerseStore",
    "build",
    "db_path",
    "ensure_translation",
    "installed_translations",
    "match_translation",
    "parse",
    "split_compound",
]
