"""Unit tests for the opencode server/client harness (DC-024).

Hermetic by construction: the client is exercised through a fake transport
(no socket), the SSE parser through fake line iterators, and the server
through tiny ``sys.executable`` child scripts (no ``opencode`` binary, no
network).
"""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from daylight.managers.opencode_client import (
    OpenCodeClient,
    OpenCodeError,
    OpenCodeServer,
    _urllib_event_opener,
    _urllib_transport,
)
from daylight.models import BuilderEvent, BuilderOption, BuilderQuestion

BASE = "http://127.0.0.1:4096"


class FakeTransport:
    """Records every call and replays scripted ``(status, body)`` responses."""

    def __init__(self, *responses: tuple[int, bytes]) -> None:
        self.calls: list[tuple[str, str, bytes | None, dict[str, str]]] = []
        self.responses = list(responses)

    def __call__(
        self,
        method: str,
        url: str,
        body: bytes | None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes]:
        self.calls.append((method, url, body, dict(headers or {})))
        if self.responses:
            return self.responses.pop(0)
        return 200, b"{}"

    def last_body(self) -> dict:
        """The JSON body of the most recent call."""
        raw = self.calls[-1][2]
        assert raw is not None
        return json.loads(raw.decode("utf-8"))


# -- client: request paths and bodies ----------------------------------------


def test_create_session_posts_directory_query_and_title_body() -> None:
    transport = FakeTransport((200, json.dumps({"id": "ses_1"}).encode()))
    client = OpenCodeClient(BASE, transport=transport)

    session_id = client.create_session(directory="/tmp/data", title="My dashboard")

    assert session_id == "ses_1"
    method, url, _body, _headers = transport.calls[0]
    assert (method, url) == ("POST", f"{BASE}/session?directory=%2Ftmp%2Fdata")
    assert transport.last_body() == {"title": "My dashboard"}


def test_create_session_omits_absent_fields() -> None:
    transport = FakeTransport((200, json.dumps({"id": "ses_2"}).encode()))
    client = OpenCodeClient(BASE, transport=transport)

    assert client.create_session() == "ses_2"
    assert transport.calls[0][1] == f"{BASE}/session"
    assert transport.last_body() == {}


def test_create_session_without_id_raises() -> None:
    client = OpenCodeClient(BASE, transport=FakeTransport((200, b'{"title": "x"}')))

    with pytest.raises(OpenCodeError):
        client.create_session(title="x")


def test_prompt_async_posts_one_text_part() -> None:
    transport = FakeTransport((200, b'{"id": "msg_1"}'))
    client = OpenCodeClient(BASE, transport=transport)

    client.prompt_async("ses_1", "build me a dashboard")

    method, url, _body, _headers = transport.calls[0]
    assert (method, url) == ("POST", f"{BASE}/session/ses_1/prompt_async")
    assert transport.last_body() == {"parts": [{"type": "text", "text": "build me a dashboard"}]}


def test_list_questions_normalizes_bundled_request() -> None:
    payload = [
        {
            "id": "que_1",
            "sessionID": "ses_1",
            "questions": [
                {
                    "question": "Which data?",
                    "header": "Data source",
                    "options": [
                        {"label": "CSV", "description": "A csv file"},
                        {"label": "JSON", "description": "A json file"},
                    ],
                    "multiple": True,
                    "custom": True,
                }
            ],
        }
    ]
    transport = FakeTransport((200, json.dumps(payload).encode()))
    client = OpenCodeClient(BASE, transport=transport)

    questions = client.list_questions()

    assert questions == [
        BuilderQuestion(
            id="que_1",
            header="Data source",
            question="Which data?",
            options=(
                BuilderOption(label="CSV", description="A csv file"),
                BuilderOption(label="JSON", description="A json file"),
            ),
            multiple=True,
            custom=True,
        )
    ]


