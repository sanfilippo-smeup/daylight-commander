"""Search-manager tests (DC-037): bounds, skips, ordering, cancellation.

These cover ``managers/search.py`` and the ``FileManager`` delegates directly on
temporary trees -- no UI, no Textual. The page behavior (modes, debounce,
Enter) lives in ``test_search_page.py``; that split keeps ADR-003 testable: the
scanning rules are manager rules, not widget rules.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from daylight.managers import FileManager, search
from daylight.managers.search import CancelToken, SearchCancelled
from daylight.models import SearchHit


def write(path: Path, text: str = "text\n") -> None:
    """Create ``path``'s parent, then write the text to it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def nested_tree(tmp_path: Path) -> Path:
    """A tree with name matches at several depths and a directory named right."""
    tree = tmp_path / "tree"
    write(tree / "alpha.txt")
    write(tree / "docs" / "alpha.md")
    write(tree / "docs" / "nested" / "alpha-note.txt")
    write(tree / "docs" / "nested" / "plain.txt")
    (tree / "alpha-dir").mkdir(exist_ok=True)
    write(tree / "alpha-dir" / "inner-alpha.txt")
    return tree


# -- recursive name matches -------------------------------------------------


def test_name_matches_include_files_and_directories_below_the_root(tmp_path) -> None:
    """A name hit is any entry -- file or directory -- whose name matches, once."""
    tree = nested_tree(tmp_path)
    found = search.iter_name_matches(tree, "alpha")
    relatives = [path.relative_to(tree) for path in found]
    assert relatives == [
        Path("alpha-dir"),
        Path("alpha-dir") / "inner-alpha.txt",
        Path("alpha.txt"),
        Path("docs") / "alpha.md",
        Path("docs") / "nested" / "alpha-note.txt",
    ]


def test_name_matches_ignore_the_root_itself(tmp_path) -> None:
    """The pane directory is the root of the search, never a hit for its name."""
    tree = nested_tree(tmp_path)
    assert list(search.iter_name_matches(tree, tree.name)) == []
    assert list(search.iter_name_matches(tree, "")) == []
    assert list(search.iter_name_matches(tree, "   ")) == []


def test_name_matches_stay_out_of_hidden_places_until_shown(tmp_path) -> None:
    """Hidden entries -- and everything below a hidden directory -- are skipped."""
    tree = tmp_path / "tree"
    write(tree / "visible.txt")
    write(tree / ".hidden.txt")
    write(tree / ".dotdir" / "alpha-inside.txt")
    assert [path.name for path in search.iter_name_matches(tree, "txt")] == ["visible.txt"]
    assert [path.name for path in search.iter_name_matches(tree, "inside")] == []
    shown = sorted(
        path.relative_to(tree) for path in search.iter_name_matches(tree, "txt", show_hidden=True)
    )
    assert shown == [
        Path(".dotdir", "alpha-inside.txt"),
        Path(".hidden.txt"),
        Path("visible.txt"),
    ]


def test_a_directory_symlink_matches_but_is_never_descended(tmp_path) -> None:
    """The link itself can match; the scan stops at its door (so loops end)."""
    tree = tmp_path / "tree"
    write(tree / "real" / "alpha-target.txt")
    (tree / "alpha-link").symlink_to(tree / "real", target_is_directory=True)
    found = sorted(path.relative_to(tree) for path in search.iter_name_matches(tree, "alpha"))
    assert found == [Path("alpha-link"), Path("real", "alpha-target.txt")]


def test_a_symlink_loop_cannot_explode_the_scan(tmp_path) -> None:
    """A self-referential tree finishes and yields nothing inside the loop."""
    tree = tmp_path / "tree"
    write(tree / "real" / "alpha.txt")
    (tree / "inside").symlink_to(tree, target_is_directory=True)
    found = list(search.iter_name_matches(tree, "alpha"))
    assert [path.relative_to(tree) for path in found] == [Path("real", "alpha.txt")]
    assert all("inside" not in str(path) for path in found)


# -- content matches ---------------------------------------------------------


def test_content_matches_carry_the_line_and_snippet(tmp_path) -> None:
    """Every hit says which 1-based line matched and what that line reads."""
    tree = tmp_path / "tree"
    write(tree / "deep" / "file.txt", "first line\nNEEDLE IS HERE\nthird\nneedle again\n")
    hits = list(search.iter_content_matches(tree, "needle"))
    assert [(path.relative_to(tree), line, snippet) for path, line, snippet in hits] == [
        (Path("deep", "file.txt"), 2, "NEEDLE IS HERE"),
        (Path("deep", "file.txt"), 4, "needle again"),
    ]


