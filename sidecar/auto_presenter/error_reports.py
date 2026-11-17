"""The engine's crash and error reports, to Sentry.

The desktop app reports its own panics, the UI's errors, and the engine
exiting (`src-tauri/src/telemetry.rs`); this reports what only the engine can
see — Python exceptions, with their real stack — to the same project and
release. It only runs when the app passes `CHURCH_SLIDES_SENTRY_DSN`, which a
release build always does and a dev build only on request.

The user's choices (Settings → Error reports: on/off, and an optional contact)
live in the app's settings table, in the same database the engine already
opens. Each report reads them, so turning reports off stops the engine's at
once, without a restart or a protocol message.

Reports carry what helps fix a bug: messages, stacks, device names, song
titles, and the end of the app's log. Two things are kept out:
  - what someone *said*: spoken text is logged inside «…» and replaced here,
    because the microphone also hears prayer requests and announcements;
  - the user's folder name in paths, and this computer's name. Reports are
    tied to an anonymous install id instead (plus a contact, only if the user
    gave one).
A stored choice that can't be read counts as off, and one run sends at most
`MAX_EVENTS_PER_RUN` reports, like the app.
Local variables are never attached (they hold transcripts), and only errors
are sent: no tracing or profiling, whose overhead would land on the live
listening pipeline.
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SETTINGS_KEY = "settings_v1"
#: How long a read of the user's settings is trusted.
_SETTING_TTL_S = 5.0
#: Lines of the app's log attached to each report.
LOG_TAIL_LINES = 200
#: Reports from one run of the engine, at most — the same cap as the app's
#: (`MAX_EVENTS_PER_RUN` in src-tauri/src/telemetry.rs): an error logged in a
#: loop must not send hundreds of copies over a church's connection.
MAX_EVENTS_PER_RUN = 50
#: Copies of any one error per run, within that.
MAX_EVENTS_PER_KIND = 5

_db_path: Path | None = None
_log_file: Path | None = None
_install_id: str | None = None
_cached: tuple[float, bool, str] | None = None
#: The last answer read successfully, and when.
_last_good: tuple[float, bool, str] | None = None
#: How long a locked database may keep the last good answer.
_BUSY_GRACE_S = 30.0
#: Exception text carried in a report, at most: an error's message can quote
#: data it was handed, so the long tail of one is dropped.
MAX_EXCEPTION_VALUE_CHARS = 500
_sent = 0
_sent_by_kind: dict[str, int] = {}
#: This computer's names ("Sarah's MacBook Air", "Sarahs-MacBook-Air"),
#: scrubbed from reports like the user's folder name. Filled by `init`.
_host_names: tuple[str, ...] = ()


def _read_choice(path: Path) -> tuple[bool, str]:
    """The stored choice, read straight from the database. Raises when a
    settings row exists but can't be understood, so the caller fails closed;
    a missing row (or database, or table) is a fresh install that hasn't been
    asked: off. Only a yes to the question counts (`error_reports_asked`):
    older versions stored reports as on by default. Mirrors
    `report_choice_from_raw` in settings.rs."""
    if not path.exists():
        return False, ""
    # Quoted, so a '#', '?' or '%' in the folder name can't end the path early.
    uri = "file:" + urllib.parse.quote(str(path)) + "?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=1.0) as conn:
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (SETTINGS_KEY,)
            ).fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return False, ""
            raise
    if not row:
        return False, ""
    stored = json.loads(row[0])
    if not isinstance(stored, dict):
        raise ValueError("settings row is not an object")
    allowed = stored.get("send_error_reports", False)
    if not isinstance(allowed, bool):
        raise ValueError("send_error_reports is not a boolean")
    asked = stored.get("error_reports_asked") is True
    return allowed and asked, str(stored.get("report_contact") or "").strip()


def _settings() -> tuple[bool, str]:
    """(reports allowed, contact). Off, with no contact, until someone says
    yes; off when the stored choice can't be read — an opted-out user must never
    get a report because their settings row was damaged. The one exception is
    a database briefly locked by the app writing to it: then the last good
    answer stands, for up to `_BUSY_GRACE_S`."""
    global _cached, _last_good
    now = time.monotonic()
    if _cached is not None and now - _cached[0] < _SETTING_TTL_S:
        return _cached[1], _cached[2]
    if _db_path is None:
        # Nothing to read the choice from: never send.
        allowed, contact = False, ""
    else:
        try:
            allowed, contact = _read_choice(_db_path)
            _last_good = (now, allowed, contact)
        except sqlite3.OperationalError as exc:
            busy = "locked" in str(exc) or "busy" in str(exc)
            if busy and _last_good is not None and now - _last_good[0] < _BUSY_GRACE_S:
                allowed, contact = _last_good[1], _last_good[2]
            else:
                allowed, contact = False, ""
        except (sqlite3.Error, ValueError, TypeError, OSError):
            allowed, contact = False, ""
    _cached = (now, allowed, contact)
    return allowed, contact


def reports_allowed() -> bool:
    return _settings()[0]


# --- scrubbing (mirrors `scrub` in src-tauri/src/telemetry.rs) ---------------

# One or more backslashes: a Windows path inside JSON or a repr is escaped
# (`C:\\Users\\sarah`). Any case (`c:\users\`). The name runs to the next
# separator, spaces and apostrophes included ("Sarah Jones", "Sarah's"); a
# quote that ends a quoted path isn't followed by a letter. `/Volumes/<name>`
# is a drive, often named after its owner.
_USER_DIR = re.compile(
    r"((?i:/Users/|\\+Users\\+|/home/|/Volumes/))"
    r"(?!Shared(?:[/\\\"'\n]|$))(?![\s])"
    r"((?:[^/\\\"'\n\r\t]|'(?=[A-Za-z]))+)"
)
_SPEECH = re.compile(r"«[^»]*»")
# The owner's name in a device name ("Sarah's AirPods" -> "<name>'s AirPods"):
# up to three capitalised words before a possessive that's followed by a device
# word. Mirrors `remove_owner_names` and `DEVICE_WORDS` in telemetry.rs.
_DEVICE_WORDS = (
    "airpods|iphone|ipad|macbook|imac|mac|microphone|mic|headphones|headset|"
    "earbuds|buds|beats|phone|galaxy|pixel|speaker|laptop"
)
_OWNER = re.compile(
    r"(?<![\w-])(?:[^\W\d_a-z][\w-]* ){0,2}[^\W\d_a-z][\w-]*(?=['’]s (?i:" + _DEVICE_WORDS + "))"
)


def said(value: Any) -> str:
    """`value` as text for a log line's «%s» — what someone said, or text
    derived from it (a model's reply, a search it made). The «» markers can't
    occur inside, so the scrubber always finds the whole of it."""
    text = value if isinstance(value, str) else repr(value)
    return text.replace("«", "").replace("»", "")[:2000]


def scrub(text: str) -> str:
    """Replace spoken text («…»), the user's folder name in paths, owners'
    names in device names, and this computer's name."""
    text = _SPEECH.sub("«speech removed»", text)
    text = _USER_DIR.sub(lambda m: m.group(1) + "~", text)
    for name in _host_names:
        text = text.replace(name, "<computer>")
    # After the computer's own names, which it would otherwise half-match.
    return _OWNER.sub("<name>", text)


def _scrub_any(value: Any) -> Any:
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {k: _scrub_any(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_any(v) for v in value]
    return value


def log_tail(path: Path | None, lines: int = LOG_TAIL_LINES) -> str:
    """The end of the app's log (which has the engine's lines too), scrubbed."""
    if path is None:
        return ""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 128 * 1024))
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    return scrub("\n".join(text.splitlines()[-lines:]))


