"""The Builder tab: describe a dashboard, watch the agent build it (DC-025).

The page is pure UI (FR-808, ADR-003): it composes a prompt, the agent
transcript, a clarifying-question panel, a live preview of the generated
dashboard and the saved-dashboards list, and drives everything through the
DC-024 managers -- :class:`OpenCodeServer`/:class:`OpenCodeClient` for the
agent conversation, :func:`run_script` for the sandbox and
:class:`DashboardStore` for persistence. Every blocking step (server start,
HTTP, sandbox run, store I/O) runs on a Textual worker thread; results come
back to the UI through ``app.call_from_thread``.

One build is one worker (AC3): it starts or reuses the embedded opencode
server (v1 or v2, detected from its startup output -- ADR-015), creates a
session, sends the prompt and consumes the manager-level
``GET /event`` SSE stream. Assistant text arrives cumulative (DC-024 review
F10), so each text event *replaces* the current transcript block instead of
appending; a question blocks the worker until the user answers, and the reply
continues the same turn. When the turn ends, the last ``python`` fenced code
block is extracted and run in the sandbox with the active pane's directory as
the working directory, its stdout streaming into the preview as it is
produced (FR-805).

For tests the page accepts a server factory, a client factory, a script
runner and a store (AC8); the defaults are the DC-024 implementations, so the
flow is exercised without a network or the ``opencode`` binary.
"""

from __future__ import annotations

import re
import threading
from collections import deque
from collections.abc import Callable
from pathlib import Path

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from daylight.managers.dashboard_store import DashboardStore
from daylight.managers.opencode_client import (
    OpenCodeClient,
    OpenCodeError,
    OpenCodeServer,
)
from daylight.managers.sandbox import run_script
from daylight.models import BuilderQuestion, DashboardArtifact, SandboxResult

__all__ = [
    "BuilderPage",
    "ClientFactory",
    "ScriptRunner",
    "ServerFactory",
    "extract_script",
]

#: Starts (or reuses) the embedded opencode server and hands out its base URL.
ServerFactory = Callable[[], OpenCodeServer]

#: Builds one client for a server's base URL, API version and password (ADR-015).
ClientFactory = Callable[[str, str, str | None], OpenCodeClient]

#: Runs one script in the sandbox: the DC-024 ``run_script`` signature.
ScriptRunner = Callable[..., SandboxResult]

#: Wall-clock limit for one sandbox run of the generated script (AC5).
SCRIPT_TIMEOUT_SECONDS = 60.0

#: How long the build worker waits for the user's answer before giving up and
#: ending the turn; also bounded by the page being unmounted (app exit).
ANSWER_TIMEOUT_SECONDS = 300.0

#: How many stdout lines the preview keeps; older lines are dropped.
PREVIEW_MAX_LINES = 500

#: How many characters a derived dashboard name keeps.
DASHBOARD_NAME_MAX = 48

#: Placeholder shown in the preview before anything ran.
PREVIEW_EMPTY = "(nothing yet -- build or load a dashboard)"

#: Placeholder shown in an empty saved-dashboards list.
NO_SAVED_DASHBOARDS = "(no saved dashboards)"

#: Transcript line when the reply carried no runnable code block.
NO_SCRIPT_MESSAGE = "No ```python block in the agent's reply -- nothing to run."

#: Hint shown when the embedded server cannot start (AC7, FR-802).
OPENCODE_HINT = (
    "Install opencode (https://opencode.ai), make sure the 'opencode' command"
    " is on your PATH, then try again. The rest of the app is unaffected."
)

#: The last ```python (or ```py) fenced code block of the agent's reply (AC5).
_FENCED_PYTHON = re.compile(r"```(?:python|py)[ \t]*\r?\n(.*?)```", re.DOTALL)

#: Fallback: the last plain fenced block, for agents that skip the language tag.
_FENCED_ANY = re.compile(r"```[ \t]*\r?\n(.*?)```", re.DOTALL)


def extract_script(text: str) -> str | None:
    """The generated Python: the last ``python`` fenced block in ``text`` (AC5).

    A plain fenced block (no language tag) is accepted as a fallback for
    agents that skip the tag. Returns ``None`` when the reply has no usable
    block.
    """
    for pattern in (_FENCED_PYTHON, _FENCED_ANY):
        blocks = pattern.findall(text)
        script = blocks[-1].strip() if blocks else ""
        if script:
            return script
    return None


def _dashboard_name(prompt_text: str) -> str:
    """A list-friendly name for a dashboard: its prompt's first line, capped."""
    first_line = prompt_text.strip().splitlines()[0].strip() if prompt_text.strip() else ""
    if len(first_line) > DASHBOARD_NAME_MAX:
        return first_line[:DASHBOARD_NAME_MAX] + "\u2026"
    return first_line or "dashboard"


