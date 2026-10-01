"""DC-006: opt-in external launches, URL opening, and persisted settings.

The card has one property worth a test of its own: nothing reaches the system
without the user having been asked. These tests cover it from both ends -- the
manager describes a launch without running it, and the page runs it only from
the answer callback of the confirmation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from daylight.managers import FileManager
from daylight.managers.external import (
    LaunchRequest,
    describe_edit,
    describe_link,
    describe_open,
    detect_url,
    detect_url_in_file,
    edit_dispatch,
    is_url,
)
from daylight.modals import LaunchScreen
from daylight.modals.launcher import launch_detail, launch_message

# -- URL recognition (FR-402) ----------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://example.com/a?b=c#d",
        "HTTPS://EXAMPLE.COM",
        "mailto:someone@example.com",
        "ftp://files.example.com/pub",
        "  https://example.com/padded  ",
    ],
)
def test_a_url_is_recognised(url: str) -> None:
    assert is_url(url) is True


@pytest.mark.parametrize(
    "text",
    [
        "",
        "example.com",
        "https://",
        "://example.com",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "not a url at all",
    ],
)
def test_something_that_is_not_a_url_is_refused(text: str) -> None:
    """Only the declared schemes reach the browser, and only with a body."""
    assert is_url(text) is False


def test_a_url_is_found_in_running_text() -> None:
    assert detect_url("see https://example.com/docs for more") == "https://example.com/docs"


def test_a_mailto_url_is_found_in_running_text() -> None:
    assert detect_url("write to mailto:me@example.com") == "mailto:me@example.com"


def test_text_without_a_url_has_none() -> None:
    assert detect_url("nothing to open here") is None


def test_trailing_punctuation_is_not_part_of_the_url() -> None:
    assert detect_url("open https://example.com.") == "https://example.com"


def test_the_head_of_a_file_is_searched_for_a_url(tmp_path: Path) -> None:
    target = tmp_path / "bookmark.txt"
    target.write_text("# links\n\nhttps://example.com/deep\n", encoding="utf-8")
    assert detect_url_in_file(target) == "https://example.com/deep"


def test_a_file_without_a_url_has_none(tmp_path: Path) -> None:
    target = tmp_path / "notes.txt"
    target.write_text("just prose", encoding="utf-8")
    assert detect_url_in_file(target) is None


def test_a_binary_file_is_never_searched_for_a_url(tmp_path: Path) -> None:
    """A binary file is not decoded looking for a link (AC2, the `o` shortcut)."""
    target = tmp_path / "shot.png"
    target.write_bytes(b"https://example.com\x00\xff\xfe")
    assert detect_url_in_file(target) is None


def test_a_missing_file_has_no_url(tmp_path: Path) -> None:
    assert detect_url_in_file(tmp_path / "absent.txt") is None


# -- describing a launch, without launching it (FR-403) ---------------------


def test_describing_an_open_does_not_run_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The description is pure: asking what would happen changes nothing."""
    target = tmp_path / "shot.png"
    target.write_bytes(b"\x89PNG")
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        "daylight.managers.external.run",
        lambda command, runner=None: calls.append(tuple(command)) or 0,
    )
    request = describe_open(target)
    assert calls == []
    assert request.target == str(target)
    assert request.kind == "file"
    assert request.command[0] in {"open", "xdg-open"}


def test_describing_a_link_names_the_browser() -> None:
    request = describe_link("https://example.com")
    assert request.kind == "link"
    assert request.target == "https://example.com"
    assert "browser" in request.application


def test_describing_a_non_url_raises() -> None:
    with pytest.raises(ValueError):
        describe_link("example.com")


def test_describing_a_text_edit_is_not_an_external_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``$EDITOR`` runs in this terminal, so it is not a launch (FR-403)."""
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "nano")
    target = tmp_path / "code.py"
    target.write_text("print(1)\n", encoding="utf-8")
    request = describe_edit(edit_dispatch(target))
    assert request.kind == "editor"
    assert request.application == "your $EDITOR"


def test_describing_a_binary_edit_falls_back_to_the_os(tmp_path: Path) -> None:
    target = tmp_path / "shot.png"
    target.write_bytes(b"\x89PNG\x00")
    request = describe_edit(edit_dispatch(target))
    assert request.kind == "file"
    assert "default application" in request.application


def test_a_launch_request_runs_the_command_it_describes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        "daylight.managers.external.run",
        lambda command, runner=None: seen.append(tuple(command)) or 7,
    )
    target = tmp_path / "shot.png"
    target.write_bytes(b"\x89PNG")
    request = LaunchRequest("file", str(target), "the default application", ("open", str(target)))
    assert request.run() == 7
    assert seen == [("open", str(target))]


# -- the manager facade -----------------------------------------------------


def test_the_manager_launches_only_through_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``FileManager.launch`` is the single way a launch reaches the system."""
    seen: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        "daylight.managers.external.run",
        lambda command, runner=None: seen.append(tuple(command)) or 0,
    )
    manager = FileManager()
    target = tmp_path / "shot.png"
    target.write_bytes(b"\x89PNG")
    manager.launch(manager.describe_open(target))
    assert seen and seen[0][-1] == str(target)


def test_the_manager_finds_a_url_in_a_file(tmp_path: Path) -> None:
    target = tmp_path / "bookmark.txt"
    target.write_text("https://example.com/x\n", encoding="utf-8")
    manager = FileManager()
    assert manager.url_in_file(target) == "https://example.com/x"
    assert manager.detect_url("no link") is None


# -- the confirmation itself (AC1, FR-403) ---------------------------------


def test_the_confirmation_names_the_target_and_the_program() -> None:
    request = LaunchRequest(
        "link", "https://example.com", "your default browser", ("open", "https://example.com")
    )
    assert "https://example.com" in launch_detail(request)
    assert launch_detail(request).split()[-1] == "https://example.com"
    assert "open" in launch_detail(request)
    assert "browser" in launch_message(request)


def test_backing_out_of_the_confirmation_answers_no() -> None:
    """Escape, ``n`` and the Cancel button all mean "do not launch"."""
    screen = LaunchScreen(
        LaunchRequest("file", "/tmp/shot.png", "the default application", ("open", "/tmp/shot.png"))
    )
    assert screen.cancelled_result() is False