def test_list_questions_accepts_flat_items_and_skips_malformed() -> None:
    payload = [
        {"id": "que_2", "question": "Flat?", "header": "H", "options": []},
        "not-a-dict",
        {"no-id": True},
    ]
    transport = FakeTransport((200, json.dumps(payload).encode()))
    client = OpenCodeClient(BASE, transport=transport)

    questions = client.list_questions()

    assert [question.id for question in questions] == ["que_2"]
    assert questions[0].multiple is False
    assert questions[0].custom is False


def test_list_questions_non_list_raises() -> None:
    client = OpenCodeClient(BASE, transport=FakeTransport((200, b'{"oops": 1}')))

    with pytest.raises(OpenCodeError):
        client.list_questions()


def test_reply_question_posts_answers_matrix() -> None:
    transport = FakeTransport()
    client = OpenCodeClient(BASE, transport=transport)

    client.reply_question("que_1", [["CSV"], ["A", "B"]])

    method, url, _body, _headers = transport.calls[0]
    assert (method, url) == ("POST", f"{BASE}/question/que_1/reply")
    assert transport.last_body() == {"answers": [["CSV"], ["A", "B"]]}


def test_list_permissions_returns_dicts() -> None:
    payload = [{"id": "per_1", "permission": "edit", "patterns": ["*"]}, "junk"]
    transport = FakeTransport((200, json.dumps(payload).encode()))
    client = OpenCodeClient(BASE, transport=transport)

    permissions = client.list_permissions()

    assert permissions == [{"id": "per_1", "permission": "edit", "patterns": ["*"]}]


def test_reply_permission_posts_reply_field() -> None:
    transport = FakeTransport()
    client = OpenCodeClient(BASE, transport=transport)

    client.reply_permission("per_1", "once")

    method, url, _body, _headers = transport.calls[0]
    assert (method, url) == ("POST", f"{BASE}/permission/per_1/reply")
    assert transport.last_body() == {"reply": "once"}


def test_messages_gets_session_messages() -> None:
    transport = FakeTransport((200, json.dumps([{"id": "msg_1"}]).encode()))
    client = OpenCodeClient(BASE, transport=transport)

    assert client.messages("ses_1") == [{"id": "msg_1"}]
    assert transport.calls[0][:3] == ("GET", f"{BASE}/session/ses_1/message", None)


def test_http_error_status_raises_opencode_error() -> None:
    client = OpenCodeClient(BASE, transport=FakeTransport((500, b"boom")))

    with pytest.raises(OpenCodeError, match="HTTP 500"):
        client.messages("ses_1")


def test_invalid_json_raises_opencode_error() -> None:
    client = OpenCodeClient(BASE, transport=FakeTransport((200, b"not json")))

    with pytest.raises(OpenCodeError, match="invalid JSON"):
        client.messages("ses_1")


def test_empty_response_body_returns_none() -> None:
    client = OpenCodeClient(BASE, transport=FakeTransport((200, b"")))

    assert client.reply_question("que_1", []) is None


# -- client: default transport (urllib, monkeypatched, no socket) -------------


class _FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


