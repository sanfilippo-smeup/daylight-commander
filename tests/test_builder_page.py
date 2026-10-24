"""Builder page tests: reachability, instruction, spec build flow, questions,
save/load and legacy artifacts (DC-031).

Hermetic by construction: the page is wired to a fake server, a scripted fake
client and a real ``DashboardStore`` rooted in ``tmp_path`` -- no network, no
``opencode`` binary, no writes outside ``tmp_path``. The scenarios drive the
real app through Textual's pilot; ``pytest-asyncio`` is not a project
dependency, so ``run_async`` bridges the async bodies onto plain sync test
functions.
"""

from __future__ import annotations

import asyncio
import functools
import json
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

import pytest
from textual.containers import Vertical, VerticalScroll
from textual.pilot import Pilot
from textual.widgets import Input, OptionList
from textual.worker import WorkerState

from daylight.app import DaylightApp
from daylight.components.modules import builder_page
from daylight.components.modules.builder_page import (
    BUILDER_INSTRUCTION,
    BuilderPage,
    _dashboard_name,
    extract_spec,
)
from daylight.config import DEFAULT_KEYBINDINGS
from daylight.managers import ConfigManager, FileManager
from daylight.managers.dashboard_spec import COLOR_TOKENS, KIND_SOURCES, parse_spec
from daylight.managers.dashboard_store import DashboardStore
from daylight.managers.opencode_client import (
    OpenCodeClient,
    OpenCodeError,
    OpenCodeServer,
)
from daylight.models import (
    BuilderEvent,
    BuilderOption,
    BuilderQuestion,
    DashboardBlock,
    DashboardSpec,
)

#: Generous ceiling: worker threads need a few turns to finish a build.
SETTLE = 120

#: Seconds the worker-settle guard waits before declaring workers stuck. The
#: fakes are synchronous and the pending-answer wait is bounded to 10s by the
#: autouse fixture below, so a real settle takes milliseconds; 15s only fails
#: a genuinely stuck worker loudly instead of hanging the suite.
WORKER_SETTLE_TIMEOUT = 15.0

#: Terminal size used for every pilot scenario.
SIZE = (120, 40)

#: The fenced ```json dashboard specification the fake agent replies with.
SPEC_JSON = (
    '{"schema_version": 1, "title": "Largest files", "blocks":'
    ' [{"kind": "bar_chart", "source": "extension_counts", "top_n": 10}]}'
)

#: The reply the fake agent streams: prose plus one fenced ```json block.
REPLY = f"Here is your dashboard:\n\n```json\n{SPEC_JSON}\n```\n"

#: A generated code block the agent is no longer allowed to slip through.
CODE = "print('hello')\nprint('world')\n"

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


@pytest.fixture(autouse=True)
def short_answer_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bound the pending-answer wait for the whole module.

    A build worker blocks on the user's answer; if a regression ever broke the
    answer path, an unbounded wait would hang the suite at interpreter exit
    (the executor joins its threads). Ten seconds keeps that observable.
    """
    monkeypatch.setattr(builder_page, "ANSWER_TIMEOUT_SECONDS", 10.0)


def run_async(fn: F) -> F:
    """Run an async test body on a fresh event loop (no pytest-asyncio needed)."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(fn(*args, **kwargs))

    return wrapper  # type: ignore[return-value]


def live_workers(page: BuilderPage) -> list[str]:
    """The names of the page's workers that have not finished (test seam).

    The page's workers are the app's workers (``page.workers`` is the app
    manager), so this covers the build worker and the store workers alike.
    """
    return [
        worker.name
        for worker in page.workers
        if worker.state in (WorkerState.PENDING, WorkerState.RUNNING)
    ]


async def await_workers_settled(pilot: Pilot, page: BuilderPage, *, what: str) -> None:
    """Bounded wait for the page's workers, then settle the UI (rework guard).

    A thread worker that outlives ``run_test`` can block forever in a
    ``call_from_thread`` once the app's event loop is gone: the executor join
    inside ``asyncio.run`` then hangs the whole suite. Every test that starts
    a worker (a build, Ctrl+S, Ctrl+L, or the on-mount refresh) must drive the
    page to zero live workers before leaving ``run_test``.

    The wait is bounded (never a sleep, never a masked race): the fake client
    streams are finite and the pending-answer wait is capped by the autouse
    fixture, so hitting the timeout means a genuinely stuck worker.
    """
    try:
        await asyncio.wait_for(page.workers.wait_for_complete(), timeout=WORKER_SETTLE_TIMEOUT)
    except TimeoutError as error:
        raise AssertionError(
            f"builder workers did not settle after {what}: {live_workers(page)}"
        ) from error
    await pilot.pause()
    assert live_workers(page) == [], (
        f"builder workers still live after {what}: {live_workers(page)}"
    )


