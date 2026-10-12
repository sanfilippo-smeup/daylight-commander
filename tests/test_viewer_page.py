"""Viewer and properties page tests (DC-004).

The manager side of the viewer lives in ``test_viewer_content.py``; these drive
the real app through Textual's pilot to cover the page contract: the viewer and
the properties table go into the *inactive* pane and are never pushed as a
screen, each file type gets the body it should, search and navigation move
inside a file, a binary file leaves for the OS, and ``f4`` edits by type.
"""

from __future__ import annotations

import asyncio
import functools
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

import pytest
from textual.pilot import Pilot
from textual.widgets import Input, Markdown

from daylight.app import DaylightApp
from daylight.components.modules.file_list import FilesPage
from daylight.components.modules.viewer import ViewerPanel
from daylight.managers import ConfigManager, FileManager, content
from daylight.managers.external import EditDispatch
from daylight.modals import LaunchScreen
from daylight.models import AppConfig

SETTLE = 60
SIZE = (120, 40)
F = TypeVar("F", bound=Callable[..., Awaitable[Any]])

SAMPLE_MD = (
    "# Daylight\n"
    "\n"
    "A *commander* with a [link](https://example.com).\n"
    "\n"
    "- first item\n"
    "- second item\n"
    "\n"
    "| key | value |\n"
    "| --- | ----- |\n"
    "| a   | 1     |\n"
    "\n"
    "```python\n"
    "x = 1\n"
    "```\n"
)


def dispatch_for(path: Path, kind: str = "editor") -> EditDispatch:
    """A stand-in dispatch: the test never starts a real editor."""
    command = ("vim", str(path)) if kind == "editor" else ("open", str(path))
    return EditDispatch(kind=kind, command=command, path=path)


def run_async(fn: F) -> F:
    """Run an async test body on a fresh event loop (no pytest-asyncio needed)."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(fn(*args, **kwargs))

    return wrapper  # type: ignore[return-value]


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A directory with one file of every kind the viewer handles."""
    (tmp_path / "doc.md").write_text(SAMPLE_MD, encoding="utf-8")
    (tmp_path / "data.json").write_text('{"b":1,"a":[1,{"c":2}]}', encoding="utf-8")
    (tmp_path / "conf.yaml").write_text(
        "name: day\nlist:\n      - a\nurl:http://x\n", encoding="utf-8"
    )
    (tmp_path / "code.py").write_text(
        "\n".join(f"line {n}" for n in range(1, 201)) + "\n", encoding="utf-8"
    )
    (tmp_path / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(range(256)))
    (tmp_path / "sub").mkdir()
    return tmp_path


def row_count(tree: Path) -> int:
    """Rows the panel will show for ``tree``: every entry plus the ``..`` row."""
    return len(list(tree.iterdir())) + 1


def make_app(tree: Path, config_path: Path) -> DaylightApp:
    """Build an app pinned to ``tree`` with an isolated config file."""
    app = DaylightApp(AppConfig())
    app.start_path = tree
    app.config_manager = ConfigManager(config_path)
    app.file_manager = FileManager()
    return app


async def settle(pilot: Pilot, page: FilesPage, expected: int = 0) -> None:
    """Wait until the active panel listed ``expected`` rows and the work drained.

    ``row_count`` on its own is not a readiness signal: the ``..`` row lands
    before the scan that fills the rest of the panel.
    """
    for _ in range(SETTLE):
        if not page.active.loading and page.active.row_count >= expected:
            if page.viewer is None and page.properties_view is None:
                return
            widget = page.viewer or page.properties_view
            if widget.is_mounted and not widget.loading:
                return
        await pilot.pause()
    raise AssertionError("the page did not settle")


def row_index(panel: Any, name: str) -> int:
    """Index of ``name`` in ``panel``'s rows, ignoring the leading icon."""
    return [row.split(" ", 1)[1] for row in panel.visible_names()].index(name)


