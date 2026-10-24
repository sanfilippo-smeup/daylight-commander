"""The Builder tab: describe a dashboard, the agent returns a spec (DC-031).

The page is pure UI (FR-808, ADR-016): it composes a prompt, the agent
transcript, a clarifying-question panel and the saved-dashboards list, and
drives everything through the DC-024 managers -- :class:`OpenCodeServer` /
:class:`OpenCodeClient` for the agent conversation and :class:`DashboardStore`
for persistence. Every blocking step (server start, HTTP, store I/O) runs on
a Textual worker thread; results come back to the UI through
``app.call_from_thread``.

One build is one worker (AC3): it starts or reuses the embedded opencode
server (v1 or v2, detected from its startup output -- ADR-015), creates a
session, sends :data:`BUILDER_INSTRUCTION` plus the user's request -- only
the user's request is displayed -- and consumes the manager-level
``GET /event`` SSE stream. Assistant text arrives cumulative (DC-024 review
F10), so each text event *replaces* the current transcript block instead of
appending; a clarifying question blocks the worker until the user answers,
and the reply continues the same turn. When the turn ends, the last fenced
``json`` block of the reply is extracted with :func:`extract_spec` and
validated by :func:`parse_spec`; on success the frozen spec opens the native
Dashboard tab over the directory captured at build start (DC-030) -- nothing
is ever generated as code or executed (FR-805, FR-808). A missing or invalid
spec shows the validator's path-bearing error and opens nothing.

For tests the page accepts a server factory, a client factory and a store
(AC8); the defaults are the DC-024 implementations, so the flow is exercised
without a network or the ``opencode`` binary.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from daylight.managers.dashboard_spec import (
    COLOR_TOKENS,
    DEFAULT_COLOR_TOKEN,
    DEFAULT_TOP_N,
    KIND_SOURCES,
    MAX_BLOCKS,
    MAX_TITLE,
    MAX_TOP_N,
    MIN_BLOCKS,
    MIN_TOP_N,
    SCHEMA_VERSION,
    DashboardSpecError,
    parse_spec,
)
from daylight.managers.dashboard_store import DashboardStore
from daylight.managers.opencode_client import (
    OpenCodeClient,
    OpenCodeError,
    OpenCodeServer,
)
from daylight.models import BuilderQuestion, DashboardArtifact, DashboardSpec

__all__ = [
    "BUILDER_INSTRUCTION",
    "BuilderPage",
    "ClientFactory",
    "ServerFactory",
    "extract_spec",
]

#: Starts (or reuses) the embedded opencode server and hands out its base URL.
ServerFactory = Callable[[], OpenCodeServer]

#: Builds one client for a server's base URL, API version and password (ADR-015).
ClientFactory = Callable[[str, str, str | None], OpenCodeClient]

#: How long the build worker waits for the user's answer before giving up and
#: ending the turn; also bounded by the page being unmounted (app exit).
ANSWER_TIMEOUT_SECONDS = 300.0

#: How many characters a derived dashboard name keeps.
DASHBOARD_NAME_MAX = 48

#: Placeholder shown in an empty saved-dashboards list.
NO_SAVED_DASHBOARDS = "(no saved dashboards)"

#: Shown when the reply carried no fenced ```json block at all (AC4).
NO_SPEC_MESSAGE = (
    "The agent's reply contained no fenced ```json dashboard specification --"
    " no dashboard was opened and nothing was executed. Try rephrasing the"
    " request."
)

#: Shown when the reply's fenced JSON does not validate (AC4); the validator's
#: path-bearing message (`{error}`) is included (FR-804).
INVALID_SPEC_TEMPLATE = (
    "The dashboard specification was rejected: {error}\n"
    "No dashboard was opened and nothing was executed."
)

#: Shown when a loaded artifact is a Revision-5 script dashboard (AC7).
LEGACY_TEMPLATE = (
    '"{name}" is a legacy script dashboard from Revision 5 -- it is kept'
    " readable but it is never rendered or executed."
)

#: Hint shown when the embedded server cannot start (AC7, FR-802).
OPENCODE_HINT = (
    "Install opencode (https://opencode.ai), make sure the 'opencode' command"
    " is on your PATH, then try again. The rest of the app is unaffected."
)

#: The allowlisted ``kind`` -> ``source`` pairs, one line each (deterministic).
_BLOCK_PAIR_LINES = "\n".join(
    f'  - "{kind}": {", ".join(sorted(sources))}' for kind, sources in sorted(KIND_SOURCES.items())
)

#: The Builder instruction sent with every prompt (DC-031, ADR-016).
#:
#: The whole validator contract is spelled out -- schema version, the exact
#: block/source allowlist, the top_n bounds, the theme tokens, the defaults
#: and the "one fenced ```json block, no code" output rule -- so the agent
#: cannot claim it did not know the shape. It travels inside the prompt sent
#: to the agent and never reaches the displayed transcript (AC1).
BUILDER_INSTRUCTION = (
    "You are the AI dashboard builder of the Daylight file manager. You always"
    " answer with one declarative dashboard specification, never with code.\n"
    "\n"
    "The JSON object must follow this contract; anything else is rejected:\n"
    f'- "schema_version" must be the integer {SCHEMA_VERSION}.\n'
    f'- "title" must be a non-empty string of at most {MAX_TITLE} characters.\n'
    f'- "blocks" must be an array of {MIN_BLOCKS} to {MAX_BLOCKS} block objects.\n'
    '- Every block has a "kind" and a "source" from exactly these allowed pairs:\n'
    f"{_BLOCK_PAIR_LINES}\n"
    '- Every block may optionally set "title" (a string of at most'
    f' {MAX_TITLE} characters, default empty), "top_n" (an integer between'
    f" {MIN_TOP_N} and {MAX_TOP_N}, default {DEFAULT_TOP_N}) and"
    ' "color_token" (one of'
    f' {", ".join(sorted(COLOR_TOKENS))}; default {DEFAULT_COLOR_TOKEN}).\n'
    '- Use no other keys than "schema_version", "title", "blocks", and per'
    ' block "kind", "source", "title", "top_n", "color_token".\n'
    "\n"
    "Output rule: return exactly one fenced ```json block and nothing else that"
    " is code. Never write Python, HTML, JavaScript, shell commands or any other"
    " code -- the dashboard is rendered natively from the JSON, so generated"
    " code would never be run.\n"
    "\n"
    "Example of the shape (the values are placeholders):\n"
    "```json\n"
    f'{{"schema_version": {SCHEMA_VERSION}, "title": "My files", "blocks":'
    ' [{"kind": "bar_chart", "source": "extension_counts", "top_n": 10},'
    ' {"kind": "summary_cards", "source": "summary"}]}\n'
    "```"
)

#: The only accepted shape: a fenced ```json block (AC2).
_FENCED_JSON = re.compile(r"```json[ \t]*\r?\n(.*?)```", re.DOTALL)


def extract_spec(text: str) -> str | None:
    """The agent's spec: the last fenced ```json block of ``text`` (AC2).

    Nothing else is accepted, fail closed: a plain fenced block, a
    ``python`` block, HTML or bare prose all return ``None``. Returns
    ``None`` when the reply has no usable ``json`` fence.
    """
    for block in reversed(_FENCED_JSON.findall(text)):
        candidate = block.strip()
        if candidate:
            return candidate
    return None


def _dashboard_name(prompt_text: str) -> str:
    """A list-friendly name for a dashboard: its prompt's first line, capped."""
    first_line = prompt_text.strip().splitlines()[0].strip() if prompt_text.strip() else ""
    if len(first_line) > DASHBOARD_NAME_MAX:
        return first_line[:DASHBOARD_NAME_MAX] + "\u2026"
    return first_line or "dashboard"