def test_content_matches_are_case_insensitive_substrings(tmp_path) -> None:
    tree = tmp_path / "tree"
    write(tree / "case.txt", "MixedCase Needle\nsmall needle\nNO MATCH\n")
    hits = list(search.iter_content_matches(tree, "neEdle"))
    assert [line for _path, line, _snippet in hits] == [1, 2]


def test_content_search_skips_binary_files(tmp_path) -> None:
    """A NUL anywhere in the first 8 KiB makes a file invisible to the scan."""
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "binary.dat").write_bytes(b"\x00\x01" + b"\x00" * 100 + b"needle\n")
    (tree / "text.dat").write_text("needle\n", encoding="utf-8")
    hits = list(search.iter_content_matches(tree, "needle"))
    assert [path.name for path, _line, _snippet in hits] == ["text.dat"]


def test_a_binary_tell_late_in_the_file_still_gets_read(tmp_path) -> None:
    """The NUL check only looks at the first 8 KiB -- text after it is fair game."""
    tree = tmp_path / "tree"
    write(tree / "late-nul.txt", "needle\n" + "x" * 10_000 + "\x00\n")
    hits = list(search.iter_content_matches(tree, "needle"))
    assert [line for _path, line, _snippet in hits] == [1]


def test_content_search_skips_files_over_the_size_limit(tmp_path) -> None:
    """A file larger than SEARCH_FILE_LIMIT (1 MiB) is skipped entirely."""
    tree = tmp_path / "tree"
    tree.mkdir()
    size_limit = search.SEARCH_FILE_LIMIT
    (tree / "big.txt").write_text("needle\n" + "z" * size_limit, encoding="utf-8")
    write(tree / "small.txt", "needle\n")
    assert [path.name for path, *_ in search.iter_content_matches(tree, "needle")] == ["small.txt"]
    # A file exactly at the limit is still read.
    exact = tmp_path / "tree2"
    write(exact / "exact.txt", "x" * (size_limit - len("needle\n")) + "needle\n")
    hits = list(search.iter_content_matches(exact, "needle"))
    assert [(path.name, line) for path, line, _snippet in hits] == [("exact.txt", 1)]


def test_content_search_never_reads_through_a_symlink(tmp_path) -> None:
    """A linked file can point outside the tree; it is skipped, not followed."""
    tree = tmp_path / "tree"
    write(tree / "needle.txt")
    (tree / "link-to-needle").symlink_to(tree / "needle.txt")
    hits = list(search.iter_content_matches(tree, "text"))
    assert [path.name for path, *_ in hits] == ["needle.txt"]


def test_directories_and_symlinks_are_never_read_for_content(tmp_path) -> None:
    tree = tmp_path / "tree"
    (tree / "folder").mkdir(parents=True)
    (tree / "folder" / "link").symlink_to(tree / "folder", target_is_directory=True)
    assert list(search.iter_content_matches(tree, "folder")) == []
    assert list(search.iter_content_matches(tree, "link")) == []


# -- snippets ----------------------------------------------------------------


def test_snippets_are_sanitized_for_a_table_cell(tmp_path) -> None:
    """Tabs are expanded and control characters are gone -- markup survives."""
    tree = tmp_path / "tree"
    write(tree / "messy.txt", "a\tb\x01 needle\x02\n")
    hits = list(search.iter_content_matches(tree, "needle"))
    assert [(line, snippet) for _path, line, snippet in hits] == [(1, "a    b needle")]


def test_a_long_line_is_trimmed_around_the_match(tmp_path) -> None:
    """Beyond 200 characters the snippet is a centred window with ellipses."""
    filler = "z" * 500
    long_line = f"{filler}NEEDLE{filler}\n"
    tree = tmp_path / "tree"
    write(tree / "long.txt", long_line)
    hits = list(search.iter_content_matches(tree, "needle"))
    for _path, _line, snippet in hits:
        assert len(snippet) <= search.SNIPPET_LIMIT
        assert snippet.startswith("\u2026") and snippet.endswith("\u2026")
        assert "NEEDLE" in snippet


def test_a_full_line_is_shown_whole_up_to_the_limit(tmp_path) -> None:
    """A line shorter than the limit is never decorated with ellipses."""
    text = "n" * 199 + "\n"
    tree = tmp_path / "tree"
    write(tree / "edge.txt", text)
    for _path, _line, snippet in search.iter_content_matches(tree, "n"):
        assert snippet == "n" * 199


