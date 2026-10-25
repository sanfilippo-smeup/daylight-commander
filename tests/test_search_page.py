"""Files-page search mode tests (DC-037), driven through Textual's pilot.

These cover the page contract end to end: the ``Ctrl+F`` mode cycle, the query
that survives it, debounce with a worker thread, stale-result rejection,
``Escape``/navigation restore, the read-only hit rows and the ``Enter``
behaviors. The scanning side is covered in ``test_search_manager.py``; FR-104
(filter unchanged) is asserted by ``test_files_page.py`` and re-checked at the
end of the cycle test here.

``pytest-asyncio`` is not a project dependency, so ``run_async`` bridges the
async scenarios onto plain sync test functions, as everywhere else.
"""

from __future__ import annotations

import asyncio
import functools
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

import pytest
from textual.pilot import Pilot
from textual.widgets import Input

from daylight.app import DaylightApp
from daylight.components.modules.file_list import FilePanel, FilesPage
from daylight.components.shell import format_hints
from daylight.managers import ConfigManager, FileManager, search
from daylight.models import AppConfig, DirEntry, SearchHit

#: Generous ceiling: worker threads, the debounce timer and a rebuild need turns.
SETTLE = 60

#: Terminal size used for every pilot scenario.
SIZE = (120, 40)

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def run_async(fn: F) -> F:
    """Run an async test body on a fresh event loop (no pytest-asyncio needed)."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(fn(*args, **kwargs))

    return wrapper  # type: ignore[return-value]


def plant(tmp_path: Path) -> Path:
    """The deterministic tree the search scenarios recurse through."""
    tree = tmp_path / "tree"
    tree.mkdir()
    write(tree / "alpha.txt", "alpha seed\n")
    write(tree / "docs" / "alpha.md", "alpha prose\n")
    write(tree / "docs" / "nested" / "alpha-note.txt", "alpha deep\nsecond alpha\n")
    write(tree / "docs" / "nested" / "plain.md", "plain prose\nalpha plain\n")
    write(tree / "docs" / "nested" / "beta-file.txt", "beta prose\n")
    (tree / "alpha-dir").mkdir()
    write(tree / "alpha-dir" / "inner.txt", "inner alpha\n")
    return tree


def write(path: Path, text: str) -> None:
    """Create ``path``'s parent, then write the text to it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_app(tree: Path, config_path: Path, show_hidden: bool = False) -> DaylightApp:
    """Build an app pinned to ``tree`` with an isolated config file."""
    app = DaylightApp(AppConfig(show_hidden=show_hidden))
    app.start_path = tree
    app.config_manager = ConfigManager(config_path)
    app.file_manager = FileManager(show_hidden=show_hidden)
    return app


async def wait_for(pilot: Pilot, predicate: Callable[[], bool]) -> None:
    """Pause until ``predicate`` holds, or fail after a bounded number of turns.

    Each turn also yields real wall-clock time, so the debounce timer can
    actually expire between pauses; ``pause()`` alone pumps messages without
    waiting, and a timer would never fire.
    """
    for _ in range(SETTLE):
        if predicate():
            return
        await pilot.pause()
        await asyncio.sleep(0.01)
    raise AssertionError("condition never became true")


async def wait_listing(pilot: Pilot, panel: FilePanel, include_hidden: bool = False) -> None:
    """Wait until ``panel`` shows the plain listing (root entries + ``..``)."""
    children = list(panel.directory.iterdir())
    if not include_hidden:
        children = [child for child in children if not child.name.startswith(".")]
    expected = len(children) + 1
    await wait_for(pilot, lambda: not panel.loading and panel.row_count == expected)


def plain_names(panel: FilePanel) -> list[str]:
    """Row display text without the leading icon (the search rows included)."""
    return [row.split(" ", 1)[1] for row in panel.visible_names()]


def fake_hit(name: str) -> DirEntry:
    """A synthetic on-disk-nothing row for one fake search result."""
    return DirEntry(
        name=name,
        path=Path("/nowhere") / name,
        is_dir=False,
        is_hidden=False,
        is_symlink=False,
        size=8,
        modified=0.0,
        permissions="rw-r--r--",
        kind="file",
        suffix="py",
    )