def _tail(text: str, limit: int) -> str:
    """The last ``limit`` lines of ``text``."""
    return "\n".join(text.strip().splitlines()[-limit:])


class BuilderPage(Static):
    """The sixth tab: build a dashboard by describing it (PRD \u00a75.8)."""

    DEFAULT_CLASSES = "builder-page"

    BINDINGS = [
        Binding("ctrl+s", "save_dashboard", "Save", show=True, id="save_dashboard"),
        Binding("ctrl+l", "load_dashboard", "Load", show=True, id="load_dashboard"),
        Binding("ctrl+r", "rerun_dashboard", "Re-run", show=True, id="rerun_dashboard"),
    ]

    def __init__(
        self,
        *,
        server_factory: ServerFactory | None = None,
        client_factory: ClientFactory | None = None,
        runner: ScriptRunner | None = None,
        store: DashboardStore | None = None,
    ) -> None:
        # The DC-024 implementations are the defaults; tests inject fakes for
        # every one of them so the flow runs without a network or a binary.
        self._server_factory: ServerFactory = server_factory or OpenCodeServer
        self._client_factory: ClientFactory = client_factory or (
            lambda base_url, api_version, password: OpenCodeClient(
                base_url, api_version=api_version, password=password
            )
        )
        self._runner: ScriptRunner = runner or run_script
        self._store: DashboardStore = store or DashboardStore()
        #: The embedded server, created on the first build and reused after.
        self._server: OpenCodeServer | None = None
        #: True while a build or a script run is in flight (UI thread only).
        self._busy = False
        #: The current assistant transcript block and its cumulative text (F10).
        self._turn: Static | None = None
        #: True when the next text event must open a fresh block: set when a
        #: question is answered, so the pre-question text is preserved.
        self._next_block = False
        self._last_assistant_text = ""
        #: The dashboard on screen: the script Ctrl+R would run again (AC6).
        self._current_script: str | None = None
        self._current_prompt = ""
        #: The preview: streamed stdout lines plus one status note.
        self._preview_lines: deque[str] = deque(maxlen=PREVIEW_MAX_LINES)
        self._preview_note = ""
        #: The pending question's answer, handed from the UI to the worker.
        self._pending_answers: list[list[str]] | None = None
        self._answer_ready = threading.Event()
        #: Set when the page is unmounted (app exit); workers check it and stop.
        self._cancelled = False
        self._prompt = Input(
            placeholder="Describe the dashboard you want, then press Enter\u2026",
            id="builder-prompt",
        )
        self._transcript = VerticalScroll(id="builder-transcript")
        self._question_panel = Vertical(id="builder-question")
        self._question_text = Static("", id="builder-question-text", markup=False)
        self._question_options = OptionList(id="builder-question-options")
        self._question_custom = Input(
            placeholder="Or type an answer, then Enter", id="builder-question-custom"
        )
        self._preview_scroll = VerticalScroll(id="builder-preview")
        self._preview = Static("", id="builder-preview-text", markup=False)
        self._dashboards = OptionList(id="builder-dashboards")
        self._transcript.border_title = "Agent"
        self._question_panel.border_title = "Question"
        self._preview_scroll.border_title = "Preview"
        self._dashboards.border_title = "Saved dashboards"
        super().__init__(id="builder-page")

    # -- queries -----------------------------------------------------------

    @property
    def transcript_text(self) -> str:
        """The whole transcript as plain text (test seam)."""
        return "\n".join(str(line.content) for line in self._transcript.query(Static))

    @property
    def preview_text(self) -> str:
        """The preview pane as plain text (test seam)."""
        return str(self._preview.content)

    # -- layout ------------------------------------------------------------

    def compose(self) -> ComposeResult:
        """Prompt on top; the agent column and the preview column below (AC2)."""
        yield self._prompt
        with Horizontal(classes="builder-columns"):
            with Vertical(classes="builder-column"):
                yield self._transcript
                with self._question_panel:
                    yield self._question_text
                    yield self._question_options
                    yield self._question_custom
            with Vertical(classes="builder-column"):
                with self._preview_scroll:
                    yield self._preview
                yield self._dashboards

    def on_mount(self) -> None:
        """Fill the saved-dashboards list once the page is in the DOM (AC6)."""
        self.refresh_dashboards()

    def on_unmount(self) -> None:
        """Release the workers and the server when the page goes away.

        The page is unmounted when the app exits: a build worker blocked on a
        pending answer must wake up (it finds no answer and ends the turn) and
        the embedded server must not outlive the app.
        """
        self._cancelled = True
        self._answer_ready.set()
        if self._server is not None:
            self._server.stop()

    def focus_prompt(self) -> None:
        """Put the keyboard in the prompt; what showing the tab means."""
        self._prompt.focus()

    # -- building (AC3) ----------------------------------------------------

    @on(Input.Submitted, "#builder-prompt")
    def _prompt_submitted(self, event: Input.Submitted) -> None:
        """Enter in the prompt starts the build (AC2)."""
        del event
        self.start_build(self._prompt.value)

    def start_build(self, prompt_text: str) -> None:
        """Submit ``prompt_text`` to the agent and start the build (AC2, AC3)."""
        text = prompt_text.strip()
        if not text:
            return
        if self._busy:
            self.notify("A build is already running", severity="warning")
            return
        directory = self._target_directory()
        self._busy = True
        self._current_prompt = text
        self._turn = None
        self._next_block = False
        self._last_assistant_text = ""
        self._transcript_user(text)
        self._transcript_system("Asking the agent\u2026")
        self._build(text, directory)

    def _target_directory(self) -> Path:
        """The directory the dashboard is built for: the active pane's (FR-801)."""
        current = getattr(self.app, "current_path", None)
        if callable(current):
            return Path(current())
        return Path.cwd()

    @work(thread=True, exclusive=True, group="builder-build")
    def _build(self, prompt_text: str, directory: Path) -> None:
        """The whole build on one worker thread; the UI is never blocked (AC3)."""
        try:
            self._build_blocking(prompt_text, directory)
        except OpenCodeError as error:
            self.app.call_from_thread(self._transcript_system, f"Builder error: {error}")
        except Exception as error:  # API drift must degrade, never crash (AC7)
            self.app.call_from_thread(self._transcript_system, f"Builder failed: {error!r}")
        finally:
            if not self._cancelled:  # an unmounted page has no UI to report to
                self.app.call_from_thread(self._work_finished)

    def _build_blocking(self, prompt_text: str, directory: Path) -> None:
        """The blocking part of one build: server, session, turn, sandbox."""
        post = self.app.call_from_thread
        try:
            base_url, api_version, password = self._ensure_server()
        except OpenCodeError as error:
            post(self._transcript_system, f"opencode is not available: {error}\n{OPENCODE_HINT}")
            return
        client = self._client_factory(base_url, api_version, password)
        session_id = client.create_session(
            directory=str(directory), title=_dashboard_name(prompt_text)
        )
        # The event stream is opened before the prompt is sent, so no early
        # event is missed; the manager owns the socket (FR-808).
        lines = client.open_event_stream()
        client.prompt_async(session_id, prompt_text)
        for event in client.stream_events(lines):
            if self._cancelled:  # the page went away; abandon the turn
                return
            if event.kind == "text":
                post(self._assistant_text_update, event.text)
            elif event.kind == "question" and event.question is not None:
                answers = self._ask_and_wait(event.question)
                if answers is None:
                    post(self._transcript_system, "No answer given -- the build stopped here.")
                    return
                client.reply_question(event.question.id, answers)
            elif event.kind == "idle":
                break
            # Unknown kinds are tolerated: the parser already filtered what it
            # could not map, and drift must not crash the builder (AC7).
        if self._cancelled:
            return
        script = extract_script(self._last_assistant_text)
        if script is None:
            post(self._transcript_system, NO_SCRIPT_MESSAGE)
            return
        post(self._set_current_script, script)
        post(self._transcript_system, "Running the generated script in the sandbox\u2026")
        self._run_script_blocking(script, directory)

    def _ensure_server(self) -> tuple[str, str, str | None]:
        """Start or reuse the embedded opencode server (AC3, FR-802).

        Returns ``(base_url, api_version, password)`` so the client factory can
        build a version-aware client (ADR-015): a v2 server is driven with
        Basic auth and ``/api`` paths, a v1 server keeps the original protocol.
        """
        if self._server is None:
            self._server = self._server_factory()
        base_url = self._server.start()
        return base_url, self._server.api_version, self._server.password

    def _ask_and_wait(self, question: BuilderQuestion) -> list[list[str]] | None:
        """Show ``question`` and block this worker until the user answers (AC4).

        Returns the answer matrix, or ``None`` when the user did not answer in
        time or the page went away: the caller then ends the turn instead of
        replying, so a worker can never wait forever.
        """
        self._answer_ready.clear()
        self._pending_answers = None
        self.app.call_from_thread(self._show_question, question)
        answered = self._answer_ready.wait(timeout=ANSWER_TIMEOUT_SECONDS)
        answers = self._pending_answers
        self._pending_answers = None
        if not answered or answers is None:
            return None
        return answers

    def _work_finished(self) -> None:
        """Clear the busy flag on the UI thread when a worker is done."""
        self._busy = False

    # -- the agent transcript (F10) -----------------------------------------

    def _assistant_text_update(self, text: str) -> None:
        """Replace the current assistant block with the cumulative ``text``.

        ``message.part.updated`` carries the text so far, not a delta (DC-024
        review F10): appending would duplicate every chunk. A block that
        follows an answered question starts fresh, because the agent's next
        message starts its own cumulative text.
        """
        if self._turn is None or self._next_block:
            self._turn = Static("", classes="builder-assistant", markup=False)
            self._transcript.mount(self._turn)
            self._next_block = False
        self._turn.update(text)
        self._last_assistant_text = text
        self._transcript.scroll_end(animate=False)

    def _transcript_user(self, text: str) -> None:
        """Echo the user's prompt into the transcript."""
        self._mount_line(Static(text, classes="builder-user", markup=False))

    def _transcript_system(self, text: str) -> None:
        """A status or error line from the page itself (AC7)."""
        self._mount_line(Static(text, classes="builder-system", markup=False))

    def _mount_line(self, line: Static) -> None:
        self._transcript.mount(line)
        self._transcript.scroll_end(animate=False)

    # -- clarifying questions (AC4) -----------------------------------------

    def _show_question(self, question: BuilderQuestion) -> None:
        """Render the pending question with its options (AC4)."""
        header = f"{question.header}: " if question.header else ""
        self._question_text.update(f"{header}{question.question}")
        self._question_options.clear_options()
        for option in question.options:
            # The label travels in the option's id: the reply must carry the
            # label itself, not the decorated "label -- description" text.
            if option.description:
                prompt = f"{option.label} -- {option.description}"
            else:
                prompt = option.label
            self._question_options.add_option(Option(prompt, id=option.label))
        has_options = bool(question.options)
        # A question without options can only be answered free-text; show the
        # input as the escape hatch so the worker can never wait forever.
        self._question_options.display = has_options
        self._question_custom.display = question.custom or not has_options
        self._question_custom.value = ""
        self._question_panel.display = True
        if has_options:
            # OptionList does not highlight by itself; without this, Enter
            # would select nothing and the build would wait forever.
            self._question_options.highlighted = 0
            self._question_options.focus()
        else:
            self._question_custom.focus()

    def _hide_question(self) -> None:
        self._question_panel.display = False

    @on(OptionList.OptionSelected, "#builder-question-options")
    def _question_option_chosen(self, event: OptionList.OptionSelected) -> None:
        """An option click answers the pending question with its label (AC4)."""
        event.stop()
        label = event.option.id if event.option.id is not None else str(event.option.prompt)
        self._answer([[label]])

    @on(Input.Submitted, "#builder-question-custom")
    def _question_custom_chosen(self, event: Input.Submitted) -> None:
        """A free-text answer for questions that allow one (AC4)."""
        text = event.value.strip()
        if text:
            self._answer([[text]])

    def _answer(self, answers: list[list[str]]) -> None:
        """Record the user's answer and wake the build worker (AC4).

        The agent's next message starts its own cumulative text, so flag a
        fresh transcript block: the assistant text shown before the question
        must survive instead of being replaced by the post-question reply.
        """
        self._pending_answers = answers
        self._next_block = True
        self._hide_question()
        self._prompt.focus()
        self._answer_ready.set()

    # -- the live preview (AC5) ---------------------------------------------

    def _set_current_script(self, script: str) -> None:
        self._current_script = script

    def _run_script_blocking(self, script: str, directory: Path) -> SandboxResult:
        """Run ``script`` through the runner, streaming stdout into the preview.

        Called on a worker thread; the runner's output callback hops back to
        the UI thread per line, so the preview fills in live (FR-805).
        """
        post = self.app.call_from_thread
        post(self._preview_start)

        def on_output(line: str) -> None:
            post(self._preview_append, line)

        result = self._runner(
            script, cwd=directory, timeout=SCRIPT_TIMEOUT_SECONDS, on_output=on_output
        )
        post(self._preview_finish, result)
        return result

    def _preview_start(self) -> None:
        self._preview_lines.clear()
        self._preview_note = ""
        self._update_preview()

    def _preview_append(self, line: str) -> None:
        self._preview_lines.append(line)
        self._update_preview()

    def _preview_finish(self, result: SandboxResult) -> None:
        """Surface the run's outcome: stderr, a non-zero exit, a timeout (AC5)."""
        notes: list[str] = []
        if result.timed_out:
            notes.append(f"timed out after {result.duration_seconds:.1f}s and was killed")
        elif result.exit_code != 0:
            notes.append(f"exited with code {result.exit_code}")
        else:
            notes.append(f"finished in {result.duration_seconds:.1f}s")
        if result.stderr.strip():
            notes.append("stderr:\n" + _tail(result.stderr, PREVIEW_MAX_LINES))
        self._preview_note = "\n".join(notes)
        self._update_preview()

    def _update_preview(self) -> None:
        body = "\n".join(self._preview_lines)
        if self._preview_note:
            body = f"{body}\n\n{self._preview_note}" if body else self._preview_note
        self._preview.update(body or PREVIEW_EMPTY)
        self._preview_scroll.scroll_end(animate=False)

    # -- re-running (AC6) ----------------------------------------------------

    def action_rerun_dashboard(self) -> None:
        """``ctrl+r``: run the current script again (AC6)."""
        script = self._current_script
        if not script:
            self.notify("Nothing to run yet -- build or load a dashboard first", severity="warning")
            return
        if self._busy:
            self.notify("A build is already running", severity="warning")
            return
        directory = self._target_directory()
        self._busy = True
        self._rerun(script, directory)

    @work(thread=True, exclusive=True, group="builder-run")
    def _rerun(self, script: str, directory: Path) -> None:
        try:
            if not self._cancelled:
                self._run_script_blocking(script, directory)
        except Exception as error:  # a broken runner must not crash the app
            self.app.call_from_thread(self._transcript_system, f"Run failed: {error!r}")
        finally:
            if not self._cancelled:  # an unmounted page has no UI to report to
                self.app.call_from_thread(self._work_finished)

    # -- saving and loading (AC6) --------------------------------------------

    def action_save_dashboard(self) -> None:
        """``ctrl+s``: save the current dashboard (name + prompt + script) (AC6)."""
        script = self._current_script
        if not script:
            self.notify("Nothing to save yet -- build a dashboard first", severity="warning")
            return
        prompt_text = self._current_prompt or self._prompt.value.strip()
        self._save(_dashboard_name(prompt_text), prompt_text, script)

    @work(thread=True, exclusive=True, group="builder-store")
    def _save(self, name: str, prompt_text: str, script: str) -> None:
        """Persist the dashboard and refresh the list, off the event loop (AC6)."""
        self._store.save(name=name, prompt=prompt_text, script=script)
        self.app.call_from_thread(self._show_dashboards, self._store.list())
        self.app.call_from_thread(self.notify, "Dashboard saved")

    @work(thread=True, exclusive=True, group="builder-store")
    def refresh_dashboards(self) -> None:
        """Re-read the saved dashboards into the list (AC6)."""
        self.app.call_from_thread(self._show_dashboards, self._store.list())

    def _show_dashboards(self, artifacts: list[DashboardArtifact]) -> None:
        self._dashboards.clear_options()
        if not artifacts:
            self._dashboards.add_option(Option(NO_SAVED_DASHBOARDS, disabled=True))
            return
        for artifact in artifacts:
            self._dashboards.add_option(Option(artifact.name, id=artifact.id))

    def action_load_dashboard(self) -> None:
        """``ctrl+l``: load the highlighted saved dashboard (AC6)."""
        index = self._dashboards.highlighted
        if index is None:
            self.notify("Select a saved dashboard first", severity="warning")
            return
        dashboard_id = self._dashboards.get_option_at_index(index).id
        if dashboard_id is None:  # the "(no saved dashboards)" placeholder
            return
        self._load(str(dashboard_id))

    @work(thread=True, exclusive=True, group="builder-store")
    def _load(self, dashboard_id: str) -> None:
        try:
            artifact = self._store.load(dashboard_id)
        except KeyError:
            self.app.call_from_thread(
                self.notify, "That dashboard no longer exists", severity="warning"
            )
            return
        self.app.call_from_thread(self._apply_artifact, artifact)

    def _apply_artifact(self, artifact: DashboardArtifact) -> None:
        """Put a loaded dashboard back on the page: prompt, script, preview (AC6)."""
        self._prompt.value = artifact.prompt
        self._current_prompt = artifact.prompt
        self._set_current_script(artifact.script)
        self._preview_start()
        for line in artifact.script.splitlines()[-PREVIEW_MAX_LINES:]:
            self._preview_lines.append(line)
        self._preview_note = "Loaded dashboard -- press the re-run key to execute it."
        self._update_preview()