# -- fakes ---------------------------------------------------------------------


class FakeServer:
    """An embedded server that is instantly 'running' at a fake URL."""

    def __init__(
        self,
        error: OpenCodeError | None = None,
        *,
        api_version: str = "v1",
        password: str | None = None,
    ) -> None:
        self.error = error
        self.starts = 0
        self.api_version = api_version
        self.password = password
        #: The ``(base_url, api_version, password)`` tuples the page handed to
        #: its client factory, recorded to prove the wiring (DC-028).
        self.factory_args: list[tuple[str, str, str | None]] = []

    def start(self) -> str:
        if self.error is not None:
            raise self.error
        self.starts += 1
        return "http://builder-fake:4096"

    def stop(self) -> None:
        return None

    @property
    def base_url(self) -> str | None:
        return "http://builder-fake:4096" if self.starts else None


class FakeClient:
    """A scripted opencode client: records calls, replays BuilderEvents."""

    def __init__(self, events: list[BuilderEvent]) -> None:
        self.events = events
        self.calls: list[tuple[str, Any, ...]] = []

    def create_session(self, directory: str | None = None, title: str | None = None) -> str:
        self.calls.append(("create_session", directory, title))
        return "ses_1"

    def open_event_stream(self) -> Any:
        self.calls.append(("open_event_stream",))
        return iter(())

    def prompt_async(self, session_id: str, text: str) -> None:
        self.calls.append(("prompt_async", session_id, text))

    def stream_events(self, lines: Any) -> Any:
        self.calls.append(("stream_events",))
        return iter(self.events)

    def reply_question(self, request_id: str, answers: list[list[str]]) -> None:
        self.calls.append(("reply_question", request_id, answers))


# -- fixtures and helpers ------------------------------------------------------


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """The directory the app browses and the dashboard is built for."""
    root = tmp_path / "data"
    root.mkdir()
    (root / "a.csv").write_text("1\n", encoding="utf-8")
    return root


def make_app(root: Path | None = None, config: Path | None = None) -> DaylightApp:
    """An app reading ``config`` and browsing ``root``."""
    if config is None:
        config = Path(tempfile.mkdtemp()) / "config.json"
    app = DaylightApp(config_path=config)
    app.config_manager = ConfigManager(config)
    app.file_manager = FileManager()
    if root is not None:
        app.start_path = root
    return app


def install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    server: FakeServer,
    client: FakeClient,
) -> DashboardStore:
    """Make the app's builder tab construct a page wired to the fakes.

    ``DaylightApp.PAGES`` is patched with a ``BuilderPage`` subclass whose
    instances carry the fakes and a ``tmp_path`` store, so the default page --
    which would touch the real user dashboard directory -- is never built.
    """
    store = DashboardStore(root=tmp_path / "dashboards")

    def client_factory(base_url: str, api_version: str, password: str | None) -> FakeClient:
        server.factory_args.append((base_url, api_version, password))
        return client

    class InjectedBuilderPage(BuilderPage):
        def __init__(self) -> None:
            super().__init__(
                server_factory=lambda: server,
                client_factory=client_factory,
                store=store,
            )

    monkeypatch.setattr(DaylightApp, "PAGES", {**DaylightApp.PAGES, "builder": InjectedBuilderPage})
    return store


def spy_open_dashboard(
    monkeypatch: pytest.MonkeyPatch, app: DaylightApp
) -> list[tuple[DashboardSpec, Path]]:
    """Record every ``open_dashboard`` call on ``app`` -- and still open the tab."""
    calls: list[tuple[DashboardSpec, Path]] = []

    def record(spec: DashboardSpec, directory: Path | str) -> None:
        calls.append((spec, Path(directory)))
        DaylightApp.open_dashboard(app, spec, directory)

    monkeypatch.setattr(app, "open_dashboard", record)
    return calls


