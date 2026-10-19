"""End-to-end integration test for the AI dashboard builder (DC-026/DC-031).

A stdlib ``http.server`` stub mimics the ``opencode serve`` API with the event
shapes verified against opencode v1.18.34: ``POST /session``, ``POST
/session/{id}/prompt_async``, ``GET /event`` (SSE), ``GET /question`` and
``POST /question/{id}/reply``. The real :class:`OpenCodeClient` -- default
urllib transport, nothing injected -- drives a full turn against it over a real
loopback socket, so URL construction, JSON bodies, HTTP status handling and SSE
framing are exercised end-to-end (the intended mitigation for opencode API
drift). The dashboard specification is extracted from the reply with the
page's own :func:`extract_spec`, parsed with :func:`parse_spec` and persisted
and reloaded by :class:`DashboardStore`.

A second stub mimics the opencode **v2** API (DC-028, ADR-015) -- HTTP Basic
auth, the ``/api`` prefix, ``session.text.delta``/``session.text.ended`` and
``form.created`` events, and the form reply endpoint -- and drives the real
version-aware client through a full turn against it, then extracts and
persists the fenced ```json spec the same way.

Hermetic by construction: loopback only, no ``opencode`` binary, no external
network, no writes outside ``tmp_path``.
"""

from __future__ import annotations

import base64
import json
import os
import queue
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from daylight.components.modules.builder_page import extract_spec
from daylight.managers.dashboard_spec import parse_spec
from daylight.managers.dashboard_store import DashboardStore
from daylight.managers.opencode_client import OpenCodeClient, OpenCodeError, OpenCodeServer

#: Seconds an SSE handler may wait for the next event before giving up. The
#: normal flow always has events or the sentinel waiting, so this is only a
#: defensive bound: a handler must never hang forever.
_SSE_IDLE_TIMEOUT = 30.0

#: The fixed ids the stub answers with (the client echoes them back).
SESSION_ID = "ses_stub_1"
QUESTION_ID = "que_stub_1"

#: The user's prompt and the answer picked for the clarifying question.
PROMPT = "Build a sales summary dashboard"
ANSWER = "sales.csv"

#: A ``question.asked`` request in the bundled shape opencode v1.18 emits.
_QUESTION_REQUEST: dict[str, Any] = {
    "id": QUESTION_ID,
    "sessionID": SESSION_ID,
    "questions": [
        {
            "question": "Which file should the dashboard summarize?",
            "header": "Data source",
            "options": [
                {"label": "sales.csv", "description": "The sales data"},
                {"label": "inventory.csv", "description": "The stock data"},
            ],
            "multiple": False,
            "custom": True,
        }
    ],
}

#: The fenced ```json dashboard specification the agent's reply carries
#: (DC-031): declarative blocks only, no code.
SPEC_JSON = (
    '{"schema_version": 1, "title": "Sales summary", "blocks":'
    ' [{"kind": "table", "source": "largest_files", "title": "Largest", "top_n": 5},'
    ' {"kind": "summary_cards", "source": "summary"}]}'
)

#: The agent's post-question reply: prose plus one fenced ```json spec.
ASSISTANT_REPLY = (
    "Here is your dashboard. It summarizes the working directory:\n"
    "\n"
    "```json\n" + SPEC_JSON + "\n```\n"
    "\n"
    "Press Ctrl+S to save it.\n"
)

# -- opencode v2 (DC-028, ADR-015) -----------------------------------------------

#: The password the v2 stub requires as HTTP Basic auth (``opencode:<password>``).
STUB_PASSWORD = "stub-password"

#: The fixed v2 ids the stub answers with (the client echoes them back).
FORM_ID = "frm_stub_1"
FORM_FIELD_KEY = "q0"

#: The two assistant message ids the stub streams (one per message, like v2).
PRE_QUESTION_MESSAGE_ID = "msg_stub_pre"
REPLY_MESSAGE_ID = "msg_stub_reply"

