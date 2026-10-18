"""Builder page tests: reachability, build flow, questions, preview, store (DC-025).

Hermetic by construction: the page is wired to a fake server, a scripted fake
client, a fake sandbox runner and a real ``DashboardStore`` rooted in
``tmp_path`` -- no network, no ``opencode`` binary, no writes outside
``tmp_path``. The scenarios drive the real app through Textual's pilot;
``pytest-asyncio`` is not a project dependency, so ``run_async`` bridges the
async bodies onto plain sync test functions.
"""

from __future__ import annotations

import asyncio
import functools
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

import pytest
from textual.containers import Vertical, VerticalScroll
from textual.pilot import Pilot
from textual.widgets import Input, OptionList

from daylight.app import DaylightApp
from daylight.components.modules import builder_page
from daylight.components.modules.builder_page import BuilderPage, _dashboard_name, extract_script
from daylight.config import DEFAULT_KEYBINDINGS
from daylight.managers import ConfigManager, FileManager
from daylight.managers.dashboard_store import DashboardStore
from daylight.managers.opencode_client import (
    OpenCodeClient,
    OpenCodeError,
    OpenCodeServer,
)
from daylight.managers.sandbox import run_script
from daylight.models import BuilderEvent, BuilderOption, BuilderQuestion, SandboxResult

#: Generous ceiling: worker threads need a few turns to finish a build.
SETTLE = 120

#: Terminal size used for every pilot scenario.
SIZE = (120, 40)

#: The generated script the fake agent replies with.
SCRIPT = "print('hello')\nprint('world')\n"

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


class FakeRunner:
    """A scripted sandbox runner: records the call, streams lines, returns."""

    def __init__(self, result: SandboxResult, output: list[str] | None = None) -> None:
        self.result = result
        self.output = output if output is not None else []
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        script: str,
        *,
        cwd: Path,
        timeout: float,
        on_output: Callable[[str], None],
    ) -> SandboxResult:
        self.calls.append({"script": script, "cwd": cwd, "timeout": timeout})
        for line in self.output:
            on_output(line)
        return self.result


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
    runner: FakeRunner,
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
                runner=runner,
                store=store,
            )

    monkeypatch.setattr(DaylightApp, "PAGES", {**DaylightApp.PAGES, "builder": InjectedBuilderPage})
    return store


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


def test_the_palette_offers_go_to_builder() -> None:
    names = {command.name for command in make_app().palette_commands()}
    assert "Go to Builder" in names


