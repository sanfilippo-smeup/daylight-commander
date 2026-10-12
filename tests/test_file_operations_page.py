"""File operations through the real Files page (DC-003 AC1-AC5).

These drive the app through Textual's pilot, so they cover the wiring the unit
tests cannot: ``space`` marks rows, a batch command acts on every marked row,
``f8`` refuses to delete anything before the confirmation modal is answered, and
a long copy runs in a worker thread with a Cancel that the event loop keeps
answering while the work goes on.
"""

from __future__ import annotations

import asyncio
import functools
import shutil
import sys
import threading
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

import pytest
from textual.coordinate import Coordinate
from textual.events import Key
from textual.pilot import Pilot
from textual.widgets import Button, Input, ProgressBar

from daylight.app import DaylightApp
from daylight.components.modules.file_list import FilePanel, FilesPage
from daylight.config import DEFAULT_KEYBINDINGS
from daylight.managers import ConfigManager, FileManager
from daylight.models import AppConfig
from daylight.modals import ConfirmScreen, FormScreen, ProgressScreen

SETTLE = 80
SIZE = (120, 40)
F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def run_async(fn: F) -> F:
    """Run an async test body on a fresh event loop (no pytest-asyncio needed)."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(fn(*args, **kwargs))

    return wrapper  # type: ignore[return-value]


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """The left pane's directory; its sibling ``right`` is the destination pane."""
    source = tmp_path / "left"
    dest = tmp_path / "right"
    source.mkdir()
    dest.mkdir()
    (source / "alpha.txt").write_text("alpha", encoding="utf-8")
    (source / "beta.txt").write_text("beta", encoding="utf-8")
    (source / "gamma.txt").write_text("gamma", encoding="utf-8")
    (source / "docs").mkdir()
    (source / "docs" / "deep.md").write_text("deep", encoding="utf-8")
    return source


@pytest.fixture
def trash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The trash directory the test owns, so ``f8`` never touches the real one.

    It is the directory the running platform actually drops items into: the
    flat macOS ``~/.Trash`` or the freedesktop ``files/`` below it.
    """
    root = tmp_path / "Trash"
    root.mkdir(parents=True)
    if sys.platform != "darwin":
        (root / "files").mkdir()
        (root / "info").mkdir()
    monkeypatch.setattr(FileManager, "_trash_root", lambda self: root)
    return root if sys.platform == "darwin" else root / "files"


def make_app(left: Path) -> DaylightApp:
    """An app whose panes start on ``left`` with an isolated config file."""
    app = DaylightApp(AppConfig())
    app.start_path = left
    app.config_manager = ConfigManager(left.parent / "config.json")
    app.file_manager = FileManager()
    return app


async def settle(pilot: Pilot, panel: FilePanel) -> None:
    """Wait until ``panel`` finished a listing and has rows."""
    for _ in range(SETTLE):
        if not panel.loading and panel.row_count:
            return
        await pilot.pause()
    raise AssertionError("panel did not finish listing")


async def wait_for(pilot: Pilot, predicate: Callable[[], bool], turns: int = SETTLE) -> None:
    """Pause until ``predicate`` holds, or fail after a bounded number of turns."""
    for _ in range(turns):
        if predicate():
            return
        await pilot.pause()
    raise AssertionError("condition never became true")


def plain_names(panel: FilePanel) -> list[str]:
    """Row names without the leading icon."""
    return [name.split(" ", 1)[1] for name in panel.visible_names()]


def cursor_on(panel: FilePanel, name: str) -> None:
    """Put the table cursor on the row called ``name``."""
    index = plain_names(panel).index(name)
    panel.table.cursor_coordinate = Coordinate(index, 0)


async def panes_ready(app: DaylightApp, pilot: Pilot) -> tuple[FilePanel, FilePanel]:
    """Settle both panels and return them."""
    page = app.query_one(FilesPage)
    left, right = page.panels
    await settle(pilot, left)
    await settle(pilot, right)
    return left, right


async def split_panes(app: DaylightApp, pilot: Pilot, tree: Path) -> tuple[FilePanel, FilePanel]:
    """Settle the app, then point the right pane at the sibling directory."""
    left, right = await panes_ready(app, pilot)
    right.go_to(tree.parent / "right")
    await settle(pilot, right)
    return left, right


def no_modal(app: DaylightApp) -> bool:
    """True when nothing is stacked on top of the app's own screen."""
    return len(app.screen_stack) == 1