#: The pre-question text, streamed as two ``session.text.delta`` chunks.
PRE_QUESTION_DELTAS = ("Reading ", "the data directory…")
#: The full pre-question text the ``session.text.ended`` event carries.
PRE_QUESTION_TEXT = "".join(PRE_QUESTION_DELTAS)

#: The reply text, streamed as two deltas before its ``session.text.ended``.
REPLY_DELTAS = (ASSISTANT_REPLY[:24], ASSISTANT_REPLY[24:])


def _text_event(text: str) -> dict[str, Any]:
    """A ``message.part.updated`` payload carrying an assistant text part."""
    return {"type": "message.part.updated", "properties": {"part": {"type": "text", "text": text}}}


def _question_event() -> dict[str, Any]:
    """A ``question.asked`` payload with one bundled question request."""
    return {"type": "question.asked", "properties": _QUESTION_REQUEST}


def _idle_event() -> dict[str, Any]:
    """A ``session.idle`` payload: the turn is over."""
    return {"type": "session.idle", "properties": {"sessionID": SESSION_ID}}


def _v2_delta_event(message_id: str, delta: str) -> dict[str, Any]:
    """A ``session.text.delta`` payload: one incremental chunk of a message."""
    return {
        "type": "session.text.delta",
        "data": {"assistantMessageID": message_id, "ordinal": 0, "delta": delta},
    }


def _v2_text_ended_event(message_id: str, text: str) -> dict[str, Any]:
    """A ``session.text.ended`` payload carrying the full message text."""
    return {
        "type": "session.text.ended",
        "data": {"assistantMessageID": message_id, "ordinal": 0, "text": text},
    }


def _v2_form_event() -> dict[str, Any]:
    """A ``form.created`` payload with one string field (opencode v2.0.32)."""
    return {
        "type": "form.created",
        "data": {
            "form": {
                "id": FORM_ID,
                "sessionID": SESSION_ID,
                "title": "Questions",
                "fields": [
                    {
                        "key": FORM_FIELD_KEY,
                        "title": "Data source",
                        "description": "Which file should the dashboard summarize?",
                        "type": "string",
                        "options": [
                            {
                                "value": "sales.csv",
                                "label": "sales.csv",
                                "description": "The sales data",
                            },
                            {
                                "value": "inventory.csv",
                                "label": "inventory.csv",
                                "description": "The stock data",
                            },
                        ],
                        "custom": True,
                    }
                ],
            }
        },
    }


def _v2_execution_succeeded_event() -> dict[str, Any]:
    """A ``session.execution.succeeded`` payload: the turn is over."""
    return {"type": "session.execution.succeeded", "data": {"sessionID": SESSION_ID}}


class _StubOpenCode:
    """State shared by the stub handlers: recorded requests and scripted SSE.

    ``events`` is the queue the ``GET /event`` handler drains; ``None`` is the
    sentinel that ends the stream. ``pending`` holds the question requests
    ``GET /question`` reports until they are replied to.
    """

    def __init__(self) -> None:
        self.base_url = ""
        self.requests: list[tuple[str, str, dict[str, str], Any]] = []
        self.events: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self.pending: list[dict[str, Any]] = []
        self.replies: list[tuple[str, Any]] = []