class GateScans:
    """A double for the manager's search delegates: counted and gatable.

    Each call is recorded under its query and yields two synthetic hits, but
    only once the test opens that query's gate (``release``). Every yield
    point also checks the cancellation token first, so a scan that is
    superseded unwinds exactly like a real one. A gate never opened simply
    times the scan out, so no test can hang a worker at teardown.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.gates: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def gate_for(self, query: str) -> threading.Event:
        """The gate that holds a scan for ``query`` until the test opens it."""
        with self._lock:
            if query not in self.gates:
                gate = threading.Event()
                self.gates[query] = gate
            return self.gates[query]

    def release(self, query: str) -> None:
        """Let the parked scan for ``query`` continue past the gate."""
        self.gate_for(query).set()

    def iter_content_matches(
        self,
        root: Path,
        needle: str,
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> Any:
        """The gatable content-scan double (two hits per query)."""
        with self._lock:
            self.calls.append(needle)
        if cancelled is not None and cancelled():
            raise search.SearchCancelled()
        gate = self.gate_for(needle)
        for index in range(2):
            if cancelled is not None and cancelled():
                raise search.SearchCancelled()
            gate.wait(timeout=2.0)
            yield SearchHit(
                entry=fake_hit(f"hit-{needle}.py"),
                relative=f"hits/{needle}-{index}.py",
                line=index + 1,
                snippet=f"a {needle} line {index}",
            )

    def iter_name_matches(
        self,
        root: Path,
        needle: str,
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> Any:
        """The name-scan double delegates straight to the content double's shape."""
        generator = self.iter_content_matches(root, needle, cancelled=cancelled)
        for hit in generator:
            yield SearchHit(entry=hit.entry, relative=hit.relative)


# -- fixtures ---------------------------------------------------------------


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """The recursive tree both panels start in."""
    return plant(tmp_path)


def fake_scans(app: DaylightApp) -> GateScans:
    """Replace the running app's search delegates with the gatable double."""
    double = GateScans()
    app.file_manager.iter_content_matches = double.iter_content_matches  # type: ignore[method-assign]
    app.file_manager.iter_name_matches = double.iter_name_matches  # type: ignore[method-assign]
    return double


def release_all(scans: GateScans) -> None:
    """Open every gate, so no worker can still be parked at teardown."""
    for gate in scans.gates.values():
        gate.set()


# -- AC1: modes, cycle, query, '/' ------------------------------------------