async def open_at(pilot: Pilot, page: FilesPage, name: str) -> ViewerPanel:
    """Put the cursor on ``name``, open the viewer and wait for its first draw."""
    page.active.move_cursor(row_index(page.active, name))
    page.open_view()
    for _ in range(SETTLE):
        await pilot.pause()
        viewer = page.viewer
        if viewer is not None and viewer.is_mounted and not viewer.loading:
            return viewer
    raise AssertionError(f"viewer for {name} never finished loading")


# -- AC1: the inactive pane, never a screen ---------------------------------


@run_async
async def test_viewer_mounts_in_the_inactive_pane(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        await settle(pilot, page, row_count(tree))
        assert page.active_panel == "left"
        viewer = await open_at(pilot, page, "doc.md")
        assert page.viewer is viewer
        assert page.viewer_pane == "right"
        assert page.panel("right") in viewer.ancestors
        # A pane, not a screen: the listing on the left stays browsable.
        assert len(app.screen_stack) == 1
        assert page.active.row_count > 0


@run_async
async def test_viewer_follows_the_focus_to_the_other_pane(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        await settle(pilot, page, row_count(tree))
        page.switch_panel()
        await pilot.pause()
        assert page.active_panel == "right"
        viewer = await open_at(pilot, page, "doc.md")
        assert page.viewer_pane == "left"
        assert page.panel("left") in viewer.ancestors
        assert len(app.screen_stack) == 1


@run_async
async def test_opening_another_file_replaces_the_viewer(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        await settle(pilot, page, row_count(tree))
        first = await open_at(pilot, page, "doc.md")
        second = await open_at(pilot, page, "code.py")
        assert second is not first
        assert first.parent is None
        assert list(page.panel("right").mount_point.children) == [second]


@run_async
async def test_escape_closes_the_viewer(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        await settle(pilot, page, row_count(tree))
        await open_at(pilot, page, "doc.md")
        await pilot.press("escape")
        await pilot.pause()
        assert page.viewer is None
        assert page.view_target is None
        assert len(page.panel("right").mount_point.children) == 0


@run_async
async def test_enter_on_a_file_row_opens_the_viewer(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        await settle(pilot, page, row_count(tree))
        page.active.move_cursor(row_index(page.active, "code.py"))
        page.active.focus()
        await pilot.press("enter")
        for _ in range(SETTLE):
            await pilot.pause()
            if page.viewer is not None and not page.viewer.loading:
                break
        assert page.viewer is not None
        assert page.viewer.path.name == "code.py"


@run_async
async def test_a_directory_is_never_opened_in_the_viewer(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        await settle(pilot, page, row_count(tree))
        page.active.move_cursor(row_index(page.active, "sub"))
        page.open_view()
        await pilot.pause()
        assert page.viewer is None


# -- AC2: a real Markdown body ----------------------------------------------


@run_async
async def test_markdown_is_rendered_as_markdown(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        await settle(pilot, page, row_count(tree))
        viewer = await open_at(pilot, page, "doc.md")
        assert viewer.kind == "markdown"
        assert viewer.is_rendered is True
        assert viewer.is_windowed is False
        widget = viewer.markdown_widget
        assert widget is not None and widget.is_mounted
        rendered = {type(node).__name__ for node in widget.walk_children()}
        assert "MarkdownH1" in rendered
        assert "MarkdownBulletList" in rendered
        assert "MarkdownFence" in rendered
        assert "MarkdownTable" in rendered
        # The Markdown widget is what is on screen, not the raw source.
        assert widget.source == SAMPLE_MD
        assert str(viewer.text_widget.content) == ""


# -- AC2/AC3: formatted json, yaml verbatim as plain text -------------------


@run_async
async def test_json_is_pretty_printed_with_line_numbers(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        await settle(pilot, page, row_count(tree))
        viewer = await open_at(pilot, page, "data.json")
        assert viewer.kind == "json"
        assert viewer.is_rendered is False
        body = str(viewer.text_widget.content)
        assert '  "b": 1,' in body
        assert body.startswith("  1  {")
        assert viewer.line_count == 9


@run_async
async def test_yaml_is_viewed_as_plain_text_with_line_numbers(
    tree: Path, tmp_path: Path
) -> None:
    """YAML is windowed like source text and reaches the screen exactly as written."""
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        await settle(pilot, page, row_count(tree))
        viewer = await open_at(pilot, page, "conf.yaml")
        assert viewer.kind == "text"
        assert viewer.is_windowed is True
        assert viewer.is_rendered is False
        # Nothing is re-indented and no ``key: value`` space is invented.
        assert viewer.lines == ["name: day", "list:", "      - a", "url:http://x"]
        body = str(viewer.text_widget.content)
        assert body.startswith("  1  name: day")
        assert "  3        - a" in body
        assert "  4  url:http://x" in body
        assert "url: http://x" not in body


# -- AC3: a window of a big file, moved without reading it all --------------


@run_async
async def test_plain_text_opens_one_window_of_lines(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        await settle(pilot, page, row_count(tree))
        viewer = await open_at(pilot, page, "code.py")
        assert viewer.kind == "text"
        assert viewer.is_windowed is True
        assert viewer.is_rendered is False
        assert viewer.line_count == 200
        assert viewer.line_start == 0
        assert len(viewer.lines) == viewer.window_rows
        assert str(viewer.text_widget.content).startswith("  1  line 1")


@run_async
async def test_paging_moves_the_window_and_clamps_at_the_end(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        await settle(pilot, page, row_count(tree))
        viewer = await open_at(pilot, page, "code.py")
        page.action_viewer_page_down()
        await settle(pilot, page, row_count(tree))
        assert viewer.line_start == viewer.window_rows
        first = str(viewer.text_widget.content).splitlines()[0]
        assert re.fullmatch(rf"\s*{viewer.window_rows + 1}\s+line {viewer.window_rows + 1}", first)
        page.action_viewer_up()
        page.action_viewer_up()
        await settle(pilot, page, row_count(tree))
        assert viewer.line_start == viewer.window_rows - 2
        # The window moved back two lines: its first row is the new start.
        first = str(viewer.text_widget.content).splitlines()[0]
        assert re.fullmatch(rf"\s*{viewer.window_rows - 1}\s+line {viewer.window_rows - 1}", first)
        # Paging far past the end leaves the last line on screen.
        for _ in range(30):
            page.action_viewer_page_down()
        await settle(pilot, page, row_count(tree))
        assert viewer.line_start == 200 - viewer.window_rows
        assert "line 200" in str(viewer.text_widget.content)


@run_async
async def test_a_huge_file_reports_its_size_without_counting(
    tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    huge = tree / "huge.txt"
    with huge.open("w", encoding="utf-8") as handle:
        for number in range(20_000):
            handle.write(f"row {number}\n")
    monkeypatch.setattr(content, "LINE_COUNT_LIMIT", 64)
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        await settle(pilot, page, row_count(tree))
        viewer = await open_at(pilot, page, "huge.txt")
        assert viewer.kind == "text"
        assert viewer.line_count is None
        assert "not counted" in str(viewer.status.content)
        assert len(viewer.lines) == viewer.window_rows
        # Still navigable, and still one window at a time.
        page.action_viewer_page_down()
        await settle(pilot, page, row_count(tree))
        assert viewer.line_start == viewer.window_rows


# -- AC3: search -----------------------------------------------------------


@run_async
async def test_search_reports_matches_and_jumps_to_the_first(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        await settle(pilot, page, row_count(tree))
        viewer = await open_at(pilot, page, "code.py")
        page.action_search_viewer()
        await pilot.pause()
        assert isinstance(viewer.search_input, Input)
        viewer.search_input.value = "line 120"
        for _ in range(SETTLE):
            await pilot.pause()
            if viewer.hits:
                break
        assert viewer.hits == [120]
        assert viewer.line_start == 120 - 1
        assert "1/1 matches" in str(viewer.status.content)
        assert "line 120" in str(viewer.text_widget.content)


@run_async
async def test_next_match_walks_the_hits_and_wraps(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        await settle(pilot, page, row_count(tree))
        viewer = await open_at(pilot, page, "code.py")
        page.action_search_viewer()
        viewer.search_input.value = "line 5 "
        for _ in range(SETTLE):
            await pilot.pause()
            if viewer.hits:
                break
        # "line 5 " only matches the lines that have a space after the 5.
        assert viewer.hits[0] == 5
        assert viewer.hits[1:4] == [50, 51, 52]
        assert viewer.hits[-1] == 59
        page.action_next_match()
        await settle(pilot, page, row_count(tree))
        assert viewer.line_start == viewer.hits[1] - 1
        for _ in range(len(viewer.hits) - 1):
            page.action_next_match()
        await settle(pilot, page, row_count(tree))
        assert viewer.line_start == viewer.hits[0] - 1


@run_async
async def test_escape_closes_the_search_first(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        await settle(pilot, page, row_count(tree))
        viewer = await open_at(pilot, page, "code.py")
        page.action_search_viewer()
        viewer.search_input.focus()
        viewer.search_input.value = "line 3"
        await settle(pilot, page, row_count(tree))
        await pilot.press("escape")
        await pilot.pause()
        assert viewer.search_input.display is False
        assert page.viewer is viewer
        await pilot.press("escape")
        await pilot.pause()
        assert page.viewer is None


# -- AC4: a binary file is not rendered inline, and never launched silently --


@run_async
async def test_a_binary_file_asks_before_it_goes_to_the_system_viewer(
    tree: Path, tmp_path: Path
) -> None:
    """DC-006/AC1: a binary file reaches the OS only after a confirmation."""
    app = make_app(tree, tmp_path / "config.json")
    opened: list[Path] = []
    app.file_manager.launch = lambda request: opened.append(Path(request.target)) or 0  # type: ignore[method-assign]
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        await settle(pilot, page, row_count(tree))
        page.active.move_cursor(row_index(page.active, "shot.png"))
        page.open_view()
        await pilot.pause()
        # Nothing has opened yet: the confirmation is what is on screen.
        assert opened == []
        screen = app.screen
        assert isinstance(screen, LaunchScreen)
        assert "shot.png" in screen.detail_text()
        await pilot.press("y")
        await pilot.pause()
        assert opened == [tree / "shot.png"]
        assert page.viewer is None


@run_async
async def test_declining_the_confirmation_opens_nothing(
    tree: Path, tmp_path: Path
) -> None:
    """DC-006/FR-403: saying no must leave the system alone."""
    app = make_app(tree, tmp_path / "config.json")
    opened: list[Path] = []
    app.file_manager.launch = lambda request: opened.append(Path(request.target)) or 0  # type: ignore[method-assign]
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        await settle(pilot, page, row_count(tree))
        page.active.move_cursor(row_index(page.active, "shot.png"))
        page.open_view()
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        assert opened == []
        assert not isinstance(app.screen, LaunchScreen)
        assert page.viewer is None


# -- AC1: ``f9`` opens a URL in the browser, after a confirmation ------------


@run_async
async def test_open_link_offers_the_url_in_the_file_under_the_cursor(
    tree: Path, tmp_path: Path
) -> None:
    """DC-006/FR-402: the link in the file is detected and offered for confirmation."""
    (tree / "bookmark.txt").write_text("see https://example.com/docs\n", encoding="utf-8")
    app = make_app(tree, tmp_path / "config.json")
    launched: list[str] = []
    app.file_manager.launch = lambda request: launched.append(request.target) or 0  # type: ignore[method-assign]
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        await settle(pilot, page, row_count(tree))
        page.active.move_cursor(row_index(page.active, "bookmark.txt"))
        assert page.link_under_cursor() == "https://example.com/docs"

        await pilot.press("f9")
        await pilot.pause()
        assert launched == []
        screen = app.screen
        assert isinstance(screen, LaunchScreen)
        assert "https://example.com/docs" in screen.detail_text()
        await pilot.press("y")
        await pilot.pause()
        assert launched == ["https://example.com/docs"]


@run_async
async def test_open_link_on_a_file_without_a_link_asks_for_a_url(
    tree: Path, tmp_path: Path
) -> None:
    """With nothing to offer, the URL is asked for and then still confirmed."""
    app = make_app(tree, tmp_path / "config.json")
    launched: list[str] = []
    app.file_manager.launch = lambda request: launched.append(request.target) or 0  # type: ignore[method-assign]
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        await settle(pilot, page, row_count(tree))
        page.active.move_cursor(row_index(page.active, "code.py"))

        await pilot.press("f9")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "FormScreen"
        field = app.screen.query_one("Input")
        field.value = "https://example.com/typed"
        await pilot.press("enter")
        await pilot.pause()

        # Typing a URL does not launch it; the confirmation comes first.
        assert launched == []
        assert isinstance(app.screen, LaunchScreen)
        await pilot.press("y")
        await pilot.pause()
        assert launched == ["https://example.com/typed"]


@run_async
async def test_a_typed_url_that_is_not_a_url_is_refused(
    tree: Path, tmp_path: Path
) -> None:
    """Only the declared schemes reach the browser (FR-402, the security note)."""
    app = make_app(tree, tmp_path / "config.json")
    launched: list[str] = []
    app.file_manager.launch = lambda request: launched.append(request.target) or 0  # type: ignore[method-assign]
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        await settle(pilot, page, row_count(tree))
        page.active.move_cursor(row_index(page.active, "code.py"))

        await pilot.press("f9")
        await pilot.pause()
        field = app.screen.query_one("Input")
        field.value = "file:///etc/passwd"
        await pilot.press("enter")
        await pilot.pause()
        assert launched == []
        assert not isinstance(app.screen, LaunchScreen)


@run_async
async def test_edit_on_a_binary_file_confirms_before_the_os_takes_it(
    tree: Path, tmp_path: Path
) -> None:
    """AC1: ``f4`` on a non-text file is a launch, so it asks first."""
    app = make_app(tree, tmp_path / "config.json")
    launched: list[Path] = []
    app.file_manager.launch = lambda request: launched.append(Path(request.target)) or 0  # type: ignore[method-assign]
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        await settle(pilot, page, row_count(tree))
        page.active.move_cursor(row_index(page.active, "shot.png"))
        page.edit_target()
        await pilot.pause()
        assert launched == []
        assert isinstance(app.screen, LaunchScreen)
        await pilot.press("n")
        await pilot.pause()
        assert launched == []


@run_async
async def test_edit_on_a_text_file_still_runs_the_editor_without_asking(
    tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``$EDITOR`` runs in this terminal: not a launch, so no modal (FR-403)."""
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "nano")
    app = make_app(tree, tmp_path / "config.json")
    calls: list[Path] = []

    def record(path: Path, **kwargs: Any) -> EditDispatch:
        calls.append(Path(path))
        return dispatch_for(Path(path))

    app.file_manager.edit = record  # type: ignore[method-assign]
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        await settle(pilot, page, row_count(tree))
        page.active.move_cursor(row_index(page.active, "code.py"))
        page.edit_target()
        await pilot.pause()
        assert calls == [tree / "code.py"]
        assert not isinstance(app.screen, LaunchScreen)


# -- AC5: edit by type -----------------------------------------------------


@run_async
async def test_editing_a_text_file_runs_the_editor(
    tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    calls: list[list[str]] = []
    app = make_app(tree, tmp_path / "config.json")

    def record(path: Path, **kwargs: Any) -> EditDispatch:
        calls.append([str(path), str(kwargs.get("editor", ""))])
        return dispatch_for(path)

    app.file_manager.edit = record  # type: ignore[method-assign]
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        await settle(pilot, page, row_count(tree))
        page.active.move_cursor(row_index(page.active, "code.py"))
        page.action_edit()
        await pilot.pause()
        assert calls[0][0].endswith("code.py")


@run_async
async def test_editing_a_binary_file_runs_the_os_application(
    tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    calls: list[list[str]] = []
    app = make_app(tree, tmp_path / "config.json")
    app.file_manager.launch = lambda request: calls.append(  # type: ignore[method-assign]
        [request.target]
    ) or 0
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        await settle(pilot, page, row_count(tree))
        page.active.move_cursor(row_index(page.active, "shot.png"))
        page.action_edit()
        await pilot.pause()
        # DC-006: a binary edit is a launch, so the confirmation comes first.
        assert calls == []
        await pilot.press("y")
        await pilot.pause()
        assert calls == [[str(tree / "shot.png")]]


@run_async
async def test_editing_reloads_an_open_view(tree: Path, tmp_path: Path) -> None:
    """A file edited behind the viewer is re-read, so the pane is never stale."""
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        await settle(pilot, page, row_count(tree))
        await open_at(pilot, page, "code.py")
        (tree / "code.py").write_text("rewritten by the editor\n", encoding="utf-8")
        app.file_manager.edit = (  # type: ignore[method-assign]
            lambda path, **kwargs: dispatch_for(path)
        )
        page.edit_target()
        for _ in range(SETTLE):
            await pilot.pause()
            if page.viewer is not None and not page.viewer.loading:
                break
        assert page.viewer is not None
        assert page.viewer.line_count == 1
        assert "rewritten" in str(page.viewer.text_widget.content)


@run_async
async def test_editing_markdown_rerenders_one_body(tree: Path, tmp_path: Path) -> None:
    """A Markdown reload replaces its body instead of stacking a second one."""
    app = make_app(tree, tmp_path / "config.json")
    app.file_manager.edit = (  # type: ignore[method-assign]
        lambda path, **kwargs: dispatch_for(path)
    )
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        await settle(pilot, page, row_count(tree))
        viewer = await open_at(pilot, page, "doc.md")
        assert len(list(viewer.body.query(Markdown))) == 1
        edited = "# Edited\n\nOne paragraph.\n"
        (tree / "doc.md").write_text(edited, encoding="utf-8")
        page.edit_target()
        for _ in range(SETTLE):
            await pilot.pause()
            if page.viewer is not None and not page.viewer.loading:
                break
        for _ in range(SETTLE):
            await pilot.pause()
        assert page.viewer is viewer
        bodies = list(viewer.body.query(Markdown))
        # One body, and it is the one that was already on screen.
        assert len(bodies) == 1
        assert bodies[0] is viewer.markdown_widget
        assert bodies[0].source == edited
        # The rendered document is the new one, not two copies of either.
        assert [type(node).__name__ for node in bodies[0].walk_children()].count(
            "MarkdownH1"
        ) == 1


# -- AC6: the properties table ---------------------------------------------


@run_async
async def test_properties_go_in_the_inactive_pane_with_the_documented_fields(
    tree: Path, tmp_path: Path
) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        await settle(pilot, page, row_count(tree))
        page.active.move_cursor(row_index(page.active, "doc.md"))
        page.action_properties()
        for _ in range(SETTLE):
            await pilot.pause()
            view = page.properties_view
            if view is not None and view.is_mounted and not view.loading:
                break
        view = page.properties_view
        assert view is not None
        assert page.viewer is None
        assert page.viewer_pane == "right"
        assert page.panel("right") in view.ancestors
        assert len(app.screen_stack) == 1
        labels = [label for label, _ in view.rows]
        for wanted in ("Name", "Path", "Size", "Inode", "Modified", "Changed", "Permissions"):
            assert wanted in labels
        assert "MIME" in labels
        assert "Owner" in labels
        assert "Group" in labels
        assert "Accessed" in labels
        assert any(value.startswith("sha256:") for _, value in view.rows)
        assert view.table.row_count == len(view.rows)


@run_async
async def test_copies_the_property_under_the_cursor(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        await settle(pilot, page, row_count(tree))
        page.active.move_cursor(row_index(page.active, "doc.md"))
        page.action_properties()
        for _ in range(SETTLE):
            await pilot.pause()
            view = page.properties_view
            if view is not None and view.is_mounted and not view.loading:
                break
        view = page.properties_view
        assert view is not None
        page.action_copy_property()
        await pilot.pause()
        assert app._clipboard == "doc.md"
        view.table.move_cursor(row=1)
        page.action_copy_property()
        await pilot.pause()
        assert app._clipboard == str(tree / "doc.md")
        assert "copied" in str(view.status.content)


@run_async
async def test_the_properties_table_takes_the_cursor_keys(tree: Path, tmp_path: Path) -> None:
    """``up``/``down``/``pageup``/``pagedown`` move the properties cursor.

    The panel's own ``home``/``end`` are vertical too, but the table binds those
    horizontally (``cursor_type="row"``), so only the row keys are asserted here.
    """
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        await settle(pilot, page, row_count(tree))
        page.active.move_cursor(row_index(page.active, "doc.md"))
        page.action_properties()
        for _ in range(SETTLE):
            await pilot.pause()
            view = page.properties_view
            if view is not None and view.is_mounted and not view.loading:
                break
        view = page.properties_view
        assert view is not None
        listing_cursor = page.active.cursor_index
        view.table.focus()
        await pilot.pause()
        assert view.table.has_focus
        await pilot.press("down", "down")
        await pilot.pause()
        assert view.table.cursor_row == 2
        # The listing behind the view kept its cursor: the panel gave the key up.
        assert page.active.cursor_index == listing_cursor
        await pilot.press("pagedown")
        await pilot.pause()
        after_page_down = view.table.cursor_row
        assert after_page_down > 2
        await pilot.press("pageup")
        await pilot.pause()
        # The table now fills the tab, so a page is the whole table: pageup
        # returns to the top rather than to the row it started on.
        assert view.table.cursor_row < after_page_down
        # up/down still step one row at a time from wherever the cursor is.
        await pilot.press("down")
        await pilot.pause()
        stepped = view.table.cursor_row
        await pilot.press("up")
        await pilot.pause()
        assert view.table.cursor_row == stepped - 1
        # Which is what makes every field copyable, not only the first one.
        await pilot.press(*["down"] * (len(view.rows) - 1))
        await pilot.pause()
        assert view.table.cursor_row == len(view.rows) - 1
        page.action_copy_property()
        await pilot.pause()
        assert app._clipboard.startswith("sha256:")


@run_async
async def test_the_viewer_replaces_the_properties_table(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        await settle(pilot, page, row_count(tree))
        page.active.move_cursor(row_index(page.active, "doc.md"))
        page.action_properties()
        for _ in range(SETTLE):
            await pilot.pause()
            if page.properties_view is not None and not page.properties_view.loading:
                break
        assert page.properties_view is not None
        await open_at(pilot, page, "doc.md")
        assert page.viewer is not None
        assert page.properties_view is None
        assert list(page.panel("right").mount_point.children) == [page.viewer]


@run_async
async def test_switching_panels_keeps_the_viewer_on_screen(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        await settle(pilot, page, row_count(tree))
        viewer = await open_at(pilot, page, "doc.md")
        page.switch_panel()
        await pilot.pause()
        assert page.viewer is viewer
        assert page.viewer_pane == "right"
        assert page.active_panel == "right"