class _StubHandler(BaseHTTPRequestHandler):
    """Routes the opencode API endpoints the builder uses (opencode v1.18).

    The default HTTP/1.0 framing keeps the SSE stream simple: the response has
    no ``Content-Length``, so the client reads until the connection closes,
    which is exactly how the stub ends the stream (sentinel or timeout).
    """

    def log_message(self, format: str, *args: object) -> None:
        """Silence per-request logging; pytest output stays clean."""
        del format, args

    @property
    def stub(self) -> _StubOpenCode:
        """The shared state, attached to the server by the fixture."""
        return self.server.stub  # type: ignore[attr-defined,no-any-return]

    def _record(self, body: Any) -> None:
        """Record one request as ``(method, path, query, decoded body)``."""
        path, _, query = self.path.partition("?")
        parsed = urllib.parse.parse_qs(query)
        flat = {key: values[0] for key, values in parsed.items()}
        self.stub.requests.append((self.command, path, flat, body))

    def _read_json(self) -> Any:
        """Decode the request body, or ``None`` when there is none."""
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length) if length else b""
        return json.loads(raw) if raw else None

    def _send_json(self, payload: Any, status: int = 200) -> None:
        """One JSON response with an explicit ``Content-Length``."""
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        body = self._read_json()
        self._record(body)
        path = self.path.partition("?")[0]
        if path == "/session":
            self._send_json({"id": SESSION_ID})
        elif path == f"/session/{SESSION_ID}/prompt_async":
            # The agent starts working: an assistant text part, then a
            # clarifying question -- both arrive on the SSE stream.
            self.stub.events.put(_text_event("Reading the data directory…"))
            self.stub.pending.append(_QUESTION_REQUEST)
            self.stub.events.put(_question_event())
            self._send_json({"id": "msg_stub_1"})
        elif path == f"/question/{QUESTION_ID}/reply":
            self.stub.replies.append((path, (body or {}).get("answers")))
            self.stub.pending.clear()
            # The agent continues: the reply text with the fenced script, then
            # the turn ends and the stream is closed via the sentinel.
            self.stub.events.put(_text_event(ASSISTANT_REPLY))
            self.stub.events.put(_idle_event())
            self.stub.events.put(None)
            self._send_json({})
        else:
            self._send_json({"error": "not found"}, status=404)

    def do_GET(self) -> None:
        self._record(None)
        path = self.path.partition("?")[0]
        if path == "/event":
            self._event_stream()
        elif path == "/question":
            self._send_json(list(self.stub.pending))
        else:
            self._send_json({"error": "not found"}, status=404)

    def _event_stream(self) -> None:
        """``GET /event``: stream scripted payloads as SSE until the sentinel.

        Framing mirrors the real server: an ``event:`` line, a ``data:`` line
        with the compact JSON payload, then a blank line. A client that stops
        reading early (it saw the idle event) just breaks the write.
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        while True:
            try:
                event = self.stub.events.get(timeout=_SSE_IDLE_TIMEOUT)
            except queue.Empty:
                break  # defensive: a handler must never hang forever
            if event is None:
                break
            try:
                self.wfile.write(f"event: {event['type']}\n".encode("utf-8"))
                payload = json.dumps(event, separators=(",", ":"))
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
            except OSError:
                break  # the client stopped reading; nothing more to stream


class _StubServer(ThreadingHTTPServer):
    """A threading HTTP server; handler threads are daemon threads."""

    daemon_threads = True


class _StubV2OpenCode:
    """State shared by the v2 stub handlers: recorded requests and scripted SSE.

    ``events`` is the queue the ``GET /api/event`` handler drains; ``None`` is
    the sentinel that ends the stream.
    """

    def __init__(self) -> None:
        self.base_url = ""
        self.requests: list[tuple[str, str, dict[str, str], Any]] = []
        self.events: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self.replies: list[tuple[str, Any]] = []


class _StubV2Handler(_StubHandler):
    """Routes the opencode v2 API endpoints the builder uses (v2.0.32, ADR-015).

    Every request must carry HTTP Basic auth (``opencode:<password>``) and the
    JSON API lives under ``/api``. The SSE framing, request recording and JSON
    helpers are inherited from the v1 handler.
    """

    def _authorized(self) -> bool:
        """True when the request carries the stub's Basic auth credentials."""
        token = base64.b64encode(f"opencode:{STUB_PASSWORD}".encode("utf-8")).decode("ascii")
        return self.headers.get("Authorization") == f"Basic {token}"

    def _send_empty(self, status: int) -> None:
        """A bodyless response (the real v2 form reply answers 204)."""
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:
        body = self._read_json()
        if not self._authorized():
            self._send_json({"error": "unauthorized"}, status=401)
            return
        self._record(body)
        path = self.path.partition("?")[0]
        if path == "/api/session":
            self._send_json({"data": {"id": SESSION_ID}})
        elif path == f"/api/session/{SESSION_ID}/prompt":
            # The agent starts working: incremental text deltas, the ended
            # event, then a clarifying form -- all on the SSE stream.
            for delta in PRE_QUESTION_DELTAS:
                self.stub.events.put(_v2_delta_event(PRE_QUESTION_MESSAGE_ID, delta))
            self.stub.events.put(_v2_text_ended_event(PRE_QUESTION_MESSAGE_ID, PRE_QUESTION_TEXT))
            self.stub.events.put(_v2_form_event())
            self._send_json({})
        elif path == f"/api/session/{SESSION_ID}/form/{FORM_ID}/reply":
            self.stub.replies.append((path, body))
            # The agent continues: the reply text with the fenced script, then
            # the turn ends and the stream is closed via the sentinel.
            for delta in REPLY_DELTAS:
                self.stub.events.put(_v2_delta_event(REPLY_MESSAGE_ID, delta))
            self.stub.events.put(_v2_text_ended_event(REPLY_MESSAGE_ID, ASSISTANT_REPLY))
            self.stub.events.put(_v2_execution_succeeded_event())
            self.stub.events.put(None)
            self._send_empty(204)
        else:
            self._send_json({"error": "not found"}, status=404)

    def do_GET(self) -> None:
        if not self._authorized():
            self._send_json({"error": "unauthorized"}, status=401)
            return
        self._record(None)
        path = self.path.partition("?")[0]
        if path == "/api/event":
            self._event_stream()
        else:
            self._send_json({"error": "not found"}, status=404)