async def wait_for(pilot: Pilot, condition: Callable[[], bool]) -> None:
    """Block until ``condition`` holds, a few turns at a time."""
    for _ in range(SETTLE):
        if condition():
            return
        await pilot.pause()
    raise AssertionError("the expected state was not reached")


async def open_builder(pilot: Pilot) -> BuilderPage:
    """Open the Builder tab and return its page."""
    await pilot.press("ctrl+6")
    app = pilot.app
    assert isinstance(app, DaylightApp)
    await wait_for(pilot, lambda: app.active_page == "builder")
    page = app._pages["builder"]
    assert isinstance(page, BuilderPage)
    return page


async def submit_prompt(pilot: Pilot, page: BuilderPage, text: str) -> None:
    """Type ``text`` into the prompt and press Enter."""
    page._prompt.value = text
    await pilot.press("enter")


# -- AC1: the sixth tab is reachable -------------------------------------------


def test_the_builder_keybinding_is_ctrl6() -> None:
    assert DEFAULT_KEYBINDINGS["goto_builder"] == "ctrl+6"
    assert DaylightApp.PAGES["builder"] is BuilderPage


def test_the_builder_bindings_are_only_save_and_load() -> None:
    """AC8: the ctrl+r re-run binding is gone with the script flow."""
    assert [binding.id for binding in BuilderPage.BINDINGS] == ["save_dashboard", "load_dashboard"]


def test_the_palette_offers_go_to_builder() -> None:
    names = {command.name for command in make_app().palette_commands()}
    assert "Go to Builder" in names


