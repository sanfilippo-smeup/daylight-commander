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
are skipped, never raised. :meth:`open_event_stream` is the manager-level
opener for that line stream (``GET /event``, DC-025): it is the only place a
socket is opened for events, so the UI never does (FR-808), and connection
failures surface as :class:`OpenCodeError` (DC-024 review F1) instead of
escaping as raw :class:`urllib.error.URLError`.

All process, HTTP and filesystem access lives here in the managers layer
(FR-808, ADR-003): the UI only sees the frozen models from
:mod:`daylight.models`.
"""

from __future__ import annotations

import base64
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
    "EventStreamOpener",
    "OpenCodeClient",
    "OpenCodeError",
    "OpenCodeServer",
    "Transport",
]

logger = logging.getLogger("daylight.managers.opencode_client")

#: Signature of the injectable HTTP transport: ``(method, url, body, headers)``
#: where ``body`` is the encoded JSON payload or ``None`` and ``headers`` is a
#: mapping of extra request headers (empty for v1, Basic auth for v2). Returns
#: the HTTP status and the raw response body. Tests inject a callable and never
#: open a socket.
Transport = Callable[[str, str, bytes | None, Mapping[str, str]], tuple[int, bytes]]

#: Signature of the injectable SSE opener: ``(url, headers) -> iterator of raw
#: lines``. The opener connects eagerly (a plain callable, not a generator), so
#: a connection failure raises from the call that opens the stream. Tests inject
#: a callable and never open a socket.
EventStreamOpener = Callable[[str, Mapping[str, str]], Iterator[str]]

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


def _urllib_transport(
    method: str, url: str, body: bytes | None, headers: Mapping[str, str] | None = None
) -> tuple[int, bytes]:
    """Default transport: :mod:`urllib.request` only (ADR-014).

    Returns ``(status, body)``; an HTTP error status is returned as-is for the
    client to judge, so the transport itself never raises for a 4xx/5xx.
    ``headers`` carries the v2 Basic-auth header (ADR-015).
    """
    request = urllib.request.Request(url, data=body, method=method, headers=dict(headers or {}))
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request) as response:
            return int(response.status), response.read()
    except urllib.error.HTTPError as error:
        return int(error.code), error.read()


def _urllib_event_opener(
    url: str, headers: Mapping[str, str] | None = None
) -> Iterator[str]:
    """Default SSE opener: ``GET url`` over urllib, decoded line by line.

    A plain function rather than a generator, so the connection opens eagerly
    and a connection failure surfaces from the call that opens the stream, not
    from the first iteration. The response is closed when the line iterator is
    exhausted or abandoned. ``headers`` carries the v2 Basic-auth header.
    """
    request = urllib.request.Request(url, method="GET", headers=dict(headers or {}))
    return _response_lines(urllib.request.urlopen(request))


def _response_lines(response: Any) -> Iterator[str]:
    """Yield the body of an open response as decoded text lines, then close it."""
    try:
        for raw in response:
            yield raw.decode("utf-8", "replace")
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()


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
        self._api_version: str = "v1"
        self._password: str | None = None
        self._quiet_pump = threading.Event()

    @property
    def base_url(self) -> str | None:
        """The discovered base URL, or ``None`` before a successful start."""
        return self._base_url

    @property
    def api_version(self) -> str:
        """The detected opencode API version: ``"v1"`` or ``"v2"`` (ADR-015)."""
        return self._api_version

    @property
    def password(self) -> str | None:
        """The v2 server password, or ``None`` for v1 (ADR-015)."""
        return self._password

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
            self._base_url, self._api_version, self._password = self._discover()
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
        self._api_version = "v1"
        self._password = None

    def _discover(self) -> tuple[str, str, str | None]:
        """Read startup output until the URL (and v2 password) appear.

        v1 prints ``opencode server listening on http://…`` and is complete as
        soon as the URL is seen. v2 prints ``server listening on http://…``
        followed by ``server password …``; both are required before the client
        can authenticate (ADR-015). Bounded by the startup deadline.
        """
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
        base_url: str | None = None
        password: str | None = None
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
            if "server password" in line:
                password = line.split("server password", 1)[1].strip()
                if base_url is not None:
                    return base_url, "v2", password
                continue
            match = _URL_PATTERN.search(line)
            if match is None:
                continue
            base_url = match.group(0)
            if "opencode server listening" in line:
                return base_url, "v1", None
            if "server listening" in line:
                # v2 listening line: wait for the password line before returning.
                if password is not None:
                    return base_url, "v2", password
                continue
            # Any other URL line is a v1-style announcement.
            return base_url, "v1", None

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

    def __init__(
        self,
        base_url: str,
        *,
        password: str | None = None,
        api_version: str = "v1",
        transport: Transport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_version = api_version
        self._password = password
        self._transport: Transport = transport if transport is not None else _urllib_transport
        self._api_prefix = "/api" if api_version == "v2" else ""
        self._headers: dict[str, str] = {}
        if api_version == "v2" and password:
            token = base64.b64encode(f"opencode:{password}".encode("utf-8")).decode("ascii")
            self._headers["Authorization"] = f"Basic {token}"
        #: v2 forms seen on the event stream, keyed by form id, so
        #: :meth:`reply_question` can route the answer (ADR-015).
        self._pending_forms: dict[str, BuilderQuestion] = {}

    @property
    def api_version(self) -> str:
        """The API version this client speaks: ``"v1"`` or ``"v2"``."""
        return self._api_version

    def _path(self, path: str) -> str:
        """Prefix a v1 path with ``/api`` for a v2 server (ADR-015)."""
        return f"{self._api_prefix}{path}"

    def create_session(self, directory: str | None = None, title: str | None = None) -> str:
        """POST ``/session`` and return the new session id (AC2).

        v1: ``directory`` is a query parameter and ``title`` a JSON body field.
        v2: both go in the body (``location.directory`` / ``title``) and the id
        is under ``data.id`` (ADR-015). Raises :class:`OpenCodeError` on an HTTP
        error or a response without an id.
        """
        if self._api_version == "v2":
            body: dict[str, Any] = {}
            if title is not None:
                body["title"] = title
            if directory is not None:
                body["location"] = {"directory": directory}
            data = self._request("POST", "/session", body=body)
            payload = data.get("data") if isinstance(data, dict) else None
            session_id = payload.get("id") if isinstance(payload, dict) else None
        else:
            body = {}
            if title is not None:
                body["title"] = title
            query = {"directory": directory} if directory is not None else None
            data = self._request("POST", "/session", query=query, body=body)
            session_id = data.get("id") if isinstance(data, dict) else None
        if not isinstance(session_id, str) or not session_id:
            raise OpenCodeError(f"POST /session returned no session id: {data!r}")
        return session_id

    def prompt_async(self, session_id: str, text: str) -> Any:
        """Send one text prompt to the session (AC2).

        v1: ``POST /session/{id}/prompt_async`` with a ``parts`` array.
        v2: ``POST /session/{id}/prompt`` with ``{"text": ...}`` (ADR-015).
        """
        if self._api_version == "v2":
            return self._request("POST", f"/session/{session_id}/prompt", body={"text": text})
        return self._request(
            "POST",
            f"/session/{session_id}/prompt_async",
            body={"parts": [{"type": "text", "text": text}]},
        )

    def list_questions(self) -> list[BuilderQuestion]:
        """Return the pending clarifying questions (AC3).

        v1: ``GET /question``. v2: ``GET /form`` (pending forms), normalized
        into :class:`BuilderQuestion` (ADR-015). Malformed items are skipped.
        """
        if self._api_version == "v2":
            data = self._request("GET", "/form")
            items = data.get("data") if isinstance(data, dict) else None
            if not isinstance(items, list):
                raise OpenCodeError(f"GET /form returned a non-list payload: {data!r}")
            questions: list[BuilderQuestion] = []
            for item in items:
                question = _question_from_form(item)
                if question is not None:
                    self._pending_forms[question.id] = question
                    questions.append(question)
            return questions
        data = self._request("GET", "/question")
        if not isinstance(data, list):
            raise OpenCodeError(f"GET /question returned a non-list payload: {data!r}")
        questions = []
        for item in data:
            question = _question_from_request(item)
            if question is not None:
                questions.append(question)
        return questions

    def reply_question(self, request_id: str, answers: Sequence[Sequence[str]]) -> None:
        """Answer a pending question (AC3).

        v1: ``POST /question/{id}/reply`` with ``{"answers": [[...], ...]}``.
        v2: ``POST /session/{session}/form/{id}/reply`` with
        ``{"answer": {field_key: value}}`` (ADR-015). ``answers`` holds one list
        of selected labels per question; the v2 form answer uses the first.
        """
        if self._api_version == "v2":
            question = self._pending_forms.get(request_id)
            if question is None:
                raise OpenCodeError(f"unknown v2 form: {request_id}")
            first = list(answers[0]) if answers else []
            value: Any = first if question.multiple else (first[0] if first else "")
            self._request(
                "POST",
                f"/session/{question.session_id}/form/{request_id}/reply",
                body={"answer": {question.field_key: value}},
            )
            self._pending_forms.pop(request_id, None)
            return
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
        if self._api_version == "v2":
            yield from self._stream_v2_events(lines)
            return
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

    def _stream_v2_events(self, lines: Iterable[str]) -> Iterator[BuilderEvent]:
        """Parse the v2 event stream into cumulative text/question/idle events.

        v2 streams incremental ``session.text.delta`` chunks; the client
        accumulates them per assistant message/ordinal and emits cumulative
        text, so the Builder page's replace-the-current-block behavior (DC-024
        F10) works unchanged. ``form.created`` becomes a question and
        ``session.execution.succeeded``/``failed`` ends the turn (ADR-015).
        """
        accumulated: dict[tuple[str, int], str] = {}
        for payload in _sse_payloads(lines):
            event = _event_from_v2_payload(payload, accumulated, self._pending_forms)
            if event is None:
                continue
            yield event
            if event.kind in ("idle", "error"):
                return
        yield BuilderEvent(kind="idle")

    def open_event_stream(self, *, opener: EventStreamOpener | None = None) -> Iterator[str]:
        """Open the server's SSE event stream and yield its raw lines (DC-025).

        The manager-level opener for ``GET /event`` (FR-808): the UI consumes
        the returned lines through :meth:`stream_events` and never opens a
        socket itself. The connection is opened eagerly, so a connection
        failure -- refused, unreachable, or an HTTP error status -- raises
        :class:`OpenCodeError` from this call (DC-024 review F1) instead of
        leaking a :class:`urllib.error.URLError`. The stream stays open until
        the server closes it or the iterator is abandoned.
        """
        url = f"{self._base_url}{self._path('/event')}"
        open_lines = opener if opener is not None else _urllib_event_opener
        try:
            return open_lines(url, self._headers)
        except urllib.error.URLError as error:
            raise OpenCodeError(
                f"cannot open the event stream at {url}: {error.reason}"
            ) from error

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        body: Any = None,
    ) -> Any:
        """One transport call; returns the decoded JSON, or ``None`` if empty."""
        url = self._base_url + self._path(path)
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        payload = None if body is None else json.dumps(body).encode("utf-8")
        try:
            status, raw = self._transport(method, url, payload, self._headers)
        except urllib.error.URLError as error:
            # A connection failure (refused, unreachable, DNS) must reach the
            # UI as an OpenCodeError it can show, not a raw URLError (F1).
            raise OpenCodeError(f"{method} {path} failed: {error.reason}") from error
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


def _question_from_form(item: Any) -> BuilderQuestion | None:
    """Normalize one opencode v2 form into a :class:`BuilderQuestion` (ADR-015).

    A v2 form carries one or more fields; the first field is normalized into
    the single-question model the Builder page already renders. The form id,
    owning session id and field key are kept so :meth:`OpenCodeClient.reply_question`
    can post the v2 answer body.
    """
    if not isinstance(item, dict):
        return None
    form_id = item.get("id")
    if not isinstance(form_id, str) or not form_id:
        return None
    session_id = item.get("sessionID")
    if not isinstance(session_id, str) or not session_id:
        return None
    fields = item.get("fields")
    field = fields[0] if isinstance(fields, list) and fields and isinstance(fields[0], dict) else {}
    field_key = field.get("key")
    if not isinstance(field_key, str) or not field_key:
        return None
    raw_options = field.get("options")
    options = tuple(
        BuilderOption(
            label=str(option.get("label", "")),
            description=str(option.get("description", "")),
        )
        for option in (raw_options or [])
        if isinstance(option, dict)
    )
    return BuilderQuestion(
        id=form_id,
        header=str(field.get("title", "")),
        question=str(field.get("description", "")),
        options=options,
        multiple=field.get("type") == "multiselect",
        custom=bool(field.get("custom", False)),
        session_id=session_id,
        field_key=field_key,
    )


def _ordinal(value: Any) -> int:
    """Coerce a v2 event ordinal to an int without ever raising (ADR-015)."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _event_from_v2_payload(
    payload: Any,
    accumulated: dict[tuple[str, int], str],
    pending_forms: dict[str, BuilderQuestion],
) -> BuilderEvent | None:
    """Map one decoded v2 SSE payload to a :class:`BuilderEvent` (ADR-015).

    ``session.text.delta`` chunks are accumulated per assistant message/ordinal
    and emitted as cumulative text (so the page replaces the current block);
    ``session.text.ended`` carries the full text; ``form.created`` becomes a
    question and is remembered for the reply; ``session.execution.succeeded``
    ends the turn and ``session.execution.failed`` surfaces an error. Unknown
    events are skipped, never raised.
    """
    if not isinstance(payload, dict):
        return None
    event_type = payload.get("type")
    if not isinstance(event_type, str):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        data = {}
    if event_type == "session.text.delta":
        key = (str(data.get("assistantMessageID", "")), _ordinal(data.get("ordinal")))
        accumulated[key] = accumulated.get(key, "") + str(data.get("delta", ""))
        return BuilderEvent(kind="text", text=accumulated[key])
    if event_type == "session.text.ended":
        key = (str(data.get("assistantMessageID", "")), _ordinal(data.get("ordinal")))
        text = data.get("text")
        if not isinstance(text, str):
            text = accumulated.get(key, "")
        accumulated[key] = text
        return BuilderEvent(kind="text", text=text)
    if event_type == "form.created":
        question = _question_from_form(data.get("form"))
        if question is not None:
            pending_forms[question.id] = question
            return BuilderEvent(kind="question", question=question)
        return None
    if event_type == "session.execution.succeeded":
        return BuilderEvent(kind="idle")
    if event_type == "session.execution.failed":
        detail = data.get("error") or data.get("message") or "the agent turn failed"
        return BuilderEvent(kind="error", text=str(detail))
    return None