@pytest.fixture()
def stub_opencode() -> Iterator[_StubOpenCode]:
    """A stub opencode server on an ephemeral loopback port (AC4).

    Bound to ``127.0.0.1:0``, served from a daemon thread, and torn down
    deterministically: the serve loop is stopped, the listening socket is
    closed, and the thread is joined -- asserted, so nothing leaks between
    tests.
    """
    stub = _StubOpenCode()
    server = _StubServer(("127.0.0.1", 0), _StubHandler)
    server.stub = stub  # attached before any request can arrive
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="stub-opencode")
    thread.start()
    stub.base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield stub
    finally:
        stub.events.put(None)  # release a handler blocked on the queue, if any
        server.shutdown()
        server.server_close()
        thread.join(timeout=5.0)
        assert not thread.is_alive(), "stub opencode server thread did not stop"


@pytest.fixture()
def stub_opencode_v2() -> Iterator[_StubV2OpenCode]:
    """A stub opencode v2 server on an ephemeral loopback port (DC-028 AC3).

    Requires HTTP Basic auth on every request, serves the JSON API under
    ``/api``, and streams the v2 event shapes. Bound to ``127.0.0.1:0``, served
    from a daemon thread, and torn down deterministically in ``finally``: the
    serve loop is stopped, the listening socket is closed, and the thread is
    joined -- asserted, so nothing leaks between tests.
    """
    stub = _StubV2OpenCode()
    server = _StubServer(("127.0.0.1", 0), _StubV2Handler)
    server.stub = stub  # attached before any request can arrive
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="stub-opencode-v2")
    thread.start()
    stub.base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield stub
    finally:
        stub.events.put(None)  # release a handler blocked on the queue, if any
        server.shutdown()
        server.server_close()
        thread.join(timeout=5.0)
        assert not thread.is_alive(), "stub opencode v2 server thread did not stop"


