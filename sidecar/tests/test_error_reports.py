"""The engine's error reports: the user's choices, and what never leaves.

Reports carry what helps fix a bug — messages, device names, song titles, the
log's tail — but never what someone said (the microphone also hears prayer
requests), nor the user's folder name. Turning "Send error reports" off in
Settings must stop them without restarting the engine.
"""

from __future__ import annotations

import ast
import json
import logging
import re
import sqlite3
import sys
from pathlib import Path

import pytest

from auto_presenter import error_reports as er


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "library.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    monkeypatch.setattr(er, "_db_path", path)
    monkeypatch.setattr(er, "_cached", None)
    monkeypatch.setattr(er, "_last_good", None)
    monkeypatch.setattr(er, "_install_id", "01INSTALL")
    monkeypatch.setattr(er, "_log_file", None)
    monkeypatch.setattr(er, "_sent", 0)
    monkeypatch.setattr(er, "_sent_by_kind", {})
    monkeypatch.setattr(er, "_host_names", ())

    def store(**settings) -> None:
        # Answered the question, unless a test says otherwise.
        settings.setdefault("error_reports_asked", True)
        with sqlite3.connect(path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings VALUES (?, ?)",
                (er.SETTINGS_KEY, json.dumps(settings)),
            )
        monkeypatch.setattr(er, "_cached", None)

    # Most tests are about what a report carries: someone who said yes.
    store(send_error_reports=True)
    return store


def test_off_until_asked_and_then_what_the_user_says(db):
    db()  # a settings row, but not asked
    assert er.reports_allowed() is False
    db(send_error_reports=True, error_reports_asked=False)  # an older version's default
    assert er.reports_allowed() is False
    db(send_error_reports=False)
    assert er.before_send({"message": "boom"}, {}) is None
    db(send_error_reports=True)
    assert er.before_send({"message": "boom"}, {}) is not None


def test_keeps_what_helps_and_removes_what_was_said(db):
    event = {
        "server_name": "Sarahs-MacBook-Air",
        "logentry": {
            "message": "spoken instruction «%s» -> no action",
            "params": ["please pray for Margaret"],
            "formatted": "spoken instruction «please pray for Margaret» -> no action",
        },
        "exception": {
            "values": [
                {
                    "type": "PortAudioError",
                    "value": "couldn't open device 'MacBook Air Microphone'",
                    "stacktrace": {
                        "frames": [
                            {"abs_path": "/Users/sarah/app/x.py", "vars": {"text": "x"}}
                        ]
                    },
                }
            ]
        },
        "breadcrumbs": {"values": [{"message": "library 'Amazing Grace' -> item 2"}]},
    }
    out = er.before_send(event, {})
    assert "server_name" not in out
    assert out["user"] == {"id": "01INSTALL"}
    assert out["logentry"]["formatted"] == "spoken instruction «speech removed» -> no action"
    assert out["logentry"]["params"] == []
    exc = out["exception"]["values"][0]
    # Device names and song titles are what a bug report needs.
    assert exc["value"] == "couldn't open device 'MacBook Air Microphone'"
    assert exc["stacktrace"]["frames"][0]["abs_path"] == "/Users/~/app/x.py"
    assert "vars" not in exc["stacktrace"]["frames"][0]
    assert out["breadcrumbs"]["values"][0]["message"] == "library 'Amazing Grace' -> item 2"


def test_names_the_contact_only_when_given(db):
    db(send_error_reports=True, report_contact="tech@stmarys.example")
    assert er.before_send({"message": "x"}, {})["user"] == {
        "id": "01INSTALL",
        "email": "tech@stmarys.example",
    }
    db(send_error_reports=True, report_contact="St Mary's, Leeds")
    assert er.before_send({"message": "x"}, {})["user"]["username"] == "St Mary's, Leeds"


def test_attaches_the_scrubbed_end_of_the_log(db, tmp_path, monkeypatch):
    log_file = tmp_path / "church-slides.log"
    lines = [f"line {i}" for i in range(300)] + ["spoken instruction «go to the bridge»"]
    log_file.write_text("\n".join(lines))
    monkeypatch.setattr(er, "_log_file", log_file)
    hint: dict = {"attachments": []}
    er.before_send({"message": "x"}, hint)
    (attachment,) = hint["attachments"]
    text = attachment.bytes.decode()
    assert text.splitlines()[0] == "line 101"
    assert text.endswith("«speech removed»")