# -- bounds and cancellation --------------------------------------------------


def test_the_hit_cap_stops_both_kinds_of_scans(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A scan stops at SEARCH_RESULT_LIMIT hits -- patched small here to prove it."""
    tree = tmp_path / "tree"
    for index in range(12):
        write(tree / f"alpha{index:02d}.txt", "needle\n")
    monkeypatch.setattr(search, "SEARCH_RESULT_LIMIT", 5)
    assert len(list(search.iter_name_matches(tree, "alpha"))) == 5
    assert len(list(search.iter_content_matches(tree, "needle"))) == 5


def test_a_cancelled_token_stops_the_walk_with_SearchCancelled(tmp_path) -> None:
    tree = nested_tree(tmp_path)
    token = CancelToken()
    token.cancel()
    with pytest.raises(SearchCancelled):
        list(search.iter_name_matches(tree, "alpha", cancel=token))


def test_a_token_cancelled_mid_scan_unwinds_the_file(tmp_path) -> None:
    """The line loop checks the token too, so a late cancel still lands."""
    tree = tmp_path / "tree"
    write(tree / "many.txt", "needle\n" * 50)
    token = CancelToken()
    found = search.iter_content_matches(tree, "needle", cancel=token)
    first = next(found)
    assert first[1] == 1
    token.cancel()
    with pytest.raises(SearchCancelled):
        next(found)
    # A plain callable works too: the batch-operation protocol stays usable.
    assert list(search.iter_name_matches(tree, "needle", cancel=lambda: False)) == []


# -- the FileManager delegates ------------------------------------------------


def test_the_manager_delegate_wraps_name_hits_as_rows(tmp_path) -> None:
    """Delegated hits keep the ADR-003 shape: rows with metadata, no UI disk hits."""
    tree = nested_tree(tmp_path)
    manager = FileManager()
    hits = manager.iter_name_matches(tree, "alpha")
    rows = list(hits)
    assert all(isinstance(hit, SearchHit) for hit in rows)
    assert [hit.entry.name for hit in rows] == [
        "alpha-dir",
        "inner-alpha.txt",
        "alpha.txt",
        "alpha.md",
        "alpha-note.txt",
    ]
    assert [hit.relative for hit in rows] == [
        "alpha-dir",
        "alpha-dir/inner-alpha.txt",
        "alpha.txt",
        "docs/alpha.md",
        "docs/nested/alpha-note.txt",
    ]
    assert [(hit.entry.kind, hit.line, hit.snippet) for hit in rows] == [
        ("dir", 0, ""),
        ("file", 0, ""),
        ("file", 0, ""),
        ("file", 0, ""),
        ("file", 0, ""),
    ]


def test_the_manager_delegate_memoizes_the_row_of_repeated_hits(tmp_path) -> None:
    """Consecutive content hits in one file share the DirEntry, not a re-stat."""
    tree = tmp_path / "tree"
    write(tree / "same.txt", "needle\nneedle\nneedle\n")
    manager = FileManager()
    rows = list(manager.iter_content_matches(tree, "needle"))
    assert len(rows) == 3
    assert rows[0].entry is rows[1].entry is rows[2].entry
    assert [(hit.line, hit.snippet) for hit in rows] == [
        (1, "needle"),
        (2, "needle"),
        (3, "needle"),
    ]


def test_the_delegate_honours_the_hidden_setting(tmp_path) -> None:
    tree = tmp_path / "tree"
    write(tree / "alpha.txt")
    write(tree / ".alpha-hidden.txt")
    assert [hit.relative for hit in FileManager().iter_name_matches(tree, "alpha")] == ["alpha.txt"]
    shown = FileManager(show_hidden=True)
    assert [hit.relative for hit in shown.iter_name_matches(tree, "alpha")] == [
        ".alpha-hidden.txt",
        "alpha.txt",
    ]


def test_a_vanished_hit_path_is_skipped_not_fatal(tmp_path) -> None:
    """A file deleted between the scan and the row build cannot break the UI."""
    tree = tmp_path / "tree"
    write(tree / "alpha.txt")
    write(tree / "gone-alpha.txt")
    manager = FileManager()
    real = manager._row_from_path  # capture before patching

    def vanish(path: Path) -> Any:
        """Simulate a race: one specific path has already vanished."""
        if path.name == "gone-alpha.txt":
            return None
        return real(path)

    manager._row_from_path = vanish  # type: ignore[method-assign]
    assert [hit.entry.name for hit in manager.iter_name_matches(tree, "alpha")] == ["alpha.txt"]