def test_full_turn_from_prompt_to_saved_dashboard(
    tmp_path: Path, stub_opencode: _StubOpenCode
) -> None:
    """The real client drives prompt → question → reply → spec → store (AC1-3)."""
    started = time.monotonic()
    client = OpenCodeClient(stub_opencode.base_url)  # default urllib transport: a real socket

    session_id = client.create_session(directory=str(tmp_path), title=PROMPT)
    assert session_id == SESSION_ID

    # The stream is opened before the prompt is sent, exactly like the page
    # does, so no early event can be missed.
    lines = client.open_event_stream()
    client.prompt_async(session_id, PROMPT)

    texts: list[str] = []
    for event in client.stream_events(lines):
        if event.kind == "text":
            texts.append(event.text)
        elif event.kind == "question" and event.question is not None:
            # While the question is pending, GET /question lists it (AC1).
            pending = client.list_questions()
            assert [question.id for question in pending] == [QUESTION_ID]
            assert pending[0].question == "Which file should the dashboard summarize?"
            client.reply_question(event.question.id, [[ANSWER]])
        elif event.kind == "idle":
            break
    # After the reply the question is no longer pending (AC1).
    assert client.list_questions() == []

    assert len(texts) == 2, f"expected pre-question text and reply, got {texts!r}"
    assert "Reading the data directory" in texts[0]
    # The fenced spec is extracted and parses against the strict validator.
    raw = extract_spec(texts[1])
    assert raw is not None
    spec = parse_spec(raw)
    assert spec.title == "Sales summary"
    assert [block.kind for block in spec.blocks] == ["table", "summary_cards"]
    assert [block.source for block in spec.blocks] == ["largest_files", "summary"]

    # The stub saw exactly the requests the real API contract implies.
    methods_paths = [(method, path) for method, path, _query, _body in stub_opencode.requests]
    assert methods_paths == [
        ("POST", "/session"),
        ("GET", "/event"),
        ("POST", f"/session/{SESSION_ID}/prompt_async"),
        ("GET", "/question"),
        ("POST", f"/question/{QUESTION_ID}/reply"),
        ("GET", "/question"),
    ]
    assert stub_opencode.requests[0][2] == {"directory": str(tmp_path)}
    assert stub_opencode.requests[0][3] == {"title": PROMPT}
    assert stub_opencode.requests[2][3] == {"parts": [{"type": "text", "text": PROMPT}]}
    assert stub_opencode.requests[4][3] == {"answers": [[ANSWER]]}
    assert stub_opencode.replies == [(f"/question/{QUESTION_ID}/reply", [[ANSWER]])]

    # Persist and reload: the spec round-trips identically (DC-031 AC6).
    store = DashboardStore(root=tmp_path / "dashboards")
    saved = store.save(name="Sales dashboard", prompt=PROMPT, spec=spec, directory=str(tmp_path))
    assert [artifact.id for artifact in store.list()] == [saved.id]
    loaded = store.load(saved.id)
    assert loaded == saved
    assert loaded.spec is not None and loaded.spec == spec
    assert loaded.directory == str(tmp_path)
    assert loaded.prompt == PROMPT
    assert time.monotonic() - started < 60.0  # a full turn is fast; it never hangs


