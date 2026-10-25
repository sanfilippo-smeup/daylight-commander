"""App shell tests: header, hint bar, page tabs, command palette, theming.

These drive the real app through Textual's pilot, so they cover DC-005's four
acceptance criteria end to end. ``pytest-asyncio`` is not a project dependency,
so ``run_async`` bridges the async scenarios onto plain sync test functions.
"""

from __future__ import annotations

import asyncio
import functools
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

import pytest
from textual.binding import Binding
from textual.fuzzy import Matcher
from textual.pilot import Pilot
from textual.widgets import Input, OptionList, Static, Tabs

from daylight import themes
from daylight.app import PAGE_LABELS, DaylightApp
from daylight.components.command_palette import DaylightCommandProvider
from daylight.components.modules.dashboard_page import DashboardPage
from daylight.components.modules.file_list import FilesPage
from daylight.components.modules.properties_page import PropertiesPage
from daylight.components.modules.properties_view import PropertiesView
from daylight.components.modules.settings_page import SettingsPage
from daylight.components.modules.viewers_page import ViewersPage
from daylight.components.shell import HINT_SEPARATOR, format_hints, format_key
from daylight.managers import ConfigManager, FileManager
from daylight.models import AppConfig, DashboardBlock, DashboardSpec

#: Generous ceiling: worker threads and the window rebuild need a few turns.
SETTLE = 60

#: Terminal size used for every pilot scenario.
SIZE = (120, 40)