def _user(contact: str) -> dict[str, str] | None:
    user: dict[str, str] = {}
    if _install_id:
        user["id"] = _install_id
    if contact:
        user["email" if "@" in contact else "username"] = contact
    return user or None


def _kind(event: dict[str, Any]) -> str:
    """What makes two reports the same error, for the per-kind cap."""
    for exc in (event.get("exception") or {}).get("values") or []:
        return f"{exc.get('type')}:{re.sub(r'[0-9]', '0', str(exc.get('value')))}"
    entry = event.get("logentry")
    if isinstance(entry, dict) and entry.get("message"):
        return str(entry["message"])
    return re.sub(r"[0-9]", "0", str(event.get("message")))


def _within_cap(event: dict[str, Any]) -> bool:
    global _sent
    if _sent >= MAX_EVENTS_PER_RUN:
        return False
    kind = _kind(event)
    if _sent_by_kind.get(kind, 0) >= MAX_EVENTS_PER_KIND:
        return False
    _sent += 1
    _sent_by_kind[kind] = _sent_by_kind.get(kind, 0) + 1
    return True


def before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    allowed, contact = _settings()
    if not allowed:
        return None
    if not _within_cap(event):
        return None
    event.pop("server_name", None)
    event.pop("request", None)
    user = _user(contact)
    if user:
        event["user"] = user
    else:
        event.pop("user", None)
    if isinstance(event.get("message"), str):
        event["message"] = scrub(event["message"])
    entry = event.get("logentry")
    if isinstance(entry, dict):
        for key in ("message", "formatted"):
            if isinstance(entry.get(key), str):
                entry[key] = scrub(entry[key])
        # The parameters are the raw, unscrubbed values (speech, paths);
        # `formatted` is the scrubbed whole, so they only add what leaks.
        if "params" in entry:
            entry["params"] = []
    for exc in (event.get("exception") or {}).get("values") or []:
        # Exception text is kept (it's what a bug report needs) with speech and
        # paths removed, but cut short: a message can quote data it was given.
        if isinstance(exc.get("value"), str):
            exc["value"] = scrub(exc["value"])[:MAX_EXCEPTION_VALUE_CHARS]
        _scrub_frames(exc.get("stacktrace"))
    for thread in (event.get("threads") or {}).get("values") or []:
        _scrub_frames(thread.get("stacktrace"))
    crumbs = event.get("breadcrumbs")
    if isinstance(crumbs, dict) and isinstance(crumbs.get("values"), list):
        crumbs["values"] = [before_breadcrumb(c, {}) for c in crumbs["values"]]
    if isinstance(event.get("extra"), dict):
        event["extra"] = _scrub_any(event["extra"])
    tail = log_tail(_log_file)
    if tail and isinstance(hint.get("attachments"), list):
        from sentry_sdk.attachments import Attachment

        hint["attachments"].append(
            Attachment(bytes=tail.encode(), filename="log-tail.txt", content_type="text/plain")
        )
    return event