def test_full_turn_from_prompt_to_saved_dashboard_v2(
    tmp_path: Path, stub_opencode_v2: _StubV2OpenCode
) -> None:
    """The real v2 client drives prompt → form → reply → spec → store (AC3-4)."""
    started = time.monotonic()
    client = OpenCodeClient(
        stub_opencode_v2.base_url, password=STUB_PASSWORD, api_version="v2"
    )  # default urllib transport: a real socket

    # The stub rejects requests without Basic auth (AC3).
    anonymous = urllib.request.Request(
        f"{stub_opencode_v2.base_url}/api/session", data=b"{}", method="POST"
    )
    with pytest.raises(urllib.error.HTTPError) as raised:
        urllib.request.urlopen(anonymous)
    assert raised.value.code == 401

    session_id = client.create_session(directory=str(tmp_path), title=PROMPT)
    assert session_id == SESSION_ID

    # The stream is opened before the prompt is sent, exactly like the page
    # does, so no early event can be missed.
    lines = client.open_event_stream()
    client.prompt_async(session_id, PROMPT)

    texts: list[str] = []
    for event in client.stream_events(lines):
        if event.kind == "text":
            texts.append(event.text)
        elif event.kind == "question" and event.question is not None:
            # The v2 form arrived normalized into the BuilderQuestion model.
            question = event.question
            assert question.id == FORM_ID
            assert question.session_id == SESSION_ID
            assert question.field_key == FORM_FIELD_KEY
            assert question.header == "Data source"
            assert question.question == "Which file should the dashboard summarize?"
            client.reply_question(question.id, [[ANSWER]])
        elif event.kind == "idle":
            break

    # The deltas streamed incrementally; each ended event carried the full text.
    assert texts[0] == PRE_QUESTION_DELTAS[0]
    assert texts[2] == PRE_QUESTION_TEXT
    assert texts[-1] == ASSISTANT_REPLY

    # The stub saw exactly the v2 requests the API contract implies, in order.
    methods_paths = [(method, path) for method, path, _query, _body in stub_opencode_v2.requests]
    assert methods_paths == [
        ("POST", "/api/session"),
        ("GET", "/api/event"),
        ("POST", f"/api/session/{SESSION_ID}/prompt"),
        ("POST", f"/api/session/{SESSION_ID}/form/{FORM_ID}/reply"),
    ]
    assert stub_opencode_v2.requests[0][3] == {
        "title": PROMPT,
        "location": {"directory": str(tmp_path)},
    }
    assert stub_opencode_v2.requests[2][3] == {"text": PROMPT}
    assert stub_opencode_v2.requests[3][3] == {"answer": {FORM_FIELD_KEY: ANSWER}}
    assert stub_opencode_v2.replies == [
        (f"/api/session/{SESSION_ID}/form/{FORM_ID}/reply", {"answer": {FORM_FIELD_KEY: ANSWER}})
    ]

    # The extracted spec parses against the strict validator (DC-031 AC2).
    raw = extract_spec(texts[-1])
    assert raw is not None
    spec = parse_spec(raw)
    assert spec.title == "Sales summary"
    assert [block.kind for block in spec.blocks] == ["table", "summary_cards"]

    # Save and reload: the spec round-trips identically (DC-031 AC6).
    store = DashboardStore(root=tmp_path / "dashboards")
    saved = store.save(name="Sales dashboard v2", prompt=PROMPT, spec=spec, directory=str(tmp_path))
    assert [artifact.id for artifact in store.list()] == [saved.id]
    loaded = store.load(saved.id)
    assert loaded == saved
    assert loaded.spec is not None and loaded.spec == spec
    assert loaded.directory == str(tmp_path)
    assert loaded.prompt == PROMPT
    assert time.monotonic() - started < 60.0  # a full turn is fast; it never hangs


def _pid_is_dead(pidfile: Path) -> bool:
    """True when the pid recorded in ``pidfile`` is no longer alive."""
    pid = int(pidfile.read_text(encoding="utf-8"))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:  # alive but owned by someone else
        return False
    time.sleep(0.05)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def test_missing_opencode_binary_raises_without_hanging_or_leaking() -> None:
    """A missing ``opencode`` binary fails fast with OpenCodeError (AC5)."""
    server = OpenCodeServer(command=("definitely-not-opencode-dc026", "serve"))
    started = time.monotonic()
    with pytest.raises(OpenCodeError, match="cannot start"):
        server.start()
    assert time.monotonic() - started < 5.0
    assert server.base_url is None
    assert server._process is None  # nothing was spawned, nothing to leak


def test_server_that_dies_at_startup_raises_and_reaps_the_child(tmp_path: Path) -> None:
    """A server exiting before announcing its URL raises and is reaped (AC5)."""
    pidfile = tmp_path / "child.pid"
    script = (
        "import os\n"
        "from pathlib import Path\n"
        f"Path({str(pidfile)!r}).write_text(str(os.getpid()), encoding='utf-8')\n"
        "raise SystemExit(1)\n"
    )
    server = OpenCodeServer(command=(sys.executable, "-c", script), startup_timeout=5.0)
    started = time.monotonic()
    with pytest.raises(OpenCodeError, match="exited before announcing"):
        server.start()
    assert time.monotonic() - started < 5.0
    assert _pid_is_dead(pidfile)


def test_unreachable_server_surfaces_as_opencode_error() -> None:
    """A refused loopback connection reaches the caller as OpenCodeError (AC5)."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    client = OpenCodeClient(f"http://127.0.0.1:{port}")
    with pytest.raises(OpenCodeError, match="failed"):
        client.create_session()
    with pytest.raises(OpenCodeError, match="cannot open the event stream"):
        client.open_event_stream()