def test_scrub_matches_the_apps_rules():
    # Same cases as `telemetry.rs`'s tests.
    assert er.scrub("heard «amazing grace» twice") == "heard «speech removed» twice"
    assert er.scrub("couldn't open 'USB Audio'") == "couldn't open 'USB Audio'"
    assert er.scrub("/Users/Shared/x") == "/Users/Shared/x"
    assert er.scrub(r"C:\Users\sarah\AppData") == r"C:\Users\~\AppData"
    assert (
        er.scrub("input: Pastor John’s iPhone Microphone (48000 Hz)")
        == "input: <name>’s iPhone Microphone (48000 Hz)"
    )
    assert er.scrub("Sarah's AirPods Pro") == "<name>'s AirPods Pro"
    assert er.scrub("song: God's Great Dance Floor") == "song: God's Great Dance Floor"
    assert er.scrub("the band's mic") == "the band's mic"


def test_does_nothing_without_a_dsn(monkeypatch):
    monkeypatch.delenv("CHURCH_SLIDES_SENTRY_DSN", raising=False)
    assert er.init(None) is False


def test_drops_raw_log_params_even_without_speech(db):
    # `params` are the unscrubbed values; a path in one carried the user's
    # folder name straight past the scrubber.
    event = {
        "logentry": {
            "message": "couldn't read %s",
            "params": ["/Users/sarah/Music/song.mp3"],
            "formatted": "couldn't read /Users/sarah/Music/song.mp3",
        }
    }
    out = er.before_send(event, {})
    assert out["logentry"]["params"] == []
    assert out["logentry"]["formatted"] == "couldn't read /Users/~/Music/song.mp3"
    assert "sarah" not in json.dumps(out)


def test_scrubs_escaped_windows_paths_and_the_computer_name(monkeypatch):
    assert er.scrub(r"C:\\Users\\sarah\\AppData") == r"C:\\Users\\~\\AppData"
    assert er.scrub(r"'C:\\\\Users\\\\sarah\\\\x'") == r"'C:\\\\Users\\\\~\\\\x'"
    monkeypatch.setattr(er, "_host_names", ("Sarah's MacBook Air", "Sarahs-MacBook-Air"))
    assert (
        er.scrub("peer sync as \"Sarah's MacBook Air\" on Sarahs-MacBook-Air.local")
        == 'peer sync as "<computer>" on <computer>.local'
    )
    # Paths that end at the marker don't trip it.
    assert er.scrub("/Users/") == "/Users/"
    assert er.scrub("\\Users\\") == "\\Users\\"


def test_off_when_the_stored_choice_cant_be_read(db, tmp_path, monkeypatch):
    # An opted-out user whose settings row is damaged must not be reported.
    with sqlite3.connect(er._db_path) as conn:
        conn.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (er.SETTINGS_KEY, "not json"))
    monkeypatch.setattr(er, "_cached", None)
    assert er.reports_allowed() is False
    db(send_error_reports="no")
    assert er.reports_allowed() is False
    # No database to read the choice from at all: never send.
    monkeypatch.setattr(er, "_db_path", None)
    monkeypatch.setattr(er, "_cached", None)
    assert er.reports_allowed() is False


def test_fresh_install_is_off_until_asked(tmp_path, monkeypatch):
    monkeypatch.setattr(er, "_cached", None)
    monkeypatch.setattr(er, "_db_path", tmp_path / "missing.db")
    assert er.reports_allowed() is False
    path = tmp_path / "no-table.db"
    sqlite3.connect(path).close()
    monkeypatch.setattr(er, "_cached", None)
    monkeypatch.setattr(er, "_db_path", path)
    assert er.reports_allowed() is False


# "?" can't be in a Windows file name, so there that case can't happen.
@pytest.mark.parametrize(
    "folder",
    ["St Mary's #2", "100% worship", *([] if sys.platform == "win32" else ["what?"])],
)
def test_reads_the_choice_from_awkward_folder_names(tmp_path, monkeypatch, folder):
    path = tmp_path / folder / "library.db"
    path.parent.mkdir()
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            "INSERT INTO settings VALUES (?, ?)",
            (er.SETTINGS_KEY, json.dumps({"send_error_reports": False})),
        )
    monkeypatch.setattr(er, "_cached", None)
    monkeypatch.setattr(er, "_db_path", path)
    assert er.reports_allowed() is False