class BuilderPage(Static):
    """The sixth tab: build a dashboard by describing it (PRD \u00a75.8)."""

    DEFAULT_CLASSES = "builder-page"

    BINDINGS = [
        Binding("ctrl+s", "save_dashboard", "Save", show=True, id="save_dashboard"),
        Binding("ctrl+l", "load_dashboard", "Load", show=True, id="load_dashboard"),
    ]

    def __init__(
        self,
        *,
        server_factory: ServerFactory | None = None,
        client_factory: ClientFactory | None = None,
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
        self._store: DashboardStore = store or DashboardStore()
        #: The embedded server, created on the first build and reused after.
        self._server: OpenCodeServer | None = None
        #: True while a build is in flight (UI thread only).
        self._busy = False
        #: The current assistant transcript block and its cumulative text (F10).
        self._turn: Static | None = None
        #: True when the next text event must open a fresh block: set when a
        #: question is answered, so the pre-question text is preserved.
        self._next_block = False
        self._last_assistant_text = ""
        #: The dashboard on screen: what Ctrl+S saves -- the validated spec,
        #: the directory it was built for (captured at build start) and the
        #: prompt that produced it (AC3, AC6).
        self._current_spec: DashboardSpec | None = None
        self._current_prompt = ""
        self._current_directory = ""
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
        self._dashboards = OptionList(id="builder-dashboards")
        self._transcript.border_title = "Agent"
        self._question_panel.border_title = "Question"
        self._dashboards.border_title = "Saved dashboards"
        super().__init__(id="builder-page")

    # -- queries -----------------------------------------------------------

    @property
    def transcript_text(self) -> str:
        """The whole transcript as plain text (test seam)."""
        return "\n".join(str(line.content) for line in self._transcript.query(Static))

    # -- layout ------------------------------------------------------------

    def compose(self) -> ComposeResult:
        """Prompt on top; the agent column and the saved list below (AC2)."""
        yield self._prompt
        with Horizontal(classes="builder-columns"):
            with Vertical(classes="builder-column"):
                yield self._transcript
                with self._question_panel:
                    yield self._question_text
                    yield self._question_options
                    yield self._question_custom
            with Vertical(classes="builder-column"):
                yield self._dashboards

    def on_mount(self) -> None:
        """Fill the saved-dashboards list once the page is in the DOM (AC6)."""
        self.refresh_dashboards()

    def on_unmount(self) -> None:
        """Release the workers and the server when the page goes away.

        The page is unmounted when the app exits, so nothing may survive it:
        every worker group the page owns is cancelled (``builder-build``,
        ``builder-store``), a build blocked on a pending answer wakes up (it
        finds no answer and ends the turn), in-flight worker callbacks are
        turned into no-ops by :meth:`_post`, and the embedded server is
        stopped. A thread worker cannot be killed mid-call, but with the
        groups cancelled and ``_cancelled`` set it returns without ever
        posting to the UI again, so no thread is left blocked.
        """
        self._cancelled = True
        self._answer_ready.set()
        self.workers.cancel_group(self, "builder-build")
        self.workers.cancel_group(self, "builder-store")
        if self._server is not None:
            self._server.stop()

    def _post(self, callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """A worker's UI callback: a no-op once the page is unmounted.

        A thread worker must never block on ``call_from_thread`` towards a
        stopped app: a callback that lands after the app's event loop is gone
        keeps the worker's thread blocked forever, and the executor join at
        interpreter exit then hangs. Unmount cancels the page's worker groups
        and sets ``_cancelled`` first, so a worker that was still running
        returns here without scheduling anything and its thread finishes.
        """
        if self._cancelled:
            return None
        return self.app.call_from_thread(callback, *args, **kwargs)

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
            self._post(self._transcript_system, f"Builder error: {error}")
        except Exception as error:  # API drift must degrade, never crash (AC7)
            self._post(self._transcript_system, f"Builder failed: {error!r}")
        finally:
            self._post(self._work_finished)  # cancelled or not, the build ended

    def _build_blocking(self, prompt_text: str, directory: Path) -> None:
        """The blocking part of one build: server, session, turn, spec."""
        post = self._post
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
        # The agent gets the whole instruction contract plus the user's
        # request; the transcript still shows only the user's request (AC1).
        client.prompt_async(session_id, f"{BUILDER_INSTRUCTION}\n\nUser request:\n{prompt_text}")
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
        raw = extract_spec(self._last_assistant_text)
        if raw is None:
            post(self._transcript_system, NO_SPEC_MESSAGE)
            return
        try:
            spec = parse_spec(raw)
        except DashboardSpecError as error:
            post(
                self._transcript_system,
                INVALID_SPEC_TEMPLATE.format(error=error),
            )
            return
        post(self._set_current_spec, spec, str(directory))
        post(self.app.open_dashboard, spec, directory)
        post(self._transcript_system, "The dashboard opened in the Dashboard tab.")

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

    # -- saving and loading (AC6, AC7) ----------------------------------------

    def _set_current_spec(self, spec: DashboardSpec, directory: str) -> None:
        """Record the spec and directory of the dashboard on screen (UI thread)."""
        self._current_spec = spec
        self._current_directory = directory

    def action_save_dashboard(self) -> None:
        """``ctrl+s``: save the current spec + directory + prompt (AC6)."""
        spec = self._current_spec
        if spec is None:
            self.notify("Nothing to save yet -- build a dashboard first", severity="warning")
            return
        prompt_text = self._current_prompt or self._prompt.value.strip()
        self._save(_dashboard_name(prompt_text), prompt_text, spec, self._current_directory)

    @work(thread=True, exclusive=True, group="builder-store")
    def _save(self, name: str, prompt_text: str, spec: DashboardSpec, directory: str) -> None:
        """Persist the dashboard and refresh the list, off the event loop (AC6)."""
        self._store.save(name=name, prompt=prompt_text, spec=spec, directory=directory)
        self._post(self._show_dashboards, self._store.list())
        self._post(self.notify, "Dashboard saved")

    @work(thread=True, exclusive=True, group="builder-store")
    def refresh_dashboards(self) -> None:
        """Re-read the saved dashboards into the list (AC6)."""
        self._post(self._show_dashboards, self._store.list())

    def _show_dashboards(self, artifacts: list[DashboardArtifact]) -> None:
        self._dashboards.clear_options()
        if not artifacts:
            self._dashboards.add_option(Option(NO_SAVED_DASHBOARDS, disabled=True))
            return
        for artifact in artifacts:
            # Legacy Revision-5 artifacts are visibly marked, never hidden: a
            # load attempt must fail clearly instead of opening anything.
            label = f"{artifact.name} (legacy)" if artifact.legacy else artifact.name
            self._dashboards.add_option(Option(label, id=artifact.id))

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
            self._post(self.notify, "That dashboard no longer exists", severity="warning")
            return
        self._post(self._apply_artifact, artifact)

    def _apply_artifact(self, artifact: DashboardArtifact) -> None:
        """Put a loaded artifact on screen (AC6, AC7).

        A spec artifact refills the prompt and reopens the Dashboard tab for
        its own directory. A legacy Revision-5 script artifact is announced
        and left untouched: it is never opened, rendered or executed.
        """
        if artifact.legacy or artifact.spec is None:
            message = LEGACY_TEMPLATE.format(name=artifact.name)
            self._transcript_system(message)
            self.notify(message, severity="warning")
            return
        self._prompt.value = artifact.prompt
        self._current_prompt = artifact.prompt
        self._set_current_spec(artifact.spec, artifact.directory)
        self.app.open_dashboard(artifact.spec, artifact.directory)
        self._transcript_system(f'"{artifact.name}" opened in the Dashboard tab.')
