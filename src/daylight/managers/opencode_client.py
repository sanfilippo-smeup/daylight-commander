"""Embedded opencode server and its HTTP client (DC-024, ADR-014, FR-802).

The AI dashboard builder talks to a local ``opencode serve`` subprocess over
its HTTP API. Two classes split the work:

:class:`OpenCodeServer` owns the subprocess lifecycle: it launches
``opencode serve`` bound to loopback, discovers the base URL from the server's
startup line (``opencode server listening on http://127.0.0.1:PORT``) and
terminates the process on demand. Startup is bounded: a missing binary or a
server that fails to announce itself raises :class:`OpenCodeError` and never
leaves the process behind.

:class:`OpenCodeClient` drives the HTTP API. Every call goes through an
injectable transport callable (``(method, url, body) -> (status, body)``) so
tests never open a socket; the default transport uses :mod:`urllib.request`
only -- no third-party HTTP dependency (ADR-014). :meth:`stream_events` is a
tolerant parser over an injected SSE line stream: malformed or unknown lines
are skipped, never raised.

All process, HTTP and filesystem access lives here in the managers layer
(FR-808, ADR-003): the UI only sees the frozen models from
:mod:`daylight.models`.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import signal
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from time import monotonic
from typing import Any

from daylight.models import BuilderEvent, BuilderOption, BuilderQuestion

__all__ = [
    "DEFAULT_SERVE_COMMAND",
    "OpenCodeClient",
    "OpenCodeError",
    "OpenCodeServer",
    "Transport",
]

logger = logging.getLogger("daylight.managers.opencode_client")

#: Signature of the injectable HTTP transport: ``(method, url, body)`` where
#: ``body`` is the encoded JSON payload or ``None``, returning the HTTP status
#: and the raw response body. Tests inject a callable and never open a socket.
Transport = Callable[[str, str, bytes | None], tuple[int, bytes]]

#: Default ``opencode serve`` argv: loopback host, ephemeral port (the server
#: prints the URL it picked, which :class:`OpenCodeServer` discovers).
DEFAULT_SERVE_COMMAND: tuple[str, ...] = ("opencode", "serve", "--hostname", "127.0.0.1")

#: Seconds ``opencode serve`` gets to announce its URL before startup fails.
DEFAULT_STARTUP_TIMEOUT = 10.0

#: A URL inside a startup-output line, e.g.
#: ``opencode server listening on http://127.0.0.1:4096``.
_URL_PATTERN = re.compile(r"https?://[^\s\"']+")


class OpenCodeError(Exception):
    """The opencode server cannot start, or an API call failed."""


def _urllib_transport(method: str, url: str, body: bytes | None) -> tuple[int, bytes]:
    """Default transport: :mod:`urllib.request` only (ADR-014).

    Returns ``(status, body)``; an HTTP error status is returned as-is for the
    client to judge, so the transport itself never raises for a 4xx/5xx.
    """
    request = urllib.request.Request(url, data=body, method=method)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request) as response:
            return int(response.status), response.read()
    except urllib.error.HTTPError as error:
        return int(error.code), error.read()


class OpenCodeServer:
    """Lifecycle of one embedded ``opencode serve`` subprocess (FR-802).

    The server is started with :meth:`start`, which returns the base URL
    discovered from its startup output, and stopped with :meth:`stop`. Startup
    is bounded by the ``startup_timeout`` constructor argument: a missing
    binary, an early exit or a server that never announces a URL raises
    :class:`OpenCodeError` and the subprocess is terminated, so a failed start
    never hangs and never leaks a process. Also usable as a context manager.
    """

    def __init__(
        self,
        *,
        command: Sequence[str] = DEFAULT_SERVE_COMMAND,
        startup_timeout: float = DEFAULT_STARTUP_TIMEOUT,
    ) -> None:
        self._command = tuple(command)
        self._startup_timeout = startup_timeout
        self._process: subprocess.Popen[str] | None = None
        self._base_url: str | None = None
        self._quiet_pump = threading.Event()

    @property
    def base_url(self) -> str | None:
        """The discovered base URL, or ``None`` before a successful start."""
        return self._base_url

    def start(self) -> str:
        """Launch the server and return its base URL (AC5).

        Idempotent while running: a second call returns the same URL. A missing
        binary, an early exit or a startup timeout raises :class:`OpenCodeError`
        with the subprocess already terminated.
        """
        running = self._process
        if running is not None and running.poll() is None and self._base_url is not None:
            return self._base_url
        self._quiet_pump.clear()
        try:
            self._process = subprocess.Popen(
                self._command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                **({"start_new_session": True} if os.name == "posix" else {}),
            )
        except OSError as error:
            self._process = None
            raise OpenCodeError(f"cannot start {self._command[0]!r}: {error}") from error
        try:
            self._base_url = self._discover_url()
        except OpenCodeError:
            self._terminate()
            self._process = None
            raise
        self._quiet_pump.set()
        return self._base_url

    def stop(self) -> None:
        """Terminate the server process; safe to call more than once."""
        if self._process is None:
            return
        self._terminate()
        self._process = None
        self._base_url = None

    def _discover_url(self) -> str:
        """Read startup output until a URL appears, bounded by the deadline."""
        process = self._process
        if process is None or process.stdout is None:  # pragma: no cover - defensive
            raise OpenCodeError("opencode serve process is not running")
        lines: queue.Queue[str | None] = queue.Queue()
        quiet = self._quiet_pump

        def pump() -> None:
            try:
                for line in process.stdout:
                    if quiet.is_set():
                        return
                    lines.put(line)
            except (ValueError, OSError):  # closed under us
                pass
            finally:
                lines.put(None)

        reader = threading.Thread(target=pump, daemon=True, name="opencode-startup")
        reader.start()
        deadline = monotonic() + self._startup_timeout
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise OpenCodeError(
                    f"opencode serve did not announce a URL within {self._startup_timeout:g}s"
                )
            try:
                line = lines.get(timeout=min(remaining, 0.2))
            except queue.Empty:
                if process.poll() is not None:
                    raise OpenCodeError("opencode serve exited before announcing its URL")
                continue
            if line is None:
                raise OpenCodeError("opencode serve exited before announcing its URL")
            match = _URL_PATTERN.search(line)
            if match is not None:
                return match.group(0)

    def _terminate(self) -> None:
        """Terminate the subprocess (whole group on POSIX) and reap it."""
        self._quiet_pump.set()
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            _signal_group(process, signal.SIGTERM)
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                _signal_group(process, signal.SIGKILL)
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:  # pragma: no cover - best effort
                    pass
        for stream in (process.stdout, process.stderr, process.stdin):
            if stream is not None:
                try:
                    stream.close()
                except OSError:  # pragma: no cover - already closed
                    pass

    def __enter__(self) -> "OpenCodeServer":
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    def __del__(self) -> None:
        try:
            self.stop()
        except Exception:  # interpreter may be shutting down
            pass


def _signal_group(process: subprocess.Popen[str], sig: int) -> None:
    """Signal the whole process group on POSIX, else just the process."""
    if os.name == "posix":
        try:
            os.killpg(process.pid, sig)
            return
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        process.send_signal(sig)
    except (OSError, ValueError):  # already gone
        pass


class OpenCodeClient:
    """Thin HTTP client for the opencode API (DC-024, ADR-014).

    Every call goes through the injectable transport callable given to the
    constructor, so tests exercise paths and bodies with a fake and never open
    a socket. The client is deliberately thin: it knows the endpoint paths, the
    few body shapes the builder needs, and returns decoded JSON (or frozen
    models) otherwise -- unknown fields and event types are tolerated, not
    rejected.
    """

    def __init__(self, base_url: str, *, transport: Transport | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._transport: Transport = transport if transport is not None else _urllib_transport

    def create_session(self, directory: str | None = None, title: str | None = None) -> str:
        """POST ``/session`` and return the new session id (AC2).

        ``directory`` is a query parameter and ``title`` a JSON body field,
        matching the opencode API. Raises :class:`OpenCodeError` on an HTTP
        error or a response without an id.
        """
        body: dict[str, Any] = {}
        if title is not None:
            body["title"] = title
        query = {"directory": directory} if directory is not None else None
        data = self._request("POST", "/session", query=query, body=body)
        session_id = data.get("id") if isinstance(data, dict) else None
        if not isinstance(session_id, str) or not session_id:
            raise OpenCodeError(f"POST /session returned no session id: {data!r}")
        return session_id

    def prompt_async(self, session_id: str, text: str) -> Any:
        """POST ``/session/{id}/prompt_async`` with one text part (AC2)."""
        return self._request(
            "POST",
            f"/session/{session_id}/prompt_async",
            body={"parts": [{"type": "text", "text": text}]},
        )

    def list_questions(self) -> list[BuilderQuestion]:
        """GET ``/question`` and return the pending questions (AC3).

        Each opencode question request may bundle several questions; the first
        one is normalized into a :class:`BuilderQuestion` keyed by the request
        id. Malformed items are skipped.
        """
        data = self._request("GET", "/question")
        if not isinstance(data, list):
            raise OpenCodeError(f"GET /question returned a non-list payload: {data!r}")
        questions: list[BuilderQuestion] = []
        for item in data:
            question = _question_from_request(item)
            if question is not None:
                questions.append(question)
        return questions

    def reply_question(self, request_id: str, answers: Sequence[Sequence[str]]) -> None:
        """POST ``/question/{id}/reply`` with ``{"answers": [[...], ...]}`` (AC3).

        ``answers`` holds one list of selected option labels per question.
        """
        self._request(
            "POST",
            f"/question/{request_id}/reply",
            body={"answers": [list(answer) for answer in answers]},
        )

    def list_permissions(self) -> list[dict[str, Any]]:
        """GET ``/permission`` and return the pending permission requests."""
        data = self._request("GET", "/permission")
        if not isinstance(data, list):
            raise OpenCodeError(f"GET /permission returned a non-list payload: {data!r}")
        return [item for item in data if isinstance(item, dict)]

    def reply_permission(self, request_id: str, reply: str) -> None:
        """POST ``/permission/{id}/reply`` with ``{"reply": reply}``.

        ``reply`` is ``"once"``, ``"always"`` or ``"reject"``.
        """
        self._request("POST", f"/permission/{request_id}/reply", body={"reply": reply})

    def messages(self, session_id: str) -> list[dict[str, Any]]:
        """GET ``/session/{id}/message`` and return the session messages."""
        data = self._request("GET", f"/session/{session_id}/message")
        if not isinstance(data, list):
            raise OpenCodeError(f"GET /session/.../message returned a non-list payload: {data!r}")
        return [item for item in data if isinstance(item, dict)]

    def stream_events(self, lines: Iterable[str]) -> Iterator[BuilderEvent]:
        """Parse an SSE line stream into :class:`BuilderEvent` items (AC4).

        ``lines`` is the injected line stream of ``GET /event`` (tests pass a
        fake iterator and never open a socket). Assistant text parts become
        ``kind="text"`` events, a pending question becomes ``kind="question"``
        with the parsed :class:`BuilderQuestion`, and the stream ends with
        ``kind="idle"``: either the server announces the idle state, or the
        line stream is exhausted. Malformed or unknown lines are skipped,
        never raised.
        """
        idle_seen = False
        for payload in _sse_payloads(lines):
            event = _event_from_payload(payload)
            if event is None:
                continue
            yield event
            if event.kind == "idle":
                idle_seen = True
                return
        if not idle_seen:
            yield BuilderEvent(kind="idle")

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        body: Any = None,
    ) -> Any:
        """One transport call; returns the decoded JSON, or ``None`` if empty."""
        url = self._base_url + path
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        payload = None if body is None else json.dumps(body).encode("utf-8")
        status, raw = self._transport(method, url, payload)
        if status < 200 or status >= 300:
            detail = raw[:200].decode("utf-8", "replace")
            raise OpenCodeError(f"{method} {path} failed with HTTP {status}: {detail}")
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise OpenCodeError(f"{method} {path} returned invalid JSON: {error}") from error


def _question_from_request(item: Any) -> BuilderQuestion | None:
    """Normalize one opencode question request into a :class:`BuilderQuestion`.

    Accepts both the real shape (``{id, questions: [spec, ...]}``) and a flat
    ``{id, question, header, ...}`` dict; returns ``None`` for anything that
    has no usable id.
    """
    if not isinstance(item, dict):
        return None
    request_id = item.get("id")
    if not isinstance(request_id, str) or not request_id:
        return None
    bundled = item.get("questions")
    spec = item
    if isinstance(bundled, list) and bundled and isinstance(bundled[0], dict):
        spec = bundled[0]
    if not isinstance(spec, dict):
        return None
    raw_options = spec.get("options")
    options = tuple(
        BuilderOption(
            label=str(option.get("label", "")),
            description=str(option.get("description", "")),
        )
        for option in (raw_options or [])
        if isinstance(option, dict)
    )
    return BuilderQuestion(
        id=request_id,
        header=str(spec.get("header", "")),
        question=str(spec.get("question", "")),
        options=options,
        multiple=bool(spec.get("multiple", False)),
        custom=bool(spec.get("custom", False)),
    )


def _sse_payloads(lines: Iterable[str]) -> Iterator[Any]:
    """Yield the JSON payload of every well-formed SSE event in ``lines``.

    Standard framing: ``data:`` lines accumulate until a blank line dispatches
    the event; ``event:``/``id:``/``retry:`` lines and ``:`` comments are
    ignored; a stream that ends mid-event still dispatches what it gathered.
    Malformed payloads are skipped, never raised.
    """
    data_lines: list[str] = []
    for raw in lines:
        line = raw.rstrip("\r\n")
        if line.startswith(":"):
            continue
        if not line:
            if data_lines:
                payload = _json_or_skip("\n".join(data_lines))
                data_lines = []
                if payload is not _SKIP:
                    yield payload
            continue
        if line.startswith("data:"):
            data_lines.append(line[len("data:") :].removeprefix(" "))
    if data_lines:
        payload = _json_or_skip("\n".join(data_lines))
        if payload is not _SKIP:
            yield payload


_SKIP = object()


def _json_or_skip(data: str) -> Any:
    """Decode one SSE ``data`` blob; ``_SKIP`` when it is not valid JSON."""
    if not data.strip():
        return _SKIP
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        logger.debug("skipping malformed SSE payload: %r", data[:120])
        return _SKIP


def _event_from_payload(payload: Any) -> BuilderEvent | None:
    """Map one decoded SSE payload to a :class:`BuilderEvent`, or ``None``.

    Known event types (opencode v1.18): ``message.part.updated`` with a text
    part, ``question.asked`` with a question request, ``session.idle``.
    Everything else -- unknown types, non-text parts, malformed shapes -- is
    skipped so API drift cannot crash the builder.
    """
    if not isinstance(payload, dict):
        return None
    event_type = payload.get("type")
    if not isinstance(event_type, str):
        return None
    properties = payload.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    if "idle" in event_type:
        return BuilderEvent(kind="idle")
    if event_type.startswith("message.part"):
        part = properties.get("part")
        if isinstance(part, dict) and part.get("type") == "text":
            text = part.get("text")
            if isinstance(text, str):
                return BuilderEvent(kind="text", text=text)
        return None
    if "question" in event_type and event_type.endswith(".asked"):
        question = _question_from_request(properties)
        if question is not None:
            return BuilderEvent(kind="question", question=question)
        return None
    return None