def test_caps_reports_per_run_and_per_error(db):
    sent = [er.before_send({"message": "same error"}, {}) for _ in range(10)]
    assert sum(e is not None for e in sent) == er.MAX_EVENTS_PER_KIND
    kinds = [f"error {a}{b}" for a in "abcdefghij" for b in "abcdefghij"]
    sent = [er.before_send({"message": k}, {}) for k in kinds]
    assert sum(e is not None for e in sent) == er.MAX_EVENTS_PER_RUN - er.MAX_EVENTS_PER_KIND


def test_said_keeps_the_marker_whole():
    assert er.scrub(f"heard «{er.said('a » b « c')}» ok") == "heard «speech removed» ok"
    assert er.scrub(f"call «{er.said({'query': 'Margaret'})}»") == "call «speech removed»"


# --- what's logged ------------------------------------------------------------

_PACKAGE = Path(er.__file__).parent
#: Values that hold speech, or text derived from it (a transcript, the lyric
#: line matched to what was sung, a model's reply or the search it made).
_SPEECH_NAMES = {
    "utterance", "query", "calls", "body", "lyric", "lyrics", "transcript",
    "section_key", "span", "text", "words", "heard",
}
#: Info and up: what becomes a breadcrumb, and what the log file keeps.
_LOG_METHODS = {"info", "warning", "error", "exception", "critical"}
#: Calls whose result says nothing of what was said (`len(words)`).
_SAFE_CALLS = {"len", "type", "id", "bool", "int", "float", "round"}
_PLACEHOLDER = re.compile(r"%(?:\([^)]*\))?[-#0 +]*\d*(?:\.\d+)?[a-zA-Z%]")


def _mentions_speech(node: ast.AST) -> bool:
    stack = [node]
    while stack:
        sub = stack.pop()
        if isinstance(sub, ast.Call):
            name = getattr(sub.func, "id", None)
            if name == "said":
                return True
            if name in _SAFE_CALLS:
                continue
        if isinstance(sub, ast.Name) and sub.id in _SPEECH_NAMES:
            return True
        if isinstance(sub, ast.Attribute):
            # `heard.span` is speech; `heard.gain` is a number.
            if sub.attr in _SPEECH_NAMES:
                return True
            if isinstance(sub.value, ast.Name):
                continue
        stack.extend(ast.iter_child_nodes(sub))
    return False


def _unmarked_speech(source: str) -> list[str]:
    problems = []
    for call in ast.walk(ast.parse(source)):
        if not (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr in _LOG_METHODS
            and getattr(call.func.value, "id", None) in ("log", "logger", "logging")
            and call.args
        ):
            continue
        fmt, *args = call.args
        if not (isinstance(fmt, ast.Constant) and isinstance(fmt.value, str)):
            if _mentions_speech(fmt):
                problems.append(f"line {call.lineno}: speech in a non-constant format")
            continue
        holes = [m for m in _PLACEHOLDER.finditer(fmt.value) if m.group() != "%%"]
        for i, arg in enumerate(args):
            if not _mentions_speech(arg):
                continue
            hole = holes[i] if i < len(holes) else None
            marked = (
                hole is not None
                and fmt.value[hole.start() - 1 : hole.start()] == "«"
                and fmt.value[hole.end() : hole.end() + 1] == "»"
            )
            if not marked:
                problems.append(f"line {call.lineno}: argument {i + 1} is not inside «%s»")
    return problems


def test_speech_in_log_lines_is_marked_for_the_scrubber():
    """Every speech-derived value an info-or-louder log line carries, anywhere
    in the engine, sits inside «…» — so it stays in the local log but never
    reaches an error report (as a breadcrumb, or in the log's tail)."""
    problems = {
        str(path.relative_to(_PACKAGE)): found
        for path in sorted(_PACKAGE.rglob("*.py"))
        if (found := _unmarked_speech(path.read_text()))
    }
    assert problems == {}


def test_the_speech_guard_catches_unmarked_lines():
    assert _unmarked_speech('log.info("heard %r (gain %.1f)", heard.span, heard.gain)')
    assert _unmarked_speech('log.warning("bad call %s", calls)')
    assert not _unmarked_speech('log.info("heard «%s» (gain %.1f)", said(heard.span), heard.gain)')
    assert not _unmarked_speech('log.info("%d words", len(words))')
    assert not _unmarked_speech('log.debug("heard %r", text)')