def _scrub_frames(stacktrace: Any) -> None:
    if not isinstance(stacktrace, dict):
        return
    for frame in stacktrace.get("frames") or []:
        frame.pop("vars", None)
        for key in ("abs_path", "filename"):
            if isinstance(frame.get(key), str):
                frame[key] = scrub(frame[key])


def before_breadcrumb(crumb: dict[str, Any], _hint: dict[str, Any]) -> dict[str, Any]:
    if isinstance(crumb.get("message"), str):
        crumb["message"] = scrub(crumb["message"])
    if isinstance(crumb.get("data"), dict):
        crumb["data"] = _scrub_any(crumb["data"])
    return crumb


def _set_host_names() -> None:
    global _host_names
    names: set[str] = set()
    try:
        host = socket.gethostname()
        names.update({host, host.split(".")[0]})
    except OSError:
        pass
    if sys.platform == "darwin":
        for key in ("ComputerName", "LocalHostName"):
            try:
                out = subprocess.run(
                    ["scutil", "--get", key], capture_output=True, text=True, timeout=2
                )
                if out.returncode == 0:
                    names.add(out.stdout.strip())
            except (OSError, subprocess.SubprocessError):
                pass
    # Longest first, so "Sarahs-MacBook-Air.local" goes before its prefix;
    # very short names would match ordinary words.
    ordinary = ("localhost", "localhost.localdomain")
    names = {n for n in names if len(n) >= 4 and n.lower() not in ordinary}
    _host_names = tuple(sorted(names, key=len, reverse=True))


def _consent_transport() -> type:
    from sentry_sdk.transport import HttpTransport

    class ConsentTransport(HttpTransport):
        """Sends nothing — no report, no session, no client report — while
        the user's choice is off, or unknown. Checked per envelope, so turning
        reports on later works without a restart, and turning them off stops
        everything at once."""

        def capture_envelope(self, envelope: Any) -> None:
            if reports_allowed():
                super().capture_envelope(envelope)

    return ConsentTransport


def init(db_path: str | None) -> bool:
    """Start reporting if the app asked for it. Returns whether it did."""
    global _db_path, _log_file, _install_id
    dsn = os.environ.get("CHURCH_SLIDES_SENTRY_DSN")
    if not dsn:
        return False
    _db_path = Path(db_path) if db_path else None
    log_file = os.environ.get("CHURCH_SLIDES_LOG_FILE")
    _log_file = Path(log_file) if log_file else None
    _install_id = os.environ.get("CHURCH_SLIDES_INSTALL_ID") or None
    _set_host_names()
    try:
        import sentry_sdk
        from sentry_sdk.integrations.atexit import AtexitIntegration
        from sentry_sdk.integrations.dedupe import DedupeIntegration
        from sentry_sdk.integrations.excepthook import ExcepthookIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
        from sentry_sdk.integrations.threading import ThreadingIntegration
    except ImportError:
        log.warning("error reports unavailable: sentry_sdk is not in this build")
        return False

    sentry_sdk.init(
        dsn=dsn,
        transport=_consent_transport(),
        # Only errors: no counts of dropped events (sent even while reports
        # are off), no sessions, logs or metrics.
        send_client_reports=False,
        auto_session_tracking=False,
        enable_logs=False,
        enable_metrics=False,
        release=os.environ.get("CHURCH_SLIDES_RELEASE"),
        environment=os.environ.get("CHURCH_SLIDES_ENVIRONMENT", "production"),
        send_default_pii=False,
        include_local_variables=False,
        server_name="",
        # Named here rather than auto-discovered: a frozen build has exactly
        # these, and nothing scans the environment for others.
        default_integrations=False,
        auto_enabling_integrations=False,
        integrations=[
            # Info and up become breadcrumbs; errors (`log.exception` in every
            # handler) become reports, with the exception's stack.
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
            ExcepthookIntegration(),
            ThreadingIntegration(),
            DedupeIntegration(),
            AtexitIntegration(callback=lambda pending, timeout: None),
        ],
        max_breadcrumbs=100,
        before_send=before_send,
        before_breadcrumb=before_breadcrumb,
    )
    sentry_sdk.set_tag("component", "engine")
    log.info(
        "error reports ready (%s); %s",
        os.environ.get("CHURCH_SLIDES_ENVIRONMENT", "production"),
        "sending" if reports_allowed() else "off in Settings, nothing is sent",
    )
    return True