@run_async
async def test_ctrl6_opens_the_builder_tab_with_all_regions(
    tmp_path: Path, tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fakes(monkeypatch, tmp_path, FakeServer(), FakeClient([]), FakeRunner(_ok()))
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
        page.query_one("#builder-preview", VerticalScroll)
        page.query_one("#builder-dashboards", OptionList)


@run_async
async def test_the_builder_tab_is_reachable_by_clicking_it(
    tmp_path: Path, tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fakes(monkeypatch, tmp_path, FakeServer(), FakeClient([]), FakeRunner(_ok()))
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        app.tabs.active = "builder"
        await wait_for(pilot, lambda: app.active_page == "builder")
        assert app.query_one(BuilderPage).display is True


# -- AC2/AC3/AC5: a build streams the agent's text and runs the script ----------


@run_async
async def test_a_build_streams_text_and_runs_the_script(
    tmp_path: Path, tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reply = f"Working on it. Here you go:\n\n```python\n{SCRIPT}```\n"
    events = [
        # F10: each event carries the cumulative text of the current message.
        BuilderEvent(kind="text", text="Working on it."),
        BuilderEvent(kind="text", text=reply),
        BuilderEvent(kind="weird"),  # unknown kinds are tolerated, not fatal
        BuilderEvent(kind="idle"),
    ]
    client = FakeClient(events)
    runner = FakeRunner(
        SandboxResult(
            exit_code=0, stdout="hello\nworld\n", stderr="", timed_out=False, duration_seconds=0.05
        ),
        output=["hello", "world"],
    )
    install_fakes(monkeypatch, tmp_path, FakeServer(), client, runner)
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        page = await open_builder(pilot)
        await submit_prompt(pilot, page, "show me the largest files")
        await wait_for(pilot, lambda: not page._busy)

        # The agent conversation went through the client, in order.
        assert ("create_session", str(tree), "show me the largest files") in client.calls
        assert ("prompt_async", "ses_1", "show me the largest files") in client.calls

        # The transcript echoes the prompt and holds the final cumulative text
        # exactly once (F10: replace, never append duplicates).
        transcript = page.transcript_text
        assert "show me the largest files" in transcript
        assert transcript.count("Working on it") == 1
        assert "Here you go" in transcript

        # The generated Python was extracted and run in the pane's directory.
        assert runner.calls, "the runner was not called"
        assert runner.calls[0]["script"] == SCRIPT.strip()
        assert runner.calls[0]["cwd"] == tree
        assert page._current_script == SCRIPT.strip()

        # The streamed stdout is in the preview, with the run's outcome.
        assert "hello" in page.preview_text
        assert "world" in page.preview_text
        assert "finished in" in page.preview_text


@run_async
async def test_a_reply_without_a_code_block_says_so(
    tmp_path: Path, tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeClient(
        [BuilderEvent(kind="text", text="I cannot help with that."), BuilderEvent(kind="idle")]
    )
    runner = FakeRunner(_ok())
    install_fakes(monkeypatch, tmp_path, FakeServer(), client, runner)
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        page = await open_builder(pilot)
        await submit_prompt(pilot, page, "make something")
        await wait_for(pilot, lambda: not page._busy)
        assert "No ```python block" in page.transcript_text
        assert runner.calls == []
        assert page._current_script is None


# -- AC4: clarifying questions --------------------------------------------------


@run_async
async def test_a_question_is_shown_and_its_answer_sent(
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
        BuilderEvent(kind="text", text=f"Done:\n```python\n{SCRIPT}```"),
        BuilderEvent(kind="idle"),
    ]
    client = FakeClient(events)
    runner = FakeRunner(_ok())
    install_fakes(monkeypatch, tmp_path, FakeServer(), client, runner)
    app = make_app(tree)
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
        await wait_for(pilot, lambda: ("reply_question", "que_1", [["CSV"]]) in client.calls)
        await wait_for(pilot, lambda: not page._busy)

        # The same turn continued: the post-answer script ran.
        assert page._question_panel.display is False
        assert runner.calls and "print('hello')" in runner.calls[0]["script"]

        # Answering a question starts a fresh transcript block, so the
        # assistant text shown before the question is preserved and the
        # post-question reply is on screen too.
        assert "Let me check." in page.transcript_text
        assert "Done:" in page.transcript_text


@run_async
async def test_a_free_text_answer_is_sent(
    tmp_path: Path, tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    question = BuilderQuestion(id="que_2", header="", question="Anything else?", custom=True)
    events = [
        BuilderEvent(kind="question", question=question),
        BuilderEvent(kind="text", text=f"Ok:\n```python\n{SCRIPT}```"),
        BuilderEvent(kind="idle"),
    ]
    client = FakeClient(events)
    runner = FakeRunner(_ok())
    install_fakes(monkeypatch, tmp_path, FakeServer(), client, runner)
    app = make_app(tree)
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
        assert runner.calls


# -- AC5: the preview surfaces failures ------------------------------------------


@run_async
async def test_a_failing_run_surfaces_stderr_and_exit_code(
    tmp_path: Path, tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reply = f"```python\n{SCRIPT}```"
    client = FakeClient([BuilderEvent(kind="text", text=reply), BuilderEvent(kind="idle")])
    runner = FakeRunner(
        SandboxResult(
            exit_code=2, stdout="partial\n", stderr="boom\n", timed_out=False, duration_seconds=0.1
        ),
        output=["partial"],
    )
    install_fakes(monkeypatch, tmp_path, FakeServer(), client, runner)
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        page = await open_builder(pilot)
        await submit_prompt(pilot, page, "build it")
        await wait_for(pilot, lambda: not page._busy)
        assert "partial" in page.preview_text
        assert "exited with code 2" in page.preview_text
        assert "boom" in page.preview_text


@run_async
async def test_a_timed_out_run_is_surfaced(
    tmp_path: Path, tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reply = f"```python\n{SCRIPT}```"
    client = FakeClient([BuilderEvent(kind="text", text=reply), BuilderEvent(kind="idle")])
    runner = FakeRunner(
        SandboxResult(exit_code=-9, stdout="", stderr="", timed_out=True, duration_seconds=60.0)
    )
    install_fakes(monkeypatch, tmp_path, FakeServer(), client, runner)
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        page = await open_builder(pilot)
        await submit_prompt(pilot, page, "build it")
        await wait_for(pilot, lambda: not page._busy)
        assert "timed out" in page.preview_text


# -- AC6: save, load, re-run ------------------------------------------------------


@run_async
async def test_save_load_and_rerun_roundtrip(
    tmp_path: Path, tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reply = f"```python\n{SCRIPT}```"
    client = FakeClient([BuilderEvent(kind="text", text=reply), BuilderEvent(kind="idle")])
    runner = FakeRunner(_ok())
    store = install_fakes(monkeypatch, tmp_path, FakeServer(), client, runner)
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        page = await open_builder(pilot)
        await submit_prompt(pilot, page, "show me the largest files")
        await wait_for(pilot, lambda: not page._busy)

        # Ctrl+S saves the current dashboard and refreshes the list.
        await pilot.press("ctrl+s")
        await wait_for(pilot, lambda: len(store.list()) == 1)
        await wait_for(pilot, lambda: page._dashboards.option_count == 1)
        artifact = store.list()[0]
        assert artifact.name == "show me the largest files"
        assert artifact.prompt == "show me the largest files"
        assert artifact.script == SCRIPT.strip()

        # Ctrl+L loads the selected dashboard into the prompt and the preview.
        page._dashboards.highlighted = 0
        await pilot.press("ctrl+l")
        await wait_for(pilot, lambda: page._prompt.value == artifact.prompt)
        assert page._current_script == SCRIPT.strip()
        assert "print('hello')" in page.preview_text

        # Ctrl+R runs the current script again.
        runner.calls.clear()
        await pilot.press("ctrl+r")
        await wait_for(pilot, lambda: len(runner.calls) == 1)
        assert runner.calls[0]["script"] == SCRIPT.strip()
        assert runner.calls[0]["cwd"] == tree


# -- AC7: graceful degradation -----------------------------------------------------


@run_async
async def test_a_missing_opencode_shows_an_actionable_message(
    tmp_path: Path, tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = FakeServer(error=OpenCodeError("cannot start 'opencode': [Errno 2] No such file"))
    client = FakeClient([])
    runner = FakeRunner(_ok())
    install_fakes(monkeypatch, tmp_path, server, client, runner)
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        page = await open_builder(pilot)
        await submit_prompt(pilot, page, "build it")
        await wait_for(pilot, lambda: "opencode is not available" in page.transcript_text)
        assert "https://opencode.ai" in page.transcript_text  # actionable
        assert client.calls == []  # the conversation never started
        assert runner.calls == []
        # The rest of the app is unaffected.
        await pilot.press("ctrl+1")
        await wait_for(pilot, lambda: app.active_page == "files")
        assert page.display is False


@run_async
async def test_a_client_error_is_surfaced_without_crashing(
    tmp_path: Path, tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class ExplodingClient(FakeClient):
        def create_session(self, directory: str | None = None, title: str | None = None) -> str:
            raise OpenCodeError("POST /session failed with HTTP 500: boom")

    client = ExplodingClient([])
    runner = FakeRunner(_ok())
    install_fakes(monkeypatch, tmp_path, FakeServer(), client, runner)
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        page = await open_builder(pilot)
        await submit_prompt(pilot, page, "build it")
        await wait_for(pilot, lambda: "Builder error" in page.transcript_text)
        assert "HTTP 500" in page.transcript_text
        assert runner.calls == []
        assert not page._busy


# -- AC8: the defaults are the DC-024 implementations -------------------------------


def test_the_default_page_uses_the_dc024_managers() -> None:
    page = BuilderPage()
    assert page._server_factory is OpenCodeServer
    client = page._client_factory("http://x", "v2", "s3cret")
    assert isinstance(client, OpenCodeClient)
    assert client.api_version == "v2"  # the default factory passes the version through
    assert page._runner is run_script
    assert page._store.root == DashboardStore().root


@run_async
async def test_the_client_factory_receives_the_server_api_version_and_password(
    tmp_path: Path, tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page hands the server's version and password to the factory (DC-028)."""
    server = FakeServer(api_version="v2", password="s3cret")
    install_fakes(monkeypatch, tmp_path, server, FakeClient([]), FakeRunner(_ok()))
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        page = await open_builder(pilot)
        await submit_prompt(pilot, page, "build it")
        await wait_for(pilot, lambda: not page._busy)
        assert server.factory_args == [("http://builder-fake:4096", "v2", "s3cret")]


# -- unit: script extraction and naming --------------------------------------------


def test_extract_script_prefers_the_python_block() -> None:
    text = "intro\n```json\n{\"a\": 1}\n```\nthen\n```python\nprint('hi')\n```\n"
    assert extract_script(text) == "print('hi')"


def test_extract_script_falls_back_to_a_plain_block() -> None:
    assert extract_script("```\nprint('plain')\n```") == "print('plain')"


def test_extract_script_returns_none_without_a_block() -> None:
    assert extract_script("no code here") is None


def test_the_dashboard_name_is_the_prompt_first_line_capped() -> None:
    assert _dashboard_name("big files\nsecond line") == "big files"
    assert _dashboard_name("x" * 100) == "x" * 48 + "\u2026"
    assert _dashboard_name("") == "dashboard"


# -- shared helpers -----------------------------------------------------------------


def _ok() -> SandboxResult:
    """A successful sandbox result."""
    return SandboxResult(exit_code=0, stdout="", stderr="", timed_out=False, duration_seconds=0.01)


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