# --- round 2 -------------------------------------------------------------------


def test_user_folder_names_with_spaces_any_case_and_drives():
    assert er.scrub(r"C:\Users\Sarah Jones\x") == r"C:\Users\~\x"
    assert er.scrub(r"c:\users\sarah\x") == r"c:\users\~\x"
    assert er.scrub("/users/sarah/x") == "/users/~/x"
    assert er.scrub("/Volumes/Sarah's Drive/Songs") == "/Volumes/~/Songs"
    assert er.scrub('File "/Users/Sarah Jones/app/x.py", line 3') == (
        'File "/Users/~/app/x.py", line 3'
    )
    assert er.scrub("/Users/Shared/x") == "/Users/Shared/x"
    assert er.scrub("/Users/Sharedwithme/x") == "/Users/~/x"
    assert er.scrub("open('/Users/sarah') failed") == "open('/Users/~') failed"


def test_scrubs_thread_frames_and_cuts_long_exception_text(db):
    event = {
        "threads": {
            "values": [
                {"stacktrace": {"frames": [{"abs_path": "/Users/sarah/x.py", "vars": {"a": 1}}]}}
            ]
        },
        "exception": {"values": [{"type": "ValueError", "value": "x" * 5000}]},
    }
    out = er.before_send(event, {})
    frame = out["threads"]["values"][0]["stacktrace"]["frames"][0]
    assert frame == {"abs_path": "/Users/~/x.py"}
    assert len(out["exception"]["values"][0]["value"]) == er.MAX_EXCEPTION_VALUE_CHARS


def test_a_failed_read_after_a_good_one_is_off(db, monkeypatch):
    db(send_error_reports=True)
    assert er.reports_allowed() is True

    def broken(_path):
        raise sqlite3.DatabaseError("file is not a database")

    monkeypatch.setattr(er, "_read_choice", broken)
    monkeypatch.setattr(er, "_cached", None)
    assert er.reports_allowed() is False


def test_a_briefly_locked_database_keeps_the_last_answer(db, monkeypatch):
    db(send_error_reports=True)
    assert er.reports_allowed() is True
    good_at = er._last_good[0]

    def locked(_path):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(er, "_read_choice", locked)
    monkeypatch.setattr(er, "_cached", None)
    assert er.reports_allowed() is True
    # ...but not for long.
    monkeypatch.setattr(er, "_last_good", (good_at - er._BUSY_GRACE_S - 1, True, ""))
    monkeypatch.setattr(er, "_cached", None)
    assert er.reports_allowed() is False


def test_nothing_leaves_while_reports_are_off(db):
    """Not a report, and not the SDK's own envelopes (client reports of
    dropped events, sessions): the transport itself checks the choice."""
    import sentry_sdk
    from sentry_sdk.envelope import Envelope

    client = sentry_sdk.Client(
        dsn="https://key@127.0.0.1:9/1",  # never contacted: the queue is replaced
        transport=er._consent_transport(),
        send_client_reports=False,
    )
    queued: list = []
    client.transport._worker.submit = lambda job: queued.append(job) or True
    try:
        db(send_error_reports=False)
        client.transport.capture_envelope(Envelope())
        assert queued == []
        db(send_error_reports=True)
        client.transport.capture_envelope(Envelope())
        assert len(queued) == 1
    finally:
        client.transport._worker.submit = lambda job: True
        client.close()


def test_init_sends_only_errors_and_says_when_its_off(db, monkeypatch, caplog):
    import sentry_sdk

    seen: dict = {}
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: seen.update(kw))
    monkeypatch.setattr(sentry_sdk, "set_tag", lambda *a: None)
    monkeypatch.setenv("CHURCH_SLIDES_SENTRY_DSN", "https://key@127.0.0.1:9/1")
    monkeypatch.setattr(er, "_set_host_names", lambda: None)
    db(send_error_reports=False)
    path = er._db_path
    with caplog.at_level(logging.INFO, logger=er.__name__):
        assert er.init(str(path)) is True
    assert seen["send_client_reports"] is False
    assert seen["auto_session_tracking"] is False
    assert seen["enable_logs"] is False
    assert issubclass(seen["transport"], __import__("sentry_sdk").transport.HttpTransport)
    assert "nothing is sent" in caplog.text
