"""Unit tests for the viewer's managers layer (DC-004).

The viewer is only as good as what it is allowed to read, so these cover the
decisions it delegates to :mod:`daylight.managers.content` and
:mod:`daylight.managers.external`: text vs binary, MIME, checksums, the
pretty-printers, the windowed reads, the properties record and the edit
dispatch. Every filesystem fixture is a ``tmp_path``.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

from daylight.components import properties_table
from daylight.components.modules.viewer import render_lines
from daylight.managers import FileManager, content, external
from daylight.models import VIEW_KINDS

SAMPLE_MD = """# Daylight

Intro *text* with a [link](https://example.com).

- first
- second

| key | value |
| --- | ----- |
| a   | 1     |

```python
x = 1
```
"""

SAMPLE_YAML = "name: day\nlist:\n      - a\n    - b\nurl:http://example.com\n"


@pytest.fixture
def samples(tmp_path: Path) -> Path:
    """A temp dir with one file of every kind the viewer knows about."""
    (tmp_path / "doc.md").write_text(SAMPLE_MD, encoding="utf-8")
    (tmp_path / "data.json").write_text('{"b":1,"a":[1,{"c":2}]}', encoding="utf-8")
    (tmp_path / "conf.yaml").write_text(SAMPLE_YAML, encoding="utf-8")
    (tmp_path / "conf.yml").write_text("name: day\n", encoding="utf-8")
    (tmp_path / "code.py").write_text("import os\n\nprint(os.name)\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text(
        "\n".join(
            f"line {number}" + (" needle" if number == 40 else "") for number in range(1, 101)
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(range(256)))
    (tmp_path / "folder").mkdir()
    return tmp_path


# -- text or binary ---------------------------------------------------------


def test_is_text_bytes_accepts_utf8() -> None:
    assert content.is_text_bytes("hello \u00e9\n".encode()) is True


def test_is_text_bytes_rejects_nul() -> None:
    assert content.is_text_bytes(b"abc\x00def") is False


def test_is_text_bytes_rejects_undecodable_bytes() -> None:
    assert content.is_text_bytes(b"\x80\x81\x82\x83") is False


def test_is_text_bytes_accepts_utf16_mark() -> None:
    assert content.is_text_bytes("hi".encode("utf-16")) is True


def test_is_text_file_reads_only_the_head(tmp_path: Path) -> None:
    big = tmp_path / "big.txt"
    big.write_bytes(b"a" * (content.SNIFF_BYTES * 2) + b"\x00")
    assert content.is_text_file(big) is True


def test_is_text_file_reports_binary(samples: Path) -> None:
    assert content.is_text_file(samples / "shot.png") is False
    assert content.is_text_file(samples / "doc.md") is True


def test_is_text_file_missing_path_is_not_text(tmp_path: Path) -> None:
    assert content.is_text_file(tmp_path / "nope.txt") is False


# -- view kind and MIME -----------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("doc.md", "markdown"),
        ("data.json", "json"),
        ("conf.yaml", "text"),
        ("conf.yml", "text"),
        ("code.py", "text"),
        ("notes.txt", "text"),
        ("shot.png", "binary"),
        ("folder", "directory"),
    ],
)
def test_view_kind_by_content(samples: Path, name: str, expected: str) -> None:
    assert content.view_kind(samples / name) == expected


def test_view_kind_of_a_binary_json_is_binary(samples: Path) -> None:
    (samples / "fake.json").write_bytes(b"\x00\x01\x02")
    assert content.view_kind(samples / "fake.json") == "binary"


def test_mime_type_uses_the_name_first(samples: Path) -> None:
    assert content.mime_type(samples / "doc.md") == "text/markdown"
    assert content.mime_type(samples / "data.json") == "application/json"
    assert content.mime_type(samples / "conf.yaml") == "application/yaml"
    assert content.mime_type(samples / "shot.png") == "image/png"
    assert content.mime_type(samples / "folder") == "inode/directory"


def test_mime_type_falls_back_to_the_bytes(samples: Path) -> None:
    script = samples / "runme"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    assert content.mime_type(script) == "text/plain"
    blob = samples / "blob.unknownext"
    blob.write_bytes(b"\x00\x01")
    assert content.mime_type(blob) == "application/octet-stream"


# -- checksums --------------------------------------------------------------


def test_checksum_matches_hashlib(samples: Path) -> None:
    target = samples / "notes.txt"
    expected = hashlib.sha256(target.read_bytes()).hexdigest()
    assert content.checksum(target) == expected


def test_checksum_streams_in_small_chunks(samples: Path) -> None:
    target = samples / "data.json"
    assert content.checksum(target, "md5", chunk=1) == hashlib.md5(target.read_bytes()).hexdigest()


# -- bounded reads ----------------------------------------------------------


def test_count_lines_counts_a_file_without_a_final_newline(tmp_path: Path) -> None:
    target = tmp_path / "three.txt"
    target.write_text("a\nb\nc", encoding="utf-8")
    assert content.count_lines(target) == 3


def test_count_lines_gives_up_on_a_very_large_file(tmp_path: Path) -> None:
    target = tmp_path / "big.txt"
    target.write_text("a\n" * 100, encoding="utf-8")
    assert content.count_lines(target, limit=10) is None


def test_read_window_returns_the_requested_lines(samples: Path) -> None:
    assert content.read_window(samples / "notes.txt", 0, 2) == ["line 1", "line 2"]
    assert content.read_window(samples / "notes.txt", 39, 1) == ["line 40 needle"]


def test_read_window_clamps_and_strips_terminators(samples: Path) -> None:
    assert content.read_window(samples / "notes.txt", -5, 2) == ["line 1", "line 2"]
    assert content.read_window(samples / "notes.txt", 100, 5) == []


def test_read_window_stops_after_the_window_it_was_asked_for(tmp_path: Path, monkeypatch) -> None:
    """A window is walked, never materialised: the rest of the file is left alone."""
    target = tmp_path / "big.txt"
    target.write_text("\n".join(f"line {n}" for n in range(50_000)) + "\n", encoding="utf-8")
    counters: list[_LineCounter] = []
    real_open = Path.open

    def counting_open(self: Path, *args: object, **kwargs: object):
        handle = real_open(self, *args, **kwargs)
        if self == target:
            spy = _LineCounter(handle)
            counters.append(spy)
            return spy
        return handle

    monkeypatch.setattr(Path, "open", counting_open)
    assert content.read_window(target, 0, 3) == ["line 0", "line 1", "line 2"]
    # The first window costs a window, not a file.
    assert sum(counter.lines_read for counter in counters) == 3
    counters.clear()
    assert content.read_window(target, 40_000, 3) == ["line 40000", "line 40001", "line 40002"]
    # A window further down skips what it passes and leaves the tail unread.
    assert sum(counter.lines_read for counter in counters) == 40_003


class _LineCounter:
    """Wraps a text file and counts how many lines were pulled out of it."""

    def __init__(self, handle) -> None:
        self._handle = handle
        self.lines_read = 0

    def __enter__(self) -> "_LineCounter":
        return self

    def __exit__(self, *args: object) -> None:
        self._handle.close()

    def __iter__(self) -> "_LineCounter":
        return self

    def __next__(self) -> str:
        line = next(self._handle)
        self.lines_read += 1
        return line

    def read(self, *args: object, **kwargs: object) -> str:
        return self._handle.read(*args, **kwargs)  # type: ignore[arg-type]


def test_read_text_stops_at_the_limit(tmp_path: Path) -> None:
    target = tmp_path / "long.txt"
    body = "\n".join(f"line {n}" for n in range(1_000))
    target.write_text(body, encoding="utf-8")
    text, truncated = content.read_text(target, limit=40)
    assert truncated is True
    assert body.startswith(text)
    assert len(text) <= 40


def test_read_text_bounds_bytes_not_characters(tmp_path: Path) -> None:
    """The limit is a work bound: multibyte text must not double the bytes read."""
    target = tmp_path / "accents.txt"
    target.write_text("\u00e9" * 100, encoding="utf-8")
    text, truncated = content.read_text(target, limit=120)
    assert truncated is True
    assert len(text.encode("utf-8")) <= 120


def test_view_limits_have_the_contract_values() -> None:
    """One byte and one line bound for every inline kind (DC-035, AC1)."""
    assert content.VIEW_LIMIT == 1_048_576
    assert content.VIEW_LINE_LIMIT == 10_000


def test_load_view_caps_the_number_of_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A megabyte of one-word lines must not mount 95,000 rows."""
    target = tmp_path / "many.txt"
    target.write_text(
        "\n".join(f"row {number}" for number in range(1, 31)) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(content, "VIEW_LINE_LIMIT", 10)
    document = content.load_view(target)
    assert document.truncated is True
    assert document.text.split("\n") == [f"row {number}" for number in range(1, 11)]
    assert "more than 10 lines" in document.notice
    assert "row 11" not in document.text


def test_load_view_states_both_bounds_when_both_apply(tmp_path: Path) -> None:
    """A file over 1 MiB *and* over the line cap must say both, not just KiB.

    1.2 MB of 2-byte lines trips the byte bound first; with the notice as a
    single fact it claimed only ``1024 KiB`` while the viewer also mounted
    only :data:`VIEW_LINE_LIMIT` of its half-million lines.
    """
    target = tmp_path / "sliver.log"
    target.write_text("x\n" * 600_000, encoding="utf-8")
    assert target.stat().st_size == 1_200_000
    document = content.load_view(target)
    assert document.truncated is True
    assert document.notice == (
        "file is larger than 1024 KiB and has more than 10000 lines: "
        "showing the first 10000 lines"
    )
    # What the viewer will actually mount: the first VIEW_LINE_LIMIT lines.
    assert len(document.text.split("\n")) == 10_000


def test_find_matches_reports_line_numbers(samples: Path) -> None:
    assert content.find_matches(samples / "notes.txt", "needle") == [40]
    assert content.find_matches(samples / "notes.txt", "NEEDLE") == [40]
    assert content.find_matches(samples / "notes.txt", "") == []


def test_find_matches_stops_at_the_cap(tmp_path: Path) -> None:
    target = tmp_path / "many.txt"
    target.write_text("hit\n" * 50, encoding="utf-8")
    assert content.find_matches(target, "hit", max_hits=5) == [1, 2, 3, 4, 5]


# -- formatting -------------------------------------------------------------


def test_load_view_keeps_markdown_as_it_is(samples: Path) -> None:
    document = content.load_view(samples / "doc.md")
    assert document.kind == "markdown"
    assert document.text == SAMPLE_MD
    assert document.truncated is False


def test_load_view_pretty_prints_json(samples: Path) -> None:
    document = content.load_view(samples / "data.json")
    assert document.kind == "json"
    assert document.text == '{\n  "b": 1,\n  "a": [\n    1,\n    {\n      "c": 2\n    }\n  ]\n}'
    assert document.notice == ""


def test_load_view_reports_invalid_json_instead_of_failing(samples: Path) -> None:
    broken = samples / "broken.json"
    broken.write_text("{oops", encoding="utf-8")
    document = content.load_view(broken)
    assert document.kind == "json"
    assert document.text == "{oops"
    assert "not valid JSON" in document.notice


# -- YAML: plain text, never rewritten (owner scope change, DC-004 attempt-004) --

#: Documents a re-indenting formatter would have changed. Every one of them must
#: come back out of the windowed reader line for line, because re-indenting YAML
#: can change what the document *means* (a block-scalar body is data).
YAML_BODIES = (
    "http://example.com\n",
    "12:30:45\n",
    "2024-01-02T03:04:05\n",
    "C:\\Users\\me\n",
    "home:C:\\Users\\me\n",
    "url:http://example.com\n",
    "steps: |\n  all:build\n  C:/Users/me\n",
    "steps: >-\n    all:build\n",
    "root:\n\tchild: 1\n",
    "a:   spaced   \n\n\n",
    "list:\n      - a\n    - b\n",
)


def test_load_view_treats_yaml_as_plain_text(samples: Path) -> None:
    """YAML has no formatter: it is read verbatim like any other source file (AC3)."""
    document = content.load_view(samples / "conf.yaml")
    assert document.kind == "text"
    assert document.is_text_kind is True
    assert document.text == SAMPLE_YAML
    assert document.notice == ""
    assert document.size == (samples / "conf.yaml").stat().st_size


def test_yaml_lines_are_shown_byte_for_byte(samples: Path) -> None:
    """The odd indentation and the glued ``key:value`` reach the screen as written."""
    assert content.read_window(samples / "conf.yaml", 0, 8) == SAMPLE_YAML.splitlines()


@pytest.mark.parametrize("body", YAML_BODIES)
def test_yaml_bodies_come_back_line_for_line(tmp_path: Path, body: str) -> None:
    target = tmp_path / "body.yaml"
    target.write_text(body, encoding="utf-8")
    assert content.view_kind(target) == "text"
    assert content.read_window(target, 0, 64) == body.splitlines()


def test_viewing_yaml_never_touches_the_file(samples: Path) -> None:
    """The viewer is read-only: the bytes on disk are the bytes that were there."""
    target = samples / "conf.yaml"
    before = target.read_bytes()
    content.load_view(target)
    content.read_window(target, 0, 8)
    content.find_matches(target, "url")
    assert target.read_bytes() == before


def test_no_yaml_formatter_is_left_behind() -> None:
    """The descoped re-indenter must not come back and rewrite a YAML document."""
    assert not hasattr(content, "_normalise_yaml")
    assert not hasattr(content, "_format_yaml")
    assert "yaml" not in VIEW_KINDS


def test_load_view_truncates_a_large_document(tmp_path: Path) -> None:
    big = tmp_path / "big.json"
    big.write_text('{"a": [' + ",".join("1" for _ in range(2_000)) + "]}", encoding="utf-8")
    document = content.load_view(big, limit=500)
    assert document.truncated is True
    assert len(document.text) <= 500
    assert "larger than" in document.notice


def test_load_view_truncates_plain_text_at_the_same_limit(tmp_path: Path) -> None:
    """Every inline kind shares VIEW_LIMIT, so a huge log degrades like JSON."""
    big = tmp_path / "big.log"
    big.write_text("\n".join(f"row {number}" for number in range(1, 201)), encoding="utf-8")
    document = content.load_view(big, limit=200)
    assert document.kind == "text"
    assert document.truncated is True
    assert len(document.text) <= 200
    assert "larger than" in document.notice
    assert "row 200" not in document.text


def test_load_view_never_returns_a_body_for_binary(samples: Path) -> None:
    document = content.load_view(samples / "shot.png")
    assert document.kind == "binary"
    assert document.text == ""


def test_load_view_of_a_directory_is_not_a_document(samples: Path) -> None:
    assert content.load_view(samples / "folder").kind == "directory"


def test_load_view_reads_plain_text_whole(samples: Path) -> None:
    """Plain text is read whole (bounded by VIEW_LIMIT) so the body can scroll."""
    target = samples / "notes.txt"
    document = content.load_view(target)
    assert document.kind == "text"
    assert document.text == target.read_text(encoding="utf-8")
    assert document.truncated is False
    assert document.size == target.stat().st_size
    assert document.is_text_kind is True


# -- properties -------------------------------------------------------------


def test_describe_reports_every_properties_field(samples: Path) -> None:
    target = samples / "doc.md"
    properties = FileManager().describe(target)
    info = target.stat()
    assert properties.name == "doc.md"
    assert properties.size == info.st_size
    assert properties.inode == info.st_ino
    assert properties.links == info.st_nlink
    # The permission bits depend on the umask, so compare with the mode itself.
    assert properties.permissions == content.permission_string(info.st_mode)
    assert properties.octal_permissions == format(info.st_mode & 0o777, "04o")
    assert properties.owner
    assert properties.group
    assert properties.mime == "text/markdown"
    assert properties.is_text is True
    assert properties.view_kind == "markdown"
    assert properties.checksum_algorithm == "sha256"
    assert properties.checksum == hashlib.sha256(target.read_bytes()).hexdigest()
    assert properties.modified == info.st_mtime


def test_describe_of_a_binary_file_says_so(samples: Path) -> None:
    properties = FileManager().describe(samples / "shot.png")
    assert properties.is_text is False
    assert properties.view_kind == "binary"


def test_describe_of_a_directory_has_no_checksum(samples: Path) -> None:
    assert FileManager().describe(samples / "folder").checksum == ""


def test_describe_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        FileManager().describe(tmp_path / "gone.txt")


def test_properties_rows_cover_the_documented_fields(samples: Path) -> None:
    rows = properties_table.build_rows(FileManager().describe(samples / "doc.md"))
    labels = [label for label, _ in rows]
    for wanted in ("Size", "Inode", "Modified", "Changed", "Accessed", "Permissions"):
        assert wanted in labels
    assert "MIME" in labels
    assert "Owner" in labels
    assert "Group" in labels
    assert any(value.startswith("sha256:") for _, value in rows)


def test_properties_value_at_is_the_copyable_field(samples: Path) -> None:
    properties = FileManager().describe(samples / "doc.md")
    assert properties_table.copy_value(properties, 0) == "doc.md"
    assert properties_table.copy_value(properties, 1) == str(samples / "doc.md")
    assert properties_table.copy_value(properties, 99) == ""


def test_properties_table_rejects_an_unknown_field(samples: Path) -> None:
    with pytest.raises(ValueError):
        properties_table.build_row(FileManager().describe(samples / "doc.md"), "nope")


def test_permission_string_matches_the_mode(tmp_path: Path) -> None:
    target = tmp_path / "modes.txt"
    target.write_text("x", encoding="utf-8")
    for bits, expected in ((0o600, "rw-------"), (0o755, "rwxr-xr-x"), (0o444, "r--r--r--")):
        target.chmod(bits)
        assert content.permission_string(target.stat().st_mode) == expected


# -- the numbered block -----------------------------------------------------


def test_render_lines_numbers_from_the_real_line() -> None:
    assert str(render_lines(["alpha", "beta"], 39, width=4)) == "  40  alpha\n  41  beta"


def test_render_lines_highlights_the_needle() -> None:
    block = render_lines(["a Needle here"], 0, needle="needle", width=2)
    assert any("reverse" in str(style) for _start, _end, style in block.spans)


def test_render_lines_expands_tabs() -> None:
    assert "\t" not in str(render_lines(["a\tb"], 0, width=2))


# -- edit dispatch ----------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_editor_in_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every dispatch test from a known editor: none is set."""
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)


def test_text_file_is_edited_with_vim_by_default(samples: Path) -> None:
    target = samples / "code.py"
    dispatch = external.edit_dispatch(target)
    assert dispatch.kind == "editor"
    assert dispatch.is_editor is True
    assert dispatch.command == ("vim", str(target))


def test_editor_environment_variable_is_honoured(samples: Path, monkeypatch) -> None:
    monkeypatch.setenv("EDITOR", "nano")
    assert external.edit_command(samples / "code.py") == ("nano", str(samples / "code.py"))


def test_visual_wins_over_editor(samples: Path, monkeypatch) -> None:
    monkeypatch.setenv("EDITOR", "nano")
    monkeypatch.setenv("VISUAL", "hx")
    assert external.editor_command() == "hx"


def test_editor_may_carry_arguments(samples: Path) -> None:
    dispatch = external.edit_dispatch(samples / "code.py", editor="code -w")
    assert dispatch.command == ("code", "-w", str(samples / "code.py"))


def test_binary_file_is_edited_in_the_os_application(samples: Path) -> None:
    target = samples / "shot.png"
    dispatch = external.edit_dispatch(target)
    assert dispatch.kind == "external"
    assert dispatch.is_external is True
    assert dispatch.command == external.open_command(target)


def test_opening_uses_the_platform_command(tmp_path: Path) -> None:
    if sys.platform == "darwin":
        opener = "open"
    elif sys.platform == "win32":
        opener = "cmd"
    else:
        opener = "xdg-open"
    assert external.open_command(tmp_path / "x")[0] == opener


def test_a_directory_is_never_handed_to_the_editor(samples: Path) -> None:
    assert external.edit_dispatch(samples / "folder").kind == "external"


def test_manager_edit_runs_the_editor_command(samples: Path) -> None:
    calls: list[list[str]] = []
    manager = FileManager()
    dispatch = manager.edit(samples / "code.py", runner=lambda argv: calls.append(list(argv)) or 0)
    assert calls == [["vim", str(samples / "code.py")]]
    assert dispatch.kind == "editor"
    assert dispatch.is_editor is True


def test_manager_edit_runs_the_os_command_for_binary(samples: Path) -> None:
    calls: list[list[str]] = []
    manager = FileManager()
    dispatch = manager.edit(samples / "shot.png", runner=lambda argv: calls.append(list(argv)) or 0)
    assert calls == [list(external.open_command(samples / "shot.png"))]
    assert dispatch.kind == "external"
    assert dispatch.is_external is True


def test_run_reports_a_missing_program_without_raising() -> None:
    def explode(argv: list[str]) -> int:
        raise OSError("no such program")

    assert external.run(["definitely-not-a-program"], explode) == 127


def test_run_returns_the_exit_status_of_the_program() -> None:
    assert external.run(["true"], lambda argv: 0) == 0
    assert external.run(["false"], lambda argv: 3) == 3


def test_manager_view_helpers_are_delegates(samples: Path) -> None:
    manager = FileManager()
    assert manager.view_kind(samples / "doc.md") == "markdown"
    assert manager.read_window(samples / "notes.txt", 0, 1) == ["line 1"]
    assert manager.count_lines(samples / "notes.txt") == 100
    assert manager.find_matches(samples / "notes.txt", "needle") == [40]
    assert manager.load_view(samples / "data.json").kind == "json"
    assert manager.describe(samples / "doc.md").mime == "text/markdown"


def test_manager_open_external_runs_the_platform_opener(samples: Path) -> None:
    calls: list[list[str]] = []
    manager = FileManager()

    def remember(argv: list[str]) -> int:
        calls.append(list(argv))
        return 0

    assert manager.open_external(samples / "shot.png", runner=remember) == 0
    assert calls == [list(external.open_command(samples / "shot.png"))]