def build_files(directory: Path, count: int, size: int) -> None:
    """Fill ``directory`` with ``count`` files of ``size`` bytes each."""
    for index in range(count):
        (directory / f"f{index:04d}.md").write_text("x" * size, encoding="utf-8")


def record_screens(app: DaylightApp) -> list[Any]:
    """Collect every screen pushed onto ``app``, so a test need not race one."""
    pushed: list[Any] = []
    original = app.push_screen

    def spy(screen: Any, callback: Any = None) -> Any:
        pushed.append(screen)
        return original(screen, callback)

    app.push_screen = spy  # type: ignore[method-assign]
    return pushed


#: The character Textual pairs with a key, for a hand-built ``Key`` event.
KEY_CHARACTERS = {"escape": "\x1b", "down": "\x1b[B", "f5": ""}


async def pump(app: DaylightApp, turns: int = 400) -> None:
    """Let the app chew on its messages without ever waiting for idle."""
    for _ in range(turns):
        await asyncio.sleep(0)


async def press_now(app: DaylightApp, key: str, *, turns: int = 400) -> None:
    """Deliver a key *while a worker thread is still running*.

    ``pilot.press`` cannot do this job: it ends on ``wait_for_idle``, which
    returns only once the queue has drained and every worker has finished, so
    by the time it returns a short operation is over and its modal gone.
    Posting the ``Key`` straight onto the app skips that wait, which is what a
    cancel needs.
    """
    app.post_message(Key(key=key, character=KEY_CHARACTERS.get(key, "")))
    await pump(app, turns)


async def until(app: DaylightApp, predicate: Callable[[], bool], *, turns: int = 20000) -> bool:
    """Spin the event loop until ``predicate`` holds; False if it never does.

    Like :func:`pump` this never waits for idle, so it can watch a worker
    thread mid-operation instead of after it.
    """
    for _ in range(turns):
        if predicate():
            return True
        await asyncio.sleep(0)
    return predicate()


def slow_disk(monkeypatch: pytest.MonkeyPatch, delay: float = 0.002) -> None:
    """Make every copied file take ``delay`` seconds.

    A real copy of a few hundred small files finishes inside the time a single
    ``pilot.press`` waits, which would turn "cancel a running copy" into a coin
    flip. This slows the real ``copy2`` instead of faking the operation, so the
    manager, the worker thread and the cancel flag are all still the real ones.
    """
    real = shutil.copy2

    def slow_copy(source: Any, destination: Any, **kwargs: Any) -> Any:
        time.sleep(delay)
        return real(source, destination, **kwargs)

    monkeypatch.setattr(shutil, "copy2", slow_copy)


# -- AC4: space marks rows, and a batch acts on all of them ------------------