#: A minimal dashboard for the DC-036 tab-walk scenarios: the walk cares about
#: the tab and its page being mounted, not about what the dashboard shows.
NAV_DASH_SPEC = DashboardSpec(
    schema_version=1,
    title="Cycle dashboard",
    blocks=(DashboardBlock(kind="summary_cards", source="summary"),),
)

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def run_async(fn: F) -> F:
    """Run an async test body on a fresh event loop (no pytest-asyncio needed)."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(fn(*args, **kwargs))

    return wrapper  # type: ignore[return-value]


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A small, fixed tree for the listing, the viewer and the properties view."""
    (tmp_path / "notes.md").write_text("# Title\n\nbody\n", encoding="utf-8")
    (tmp_path / "data.json").write_text('{"a": 1}', encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "deep.txt").write_text("deep\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def config_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A config file of this test's own, so no run inherits another's choices."""
    return tmp_path_factory.mktemp("dc005") / "config.json"


def make_app(root: Path | None = None, config: Path | None = None) -> DaylightApp:
    """An app reading ``config`` and browsing ``root``.

    The config file is handed to the constructor rather than built in memory, so
    the app takes the same "load what was persisted" path a second run would. A
    file of its own per app keeps one test's theme out of the next one's.
    """
    if config is None:
        config = Path(tempfile.mkdtemp()) / "config.json"
    app = DaylightApp(config_path=config)
    app.config_manager = ConfigManager(config)
    app.file_manager = FileManager()
    if root is not None:
        app.start_path = root
    return app


async def wait_for_listing(pilot: Pilot) -> FilesPage:
    """Block until the Files page is composed and both panes listed."""
    app = pilot.app
    assert isinstance(app, DaylightApp)
    for _ in range(SETTLE):
        page = app.files_page
        if page is not None and page.is_ready:
            if all(not panel.loading and panel.row_count for panel in page.panels):
                return page
        await pilot.pause()
    raise AssertionError("the Files page did not finish listing")


async def wait_for(pilot: Pilot, condition: Callable[[DaylightApp], bool]) -> None:
    """Block until ``condition`` holds for the app."""
    for _ in range(SETTLE):
        if condition(pilot.app):  # type: ignore[arg-type]
            return
        await pilot.pause()
    raise AssertionError("the app did not reach the expected state")


# -- AC1: the persistent header and the hint bar ---------------------------


@run_async
async def test_the_header_shows_the_title_the_path_and_a_tab_per_page(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        title = app.query_one("#daylight-title", Static)
        assert DaylightApp.TITLE in str(title.content)
        assert app.header.path_text() == str(tree)
        labels = [str(tab.label) for tab in app.tabs.query("Tab")]
        assert labels == [PAGE_LABELS[name] for name in PAGE_LABELS]


@run_async
async def test_navigating_moves_the_path_in_the_header(tree: Path) -> None:
    """The header follows the listing: the page announces where it went."""
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        page = await wait_for_listing(pilot)
        page.active.move_cursor(1)  # docs/
        await pilot.pause()
        page.active.enter_selected()
        await wait_for(pilot, lambda app: app.header.path_text() == str(tree / "docs"))
        assert app.current_path() == tree / "docs"


@run_async
async def test_switching_pane_moves_the_path_in_the_header(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        page = await wait_for_listing(pilot)
        page.active.go_to(tree / "docs")
        await wait_for(pilot, lambda app: app.header.path_text() == str(tree / "docs"))
        page.switch_panel()
        await wait_for(pilot, lambda app: app.header.path_text() == str(tree))
        assert app.current_path() == tree


@run_async
async def test_the_hint_bar_shows_the_page_keys_and_the_app_keys(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        hints = app.hints.hint_text()
        assert hints == format_hints(app.key_hints())
        # DC-019: the footer reads the frequent operations, then the global
        # keys -- Palette, Switch pane, Quit -- and nothing else.
        assert app.key_hints() == [
            ("f5", "Copy"),
            ("f6", "Move"),
            ("f7", "New dir"),
            ("f8", "Delete"),
            ("ctrl+p", "Palette"),
            ("ctrl+t", "Switch pane"),
            ("ctrl+q", "Quit"),
        ]
        assert hints == (
            "F5 Copy · F6 Move · F7 New dir · F8 Delete · Ctrl+P Palette"
            " · Ctrl+T Switch pane · Ctrl+Q Quit"
        )
        # No stale hint: navigation, sort, filter, view and edit are
        # palette-only now (DC-019).
        for stale in ("Sort", "Reverse", "Filter", "Up", "View", "Edit", "Hidden"):
            assert stale not in hints
        # The footer stays one line at ~120 columns.
        assert len(hints) <= 120


@run_async
async def test_the_hint_bar_follows_the_page_on_screen(tree: Path) -> None:
    """AC1: the hints are the ones that are active in the current view."""
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        assert "Switch pane" in app.hints.hint_text()
        await pilot.press("ctrl+4")
        await wait_for(pilot, lambda app: app.active_page == "settings")
        # The settings page has its own navigation keys, and AC1 asks for the
        # hints of the page that is actually on screen.
        assert app.hints.hint_text() == format_hints(
            [
                ("d", "Defaults"),
                ("k", "Keys"),
                ("ctrl+p", "Palette"),
                ("ctrl+q", "Quit"),
            ]
        )


def test_a_page_binding_and_an_app_binding_on_the_same_key_keep_one_hint() -> None:
    app = make_app()
    app._pages["files"] = FilesPage(Path("/tmp"))
    keys = [key for key, _ in app.key_hints()]
    assert len(keys) == len(set(keys))


# -- AC2: every action in the palette ---------------------------------------


def test_the_palette_offers_every_page_action() -> None:
    """Every action a page can run is reachable from the palette, hidden ones too.

    Pages without a tab label (DC-030's dynamic Dashboard) are covered where
    they are opened: their palette entries exist only while the page exists.
    """
    app = make_app()
    names = {command.name for command in app.palette_commands()}
    for page_name, page_class in DaylightApp.PAGES.items():
        label = PAGE_LABELS.get(page_name)
        if label is None:
            continue
        for binding in Binding.make_bindings(list(page_class.BINDINGS)):
            title = binding.action.replace("_", " ").capitalize()
            assert f"{label}: {title}" in names, binding.action


def test_the_palette_offers_every_app_binding() -> None:
    app = make_app()
    names = {command.name for command in app.palette_commands()}
    for binding in Binding.make_bindings(list(DaylightApp.BINDINGS)):
        assert (binding.description or binding.action) in names


def test_the_palette_offers_a_command_per_page_and_per_theme() -> None:
    names = {command.name for command in make_app().palette_commands()}
    for label in PAGE_LABELS.values():
        assert f"Go to {label}" in names
    for theme in themes.theme_names():
        assert f"Theme: {theme}" in names


def test_every_palette_command_is_listed_exactly_once() -> None:
    """A duplicated entry is a row the user has to read past to reach the action."""
    names = [command.name for command in make_app().palette_commands()]
    assert len(names) == len(set(names))


def test_a_go_to_search_offers_one_entry_per_page() -> None:
    """The tab switches must not be listed twice, once per tab and once per page."""
    commands = make_app().palette_commands()
    matcher = Matcher("go to")
    assert [command.name for command in commands if matcher.match(command.name) > 0] == [
        f"Go to {label}" for label in PAGE_LABELS.values()
    ]


def test_every_palette_command_is_fuzzy_findable() -> None:
    """A fuzzy matcher must find a command from its initials, not only in full."""
    for command in make_app().palette_commands():
        assert Matcher(command.name).match(command.name) > 0, command.name
        initials = "".join(part[0] for part in command.name.split() if part)
        assert Matcher(initials).match(command.name) > 0, initials


def test_every_palette_command_has_a_description_and_a_callback() -> None:
    for command in make_app().palette_commands():
        assert command.name and command.description
        assert callable(command.run)


@run_async
async def test_clearing_the_palette_query_offers_every_command() -> None:
    """An empty query means "everything", which is what an emptied palette asks for."""
    app = make_app()
    listed = [hit async for hit in DaylightCommandProvider(app).search("")]
    assert [hit.display for hit in listed] == [command.name for command in app.palette_commands()]


def test_the_palette_is_a_registered_command_provider() -> None:
    """Without this the palette would only offer Textual's own system commands."""
    assert DaylightCommandProvider in DaylightApp.COMMANDS
    assert DaylightApp.COMMAND_PALETTE_BINDING == "ctrl+p"


@run_async
async def test_ctrl_p_opens_the_palette_and_fuzzy_search_finds_a_page(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        await pilot.press("ctrl+p")
        await wait_for(pilot, lambda app: app.screen.__class__.__name__ == "CommandPalette")
        await pilot.press(*"setng")
        await pilot.pause()
        assert "Go to Settings" in palette_prompts(app)
        await pilot.press("enter")
        await wait_for(pilot, lambda app: app.active_page == "settings")


@run_async
async def test_a_page_action_from_the_palette_runs_on_that_page(tree: Path) -> None:
    """The palette is a remote control: it shows the page and acts on it."""
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        app.show_page("settings")
        await wait_for(pilot, lambda app: app.active_page == "settings")
        app.run_page_action("files", "cycle_sort")
        await pilot.pause()
        page = app.query_one(FilesPage)
        assert page.panel("left").sort_key == "size"
        assert app.active_page == "files"


@run_async
async def test_running_an_action_a_page_does_not_have_warns_instead_of_crashing(
    tree: Path,
) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        app.run_page_action("files", "no_such_action")
        await pilot.pause()
        assert app.active_page == "files"


# -- AC3: tabs switch pages without losing state ----------------------------


@run_async
async def test_activating_a_tab_shows_that_page_and_hides_the_last_one(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        page = await wait_for_listing(pilot)
        assert page.display is True
        for name in ("viewers", "properties", "settings"):
            await pilot.press(f"ctrl+{list(PAGE_LABELS).index(name) + 1}")
            await wait_for(pilot, lambda app, name=name: app.active_page == name)
            assert page.display is False
            assert app.tabs.active == name
            assert app.query_one(f"#{name}-page").display is True


@run_async
async def test_every_tab_is_reachable_by_clicking_it(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        await pilot.click(Tabs)
        for _ in range(SETTLE):
            await pilot.pause()
        assert app.active_page == "files"


@run_async
async def test_a_tab_round_trip_keeps_the_files_page_state(tree: Path) -> None:
    """AC3: no state loss. Marks, filter, sort and scroll position all survive."""
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        page = await wait_for_listing(pilot)
        page.active.go_to(tree / "docs")
        await wait_for(pilot, lambda app: page.active.row_count > 0)
        page.active.set_filter("deep")
        page.active.cycle_sort()
        await pilot.pause()
        page.active.toggle_selection(0)
        before = (page.active.directory, page.active.filter_text, page.active.sort_key)
        marked = page.active.selected_count
        assert marked == 1
        for key in ("ctrl+4", "ctrl+2", "ctrl+3", "ctrl+1"):
            await pilot.press(key)
            await pilot.pause()
        await wait_for(pilot, lambda app: app.active_page == "files")
        assert page.display is True
        assert page.active.directory == before[0]
        assert page.active.filter_text == before[1]
        assert page.active.sort_key == before[2]
        assert page.active.selected_count == marked
        assert [entry.name for entry in page.active.visible_entries] == ["deep.txt"]


@run_async
async def test_the_viewers_tab_reads_the_file_the_files_page_opened(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        page = await wait_for_listing(pilot)
        page.active.move_cursor(_index_of(page, "notes.md"))
        page.open_view()
        await pilot.pause()
        await pilot.press("ctrl+2")
        await wait_for(pilot, lambda app: app.active_page == "viewers")
        viewers = app.query_one(ViewersPage)
        assert viewers.path == tree / "notes.md"
        await wait_for(pilot, lambda app: viewers.viewer is not None)
        assert viewers.viewer is not None
        assert viewers.viewer.kind == "markdown"


@run_async
async def test_the_viewers_tab_says_so_when_there_is_nothing_to_read(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        await pilot.press("ctrl+2")
        await wait_for(pilot, lambda app: app.active_page == "viewers")
        viewers = app.query_one(ViewersPage)
        assert viewers.path is None
        assert viewers.viewer is None
        assert "No file open" in str(viewers.notice.content)


@run_async
async def test_the_properties_tab_describes_the_opened_entry(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        page = await wait_for_listing(pilot)
        page.active.move_cursor(_index_of(page, "notes.md"))
        page.open_properties()
        await pilot.pause()
        await pilot.press("ctrl+3")
        await wait_for(pilot, lambda app: app.active_page == "properties")
        properties = app.query_one(PropertiesPage)
        assert properties.path == tree / "notes.md"
        await wait_for(pilot, lambda app: properties.view is not None)
        assert properties.view is not None
        await wait_for(pilot, lambda app: bool(properties.view.rows))
        assert "Size" in {label for label, _ in properties.view.rows}


@run_async
async def test_every_tab_says_how_to_get_back_to_the_listing(tree: Path) -> None:
    """AC3: no dead ends -- each tab's advertised keys really work from that tab.

    A page that shows a key in the hint bar but cannot receive it is a dead end,
    so this presses the page's own key on the page itself.
    """
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        page = await wait_for_listing(pilot)
        page.active.move_cursor(_index_of(page, "notes.md"))
        page.open_view()
        await pilot.pause()

        await pilot.press("ctrl+2")
        await wait_for(pilot, lambda app: app.active_page == "viewers")
        viewers = app.query_one(ViewersPage)
        await wait_for(pilot, lambda app: viewers.viewer is not None)
        await pilot.press("escape")
        await wait_for(pilot, lambda app: viewers.viewer is None)
        assert viewers.path is None
        # The Files page was never disturbed by closing the tab's viewer.
        assert page.viewer is not None


@run_async
async def test_the_properties_tab_key_works_from_the_properties_tab(tree: Path) -> None:
    """AC3: ``c`` on the Properties tab reaches the tab, not just the Files page."""
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        page = await wait_for_listing(pilot)
        page.active.move_cursor(_index_of(page, "notes.md"))
        page.open_properties()
        await pilot.pause()
        await pilot.press("ctrl+3")
        await wait_for(pilot, lambda app: app.active_page == "properties")
        properties = app.query_one(PropertiesPage)
        view = properties.view
        assert isinstance(view, PropertiesView)
        await wait_for(pilot, lambda app: bool(view.rows))

        copied: list[str] = []

        def record() -> str:
            value = view.rows[0][1]
            copied.append(value)
            return value

        view.copy_selected = record  # type: ignore[method-assign]
        await pilot.press("c")
        await wait_for(pilot, lambda app: bool(copied))
        assert copied


@run_async
async def test_the_settings_tab_lists_the_themes_and_takes_the_keyboard(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        await pilot.press("ctrl+4")
        await wait_for(pilot, lambda app: app.active_page == "settings")
        settings = app.query_one(SettingsPage)
        assert [str(option.prompt) for option in settings.themes_list._options] == list(
            themes.theme_names()
        )
        await wait_for(pilot, lambda app: app.focused is settings.themes_list)
        assert settings.selected_theme == app.app_config.theme


# -- DC-036: the portable tab walk (ctrl+n / ctrl+b) -------------------------


@run_async
async def test_ctrl_n_visits_every_tab_in_strip_order_and_wraps(tree: Path) -> None:
    """AC1/AC3: no dashboard open, the walk covers the seven static tabs once."""
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        for name in list(PAGE_LABELS)[1:]:
            await pilot.press("ctrl+n")
            await wait_for(pilot, lambda app, name=name: app.active_page == name)
        # AC4: the tab highlight, the page and the hints all agree at each stop.
        assert app.tabs.active == app.active_page
        assert app.query_one("#filetypes-page").display is True
        assert app.hints.hint_text() == format_hints(app.key_hints())
        await pilot.press("ctrl+n")
        await wait_for(pilot, lambda app: app.active_page == "files")
        assert app.tabs.active == "files"


@run_async
async def test_ctrl_b_walks_backwards_and_wraps_first_to_last(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        await pilot.press("ctrl+b")
        await wait_for(pilot, lambda app: app.active_page == "filetypes")
        for name in list(PAGE_LABELS)[-2:0:-1]:
            await pilot.press("ctrl+b")
            await wait_for(pilot, lambda app, name=name: app.active_page == name)
        await pilot.press("ctrl+b")
        await wait_for(pilot, lambda app: app.active_page == "files")


@run_async
async def test_the_walk_pair_returns_to_the_starting_page(tree: Path) -> None:
    """AC1: one step out and one step back lands where it began."""
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        await pilot.press("ctrl+n")
        await wait_for(pilot, lambda app: app.active_page == "viewers")
        await pilot.press("ctrl+b")
        await wait_for(pilot, lambda app: app.active_page == "files")
        await pilot.press("ctrl+b")
        await wait_for(pilot, lambda app: app.active_page == "filetypes")
        await pilot.press("ctrl+n")
        await wait_for(pilot, lambda app: app.active_page == "files")


@run_async
async def test_the_walk_fires_from_an_input_and_the_viewer_body(tree: Path) -> None:
    """AC2: app-level keys, never falling through to a page binding.

    The same walk is pressed once with the viewer's body focused (the Viewers
    tab hands its keyboard to the whole page) and once with the filter input's
    keystroke pending, so the keys fire whatever is on screen.
    """
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        page = await wait_for_listing(pilot)
        page.active.move_cursor(_index_of(page, "notes.md"))
        page.open_view()
        await pilot.pause()
        await pilot.press("ctrl+2")
        await wait_for(pilot, lambda app: app.active_page == "viewers")
        await wait_for(pilot, lambda app: app.focused is app.query_one(ViewersPage))
        await pilot.press("ctrl+n")
        await wait_for(pilot, lambda app: app.active_page == "properties")

        await pilot.press("ctrl+1")
        await wait_for(pilot, lambda app: app.active_page == "files")
        await pilot.press("slash")
        await wait_for(pilot, lambda app: isinstance(app.focused, Input))
        await pilot.press("ctrl+n")
        await wait_for(pilot, lambda app: app.active_page == "viewers")
        assert app.tabs.active == "viewers"


@run_async
async def test_a_dashboard_tab_joins_the_walk_only_while_open(tree: Path) -> None:
    """AC3: the dynamic tab is one more stop while it is mounted, none otherwise."""
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        # Without a dashboard the last tab never walks past the first page.
        await pilot.press("ctrl+7")
        await wait_for(pilot, lambda app: app.active_page == "filetypes")
        assert app._live_tab_pages() == list(PAGE_LABELS)
        await pilot.press("ctrl+n")
        await wait_for(pilot, lambda app: app.active_page == "files")

        # Once a dashboard is opened, its tab is the strip's one dynamic stop.
        app.open_dashboard(NAV_DASH_SPEC, tree)
        await wait_for(pilot, lambda app: app.active_page == "dashboard")
        await wait_for(pilot, lambda app: app._dashboard_is_live())
        await wait_for(pilot, lambda app: app.tabs.get_tab("dashboard") is not None)
        assert app._live_tab_pages() == [*PAGE_LABELS, "dashboard"]
        # Backwards from the dashboard re-enters the static walk at its end.
        await pilot.press("ctrl+b")
        await wait_for(pilot, lambda app: app.active_page == "filetypes")
        assert app.tabs.active == "filetypes"
        # Forwards from the filetypes tab the dashboard stop is on the way first.
        await pilot.press("ctrl+n")
        await wait_for(pilot, lambda app: app.active_page == "dashboard")
        assert app.tabs.active == "dashboard"
        assert isinstance(app._pages["dashboard"], DashboardPage)
        assert app._pages["dashboard"].display is True  # no empty page
        await pilot.press("ctrl+n")
        await wait_for(pilot, lambda app: app.active_page == "files")
        await pilot.press("ctrl+b")
        await wait_for(pilot, lambda app: app.active_page == "dashboard")


@run_async
async def test_the_palette_offers_next_and_previous_tab_and_runs_them(
    tree: Path,
) -> None:
    """AC5: the exact titles exist and run the same walk actions."""
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        commands = {command.name: command for command in app.palette_commands()}
        commands["Next tab"].run()
        await wait_for(pilot, lambda app: app.active_page == "viewers")
        commands["Previous tab"].run()
        await wait_for(pilot, lambda app: app.active_page == "files")


def test_the_walk_is_a_no_op_before_the_app_composes() -> None:
    """Fewer than two live tabs: nothing to cycle, nothing to crash."""
    app = make_app()
    app.action_next_page()
    app.action_previous_page()
    assert app.active_page == "files"


# -- AC4: themes switch at runtime and the choice persists ------------------


@run_async
async def test_switching_the_theme_changes_the_published_tokens(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        before = app.get_css_variables()["background"]
        app.set_theme("daylight-light")
        await pilot.pause()
        assert app.theme == "daylight-light"
        assert app.get_css_variables()["background"] == themes.THEMES["daylight-light"].background
        assert app.get_css_variables()["background"] != before


@run_async
async def test_a_theme_switch_is_remembered_for_the_next_run(tree: Path, config_path: Path) -> None:
    """AC4 read back from disk, the way the hidden-toggle persistence is tested.

    Nothing is carried over in memory: the second app is a different object with
    its own config manager, standing in for the next run.
    """
    app = make_app(tree, config_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        app.set_theme("daylight-contrast")
        await pilot.pause()
    assert ConfigManager(config_path).load().theme == "daylight-contrast"
    assert make_app(tree, config_path).app_config.theme == "daylight-contrast"


@run_async
async def test_the_settings_picker_switches_the_theme_and_persists_it(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        await pilot.press("ctrl+4")
        await wait_for(pilot, lambda app: app.focused is not None)
        settings = app.query_one(SettingsPage)
        await pilot.press("down")
        await wait_for(pilot, lambda app: settings.selected_theme == "daylight-light")
        await pilot.press("enter")
        await wait_for(pilot, lambda app: app.app_config.theme == "daylight-light")
        assert app.theme == "daylight-light"
        assert settings.selected_theme == "daylight-light"
        assert ConfigManager(app.config_manager.path).load().theme == "daylight-light"


@run_async
async def test_the_palette_switches_the_theme(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        await pilot.press("ctrl+p")
        await wait_for(pilot, lambda app: app.screen.__class__.__name__ == "CommandPalette")
        await pilot.press(*"Theme: daylight-light")
        await pilot.pause()
        await pilot.press("enter")
        await wait_for(pilot, lambda app: app.app_config.theme == "daylight-light")


@run_async
async def test_next_theme_walks_the_catalogue_and_wraps(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        names = themes.theme_names()
        for expected in names[1:] + names[:1]:
            app.action_next_theme()
            await pilot.pause()
            assert app.app_config.theme == expected


@run_async
async def test_an_unknown_theme_is_refused_and_nothing_is_persisted(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        app.set_theme("no-such-theme")
        await pilot.pause()
        assert app.app_config.theme == "daylight-dark"
        assert app.theme == "daylight-dark"
        assert not app.config_manager.path.exists()


@run_async
async def test_every_theme_is_registered_with_the_app(tree: Path) -> None:
    app = make_app(tree)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        for name in themes.theme_names():
            assert app.get_theme(name) is not None


# -- the settings page reports the configured keybindings -------------------


@run_async
async def test_the_settings_tab_shows_the_configured_keybinding_overrides(
    tree: Path, config_path: Path
) -> None:
    ConfigManager(config_path).save(AppConfig(keybindings={"quit": "ctrl+x"}))
    app = make_app(tree, config_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for_listing(pilot)
        await pilot.press("ctrl+4")
        await wait_for(pilot, lambda app: app.active_page == "settings")
        listing = app.query_one(SettingsPage).keys_text()
        quit_row = next(line for line in listing.splitlines() if line.endswith("quit"))
        assert quit_row.startswith("Ctrl+X")


# -- the hint text itself ---------------------------------------------------


def test_a_key_is_shown_the_way_the_user_presses_it() -> None:
    assert format_key("ctrl+p") == "Ctrl+P"
    assert format_key("slash") == "/"
    assert format_key("backspace") == "⌫"
    assert format_key("f5") == "F5"


def test_hints_are_one_line_of_key_then_description() -> None:
    assert format_hints([("ctrl+p", "Palette"), ("ctrl+q", "Quit")]) == (
        f"Ctrl+P Palette{HINT_SEPARATOR}Ctrl+Q Quit"
    )


def test_the_bar_can_be_empty() -> None:
    assert format_hints([]) == ""


# -- helpers ----------------------------------------------------------------


def palette_prompts(app: DaylightApp) -> list[str]:
    """The command titles the open palette is showing, one per option.

    The palette renders a title and its description on the same option, so only
    the first line is the command itself.
    """
    options = app.screen.query_one(OptionList)._options
    return [str(option.prompt).splitlines()[0] for option in options]


def _index_of(page: FilesPage, name: str) -> int:
    """Index of ``name`` in the active panel's listing."""
    for index, entry in enumerate(page.active.visible_entries):
        if entry.name == name:
            return index
    raise AssertionError(f"{name} is not in the listing")
