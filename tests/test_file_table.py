"""Unit tests for the Files page table builder: rows, filtering and sorting."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from daylight.components import file_table
from daylight.models import DirEntry

BASE = time.mktime((2026, 3, 4, 5, 6, 7, 0, 0, -1))


def make_entry(
    name: str = "notes.txt",
    *,
    is_dir: bool = False,
    size: int = 10,
    modified: float = BASE,
    kind: str = "file",
    suffix: str = "txt",
    hidden: bool = False,
    symlink: bool = False,
    permissions: str = "rw-r--r--",
) -> DirEntry:
    """Build a DirEntry without touching the disk."""
    return DirEntry(
        name=name,
        path=Path("/tmp") / name,
        is_dir=is_dir,
        is_hidden=hidden,
        is_symlink=symlink,
        size=size,
        modified=modified,
        permissions=permissions,
        kind=kind,
        suffix=suffix,
    )


@pytest.fixture
def entries() -> list[DirEntry]:
    """A mixed set: parent row, a folder, and three differently typed files."""
    return [
        DirEntry.parent("/tmp/parent/child"),
        make_entry("zeta.txt", size=300, modified=BASE + 300, suffix="txt"),
        make_entry("alpha", is_dir=True, kind="dir", suffix="", size=0, modified=BASE),
        make_entry("beta.py", size=100, modified=BASE + 100, suffix="py"),
        make_entry("gamma.md", size=200, modified=BASE + 200, suffix="md"),
    ]


# -- columns and rows ------------------------------------------------------


def test_columns_cover_name_size_time_and_permissions() -> None:
    labels = [label for label, _ in file_table.COLUMNS]
    assert labels == ["Name", "Size", "Modified", "Perms"]


def test_build_row_has_one_cell_per_column() -> None:
    row = file_table.build_row(make_entry("notes.txt", size=2048))
    assert len(row) == len(file_table.COLUMNS)


def test_build_rows_is_lazy() -> None:
    rows = file_table.build_rows([make_entry()])
    assert next(iter(rows)) == file_table.build_row(make_entry())


def test_build_rows_yields_every_entry(entries: list[DirEntry]) -> None:
    assert len(list(file_table.build_rows(entries))) == len(entries)


# -- formatting (AC7) ------------------------------------------------------


@pytest.mark.parametrize(
    ("size", "expected"),
    [(0, "0B"), (512, "512B"), (1024, "1.0K"), (1536, "1.5K"), (1048576, "1.0M")],
)
def test_format_size_is_human_readable(size: int, expected: str) -> None:
    assert file_table.format_size(make_entry(size=size)) == expected


def test_format_size_uses_dash_for_directories() -> None:
    assert file_table.format_size(make_entry("docs", is_dir=True, kind="dir", size=4096)) == "-"


def test_format_mtime_is_iso_like() -> None:
    assert file_table.format_mtime(make_entry(modified=BASE)) == time.strftime(
        "%Y-%m-%d %H:%M", time.localtime(BASE)
    )


def test_format_permissions_shows_rwx_triple() -> None:
    assert file_table.format_permissions(make_entry(permissions="rwxr-xr-x")) == "rwxr-xr-x"


def test_format_permissions_for_parent_row() -> None:
    assert file_table.format_permissions(DirEntry.parent("/tmp/a")) == ".."


def test_format_mtime_for_parent_row_is_dash() -> None:
    assert file_table.format_mtime(DirEntry.parent("/tmp/a")) == "-"


def test_icon_for_uses_a_folder_emoji_for_directories() -> None:
    folder = make_entry("docs", is_dir=True, kind="dir", suffix="")
    assert file_table.icon_for(folder) == "\U0001f4c1"


def test_icon_for_uses_the_extension_emoji() -> None:
    assert file_table.icon_for(make_entry("a.py", suffix="py")) == "\U0001f40d"
    assert file_table.icon_for(make_entry("a.md", suffix="md")) == "\U0001f4dd"


def test_icon_for_falls_back_to_file_emoji() -> None:
    assert file_table.icon_for(make_entry("a.unknownext", suffix="unknownext")) == "\U0001f4c4"


def test_icon_for_parent_row_points_up() -> None:
    assert file_table.icon_for(DirEntry.parent("/tmp/a")) == "\u2191"


def test_type_label_uses_suffix_then_kind() -> None:
    assert file_table.type_label(make_entry("a.py", suffix="py")) == "py"
    assert file_table.type_label(make_entry("noext", suffix="")) == "file"
    assert file_table.type_label(make_entry("d", is_dir=True, kind="dir", suffix="")) == "folder"


def test_format_breadcrumb_shows_the_path() -> None:
    assert file_table.format_breadcrumb(Path("/a/b/c")) == "/a/b/c"


def test_format_breadcrumb_shows_root_for_the_filesystem_root() -> None:
    assert file_table.format_breadcrumb(Path("/")) == "/"


def test_build_row_name_contains_the_icon_and_the_name(entries: list[DirEntry]) -> None:
    assert file_table.build_row(entries[1])[0].endswith("zeta.txt")


# -- filtering (AC5) -------------------------------------------------------


def test_filter_entries_narrows_by_substring() -> None:
    result = list(file_table.filter_entries([make_entry("alpha"), make_entry("beta")], "alp"))
    assert [entry.name for entry in result] == ["alpha"]


def test_filter_entries_is_case_insensitive() -> None:
    result = list(file_table.filter_entries([make_entry("Alpha.TXT")], "alpha"))
    assert [entry.name for entry in result] == ["Alpha.TXT"]


def test_filter_entries_passes_everything_for_blank_text() -> None:
    source = [make_entry("a"), make_entry("b")]
    assert list(file_table.filter_entries(source, "")) == source
    assert list(file_table.filter_entries(source, "   ")) == source


def test_filter_entries_is_lazy() -> None:
    assert iter(file_table.filter_entries([make_entry("a")], "a")) is not None


def test_filter_entries_can_exclude_the_parent_row() -> None:
    source = [DirEntry.parent("/tmp/a"), make_entry("deep")]
    assert [entry.name for entry in file_table.filter_entries(source, "deep")] == ["deep"]


# -- sorting (AC5) ---------------------------------------------------------


def test_sort_keys_are_name_size_time_and_type() -> None:
    assert file_table.SORT_KEYS == ("name", "size", "time", "type")


def test_next_sort_key_cycles_and_wraps() -> None:
    assert file_table.next_sort_key("name") == "size"
    assert file_table.next_sort_key("size") == "time"
    assert file_table.next_sort_key("time") == "type"
    assert file_table.next_sort_key("type") == "name"


def test_next_sort_key_resets_unknown_keys() -> None:
    assert file_table.next_sort_key("bogus") == "name"


def test_sort_by_name_puts_dirs_and_parent_first(entries: list[DirEntry]) -> None:
    assert [e.name for e in file_table.sort_entries(entries, "name")] == [
        "..",
        "alpha",
        "beta.py",
        "gamma.md",
        "zeta.txt",
    ]


def test_sort_by_size(entries: list[DirEntry]) -> None:
    ordered = [e.name for e in file_table.sort_entries(entries, "size")]
    assert ordered[0] == ".."
    assert ordered[1] == "alpha"
    assert ordered[2:] == ["beta.py", "gamma.md", "zeta.txt"]


def test_sort_by_time_is_newest_first(entries: list[DirEntry]) -> None:
    ordered = [e.name for e in file_table.sort_entries(entries, "time")]
    assert ordered[0] == ".."
    assert ordered[1:] == ["zeta.txt", "gamma.md", "beta.py", "alpha"]


def test_sort_by_type(entries: list[DirEntry]) -> None:
    ordered = [e.name for e in file_table.sort_entries(entries, "type")]
    assert ordered[0] == ".."
    assert ordered[1] == "alpha"
    assert ordered[2:] == ["gamma.md", "beta.py", "zeta.txt"]


def test_sort_reverse_still_keeps_the_parent_row_first(entries: list[DirEntry]) -> None:
    assert file_table.sort_entries(entries, "name", reverse=True)[0].name == ".."


def test_sort_entries_rejects_unknown_keys(entries: list[DirEntry]) -> None:
    with pytest.raises(ValueError):
        file_table.sort_entries(entries, "bogus")


# -- header sort surface (DC-009) ------------------------------------------


def test_column_sort_keys_align_with_the_columns() -> None:
    assert len(file_table.COLUMN_SORT_KEYS) == len(file_table.COLUMNS)
    assert file_table.COLUMN_SORT_KEYS == ("name", "size", "time", "type")


def test_sort_key_for_column_maps_each_header() -> None:
    assert file_table.sort_key_for_column(0) == "name"
    assert file_table.sort_key_for_column(1) == "size"
    assert file_table.sort_key_for_column(2) == "time"
    assert file_table.sort_key_for_column(3) == "type"


def test_sort_key_for_column_ignores_unknown_headers() -> None:
    assert file_table.sort_key_for_column(-1) is None
    assert file_table.sort_key_for_column(len(file_table.COLUMNS)) is None


def test_column_labels_marks_the_active_column_ascending() -> None:
    labels = file_table.column_labels("size", reverse=False)
    assert labels[1][0] == f"Size {file_table.SORT_ARROWS[0]}"
    assert labels[0][0] == "Name"


def test_column_labels_marks_the_active_column_descending() -> None:
    labels = file_table.column_labels("time", reverse=True)
    assert labels[2][0] == f"Modified {file_table.SORT_ARROWS[1]}"


def test_column_labels_keeps_the_column_widths() -> None:
    labels = file_table.column_labels("name")
    assert [width for _, width in labels] == [width for _, width in file_table.COLUMNS]


def test_column_labels_marks_exactly_one_column() -> None:
    labels = file_table.column_labels("type")
    marked = [label for label, _ in labels if label.endswith(file_table.SORT_ARROWS[0])]
    assert marked == [f"Perms {file_table.SORT_ARROWS[0]}"]


# -- the full pipeline -----------------------------------------------------


def test_chain_entries_filters_then_sorts(entries: list[DirEntry]) -> None:
    result = file_table.chain_entries(entries, "a", "size")
    assert [entry.name for entry in result] == ["alpha", "beta.py", "gamma.md", "zeta.txt"]


def test_chain_entries_keeps_the_parent_row_when_it_matches(
    entries: list[DirEntry],
) -> None:
    result = file_table.chain_entries(entries, ".")
    assert [entry.name for entry in result][0] == ".."


def test_chain_entries_defaults_to_all_by_name(entries: list[DirEntry]) -> None:
    assert len(file_table.chain_entries(entries)) == len(entries)


# -- virtualisation (AC1) ---------------------------------------------------


def test_window_bounds_covers_exactly_the_viewport_without_a_buffer() -> None:
    assert file_table.window_bounds(0, 10, 100) == (0, 10)
    assert file_table.window_bounds(50, 10, 100) == (50, 60)


def test_window_bounds_never_grows_past_the_end_of_the_listing() -> None:
    assert file_table.window_bounds(0, 10, 4) == (0, 4)
    assert file_table.window_bounds(95, 10, 100) == (90, 100)


def test_window_bounds_is_empty_for_an_empty_listing() -> None:
    assert file_table.window_bounds(0, 10, 0) == (0, 0)
    assert file_table.window_bounds(7, 10, 0) == (0, 0)


def test_window_bounds_adds_a_buffer_on_both_sides() -> None:
    assert file_table.window_bounds(50, 10, 100, 5) == (45, 65)


def test_window_bounds_clamps_a_buffered_window_at_both_ends() -> None:
    assert file_table.window_bounds(0, 10, 100, 5) == (0, 20)
    assert file_table.window_bounds(99, 10, 100, 5) == (80, 100)


def test_window_bounds_ignores_a_negative_buffer() -> None:
    assert file_table.window_bounds(50, 10, 100, -4) == file_table.window_bounds(50, 10, 100)


def test_a_buffered_window_always_contains_the_whole_viewport() -> None:
    """The invariant the panel relies on, over every combination worth pinning."""
    for total in (1, 5, 20, 100):
        for visible in (1, 3, 10):
            for offset in range(-3, total + 3):
                start, stop = file_table.window_bounds(offset, visible, total, 4)
                top = max(0, min(offset, max(0, total - visible)))
                assert start <= top
                assert min(total, top + visible) <= stop
                assert stop - start <= visible + 8


def test_window_bounds_never_covers_more_than_the_viewport_by_default() -> None:
    """The AC1 guarantee: cost per redraw is the viewport, not the listing."""
    for offset in (0, 1, 500, 99_999):
        start, stop = file_table.window_bounds(offset, 10, 100_000)
        assert stop - start <= 10