@run_async
async def test_space_marks_and_unmarks_the_cursor_row(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        left, _ = await panes_ready(app, pilot)
        assert left.selected_count == 0
        cursor_on(left, "alpha.txt")
        await pilot.press("space")
        assert left.selected_count == 1
        assert left.is_selected(left.visible_entries[plain_names(left).index("alpha.txt")])
        assert "1 item selected" in left.breadcrumb_text()
        await pilot.press("space")
        assert left.selected_count == 0
        assert left.selection_marker() == ""


@run_async
async def test_the_dot_dot_row_can_never_be_marked(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        left, _ = await panes_ready(app, pilot)
        left.table.cursor_coordinate = Coordinate(0, 0)
        await pilot.pause()
        assert plain_names(left)[0] == ".."
        await pilot.press("space")
        assert left.selected_count == 0


@run_async
async def test_a_batch_copy_copies_every_marked_row(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        left, right = await split_panes(app, pilot, tree)
        for name in ("alpha.txt", "beta.txt"):
            cursor_on(left, name)
            await pilot.press("space")
        assert left.selected_count == 2
        await pilot.press("f5")
        await wait_for(pilot, lambda: (tree.parent / "right" / "beta.txt").exists())
        await wait_for(pilot, lambda: not right.loading)
        assert sorted(item.name for item in (tree.parent / "right").iterdir()) == [
            "alpha.txt",
            "beta.txt",
        ]
        assert (tree / "alpha.txt").exists(), "a copy leaves the source alone"


@run_async
async def test_copy_without_marks_uses_only_the_cursor_row(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        left, _ = await split_panes(app, pilot, tree)
        cursor_on(left, "gamma.txt")
        await pilot.press("f5")
        await wait_for(pilot, lambda: (tree.parent / "right" / "gamma.txt").exists())
        assert [item.name for item in (tree.parent / "right").iterdir()] == ["gamma.txt"]


@run_async
async def test_a_batch_move_takes_every_marked_row_with_it(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        left, _ = await split_panes(app, pilot, tree)
        for name in ("alpha.txt", "docs"):
            cursor_on(left, name)
            await pilot.press("space")
        await pilot.press("f6")
        await wait_for(pilot, lambda: (tree.parent / "right" / "docs").exists())
        assert not (tree / "alpha.txt").exists()
        assert not (tree / "docs").exists()
        assert (tree / "beta.txt").exists(), "an unmarked sibling stays put"


@run_async
async def test_a_copy_never_clobbers_an_existing_name(tree: Path) -> None:
    destination = tree.parent / "right"
    (destination / "alpha.txt").write_text("already here", encoding="utf-8")
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        left, _ = await split_panes(app, pilot, tree)
        cursor_on(left, "alpha.txt")
        await pilot.press("f5")
        await wait_for(pilot, lambda: (destination / "alpha-1.txt").exists())
        assert (destination / "alpha.txt").read_text(encoding="utf-8") == "already here"


@run_async
async def test_select_all_and_clear_selection(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        left, _ = await panes_ready(app, pilot)
        await pilot.press("ctrl+a")
        assert left.selected_count == len(plain_names(left)) - 1, "``..`` is never marked"
        assert "4 items selected" in left.breadcrumb_text()
        await pilot.press("ctrl+shift+a")
        assert left.selected_count == 0


@run_async
async def test_marks_survive_a_re_sort_but_not_a_navigation(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        left, _ = await panes_ready(app, pilot)
        cursor_on(left, "alpha.txt")
        await pilot.press("space")
        await pilot.press("ctrl+s")
        await wait_for(pilot, lambda: left.sort_key == "size" and not left.loading)
        assert left.selected_count == 1, "a re-sort is the same directory"
        cursor_on(left, "docs")
        await pilot.press("enter")
        await wait_for(pilot, lambda: left.directory == tree / "docs")
        assert left.selected_count == 0, "marks belong to the directory they were made in"


@run_async
async def test_filtering_drops_marks_it_hides(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        left, _ = await panes_ready(app, pilot)
        for name in ("alpha.txt", "beta.txt"):
            cursor_on(left, name)
            await pilot.press("space")
        await pilot.press("slash")
        await pilot.press(*"alpha")
        await wait_for(pilot, lambda: left.row_count == 1)
        assert left.selected_count == 1, "beta.txt is not visible, so it is not marked"


@run_async
async def test_the_filter_keeps_its_own_letters(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        left, _ = await panes_ready(app, pilot)
        await pilot.press("slash")
        assert isinstance(app.focused, Input)
        await pilot.press("d")
        assert left.filter_text == "d", "``d`` types a d, it must not fire an operation"
        assert no_modal(app), "no modal opened while typing in the filter"


# -- DC-012: no operation sits on a printable letter -------------------------


#: The actions DC-012 remapped and the key each must now carry.
DC012_OPERATION_KEYS = {
    "view": "f3",
    "edit": "f4",
    "copy_to_other": "f5",
    "move_to_other": "f6",
    "new_directory": "f7",
    "new_file": "shift+f7",
    "rename": "shift+f6",
    "delete": "f8",
    "delete_permanent": "shift+f8",
    "open_link": "f9",
    "properties": "alt+enter",
    "cycle_sort": "ctrl+s",
    "reverse_sort": "ctrl+r",
    "clear_selection": "ctrl+shift+a",
    "copy_property": "ctrl+c",
    "next_match": "ctrl+g",
}

#: The single printable letters the remap retired from the operation surface.
RETIRED_LETTER_KEYS = frozenset("vednDosriacN")


def test_no_operation_is_bound_to_a_printable_letter() -> None:
    """AC1/AC2: every operation key is an F-key or a ``ctrl+``/``shift+``/``alt+``
    chord, and no retired single letter survives in either binding layer."""
    page_keys = {binding.action: binding.key for binding in FilesPage.BINDINGS}
    for action, key in DC012_OPERATION_KEYS.items():
        assert page_keys[action] == key, f"{action} should be on {key!r}"
        assert DEFAULT_KEYBINDINGS[action] == key, f"config.py disagrees on {action}"
    assert not (set(page_keys.values()) & RETIRED_LETTER_KEYS), "a retired letter survived"
    for key in page_keys.values():
        assert len(key) > 1, f"{key!r} is a single printable letter"


# -- AC2: nothing destructive happens before the confirmation ----------------


@run_async
async def test_delete_asks_first_and_the_escape_deletes_nothing(tree: Path, trash: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        left, _ = await panes_ready(app, pilot)
        cursor_on(left, "alpha.txt")
        await pilot.press("f8")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        assert "alpha.txt" in app.screen.message
        await pilot.press("escape")
        await pilot.pause()
        assert (tree / "alpha.txt").exists(), "escape must leave the file alone"
        assert (tree / "alpha.txt").read_text(encoding="utf-8") == "alpha"
        assert list(trash.iterdir()) == []


@run_async
async def test_the_confirmation_no_button_deletes_nothing(tree: Path, trash: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        left, _ = await panes_ready(app, pilot)
        cursor_on(left, "alpha.txt")
        await pilot.press("f8")
        await wait_for(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        app.screen.query_one("#confirm-no", Button).press()
        await pilot.pause()
        assert (tree / "alpha.txt").exists()
        assert list(trash.iterdir()) == []


@run_async
async def test_the_n_key_also_means_no(tree: Path, trash: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        left, _ = await panes_ready(app, pilot)
        cursor_on(left, "alpha.txt")
        await pilot.press("f8")
        await wait_for(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        await pilot.press("n")
        await pilot.pause()
        assert (tree / "alpha.txt").exists()


@run_async
async def test_confirming_a_delete_sends_the_file_to_the_trash(tree: Path, trash: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        left, _ = await panes_ready(app, pilot)
        cursor_on(left, "alpha.txt")
        await pilot.press("f8")
        await wait_for(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        await pilot.press("y")
        await wait_for(pilot, lambda: not (tree / "alpha.txt").exists())
        assert (trash / "alpha.txt").read_text(encoding="utf-8") == "alpha"
        await wait_for(pilot, lambda: not left.loading)
        assert "alpha.txt" not in plain_names(left), "the listing refreshed"


@run_async
async def test_a_confirmed_batch_delete_names_everything_it_will_take(
    tree: Path, trash: Path
) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        left, _ = await panes_ready(app, pilot)
        for name in ("alpha.txt", "beta.txt", "gamma.txt"):
            cursor_on(left, name)
            await pilot.press("space")
        await pilot.press("f8")
        await wait_for(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        assert "3 items" in app.screen.message
        for name in ("alpha.txt", "beta.txt", "gamma.txt"):
            assert name in app.screen.detail
        assert "docs" not in app.screen.detail, "an unmarked row is not in the batch"
        await pilot.press("y")
        await wait_for(
            pilot,
            lambda: sorted(item.name for item in trash.iterdir())
            == ["alpha.txt", "beta.txt", "gamma.txt"],
        )
        assert (tree / "docs").exists(), "the unmarked directory survives"


@run_async
async def test_permanent_delete_is_a_separate_key_and_says_so(tree: Path, trash: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        left, _ = await panes_ready(app, pilot)
        cursor_on(left, "alpha.txt")
        await pilot.press("shift+f8")
        await wait_for(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        assert app.screen.dialog_title == "Permanently delete"
        assert "cannot be undone" in app.screen.message
        await pilot.press("y")
        await wait_for(pilot, lambda: not (tree / "alpha.txt").exists())
        assert list(trash.iterdir()) == [], "permanent skips the trash entirely"


# -- AC1: creating and renaming through the declarative form ----------------


@run_async
async def test_new_file_key_creates_a_file_through_the_form(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        left, _ = await panes_ready(app, pilot)
        await pilot.press("shift+f7")
        await wait_for(pilot, lambda: isinstance(app.screen, FormScreen))
        assert app.screen.form.name == "new_file"
        await pilot.press(*"notes.md")
        await pilot.press("enter")
        await wait_for(pilot, lambda: (tree / "notes.md").exists())
        assert (tree / "notes.md").read_text(encoding="utf-8") == ""
        await wait_for(pilot, lambda: "notes.md" in plain_names(left))


@run_async
async def test_f7_creates_a_directory_through_the_form(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        left, _ = await panes_ready(app, pilot)
        await pilot.press("f7")
        await wait_for(pilot, lambda: isinstance(app.screen, FormScreen))
        await pilot.press(*"newdir")
        await pilot.press("enter")
        await wait_for(pilot, lambda: (tree / "newdir").is_dir())
        assert (tree / "newdir").is_dir()


@run_async
async def test_a_cancelled_form_creates_nothing(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        _, _ = await panes_ready(app, pilot)
        await pilot.press("shift+f7")
        await wait_for(pilot, lambda: isinstance(app.screen, FormScreen))
        await pilot.press(*"notes.md")
        await pilot.press("escape")
        await pilot.pause()
        assert not (tree / "notes.md").exists()


@run_async
async def test_an_empty_name_never_reaches_the_disk(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        _, _ = await panes_ready(app, pilot)
        await pilot.press("shift+f7")
        await wait_for(pilot, lambda: isinstance(app.screen, FormScreen))
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, FormScreen), "an empty required name keeps the form open"
        assert app.screen.errors() == {"name": "Name is required"}
        assert sorted(item.name for item in tree.iterdir()) == [
            "alpha.txt",
            "beta.txt",
            "docs",
            "gamma.txt",
        ]


@run_async
async def test_rename_key_renames_the_entry_under_the_cursor(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        left, _ = await panes_ready(app, pilot)
        cursor_on(left, "alpha.txt")
        await pilot.press("shift+f6")
        await wait_for(pilot, lambda: isinstance(app.screen, FormScreen))
        assert app.screen.form.name == "rename"
        assert app.screen.values() == {"name": "alpha.txt"}, "the form is prefilled"
        for _ in range(len("alpha.txt")):
            await pilot.press("backspace")
        await pilot.press(*"renamed.txt")
        await pilot.press("enter")
        await wait_for(pilot, lambda: (tree / "renamed.txt").exists())
        assert not (tree / "alpha.txt").exists()
        assert (tree / "renamed.txt").read_text(encoding="utf-8") == "alpha"


@run_async
async def test_a_rename_onto_an_existing_name_is_refused(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        left, _ = await panes_ready(app, pilot)
        cursor_on(left, "alpha.txt")
        await pilot.press("shift+f6")
        await wait_for(pilot, lambda: isinstance(app.screen, FormScreen))
        for _ in range(len("alpha.txt")):
            await pilot.press("backspace")
        await pilot.press(*"beta.txt")
        await pilot.press("enter")
        await pilot.pause()
        assert (tree / "alpha.txt").exists(), "the refused rename changed nothing"
        assert (tree / "beta.txt").read_text(encoding="utf-8") == "beta"
        assert app._notifications, "the refusal is reported to the user"


@run_async
async def test_rename_does_nothing_on_the_dot_dot_row(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        left, _ = await panes_ready(app, pilot)
        left.table.cursor_coordinate = Coordinate(0, 0)
        await pilot.pause()
        await pilot.press("shift+f6")
        await pilot.pause()
        assert no_modal(app), "there is nothing to rename on ``..``"


# -- AC3: a long operation is a worker thread with progress and Cancel -------


def slow_operation(steps: int, delay: float = 0.004) -> Callable[..., object]:
    """A long operation that reports progress and honours the cancel flag.

    Deterministic on purpose: a real 400-file copy can finish before the keypress
    that cancels it reaches the worker, which would make the cancel test a race.
    """

    def operation(
        progress: Callable[[int, int, Path], None], cancelled: Callable[[], bool]
    ) -> object:
        from daylight.managers import OperationCancelled

        for step in range(1, steps + 1):
            if cancelled():
                raise OperationCancelled(f"cancelled at step {step}")
            time.sleep(delay)
            progress(step, steps, Path(f"/nowhere/step{step:04d}"))
        return steps

    return operation


@run_async
async def test_a_long_copy_runs_off_the_event_loop(
    tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC3: the worker thread does the work, so the loop keeps answering."""
    build_files(tree / "docs", 200, 256)
    slow_disk(monkeypatch)
    threads: list[str] = []
    original = FileManager.copy_into

    def spy(manager: FileManager, sources: Any, *args: Any, **kwargs: Any) -> Any:
        threads.append(threading.current_thread().name)
        return original(manager, sources, *args, **kwargs)

    monkeypatch.setattr(FileManager, "copy_into", spy)
    app = make_app(tree)
    pushed = record_screens(app)
    async with app.run_test(size=SIZE) as pilot:
        left, _ = await split_panes(app, pilot, tree)
        cursor_on(left, "docs")
        await press_now(app, "f5")
        screen = pushed[-1]
        assert isinstance(screen, ProgressScreen)
        assert screen.total == 1, "one marked entry means one batch item"
        assert screen.running is True
        await wait_for(pilot, lambda: (tree.parent / "right" / "docs" / "f0199.md").exists())
        await wait_for(pilot, lambda: no_modal(app), turns=4000)
    assert threads, "the copy never ran"
    assert all(
        name != threading.main_thread().name for name in threads
    ), f"the copy ran on the event loop: {threads}"


@run_async
async def test_the_progress_bar_counts_every_step_and_closes(tree: Path) -> None:
    app = make_app(tree)
    pushed: list[Any] = []
    async with app.run_test(size=SIZE) as pilot:
        left, _ = await panes_ready(app, pilot)
        pushed.append(ProgressScreen("Copying", slow_operation(30, delay=0.002), total=30))
        app.push_screen(pushed[-1])
        screen = pushed[-1]
        assert isinstance(screen.bar, ProgressBar)
        await wait_for(pilot, lambda: screen.bar.progress == 30, turns=400)
        assert screen.bar.total == 30
        assert str(screen.status.render()) == "done"
        await wait_for(pilot, lambda: no_modal(app), turns=200)
        await wait_for(pilot, lambda: not left.loading)


@run_async
async def test_the_event_loop_keeps_turning_during_a_long_copy(
    tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real AC3 measure: a timer on the loop still fires on time."""
    build_files(tree / "docs", 200, 512)
    slow_disk(monkeypatch)
    app = make_app(tree)
    pushed = record_screens(app)
    async with app.run_test(size=SIZE) as pilot:
        left, _ = await split_panes(app, pilot, tree)
        cursor_on(left, "docs")
        loop = asyncio.get_running_loop()
        fired = loop.create_future()
        scheduled = time.perf_counter()
        loop.call_later(0.01, lambda: fired.set_result(time.perf_counter() - scheduled))
        await press_now(app, "f5")
        screen = pushed[-1]
        assert isinstance(screen, ProgressScreen)
        assert screen.running is True, "the copy is still going"
        latency = await fired
        assert latency < 0.25, f"the event loop stalled for {latency:.3f}s during the copy"
        # The modal repaints as the copy runs, which only happens if the loop
        # is turning while the worker thread is busy.
        assert await until(app, lambda: screen.bar.progress > 0)
        assert str(screen.status.render()).endswith(".md")
        await wait_for(pilot, lambda: not screen.running, turns=8000)
        assert (tree.parent / "right" / "docs" / "f0199.md").exists()


@run_async
async def test_cancel_stops_a_running_operation_and_reports_it(tree: Path) -> None:
    app = make_app(tree)
    pushed: list[Any] = []
    async with app.run_test(size=SIZE) as pilot:
        left, _ = await panes_ready(app, pilot)
        pushed.append(ProgressScreen("Copying", slow_operation(400), total=400))
        app.push_screen(pushed[-1])
        screen = pushed[-1]
        await wait_for(pilot, lambda: screen.bar.progress > 0)
        await pilot.press("escape")
        assert screen.cancelled is True
        await wait_for(pilot, lambda: not screen.running, turns=400)
        assert str(screen.status.render()) == "cancelled"
        assert screen.bar.progress < 400, "it stopped short of the end"
        assert not no_modal(app), "a cancel stays on screen to be read"
        assert screen.query_one("#op-cancel", Button).label == "Close"
        await pilot.press("escape")
        await pilot.pause()
        assert no_modal(app)


@run_async
async def test_a_cancel_on_a_finished_operation_just_closes_it(tree: Path) -> None:
    app = make_app(tree)
    pushed: list[Any] = []
    async with app.run_test(size=SIZE) as pilot:
        left, _ = await panes_ready(app, pilot)
        pushed.append(ProgressScreen("Copying", slow_operation(3, delay=0.001), total=3))
        app.push_screen(pushed[-1])
        screen = pushed[-1]
        await wait_for(pilot, lambda: not screen.running)
        assert str(screen.status.render()) == "done"
        assert screen.bar.progress == 3
        assert no_modal(app), "the modal closed itself once the work was done"


@run_async
async def test_cancel_of_a_real_copy_leaves_the_rest_behind(
    tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    build_files(tree / "docs", 200, 512)
    slow_disk(monkeypatch)
    app = make_app(tree)
    pushed = record_screens(app)
    async with app.run_test(size=SIZE) as pilot:
        left, _ = await split_panes(app, pilot, tree)
        cursor_on(left, "docs")
        await press_now(app, "f5")
        screen = pushed[-1]
        assert isinstance(screen, ProgressScreen)
        assert await until(app, lambda: screen.bar.progress > 0)
        await press_now(app, "escape")
        assert screen.cancelled is True, "escape reached the running operation"
        assert await until(app, lambda: not screen.running), "the worker ignored the cancel"
        copied = tree.parent / "right" / "docs"
        assert copied.is_dir()
        landed = len(list(copied.iterdir()))
        assert 0 < landed < 200, f"the cancel must land part way, {landed} landed"
        assert (tree / "docs" / "f0199.md").exists(), "the source is untouched by a cancel"
        assert str(screen.status.render()) == "cancelled"


@run_async
async def test_a_finished_copy_refreshes_the_listing(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        left, right = await split_panes(app, pilot, tree)
        cursor_on(left, "alpha.txt")
        await pilot.press("f5")
        await wait_for(pilot, lambda: (tree.parent / "right" / "alpha.txt").exists())
        await wait_for(pilot, lambda: no_modal(app), turns=4000)
        await wait_for(pilot, lambda: not right.loading)
        assert [item.name for item in (tree.parent / "right").iterdir()] == ["alpha.txt"]
        await wait_for(pilot, lambda: not left.loading)
        assert "alpha.txt" in plain_names(left), "the source is still listed"


@run_async
async def test_a_failed_operation_says_so_and_does_not_freeze(tree: Path) -> None:
    """A long op that raises must leave a closable modal, not a stuck bar."""

    def explode(
        progress: Callable[[int, int, Path], None], cancelled: Callable[[], bool]
    ) -> object:
        raise OSError("no space left on device")

    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        left, _ = await panes_ready(app, pilot)
        left._run_operation("Copying", explode, total=1)
        screen = app.screen_stack[-1]
        assert isinstance(screen, ProgressScreen)
        await wait_for(pilot, lambda: not screen.running)
        assert "no space left on device" in str(screen.status.render())
        assert not no_modal(app), "a failure stays on screen to be read"
        await pilot.press("escape")
        await pilot.pause()
        assert no_modal(app)


@run_async
async def test_an_unexpected_error_also_ends_the_modal(tree: Path) -> None:
    def explode(
        progress: Callable[[int, int, Path], None], cancelled: Callable[[], bool]
    ) -> object:
        raise RuntimeError("something nobody planned for")

    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        left, _ = await panes_ready(app, pilot)
        left._run_operation("Copying", explode, total=1)
        screen = app.screen_stack[-1]
        await wait_for(pilot, lambda: not screen.running)
        assert "nobody planned for" in str(screen.status.render())
        await pilot.press("escape")
        await pilot.pause()
        assert no_modal(app)


# -- wiring ------------------------------------------------------------------


@run_async
async def test_the_modals_use_the_shared_container_frame(tree: Path) -> None:
    """ADR-007: one scrollable container with a header, a body and a footer."""
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await panes_ready(app, pilot)
        assert any("operations" in str(path) for path in app.css_path)
        app.push_screen(ConfirmScreen("Sure?", title="Confirm"))
        for _ in range(40):
            if app.screen.query(".modal-frame"):
                break
            await pilot.pause()
        assert app.screen.has_class("modal-container")
        assert app.screen.query_one(".modal-frame")
        assert app.screen.query_one(".modal-header")
        assert app.screen.query_one(".modal-body")
        assert app.screen.query_one(".modal-footer")
        assert str(app.screen.heading.render()) == "Confirm"