def test_default_transport_builds_urllib_request(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, urllib.request.Request] = {}

    def fake_urlopen(request: urllib.request.Request, timeout: float = 30) -> _FakeResponse:
        captured["request"] = request
        return _FakeResponse(201, b'{"id": "ses_9"}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    status, body = _urllib_transport("POST", f"{BASE}/session", b'{"title": "x"}')

    assert (status, json.loads(body)) == (201, {"id": "ses_9"})
    request = captured["request"]
    assert request.method == "POST"
    assert request.full_url == f"{BASE}/session"
    assert request.data == b'{"title": "x"}'
    assert request.get_header("Content-type") == "application/json"


def test_default_transport_returns_http_error_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request: urllib.request.Request, timeout: float = 30) -> _FakeResponse:
        raise urllib.error.HTTPError(request.full_url, 404, "nope", {}, io.BytesIO(b"missing"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    assert _urllib_transport("GET", f"{BASE}/question", None) == (404, b"missing")


def test_client_defaults_to_urllib_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda request, timeout=30: _FakeResponse(200, b'{"id": "ses_8"}'),
    )
    client = OpenCodeClient(BASE)

    assert client.create_session() == "ses_8"


# -- client: SSE event parsing ------------------------------------------------


def sse(payload: dict) -> list[str]:
    """One SSE event as the line list a server would emit."""
    return [f"data: {json.dumps(payload, separators=(',', ':'))}", ""]


def text_event(text: str) -> dict:
    """A ``message.part.updated`` payload carrying an assistant text part."""
    return {"type": "message.part.updated", "properties": {"part": {"type": "text", "text": text}}}


def question_event() -> dict:
    """A ``question.asked`` payload with one bundled question."""
    return {
        "type": "question.asked",
        "properties": {
            "id": "que_1",
            "sessionID": "ses_1",
            "questions": [
                {
                    "question": "Which data?",
                    "header": "Data",
                    "options": [{"label": "CSV", "description": ""}],
                }
            ],
        },
    }


def test_stream_events_parses_text_question_idle_in_order() -> None:
    lines = [
        ": keepalive comment",
        "event: message.part.updated",
        *sse(text_event("Working on it")),
        *sse(question_event()),
        *sse(text_event("Done")),
        *sse({"type": "session.idle", "properties": {"sessionID": "ses_1"}}),
        *sse(text_event("never")),
    ]
    client = OpenCodeClient(BASE)

    events = list(client.stream_events(lines))

    assert events == [
        BuilderEvent(kind="text", text="Working on it"),
        BuilderEvent(
            kind="question",
            question=BuilderQuestion(
                id="que_1",
                header="Data",
                question="Which data?",
                options=(BuilderOption(label="CSV", description=""),),
            ),
        ),
        BuilderEvent(kind="text", text="Done"),
        BuilderEvent(kind="idle"),
    ]


def test_stream_events_skips_malformed_and_unknown_lines() -> None:
    lines = [
        "this is not sse at all",
        "data: not-json",
        "",
        'data: {"type":"message.part.updated"}',  # no part -> skipped
        "",
        'data: {"type":"message.part.updated","properties":{"part":{"type":"tool"}}}',
        "",
        'data: {"type":"file.edited","properties":{}}',  # unknown type
        "",
        "data: [1, 2, 3]",  # valid JSON, not an object
        "",
        *sse(text_event("ok")),
    ]
    client = OpenCodeClient(BASE)

    # The stream never announced idle, so the parser synthesizes it at the end.
    assert list(client.stream_events(lines)) == [
        BuilderEvent(kind="text", text="ok"),
        BuilderEvent(kind="idle"),
    ]


def test_stream_events_joins_multi_data_lines_and_final_event() -> None:
    lines = [
        "data: {",
        'data: "type": "session.idle",',
        'data: "properties": {"sessionID": "ses_1"}',
        "data: }",
    ]
    client = OpenCodeClient(BASE)

    assert list(client.stream_events(lines)) == [BuilderEvent(kind="idle")]


def test_stream_events_synthesizes_idle_when_stream_ends() -> None:
    client = OpenCodeClient(BASE)

    assert list(client.stream_events(sse(text_event("partial")))) == [
        BuilderEvent(kind="text", text="partial"),
        BuilderEvent(kind="idle"),
    ]


def test_stream_events_on_empty_stream_yields_idle() -> None:
    client = OpenCodeClient(BASE)

    assert list(client.stream_events([])) == [BuilderEvent(kind="idle")]


# -- client: manager-level SSE event-stream opener (DC-025, F1/F3) ------------


def test_open_event_stream_returns_the_opener_lines() -> None:
    lines = ["data: one\n", "\n", "data: two\n"]

    def fake_opener(url: str, headers: dict[str, str] | None = None) -> Iterator[str]:
        assert url == f"{BASE}/event"
        return iter(lines)

    client = OpenCodeClient(BASE)

    assert list(client.open_event_stream(opener=fake_opener)) == lines


def test_open_event_stream_wraps_connection_errors() -> None:
    """A URLError from the opener surfaces as OpenCodeError (F1)."""

    def refusing_opener(url: str, headers: dict[str, str] | None = None) -> Iterator[str]:
        del url
        raise urllib.error.URLError("connection refused")

    client = OpenCodeClient(BASE)

    with pytest.raises(OpenCodeError, match="connection refused"):
        client.open_event_stream(opener=refusing_opener)


def test_open_event_stream_feeds_stream_events() -> None:
    lines = sse(text_event("hi"))
    client = OpenCodeClient(BASE)

    events = list(
        client.stream_events(client.open_event_stream(opener=lambda url, headers=None: iter(lines)))
    )

    assert events == [BuilderEvent(kind="text", text="hi"), BuilderEvent(kind="idle")]


class _FakeStreamResponse:
    """A byte-line response like ``http.client.HTTPResponse`` in a SSE GET."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.closed = False

    def __iter__(self) -> Iterator[bytes]:
        return iter(self._chunks)

    def close(self) -> None:
        self.closed = True


def test_default_event_opener_decodes_response_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _FakeStreamResponse([b"data: 1\n", b"\n", b"data: 2\n"])
    captured: dict[str, urllib.request.Request] = {}

    def fake_urlopen(request: urllib.request.Request, timeout: float | None = None) -> object:
        captured["request"] = request
        return response

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    lines = list(_urllib_event_opener(f"{BASE}/event"))

    assert lines == ["data: 1\n", "\n", "data: 2\n"]
    assert captured["request"].method == "GET"
    assert captured["request"].full_url == f"{BASE}/event"
    assert response.closed  # the iterator closes the response when exhausted


def test_default_event_opener_wraps_url_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(request: urllib.request.Request, timeout: float | None = None) -> object:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    client = OpenCodeClient(BASE)

    with pytest.raises(OpenCodeError, match="cannot open the event stream"):
        client.open_event_stream()


def test_request_wraps_url_error_as_opencode_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every client call turns a connection failure into OpenCodeError (F1)."""

    def refuse(request: urllib.request.Request, timeout: float | None = None) -> object:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    client = OpenCodeClient(BASE)

    with pytest.raises(OpenCodeError, match="connection refused"):
        client.create_session()


# -- server: subprocess lifecycle ---------------------------------------------

CHILD_TEMPLATE = """\
import os, time
Path({pidfile!r}).write_text(str(os.getpid()), encoding="utf-8")
print({banner!r}, flush=True)
time.sleep(60)
"""


def _child_command(tmp_path: Path, banner: str) -> tuple[str, ...]:
    pidfile = tmp_path / "child.pid"
    script = "from pathlib import Path\n" + CHILD_TEMPLATE.format(
        pidfile=str(pidfile), banner=banner
    )
    return ("-c", script)  # completed by the caller with sys.executable


def _pid_is_dead(pidfile: Path) -> bool:
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


def test_server_discovers_base_url_and_stops(tmp_path: Path) -> None:
    command = (sys.executable,) + _child_command(
        tmp_path, "opencode server listening on http://127.0.0.1:43210"
    )
    server = OpenCodeServer(command=command, startup_timeout=5.0)

    base_url = server.start()

    assert base_url == "http://127.0.0.1:43210"
    assert server.base_url == base_url
    server.stop()
    assert server.base_url is None
    assert _pid_is_dead(tmp_path / "child.pid")


def test_server_start_is_idempotent_while_running(tmp_path: Path) -> None:
    command = (sys.executable,) + _child_command(tmp_path, "listening on http://127.0.0.1:43211")
    server = OpenCodeServer(command=command, startup_timeout=5.0)

    assert server.start() == "http://127.0.0.1:43211"
    assert server.start() == "http://127.0.0.1:43211"
    server.stop()
    server.stop()  # idempotent


def test_server_missing_binary_raises_without_leak() -> None:
    server = OpenCodeServer(command=("definitely-not-a-binary-dc024", "serve"))

    with pytest.raises(OpenCodeError, match="cannot start"):
        server.start()
    assert server.base_url is None
    assert server._process is None


def test_server_startup_timeout_raises_and_terminates(tmp_path: Path) -> None:
    command = (sys.executable,) + _child_command(tmp_path, "no url here, just booting")
    server = OpenCodeServer(command=command, startup_timeout=0.5)

    started = time.monotonic()
    with pytest.raises(OpenCodeError, match="did not announce"):
        server.start()
    assert time.monotonic() - started < 5.0
    assert _pid_is_dead(tmp_path / "child.pid")


def test_server_early_exit_raises(tmp_path: Path) -> None:
    script = "print('dying', flush=True)\n"
    server = OpenCodeServer(command=(sys.executable, "-c", script), startup_timeout=5.0)

    with pytest.raises(OpenCodeError, match="exited before announcing"):
        server.start()
    assert server._process is None


def test_server_context_manager_stops(tmp_path: Path) -> None:
    command = (sys.executable,) + _child_command(tmp_path, "listening on http://127.0.0.1:43212")
    with OpenCodeServer(command=command, startup_timeout=5.0) as server:
        assert server.base_url == "http://127.0.0.1:43212"
    assert server.base_url is None
    assert _pid_is_dead(tmp_path / "child.pid")


# -- v2: version detection, auth, endpoints, events, forms (DC-027, ADR-015) --


def _v2_child_command(tmp_path: Path, url: str, password: str) -> tuple[str, ...]:
    """A child that prints the v2 startup lines (listening + password)."""
    pidfile = tmp_path / "child.pid"
    script = (
        "from pathlib import Path\n"
        "import os, time\n"
        f"Path({str(pidfile)!r}).write_text(str(os.getpid()), encoding='utf-8')\n"
        f"print('server listening on {url}', flush=True)\n"
        f"print('server password {password}', flush=True)\n"
        "time.sleep(60)\n"
    )
    return (sys.executable, "-c", script)


def test_server_detects_v2_and_exposes_password(tmp_path: Path) -> None:
    command = _v2_child_command(tmp_path, "http://127.0.0.1:43212", "s3cret")
    server = OpenCodeServer(command=command, startup_timeout=5.0)

    assert server.start() == "http://127.0.0.1:43212"
    assert server.api_version == "v2"
    assert server.password == "s3cret"
    server.stop()
    assert server.api_version == "v1"
    assert server.password is None


def test_server_detects_v1_without_password(tmp_path: Path) -> None:
    command = (sys.executable,) + _child_command(
        tmp_path, "opencode server listening on http://127.0.0.1:43213"
    )
    server = OpenCodeServer(command=command, startup_timeout=5.0)

    assert server.start() == "http://127.0.0.1:43213"
    assert server.api_version == "v1"
    assert server.password is None
    server.stop()


def test_v2_create_session_uses_api_prefix_and_basic_auth() -> None:
    transport = FakeTransport((200, json.dumps({"data": {"id": "ses_v2"}}).encode()))
    client = OpenCodeClient(BASE, password="s3cret", api_version="v2", transport=transport)

    assert client.create_session(directory="/tmp", title="T") == "ses_v2"

    method, url, body, headers = transport.calls[0]
    assert (method, url) == ("POST", f"{BASE}/api/session")
    assert json.loads(body) == {"title": "T", "location": {"directory": "/tmp"}}
    token = headers["Authorization"].split(" ", 1)[1]
    assert base64.b64decode(token).decode("utf-8") == "opencode:s3cret"


def test_v2_prompt_posts_text() -> None:
    transport = FakeTransport((200, b"{}"))
    client = OpenCodeClient(BASE, password="s3cret", api_version="v2", transport=transport)

    client.prompt_async("ses_v2", "hello")

    method, url, body, _headers = transport.calls[0]
    assert (method, url) == ("POST", f"{BASE}/api/session/ses_v2/prompt")
    assert json.loads(body) == {"text": "hello"}


def test_v2_list_questions_reads_forms() -> None:
    payload = {
        "data": [
            {
                "id": "frm_1",
                "sessionID": "ses_v2",
                "title": "Questions",
                "fields": [
                    {
                        "key": "q0",
                        "title": "Data",
                        "description": "Which?",
                        "type": "string",
                        "options": [{"value": "a", "label": "A", "description": ""}],
                        "custom": True,
                    }
                ],
            }
        ]
    }
    transport = FakeTransport((200, json.dumps(payload).encode()))
    client = OpenCodeClient(BASE, password="s3cret", api_version="v2", transport=transport)

    questions = client.list_questions()

    assert questions == [
        BuilderQuestion(
            id="frm_1",
            header="Data",
            question="Which?",
            options=(BuilderOption(label="A", description=""),),
            custom=True,
            session_id="ses_v2",
            field_key="q0",
        )
    ]
    assert transport.calls[0][1] == f"{BASE}/api/form"


def test_v2_stream_events_accumulates_text_and_emits_form() -> None:
    lines = [
        *sse(
            {
                "type": "session.text.delta",
                "data": {"assistantMessageID": "m1", "ordinal": 0, "delta": "Hel"},
            }
        ),
        *sse(
            {
                "type": "session.text.delta",
                "data": {"assistantMessageID": "m1", "ordinal": 0, "delta": "lo"},
            }
        ),
        *sse(
            {
                "type": "session.text.ended",
                "data": {"assistantMessageID": "m1", "ordinal": 0, "text": "Hello"},
            }
        ),
        *sse(
            {
                "type": "form.created",
                "data": {
                    "form": {
                        "id": "frm_1",
                        "sessionID": "ses_v2",
                        "title": "Questions",
                        "fields": [
                            {
                                "key": "q0",
                                "title": "Data",
                                "description": "Which?",
                                "type": "string",
                                "options": [{"value": "a", "label": "A", "description": ""}],
                                "custom": True,
                            }
                        ],
                    }
                },
            }
        ),
        *sse({"type": "session.execution.succeeded", "data": {"sessionID": "ses_v2"}}),
    ]
    client = OpenCodeClient(BASE, password="s3cret", api_version="v2")

    events = list(client.stream_events(lines))

    assert events[0] == BuilderEvent(kind="text", text="Hel")
    assert events[1] == BuilderEvent(kind="text", text="Hello")
    assert events[2] == BuilderEvent(kind="text", text="Hello")
    assert events[3].kind == "question"
    assert events[3].question is not None
    assert events[3].question.id == "frm_1"
    assert events[3].question.session_id == "ses_v2"
    assert events[3].question.field_key == "q0"
    assert events[4] == BuilderEvent(kind="idle")


def test_v2_reply_question_posts_form_answer() -> None:
    transport = FakeTransport((200, b"{}"))
    client = OpenCodeClient(BASE, password="s3cret", api_version="v2", transport=transport)
    form = {
        "type": "form.created",
        "data": {
            "form": {
                "id": "frm_1",
                "sessionID": "ses_v2",
                "title": "Questions",
                "fields": [
                    {
                        "key": "q0",
                        "title": "Data",
                        "description": "Which?",
                        "type": "string",
                        "options": [],
                        "custom": True,
                    }
                ],
            }
        },
    }
    list(client.stream_events(sse(form)))

    client.reply_question("frm_1", [["A"]])

    method, url, body, _headers = transport.calls[0]
    assert (method, url) == ("POST", f"{BASE}/api/session/ses_v2/form/frm_1/reply")
    assert json.loads(body) == {"answer": {"q0": "A"}}


def test_v2_execution_failure_surfaces_as_error() -> None:
    lines = sse({"type": "session.execution.failed", "data": {"message": "boom"}})
    client = OpenCodeClient(BASE, password="s3cret", api_version="v2")

    assert list(client.stream_events(lines)) == [BuilderEvent(kind="error", text="boom")]


def _basic_auth(headers: dict[str, str]) -> str:
    token = headers["Authorization"].split(" ", 1)[1]
    return base64.b64decode(token).decode("utf-8")


def test_v2_prompt_and_reply_carry_basic_auth() -> None:
    transport = FakeTransport((200, b"{}"), (200, b"{}"))
    client = OpenCodeClient(BASE, password="s3cret", api_version="v2", transport=transport)
    form = {
        "type": "form.created",
        "data": {
            "form": {
                "id": "frm_1",
                "sessionID": "ses_v2",
                "title": "Questions",
                "fields": [
                    {
                        "key": "q0",
                        "title": "Data",
                        "description": "Which?",
                        "type": "string",
                        "options": [],
                        "custom": True,
                    }
                ],
            }
        },
    }
    list(client.stream_events(sse(form)))

    client.prompt_async("ses_v2", "hi")
    client.reply_question("frm_1", [["A"]])

    assert _basic_auth(transport.calls[0][3]) == "opencode:s3cret"
    assert _basic_auth(transport.calls[1][3]) == "opencode:s3cret"


def test_v2_open_event_stream_carries_basic_auth() -> None:
    captured: dict[str, dict[str, str]] = {}

    def opener(url: str, headers: dict[str, str] | None = None) -> Iterator[str]:
        captured["headers"] = dict(headers or {})
        return iter([])

    client = OpenCodeClient(BASE, password="s3cret", api_version="v2")

    list(client.open_event_stream(opener=opener))

    assert _basic_auth(captured["headers"]) == "opencode:s3cret"


def test_v2_stream_events_skips_malformed_ordinal() -> None:
    lines = [
        *sse(
            {
                "type": "session.text.delta",
                "data": {"assistantMessageID": "m1", "ordinal": "abc", "delta": "ok"},
            }
        ),
        *sse({"type": "session.execution.succeeded", "data": {}}),
    ]
    client = OpenCodeClient(BASE, password="s3cret", api_version="v2")

    assert list(client.stream_events(lines)) == [
        BuilderEvent(kind="text", text="ok"),
        BuilderEvent(kind="idle"),
    ]


def test_v2_malformed_form_is_skipped() -> None:
    lines = [
        *sse({"type": "form.created", "data": {"form": {"id": "frm_1", "sessionID": "s1"}}}),
        *sse(
            {
                "type": "form.created",
                "data": {
                    "form": {
                        "id": "frm_2",
                        "title": "Questions",
                        "fields": [
                            {"key": "q0", "title": "T", "description": "D", "type": "string"}
                        ],
                    }
                },
            }
        ),
        *sse({"type": "session.execution.succeeded", "data": {}}),
    ]
    client = OpenCodeClient(BASE, password="s3cret", api_version="v2")

    assert list(client.stream_events(lines)) == [BuilderEvent(kind="idle")]


def test_v2_list_questions_registers_forms_for_reply() -> None:
    payload = {
        "data": [
            {
                "id": "frm_1",
                "sessionID": "ses_v2",
                "title": "Questions",
                "fields": [
                    {
                        "key": "q0",
                        "title": "Data",
                        "description": "Which?",
                        "type": "string",
                        "options": [],
                        "custom": True,
                    }
                ],
            }
        ]
    }
    transport = FakeTransport((200, json.dumps(payload).encode()), (200, b"{}"))
    client = OpenCodeClient(BASE, password="s3cret", api_version="v2", transport=transport)

    assert [question.id for question in client.list_questions()] == ["frm_1"]
    client.reply_question("frm_1", [["A"]])

    assert transport.calls[1][1] == f"{BASE}/api/session/ses_v2/form/frm_1/reply"
    assert json.loads(transport.calls[1][2]) == {"answer": {"q0": "A"}}