@run_async
async def test_ctrl_f_cycles_the_three_modes(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await wait_listing(pilot, left)
        await pilot.press("ctrl+f")
        assert isinstance(app.focused, Input)
        assert left.search_mode == "recursive"
        assert page.filter_input.placeholder == "Recursive search\u2026"
        await pilot.press(*"alpha")
        await wait_for(
            pilot, lambda: left.search_active and [h.relative for h in left._search_rows]
        )
        assert [h.relative for h in left._search_rows][0] == "alpha-dir"
        # Cycle: content now, same query, and it re-runs the new mode.
        await pilot.press("ctrl+f")
        assert left.search_mode == "content"
        assert page.filter_input.placeholder == "Content search\u2026"
        assert page.filter_input.value == "alpha"
        await wait_for(
            pilot,
            lambda: left.search_active and left._search_rows and left._search_rows[0].is_content,
        )
        assert left._search_rows[0].relative.endswith(".txt")
        # Cycle: back to filter, query kept *and applied* to the listing -- the
        # bar says Filter over the query, so the table must agree with it (AC1).
        await pilot.press("ctrl+f")
        assert left.search_mode == "filter"
        assert page.filter_input.placeholder == "Filter\u2026"
        assert page.filter_input.value == "alpha"
        assert not left.search_active
        assert left.filter_text == "alpha"
        await wait_for(pilot, lambda: plain_names(left) == ["alpha-dir", "alpha.txt"])
        # The cycle wraps back round to recursive, query still intact.
        await pilot.press("ctrl+f")
        assert left.search_mode == "recursive"
        assert page.filter_input.placeholder == "Recursive search\u2026"
        assert page.filter_input.value == "alpha"


# -- AC1 + FR-104: '/' --------------------------------------------------------


@run_async
async def test_slash_always_opens_filter_mode(tree: Path, tmp_path: Path) -> None:
    """From a hit view with the bar already away, ``/`` is the Filter shortcut."""
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await wait_listing(pilot, left)
        await pilot.press("ctrl+f")
        await pilot.press("ctrl+f")
        await pilot.press(*"alpha")
        await wait_for(pilot, lambda: left.search_active and left._search_rows)
        await pilot.press("escape")  # bar away, listing restored
        await wait_for(pilot, lambda: not left.search_active)
        await pilot.press("slash")
        assert left.search_mode == "filter"
        assert page.filter_input.placeholder == "Filter\u2026"
        assert isinstance(app.focused, Input)
        # AC1: the bar still holds the preserved query and the table agrees
        # with it -- the listing is narrowed without a retype.
        assert page.filter_input.value == "alpha"
        assert left.filter_text == "alpha"
        await wait_for(pilot, lambda: plain_names(left) == ["alpha-dir", "alpha.txt"])
        # FR-104 filter-over-the-listing still works from here, as you type.
        page.filter_input.value = "docs"
        await wait_for(pilot, lambda: plain_names(left) == ["docs"])


@run_async
async def test_slash_on_an_engaged_panel_resets_to_filter(tree: Path, tmp_path: Path) -> None:
    """``/`` on a panel in a scan mode: scan off, hits gone, query applied (AC1).

    The query is typed through the shared bar the way a user does, so ``/``
    re-opens the bar in Filter mode with that query still in it -- and the
    listing it filters is narrowed by the same text, never left whole behind
    a bar that shows the query.
    """
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await wait_listing(pilot, left)
        await pilot.press("ctrl+f")  # recursive: the bar opens
        await pilot.press(*"alpha")
        await wait_for(pilot, lambda: left.search_active and left._search_rows)
        left.table.focus()  # back to the table, like a real click on the listing
        await pilot.pause()
        await pilot.press("slash")  # '/' on the engaged panel: Filter again
        await wait_for(pilot, lambda: not left.search_active)
        assert left.search_mode == "filter"
        assert page.filter_input.placeholder == "Filter\u2026"
        # AC1: the preserved query is applied to the plain listing as well;
        # the bar and the table agree from the first turn.
        assert page.filter_input.value == "alpha"
        assert left.filter_text == "alpha"
        await wait_for(pilot, lambda: plain_names(left) == ["alpha-dir", "alpha.txt"])


# -- AC1: the footer hint while a scan mode is engaged ------------------------


@run_async
async def test_the_footer_hints_the_engaged_search_mode(tree: Path, tmp_path: Path) -> None:
    """One compact footer hint names the cycling key and the engaged mode.

    The default Files footer outside an engaged recursive/content search stays
    byte-identical (test_shell.py pins it); this asserts only the dynamic
    addition and its disappearance on a mode change or a close.
    """
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await wait_listing(pilot, left)
        default = app.key_hints()
        assert not any("Search: " in description for _, description in default)
        # Recursive engaged, bar open: the footer names the mode on ctrl+f.
        await pilot.press("ctrl+f")
        await wait_for(pilot, lambda: ("ctrl+f", "Search: Recursive") in app.key_hints())
        assert app.hints.hint_text() == format_hints(app.key_hints())
        assert "Search: Recursive" in app.hints.hint_text()
        await pilot.press("ctrl+f")  # content engaged
        await wait_for(pilot, lambda: ("ctrl+f", "Search: Content") in app.key_hints())
        # Filter leg: the hint leaves again and the footer is the default one.
        await pilot.press("ctrl+f")
        await wait_for(pilot, lambda: app.key_hints() == default)
        # A closed search shows the default footer too, even in Recursive mode.
        await pilot.press("ctrl+f")  # recursive, bar open, hint back
        await wait_for(pilot, lambda: ("ctrl+f", "Search: Recursive") in app.key_hints())
        await pilot.press("escape")
        await wait_for(pilot, lambda: app.key_hints() == default)
        assert left.search_mode == "recursive"  # the mode survives; the hint does not


# -- AC3/AC4: relative rows, cap notice, snippets ------------------------------


@run_async
async def test_recursive_rows_are_relative_and_the_cap_notice_shows(
    tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Relative hit rows render in the table; a small cap turns into a notice."""
    monkeypatch.setattr(search, "SEARCH_RESULT_LIMIT", 4)
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await wait_listing(pilot, left)
        await pilot.press("ctrl+f")
        await pilot.press(*"alpha")
        await wait_for(pilot, lambda: left.search_active and len(left._search_rows) == 4)
        assert left.listing_marker() == "showing the first 4 matches"
        assert [h.relative for h in left._search_rows][0] == "alpha-dir"


@run_async
async def test_content_rows_render_path_line_and_snippet(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await wait_listing(pilot, left)
        await pilot.press("ctrl+f")
        await pilot.press("ctrl+f")
        await pilot.press(*"alpha")
        await wait_for(
            pilot,
            lambda: left.search_active and len(plain_names(left)) == 6,
        )
        # Walk order: inner-alpha (inside the alpha-named dir), then the root
        # file, then docs -- two hits in alpha-note.txt, one in plain.md.
        rows = plain_names(left)
        assert rows[0].startswith("alpha-dir/inner.txt:1")
        assert "inner alpha" in rows[0]
        assert rows[1].startswith("alpha.txt:1")
        assert "alpha seed" in rows[1]
        assert rows[2].startswith("docs/alpha.md:1")
        assert rows[3].startswith("docs/nested/alpha-note.txt:1")
        assert "alpha deep" in rows[3]
        assert rows[4].startswith("docs/nested/alpha-note.txt:2")
        assert "second alpha" in rows[4]
        assert rows[5].startswith("docs/nested/plain.md:2")
        assert left.listing_marker() == "6 matches"


# -- AC5: debounce, stale results, restore -------------------------------------


@run_async
async def test_a_superseded_scan_never_lands_and_the_ui_stays_responsive(
    tree: Path, tmp_path: Path
) -> None:
    """Blocked scans: the newer one replaces them; the loop keeps turning."""
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left, right = page.panels
        await wait_listing(pilot, left)
        scans = fake_scans(app)
        try:
            await pilot.press("ctrl+f")  # recursive
            await pilot.press(*"alpha")
            await wait_for(pilot, lambda: scans.calls == ["alpha"])
            assert scans.gate_for("alpha").is_set() is False
            # The event loop stays responsive while the first scan is parked.
            await pilot.press("ctrl+t")
            await pilot.pause()
            assert page.active_panel == "right"
            assert isinstance(app.focused, right.table.__class__)
            await pilot.press("ctrl+t")
            await wait_for(pilot, lambda: page.active_panel == "left")
            # A newer query schedules a second scan and cancels the first.
            # (The shared input keeps its focus through the pane switch, and
            # re-typing replaces the selection; the query is set the same way
            # any later turn would deliver it.)
            page.filter_input.value = "alphax"
            await wait_for(pilot, lambda: scans.calls == ["alpha", "alphax"])
            # Release the stale scan first: its late results must never land.
            scans.release("alpha")
            await pilot.pause()
            await pilot.pause()
            assert left.search_active is False or not any(
                hit.relative.endswith("alpha-0.py") or hit.relative.endswith("alpha-1.py")
                for hit in left._search_rows
            )
            scans.release("alphax")
            await wait_for(
                pilot,
                lambda: left.search_active and len(left._search_rows) == 2,
            )
            assert [hit.relative for hit in left._search_rows] == [
                "hits/alphax-0.py",
                "hits/alphax-1.py",
            ]
        finally:
            release_all(scans)


@run_async
async def test_a_completed_scan_delivered_after_the_escape_cannot_install(
    tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scan that finished before ``Escape`` still cannot install after it.

    The delivery a completed scan hands to ``call_from_thread`` is held back
    here, the user escapes, and only afterwards is that completed set
    delivered exactly as the worker queued it -- same mode, query, root and
    generation. The abandon advanced the generation, so the guards on
    ``_apply_search`` drop it although every other one still matches, and the
    plain listing survives.
    """
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await wait_listing(pilot, left)
        # Hold every finished delivery back instead of running it in place, so
        # the escape can pass between "scan done" and "hits applied".
        held: list[tuple[Any, ...]] = []
        real_apply = left._apply_search

        def hold(mode: str, root: Path, query: str, generation: int, hits: list[SearchHit]) -> None:
            held.append((mode, root, query, generation, hits))

        monkeypatch.setattr(left, "_apply_search", hold)
        await pilot.press("ctrl+f")
        await pilot.press(*"alpha")
        await wait_for(pilot, lambda: held)
        assert len(held) == 1
        assert left.searching  # the scan finished; its delivery is parked
        await pilot.press("escape")
        await wait_for(
            pilot,
            lambda: not left.searching
            and not page.filter_input.display
            and left.row_count == len(list(tree.iterdir())) + 1,
        )
        # The escape is processed; the queued delivery lands afterwards, through
        # the real application path with the parameters the scan captured.
        real_apply(*held[0])
        await pilot.pause()
        assert not left.search_active
        assert left._search_rows == []
        assert left.row_count == len(list(tree.iterdir())) + 1


@run_async
async def test_escape_restores_the_listing_and_drops_late_results(
    tree: Path, tmp_path: Path
) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await wait_listing(pilot, left)
        scans = fake_scans(app)
        await pilot.press("ctrl+f")
        await pilot.press(*"alpha")
        await wait_for(pilot, lambda: len(scans.calls) == 1)
        await pilot.press("escape")
        await wait_for(
            pilot,
            lambda: not left.search_active
            and page.filter_input.display is False
            and left.row_count == len(list(tree.iterdir())) + 1,
        )
        # The parked scan for "alpha" is released now; a late result may never
        # reopen the search view the user just left.
        scans.release("alpha")
        for _ in range(10):
            await pilot.pause()
        assert not left.search_active
        assert left.row_count == len(list(tree.iterdir())) + 1


@run_async
async def test_emptying_the_query_restores_the_listing(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await wait_listing(pilot, left)
        await pilot.press("ctrl+f")
        await pilot.press(*"alpha")
        await wait_for(pilot, lambda: left.search_active and left._search_rows)
        # One character left: below the scan floor, the listing returns.
        while len(page.filter_input.value) > 1:
            page.filter_input.value = page.filter_input.value[:-1]
            await pilot.pause()
        assert page.filter_input.value == "a"
        await wait_for(pilot, lambda: not left.search_active)
        assert left.row_count == len(list(tree.iterdir())) + 1
        page.filter_input.value = ""
        await pilot.pause()
        await wait_for(pilot, lambda: not left.search_active)


@run_async
async def test_a_directory_change_cancels_and_restores(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await wait_listing(pilot, left)
        scans = fake_scans(app)
        try:
            await pilot.press("ctrl+f")
            await pilot.press(*"alpha")
            await wait_for(pilot, lambda: len(scans.calls) == 1)
            await pilot.press("escape")
            await wait_for(pilot, lambda: not left.search_active)
            await pilot.press("ctrl+f")
            left.set_search_query("alpha")
            await wait_for(pilot, lambda: len(scans.calls) == 2)
            left.go_to(tree / "docs")
            await wait_for(
                pilot,
                lambda: left.directory == tree / "docs"
                and not left.search_active
                and not left.loading
                and left.row_count == 3,
            )
            # The parked "alpha" scans never land after the move.
            scans.release("alpha")
            for _ in range(10):
                await pilot.pause()
            assert not left.search_active and left.row_count == 3
            assert page.filter_input.display is False
        finally:
            release_all(scans)


@run_async
async def test_below_two_characters_no_scan_runs(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await wait_listing(pilot, left)
        scans = fake_scans(app)
        try:
            await pilot.press("ctrl+f")
            await pilot.press("a")
            for _ in range(12):
                await pilot.pause()
            assert scans.calls == []
            assert not left.search_active
            assert left.row_count == len(list(tree.iterdir())) + 1
            await pilot.press("l")  # "al" clears the scan floor now
            await wait_for(pilot, lambda: scans.calls == ["al"])
            await pilot.press("p")  # "alp": the pending debounce is replaced
            await wait_for(pilot, lambda: scans.calls.count("alp") == 1)
            assert scans.calls == ["al", "alp"]
        finally:
            release_all(scans)


# -- AC6: Enter per result kind -----------------------------------------------


@run_async
async def test_enter_on_a_recursive_file_hit_opens_the_viewer(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await wait_listing(pilot, left)
        await pilot.press("ctrl+f")
        await pilot.press(*"beta-f")
        await wait_for(
            pilot,
            lambda: left.search_active and [h.entry.name for h in left._search_rows],
        )
        assert left._search_rows[0].entry.name == "beta-file.txt"
        await pilot.press("enter")  # bar away, cursor onto the first hit
        await wait_for(pilot, lambda: left.cursor_index == 0 and not page.filter_input.display)
        await pilot.press("enter")  # open the hit
        await wait_for(pilot, lambda: page.viewer is not None and not page.viewer.loading)
        viewer = page.viewer
        assert viewer.path == tree / "docs" / "nested" / "beta-file.txt"
        assert viewer.kind == "text"


@run_async
async def test_enter_on_a_recursive_directory_hit_navigates(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await wait_listing(pilot, left)
        await pilot.press("ctrl+f")
        await pilot.press(*"nested")
        await wait_for(
            pilot,
            lambda: left.search_active and [h.entry.name for h in left._search_rows] == ["nested"],
        )
        await pilot.press("enter")
        await wait_for(pilot, lambda: left.cursor_index == 0 and not page.filter_input.display)
        await pilot.press("enter")
        await wait_for(pilot, lambda: left.directory == tree / "docs" / "nested")
        assert page.viewer is None
        assert not left.search_active
        assert page.filter_input.value == ""


@run_async
async def test_enter_on_a_content_hit_opens_the_viewer_on_the_matched_line(
    tree: Path, tmp_path: Path
) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await wait_listing(pilot, left)
        await pilot.press("ctrl+f")
        await pilot.press("ctrl+f")
        await pilot.press(*"second")
        await wait_for(
            pilot,
            lambda: left.search_active and len(left._search_rows) == 1,
        )
        hit = left._search_rows[0]
        assert (hit.relative, hit.line) == ("docs/nested/alpha-note.txt", 2)
        await pilot.press("enter")
        await wait_for(pilot, lambda: left.cursor_index == 0 and not page.filter_input.display)
        await pilot.press("enter")
        await wait_for(pilot, lambda: page.viewer is not None and not page.viewer.loading)
        viewer = page.viewer
        assert viewer.kind == "text"
        assert viewer.lines[1] == "second alpha"
        rows = max(1, viewer.window_rows)
        expected_top = max(0, 2 - 1 - (rows - 1) // 2)
        assert viewer.line_start == expected_top, (viewer.line_start, expected_top)


# -- AC7: result rows are read-only -------------------------------------------


@run_async
async def test_batch_operations_refuse_search_rows(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await wait_listing(pilot, left)
        await pilot.press("ctrl+f")
        await pilot.press(*"alpha")
        await wait_for(pilot, lambda: left.search_active and left._search_rows)
        assert left.operation_targets() == []
        assert left.select_all() == 0
        assert left.invert_selection() == 0
        assert left.selected_count == 0
        assert left.selected_entry() is None
        assert left.toggle_selection() is None
        assert left.selected_hit() is left._search_rows[left.cursor_index]


# -- hidden toggle re-run (card constraint) ------------------------------------


@run_async
async def test_the_hidden_toggle_reruns_the_active_search(tmp_path: Path) -> None:
    """A hidden toggle re-runs a live scan-mode search against the new setting."""
    tree = tmp_path / "tree"
    tree.mkdir()
    write(tree / "alpha.txt", "alpha seed\n")
    write(tree / ".secret.txt", "alpha secret\n")
    app = make_app(tree, tmp_path / "config.json", show_hidden=False)
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left = page.panel("left")
        await wait_listing(pilot, left)
        await pilot.press("ctrl+f")  # recursive
        await pilot.press(*"secret")
        await wait_for(
            pilot,
            lambda: left.search_active and left.listing_marker() == "no matches",
        )
        app.set_default("show_hidden", "yes")
        await wait_for(
            pilot,
            lambda: left.search_active and [h.entry.name for h in left._search_rows],
        )
        assert [h.entry.name for h in left._search_rows] == [".secret.txt"]
        assert left._search_rows[0].relative == ".secret.txt"


# -- panel isolation (card constraint) -----------------------------------------


@run_async
async def test_a_search_mode_and_its_results_stay_in_one_panel(tree: Path, tmp_path: Path) -> None:
    app = make_app(tree, tmp_path / "config.json")
    async with app.run_test(size=SIZE) as pilot:
        page = app.query_one(FilesPage)
        left, right = page.panels
        await wait_listing(pilot, left)
        await pilot.press("ctrl+f")
        await pilot.press(*"alpha")
        await wait_for(pilot, lambda: left.search_active and left._search_rows)
        # The other pane stays exactly where it was.
        assert right.search_mode == "filter"
        before_right = right.row_count
        page.switch_panel()
        await pilot.pause()
        assert right.search_active is False
        # The bar now names the new active pane's mode.
        assert page.filter_input.placeholder == "Filter\u2026"
        # ...and switching back finds the search view exactly as it was.
        page.switch_panel()
        await wait_for(pilot, lambda: page.filter_input.placeholder == "Recursive search\u2026")
        assert page.filter_input.value == "alpha"
        assert left.search_active and len(left._search_rows) == 4
        assert right.row_count == before_right