@run_async
async def test_ctrl6_opens_the_builder_tab_with_all_regions(
    tmp_path: Path, tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fakes(monkeypatch, tmp_path, FakeServer(), FakeClient([]))
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        await pilot.press("ctrl+6")
        await wait_for(pilot, lambda: app.active_page == "builder")
        assert app.tabs.active == "builder"
        page = app._pages["builder"]
        assert isinstance(page, BuilderPage)
        assert page.display is True
        page.query_one("#builder-prompt", Input)
        page.query_one("#builder-transcript", VerticalScroll)
        page.query_one("#builder-question", Vertical)
        page.query_one("#builder-dashboards", OptionList)
        await await_workers_settled(pilot, page, what="refresh")


@run_async
async def test_the_builder_tab_is_reachable_by_clicking_it(
    tmp_path: Path, tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fakes(monkeypatch, tmp_path, FakeServer(), FakeClient([]))
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        app.tabs.active = "builder"
        await wait_for(pilot, lambda: app.active_page == "builder")
        assert app.query_one(BuilderPage).display is True
        await await_workers_settled(pilot, app.query_one(BuilderPage), what="refresh")


# -- the Builder instruction (AC1) -----------------------------------------------


def test_the_builder_instruction_states_the_whole_contract() -> None:
    """The instruction carries the whole spec contract (DC-031, ADR-016)."""
    assert "```json" in BUILDER_INSTRUCTION
    assert "schema_version" in BUILDER_INSTRUCTION
    assert "the integer 1" in BUILDER_INSTRUCTION
    assert "top_n" in BUILDER_INSTRUCTION
    assert "between 1 and 50" in BUILDER_INSTRUCTION
    assert "default 8" in BUILDER_INSTRUCTION
    assert "exactly one fenced ```json block" in BUILDER_INSTRUCTION
    assert "Never write Python, HTML, JavaScript" in BUILDER_INSTRUCTION
    # Every allowlisted kind/source pair and every theme token is spelled out.
    for kind, sources in KIND_SOURCES.items():
        assert f'"{kind}"' in BUILDER_INSTRUCTION
        for source in sources:
            assert source in BUILDER_INSTRUCTION
    for token in COLOR_TOKENS:
        assert token in BUILDER_INSTRUCTION


def test_the_builder_instruction_example_is_a_valid_spec() -> None:
    """The instruction's example shape parses against the real validator (AC1)."""
    example = extract_spec(BUILDER_INSTRUCTION)
    assert example is not None
    spec = parse_spec(example)
    assert spec.schema_version == 1
    assert [block.kind for block in spec.blocks] == ["bar_chart", "summary_cards"]


# -- AC2/AC3: a build sends the instruction and opens the Dashboard tab ----------


@run_async
async def test_a_build_sends_the_instruction_and_opens_the_dashboard(
    tmp_path: Path, tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cumulative = f"Working on it. Here is your dashboard:\n\n```json\n{SPEC_JSON}\n```\n"
    client = FakeClient(
        [
            # F10: each event carries the cumulative text of the current message.
            BuilderEvent(kind="text", text="Working on it."),
            BuilderEvent(kind="text", text=cumulative),
            BuilderEvent(kind="weird"),  # unknown kinds are tolerated, not fatal
            BuilderEvent(kind="idle"),
        ]
    )
    install_fakes(monkeypatch, tmp_path, FakeServer(), client)
    app = make_app(tree)
    calls = spy_open_dashboard(monkeypatch, app)
    async with app.run_test(size=SIZE) as pilot:
        page = await open_builder(pilot)
        await submit_prompt(pilot, page, "show me the largest files")
        await wait_for(pilot, lambda: not page._busy)

        # The conversation went through the client, in order: the session is
        # named after the user's request; the prompt carries the Builder
        # instruction plus the user request.
        assert ("create_session", str(tree), "show me the largest files") in client.calls
        assert (
            "prompt_async",
            "ses_1",
            f"{BUILDER_INSTRUCTION}\n\nUser request:\nshow me the largest files",
        ) in client.calls

        # The transcript echoes the prompt -- and only the prompt: the
        # instruction travels to the agent, not to the screen (AC1).
        transcript = page.transcript_text
        assert "show me the largest files" in transcript
        assert "declarative dashboard specification" not in transcript
        assert transcript.count("Working on it") == 1  # replaced, not duplicated (F10)
        assert "Here is your dashboard" in transcript

        # The fenced spec was validated and the Dashboard tab opened exactly
        # once, for the directory captured at build start (AC3).
        assert len(calls) == 1
        assert calls[0][0] == _spec()
        assert calls[0][1] == tree
        assert page._current_spec == _spec()
        assert page._current_prompt == "show me the largest files"
        assert page._current_directory == str(tree)
        assert "opened in the Dashboard tab" in page.transcript_text
        await wait_for(pilot, lambda: app.active_page == "dashboard")
        await await_workers_settled(pilot, page, what="build")


def _spec() -> DashboardSpec:
    """The spec the fenced reply parses to."""
    return DashboardSpec(
        schema_version=1,
        title="Largest files",
        blocks=(DashboardBlock(kind="bar_chart", source="extension_counts", top_n=10),),
    )


# -- AC2/AC4: wrong agent output fails closed ------------------------------------


@run_async
async def test_a_reply_without_a_json_fence_says_so(
    tmp_path: Path, tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeClient(
        [BuilderEvent(kind="text", text="I cannot help with that."), BuilderEvent(kind="idle")]
    )
    install_fakes(monkeypatch, tmp_path, FakeServer(), client)
    app = make_app(tree)
    calls = spy_open_dashboard(monkeypatch, app)
    async with app.run_test(size=SIZE) as pilot:
        page = await open_builder(pilot)
        await submit_prompt(pilot, page, "make something")
        await wait_for(pilot, lambda: not page._busy)
        assert "no fenced ```json dashboard specification" in page.transcript_text
        assert "nothing was executed" in page.transcript_text
        assert calls == []
        assert page._current_spec is None
        await await_workers_settled(pilot, page, what="build")


@run_async
async def test_a_python_fence_is_never_a_spec(
    tmp_path: Path, tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC2: only a ```json fence counts; a ```python block fails closed."""
    reply = f"Here you go:\n\n```python\n{CODE}```\n"
    client = FakeClient([BuilderEvent(kind="text", text=reply), BuilderEvent(kind="idle")])
    install_fakes(monkeypatch, tmp_path, FakeServer(), client)
    app = make_app(tree)
    calls = spy_open_dashboard(monkeypatch, app)
    async with app.run_test(size=SIZE) as pilot:
        page = await open_builder(pilot)
        await submit_prompt(pilot, page, "make something")
        await wait_for(pilot, lambda: not page._busy)
        assert "no fenced ```json dashboard specification" in page.transcript_text
        assert calls == []
        assert page._current_spec is None
        await await_workers_settled(pilot, page, what="build")


@run_async
async def test_an_invalid_spec_shows_the_validator_path_and_opens_nothing(
    tmp_path: Path, tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = '{"schema_version": 1, "title": "X", "blocks": [{"kind": "nope", "source": "summary"}]}'
    reply = f"Try this:\n\n```json\n{bad}\n```\n"
    client = FakeClient([BuilderEvent(kind="text", text=reply), BuilderEvent(kind="idle")])
    install_fakes(monkeypatch, tmp_path, FakeServer(), client)
    app = make_app(tree)
    calls = spy_open_dashboard(monkeypatch, app)
    async with app.run_test(size=SIZE) as pilot:
        page = await open_builder(pilot)
        await submit_prompt(pilot, page, "make something")
        await wait_for(pilot, lambda: not page._busy)
        # The validator's path-bearing message is on screen (FR-804).
        assert "blocks[0].kind" in page.transcript_text
        assert "unknown kind 'nope'" in page.transcript_text
        assert "No dashboard was opened" in page.transcript_text
        assert calls == []
        assert page._current_spec is None
        await await_workers_settled(pilot, page, what="build")


# -- AC5: clarifying questions ---------------------------------------------------


@run_async
async def test_a_question_is_shown_and_the_answer_opens_the_spec(
    tmp_path: Path, tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    question = BuilderQuestion(
        id="que_1",
        header="Data",
        question="Which format?",
        options=(
            BuilderOption(label="CSV", description="a csv file"),
            BuilderOption(label="JSON", description=""),
        ),
    )
    events = [
        BuilderEvent(kind="text", text="Let me check."),
        BuilderEvent(kind="question", question=question),
        BuilderEvent(kind="text", text=REPLY),
        BuilderEvent(kind="idle"),
    ]
    client = FakeClient(events)
    install_fakes(monkeypatch, tmp_path, FakeServer(), client)
    app = make_app(tree)
    calls = spy_open_dashboard(monkeypatch, app)
    async with app.run_test(size=SIZE) as pilot:
        page = await open_builder(pilot)
        await submit_prompt(pilot, page, "build it")
        await wait_for(pilot, lambda: page._question_panel.display)
        assert "Which format?" in str(page._question_text.content)

        # Answer by selecting the first option with the keyboard. Focus is
        # deferred by Textual (call_later), so give it a turn to land.
        page._question_options.focus()
        await pilot.pause()
        await pilot.press("enter")
        # The answer went to the same session's pending question (AC5).
        await wait_for(pilot, lambda: ("reply_question", "que_1", [["CSV"]]) in client.calls)
        await wait_for(pilot, lambda: not page._busy)

        # The same turn continued: the spec was extracted and opened once.
        assert page._question_panel.display is False
        assert len(calls) == 1
        assert calls[0][0] == _spec()

        # Answering a question starts a fresh transcript block, so the
        # assistant text shown before the question is preserved and the
        # post-question reply is on screen too (F10).
        assert "Let me check." in page.transcript_text
        assert "Here is your dashboard" in page.transcript_text
        await await_workers_settled(pilot, page, what="build")


@run_async
async def test_a_free_text_answer_is_sent(
    tmp_path: Path, tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    question = BuilderQuestion(id="que_2", header="", question="Anything else?", custom=True)
    events = [
        BuilderEvent(kind="question", question=question),
        BuilderEvent(kind="text", text=REPLY),
        BuilderEvent(kind="idle"),
    ]
    client = FakeClient(events)
    install_fakes(monkeypatch, tmp_path, FakeServer(), client)
    app = make_app(tree)
    calls = spy_open_dashboard(monkeypatch, app)
    async with app.run_test(size=SIZE) as pilot:
        page = await open_builder(pilot)
        await submit_prompt(pilot, page, "build it")
        await wait_for(pilot, lambda: page._question_custom.display)
        page._question_custom.focus()
        await pilot.pause()  # focus is deferred; give it a turn to land
        page._question_custom.value = "bars please"
        await pilot.press("enter")
        expected_reply = ("reply_question", "que_2", [["bars please"]])
        await wait_for(pilot, lambda: expected_reply in client.calls)
        await wait_for(pilot, lambda: not page._busy)
        assert len(calls) == 1
        assert calls[0][0] == _spec()
        await await_workers_settled(pilot, page, what="build")


# -- AC6: save and load ------------------------------------------------------------


@run_async
async def test_save_load_roundtrip_reopens_the_dashboard(
    tmp_path: Path, tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeClient([BuilderEvent(kind="text", text=REPLY), BuilderEvent(kind="idle")])
    store = install_fakes(monkeypatch, tmp_path, FakeServer(), client)
    app = make_app(tree)
    calls = spy_open_dashboard(monkeypatch, app)
    async with app.run_test(size=SIZE) as pilot:
        page = await open_builder(pilot)
        await submit_prompt(pilot, page, "show me the largest files")
        await wait_for(pilot, lambda: not page._busy)
        assert len(calls) == 1

        # The build switched the active tab to the Dashboard tab; the user
        # returns to the Builder tab before saving (AC6).
        await pilot.press("ctrl+6")
        await wait_for(pilot, lambda: app.active_page == "builder")

        # Ctrl+S saves the current spec + directory + prompt and refreshes the list.
        await pilot.press("ctrl+s")
        await wait_for(pilot, lambda: len(store.list()) == 1)
        await wait_for(pilot, lambda: page._dashboards.option_count == 1)
        artifact = store.list()[0]
        assert artifact.name == "show me the largest files"
        assert artifact.prompt == "show me the largest files"
        assert artifact.spec == _spec()
        assert artifact.directory == str(tree)
        assert artifact.legacy is False

        # Ctrl+L loads it and reopens the Dashboard tab for the same directory.
        page._dashboards.highlighted = 0
        await pilot.press("ctrl+l")
        await wait_for(pilot, lambda: len(calls) == 2)
        assert calls[1][0] == _spec()
        assert calls[1][1] == tree
        assert page._prompt.value == artifact.prompt
        assert page._current_spec == _spec()

        # Rework regression (attempt-002): a save/load must settle to zero
        # live workers -- a worker surviving run_test can block forever in a
        # call_from_thread and hang the executor join at the end of the suite.
        await await_workers_settled(pilot, page, what="save/load")
        assert live_workers(page) == []


@run_async
async def test_save_without_a_spec_notifies_instead_of_saving(
    tmp_path: Path, tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = install_fakes(monkeypatch, tmp_path, FakeServer(), FakeClient([]))
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        page = await open_builder(pilot)
        page.focus_prompt()
        await pilot.pause()
        notices: list[str] = []
        monkeypatch.setattr(page, "notify", lambda message, **_: notices.append(str(message)))
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert notices and "Nothing to save" in notices[0]
        assert store.list() == []
        await await_workers_settled(pilot, page, what="refresh")


# -- AC7: legacy Revision-5 artifacts ----------------------------------------------


@run_async
async def test_a_legacy_artifact_is_listed_as_legacy_and_never_opened(
    tmp_path: Path, tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Revision-5 script-only file is legacy: listed, never opened or run."""
    client = FakeClient([])
    store = install_fakes(monkeypatch, tmp_path, FakeServer(), client)
    app = make_app(tree)
    calls = spy_open_dashboard(monkeypatch, app)
    # Hand-write a Revision-5 artifact; save() no longer writes this shape.
    root = tmp_path / "dashboards"
    root.mkdir(parents=True, exist_ok=True)
    legacy_data = {
        "id": "legacy1",
        "name": "Old script",
        "prompt": "old prompt",
        "script": CODE,
        "created": 1.0,
        "modified": 2.0,
    }
    (root / "legacy1.json").write_text(json.dumps(legacy_data), encoding="utf-8")
    assert store.load("legacy1").legacy is True

    async with app.run_test(size=SIZE) as pilot:
        page = await open_builder(pilot)
        await wait_for(pilot, lambda: page._dashboards.option_count == 1)
        option = page._dashboards.get_option_at_index(0)
        assert "(legacy)" in str(option.prompt)
        assert option.id == "legacy1"

        # Loading it shows a clear message and opens/executes nothing (AC7).
        page._dashboards.highlighted = 0
        await pilot.press("ctrl+l")
        await wait_for(pilot, lambda: "never rendered or executed" in page.transcript_text)
        assert calls == []
        assert page._current_spec is None
        assert client.calls == []
        await await_workers_settled(pilot, page, what="load legacy")


# -- AC7: graceful degradation -----------------------------------------------------


@run_async
async def test_a_missing_opencode_shows_an_actionable_message(
    tmp_path: Path, tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = FakeServer(error=OpenCodeError("cannot start 'opencode': [Errno 2] No such file"))
    client = FakeClient([])
    install_fakes(monkeypatch, tmp_path, server, client)
    app = make_app(tree)
    calls = spy_open_dashboard(monkeypatch, app)
    async with app.run_test(size=SIZE) as pilot:
        page = await open_builder(pilot)
        await submit_prompt(pilot, page, "build it")
        await wait_for(pilot, lambda: "opencode is not available" in page.transcript_text)
        assert "https://opencode.ai" in page.transcript_text  # actionable
        assert client.calls == []  # the conversation never started
        assert calls == []
        # The rest of the app is unaffected.
        await pilot.press("ctrl+1")
        await wait_for(pilot, lambda: app.active_page == "files")
        assert page.display is False
        await await_workers_settled(pilot, page, what="build")


@run_async
async def test_a_client_error_is_surfaced_without_crashing(
    tmp_path: Path, tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class ExplodingClient(FakeClient):
        def create_session(self, directory: str | None = None, title: str | None = None) -> str:
            raise OpenCodeError("POST /session failed with HTTP 500: boom")

    client = ExplodingClient([])
    install_fakes(monkeypatch, tmp_path, FakeServer(), client)
    app = make_app(tree)
    calls = spy_open_dashboard(monkeypatch, app)
    async with app.run_test(size=SIZE) as pilot:
        page = await open_builder(pilot)
        await submit_prompt(pilot, page, "build it")
        await wait_for(pilot, lambda: "Builder error" in page.transcript_text)
        assert "HTTP 500" in page.transcript_text
        assert calls == []
        assert not page._busy
        await await_workers_settled(pilot, page, what="build")


# -- AC8: the defaults are the DC-024 implementations -------------------------------


def test_the_default_page_uses_the_dc024_managers() -> None:
    page = BuilderPage()
    assert page._server_factory is OpenCodeServer
    client = page._client_factory("http://x", "v2", "s3cret")
    assert isinstance(client, OpenCodeClient)
    assert client.api_version == "v2"  # the default factory passes the version through
    assert page._store.root == DashboardStore().root


@run_async
async def test_the_client_factory_receives_the_server_api_version_and_password(
    tmp_path: Path, tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page hands the server's version and password to the factory (DC-028)."""
    server = FakeServer(api_version="v2", password="s3cret")
    install_fakes(monkeypatch, tmp_path, server, FakeClient([]))
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        page = await open_builder(pilot)
        await submit_prompt(pilot, page, "build it")
        await wait_for(pilot, lambda: not page._busy)
        assert server.factory_args == [("http://builder-fake:4096", "v2", "s3cret")]
        await await_workers_settled(pilot, page, what="build")


# -- unit: spec extraction and naming -----------------------------------------------


def test_extract_spec_takes_the_last_json_block() -> None:
    text = 'intro\n```json\n{"a": 1}\n```\nthen\n```json\n{"b": 2}\n```\n'
    assert extract_spec(text) == '{"b": 2}'


def test_extract_spec_rejects_a_python_block() -> None:
    assert extract_spec(f"```python\n{CODE}```") is None


def test_extract_spec_rejects_a_plain_block() -> None:
    assert extract_spec('```\n{"a": 1}\n```') is None


def test_extract_spec_returns_none_without_a_block() -> None:
    assert extract_spec("no code here") is None
    assert extract_spec("```json\n   \n```") is None


def test_the_dashboard_name_is_the_prompt_first_line_capped() -> None:
    assert _dashboard_name("big files\nsecond line") == "big files"
    assert _dashboard_name("x" * 100) == "x" * 48 + "\u2026"
    assert _dashboard_name("") == "dashboard"


# -- shared helpers -----------------------------------------------------------------


async def wait_for_listing(pilot: Pilot) -> None:
    """Block until the Files page finished its first listing."""
    app = pilot.app
    assert isinstance(app, DaylightApp)
    for _ in range(SETTLE):
        files = app.files_page
        if files is not None and files.is_ready:
            if all(not panel.loading and panel.row_count for panel in files.panels):
                return
        await pilot.pause()
    raise AssertionError("the Files page did not finish listing")
