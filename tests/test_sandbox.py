"""Unit tests for the sandboxed script runner (DC-024).

Every run is hermetic: scripts are tiny ``sys.executable`` snippets executed
inside ``tmp_path`` (or a runner-created temp directory), with no network and
no shell.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from time import monotonic

import pytest

from daylight.managers import sandbox
from daylight.managers.sandbox import run_script
from daylight.models import SandboxResult

# -- basic execution ----------------------------------------------------------


def test_run_script_captures_exit_code_stdout_stderr(tmp_path: Path) -> None:
    script = "import sys\n" "print('hello')\n" "print('problem', file=sys.stderr)\n" "sys.exit(3)\n"

    result = run_script(script, cwd=tmp_path, timeout=10)

    assert isinstance(result, SandboxResult)
    assert result.exit_code == 3
    assert result.stdout == "hello\n"
    assert result.stderr == "problem\n"
    assert result.timed_out is False
    assert result.duration_seconds >= 0.0


def test_run_script_success_exit_zero(tmp_path: Path) -> None:
    result = run_script("print('ok')\n", cwd=tmp_path, timeout=10)

    assert result.exit_code == 0
    assert result.stdout == "ok\n"
    assert result.stderr == ""


def test_run_script_reads_data_from_working_directory(tmp_path: Path) -> None:
    (tmp_path / "data.txt").write_text("payload", encoding="utf-8")
    script = "from pathlib import Path\nprint(Path('data.txt').read_text())\n"

    result = run_script(script, cwd=tmp_path, timeout=10)

    assert result.exit_code == 0
    assert "payload" in result.stdout


def test_run_script_runs_in_the_given_directory(tmp_path: Path) -> None:
    result = run_script("import os\nprint(os.getcwd())\n", cwd=tmp_path, timeout=10)

    assert result.exit_code == 0
    assert result.stdout.strip() == str(tmp_path)


def test_run_script_without_cwd_uses_a_temporary_directory() -> None:
    result = run_script("import os\nprint(os.getcwd())\n", timeout=10)

    assert result.exit_code == 0
    cwd = os.path.realpath(result.stdout.strip())
    assert cwd.startswith(os.path.realpath(tempfile.gettempdir()))
    assert "daylight-sandbox" in cwd


def test_run_script_leaves_no_script_file_in_the_working_directory(
    tmp_path: Path,
) -> None:
    (tmp_path / "data.txt").write_text("payload", encoding="utf-8")

    run_script("print('x')\n", cwd=tmp_path, timeout=10)

    assert sorted(path.name for path in tmp_path.iterdir()) == ["data.txt"]


def test_run_script_spawn_failure_is_a_result_not_an_exception(tmp_path: Path) -> None:
    result = run_script("print('x')\n", cwd=tmp_path, python="no-such-python-dc024", timeout=10)

    assert result.exit_code == 127
    assert result.stdout == ""
    assert result.timed_out is False
    assert "no-such-python-dc024" in result.stderr


# -- streamed output ----------------------------------------------------------


def test_on_output_receives_lines_as_produced(tmp_path: Path) -> None:
    script = (
        "import time\n"
        "print('one', flush=True)\n"
        "time.sleep(0.8)\n"
        "print('two', flush=True)\n"
    )
    stamps: list[tuple[str, float]] = []

    result = run_script(
        script,
        cwd=tmp_path,
        timeout=10,
        on_output=lambda line: stamps.append((line, monotonic())),
    )

    assert result.exit_code == 0
    assert [line for line, _stamp in stamps] == ["one", "two"]
    assert stamps[1][1] - stamps[0][1] >= 0.5  # streamed, not batched at exit


def test_on_output_lines_have_no_trailing_newline(tmp_path: Path) -> None:
    lines: list[str] = []

    run_script("print('a')\nprint('b')\n", cwd=tmp_path, timeout=10, on_output=lines.append)

    assert lines == ["a", "b"]


def test_broken_on_output_callback_does_not_break_the_run(tmp_path: Path) -> None:
    def broken(line: str) -> None:
        raise RuntimeError("callback bug")

    result = run_script("print('still fine')\n", cwd=tmp_path, timeout=10, on_output=broken)

    assert result.exit_code == 0
    assert result.stdout == "still fine\n"


# -- wall-clock timeout -------------------------------------------------------


def test_timeout_kills_the_script_and_reports_timed_out(tmp_path: Path) -> None:
    script = "import time\nprint('started', flush=True)\ntime.sleep(60)\n"

    result = run_script(script, cwd=tmp_path, timeout=1.0)

    assert result.timed_out is True
    assert result.exit_code != 0
    assert "started" in result.stdout
    assert result.duration_seconds >= 1.0


# -- POSIX resource limits ----------------------------------------------------


def test_cpu_limit_kills_a_runaway_script(tmp_path: Path) -> None:
    if sandbox.resource is None:
        pytest.skip("resource module unavailable on this platform")
    script = "while True:\n    pass\n"

    result = run_script(script, cwd=tmp_path, timeout=15, cpu_seconds=1)

    assert result.exit_code != 0
    assert result.timed_out is False  # the rlimit killed it, not the wall clock


def test_memory_limit_is_accepted_and_applied_without_crash(tmp_path: Path) -> None:
    if sandbox.resource is None:
        pytest.skip("resource module unavailable on this platform")
    script = "print('fine')\n"

    result = run_script(script, cwd=tmp_path, timeout=10, memory_limit_bytes=256 * 1024 * 1024)

    assert result.exit_code == 0
    assert result.stdout == "fine\n"


def test_limits_degrade_gracefully_without_resource_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sandbox, "resource", None)

    result = run_script(
        "print('degraded')\n",
        cwd=tmp_path,
        timeout=10,
        memory_limit_bytes=1024,
        cpu_seconds=1,
    )

    assert result.exit_code == 0
    assert result.stdout == "degraded\n"


def test_run_script_signature_is_keyword_only_after_script() -> None:
    import inspect

    parameters = inspect.signature(run_script).parameters
    assert list(parameters) == [
        "script",
        "cwd",
        "timeout",
        "python",
        "on_output",
        "memory_limit_bytes",
        "cpu_seconds",
    ]
    assert parameters["script"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    for name in ("cwd", "timeout", "python", "on_output", "memory_limit_bytes", "cpu_seconds"):
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY, name


def test_duration_is_measured(tmp_path: Path) -> None:
    started = monotonic()

    result = run_script("import time\ntime.sleep(0.3)\n", cwd=tmp_path, timeout=10)

    assert result.duration_seconds >= 0.25
    assert result.duration_seconds <= monotonic() - started + 1.0
