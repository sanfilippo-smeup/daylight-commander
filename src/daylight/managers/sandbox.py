"""Sandboxed runner for AI-generated dashboard scripts (DC-024, FR-804, ADR-014).

The builder's generated script is untrusted code: :func:`run_script` executes
it as an isolated subprocess (``python -I``, no shell, argument-list argv),
writes it to a temporary file outside the working directory, bounds it with a
wall-clock timeout and -- where the POSIX :mod:`resource` module exists -- with
memory and CPU rlimits. Stdout is streamed line by line to an ``on_output``
callback as the script produces it (FR-805); stderr and the exit status are
captured and surfaced in the returned :class:`daylight.models.SandboxResult`.

This is a best-effort sandbox (documented in ADR-014): on platforms without
``resource`` the limits are skipped instead of crashing, and a wall-clock
timeout always remains. Like every manager, this module never imports UI code
(ADR-003).
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from time import monotonic
from typing import Any

from daylight.models import SandboxResult

try:  # POSIX only; where it is absent the limits degrade gracefully (AC7).
    import resource
except ImportError:  # pragma: no cover - Windows
    resource = None  # type: ignore[assignment]

__all__ = ["OutputCallback", "run_script"]

logger = logging.getLogger("daylight.managers.sandbox")

#: Signature of the callback that receives stdout lines as they are produced,
#: without their trailing newline.
OutputCallback = Callable[[str], None]

#: Seconds a killed child gets to exit before the SIGKILL fallback.
_KILL_GRACE_SECONDS = 2.0

#: Exit status reported when the script could not be spawned at all.
_SPAWN_FAILURE_EXIT = 127


def run_script(
    script: str,
    *,
    cwd: str | Path | None = None,
    timeout: float = 30.0,
    python: str = sys.executable,
    on_output: OutputCallback | None = None,
    memory_limit_bytes: int | None = None,
    cpu_seconds: int | None = None,
) -> SandboxResult:
    """Run ``script`` in a sandboxed subprocess and return its outcome (AC6).

    The script is written to a temporary file (never into the working
    directory, so a data directory stays clean per FR-807) and executed as
    ``python -I <script>`` with no shell. ``cwd`` is the working directory the
    script runs in -- the data directory it reads from; when ``None`` a fresh
    temporary directory is created and removed afterwards. ``timeout`` is the
    wall-clock limit: a script that exceeds it is killed and reported with
    ``timed_out=True``. ``memory_limit_bytes`` and ``cpu_seconds`` become POSIX
    rlimits where the :mod:`resource` module exists and are ignored where it
    does not (AC7). ``on_output``, when given, receives every stdout line
    (newline stripped) as it is produced.

    A script that cannot even be spawned (missing interpreter, missing working
    directory) is reported as a normal failed run with exit code 127 instead of
    raising, so the UI always gets a :class:`SandboxResult` to show.
    """
    owns_workdir = cwd is None
    workdir = Path(tempfile.mkdtemp(prefix="daylight-sandbox-")) if owns_workdir else Path(cwd)
    script_path = _write_script_file(script)
    try:
        return _execute(
            script_path,
            workdir=workdir,
            timeout=timeout,
            python=python,
            on_output=on_output,
            memory_limit_bytes=memory_limit_bytes,
            cpu_seconds=cpu_seconds,
        )
    finally:
        _remove(script_path)
        if owns_workdir:
            shutil.rmtree(workdir, ignore_errors=True)


def _write_script_file(script: str) -> Path:
    """Write ``script`` to a temporary file outside the working directory."""
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".py", prefix="daylight-script-", encoding="utf-8", delete=False
    )
    try:
        handle.write(script)
    finally:
        handle.close()
    return Path(handle.name)


def _execute(
    script_path: Path,
    *,
    workdir: Path,
    timeout: float,
    python: str,
    on_output: OutputCallback | None,
    memory_limit_bytes: int | None,
    cpu_seconds: int | None,
) -> SandboxResult:
    """Spawn, watch and reap one sandbox run; the caller cleans up files."""
    argv = [python, "-I", str(script_path)]
    preexec = _limit_preexec(memory_limit_bytes, cpu_seconds)
    start = monotonic()
    try:
        process = subprocess.Popen(
            argv,
            cwd=str(workdir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **_child_kwargs(preexec),
        )
    except OSError as error:
        logger.warning("cannot spawn sandbox script: %s", error)
        return SandboxResult(
            exit_code=_SPAWN_FAILURE_EXIT,
            stdout="",
            stderr=str(error),
            timed_out=False,
            duration_seconds=monotonic() - start,
        )

    stdout_lines: list[str] = []
    stderr_chunks: list[str] = []

    def pump_stdout() -> None:
        if process.stdout is None:  # pragma: no cover - defensive
            return
        try:
            for line in process.stdout:
                stdout_lines.append(line)
                if on_output is not None:
                    try:
                        on_output(_chomp(line))
                    except Exception:  # a broken callback must not kill the pump
                        logger.warning("on_output callback failed", exc_info=True)
        except (ValueError, OSError):  # closed under us
            pass

    def pump_stderr() -> None:
        if process.stderr is None:  # pragma: no cover - defensive
            return
        try:
            stderr_chunks.append(process.stderr.read())
        except (ValueError, OSError):  # closed under us
            pass

    readers = (
        threading.Thread(target=pump_stdout, daemon=True, name="sandbox-stdout"),
        threading.Thread(target=pump_stderr, daemon=True, name="sandbox-stderr"),
    )
    for reader in readers:
        reader.start()

    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_process_group(process)
        try:
            process.wait(timeout=_KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:  # pragma: no cover - best effort
            pass
    for reader in readers:
        reader.join(timeout=_KILL_GRACE_SECONDS)

    return SandboxResult(
        exit_code=process.returncode if process.returncode is not None else -1,
        stdout="".join(stdout_lines),
        stderr="".join(stderr_chunks),
        timed_out=timed_out,
        duration_seconds=monotonic() - start,
    )


def _child_kwargs(preexec: Callable[[], None] | None) -> dict[str, Any]:
    """Popen keyword arguments: process group on POSIX, rlimits where available."""
    kwargs: dict[str, Any] = {}
    if os.name == "posix":
        kwargs["start_new_session"] = True
        if preexec is not None:
            kwargs["preexec_fn"] = preexec
    return kwargs


def _limit_preexec(
    memory_limit_bytes: int | None, cpu_seconds: int | None
) -> Callable[[], None] | None:
    """A preexec callback applying the rlimits, or ``None`` when not applicable.

    Only POSIX with an importable :mod:`resource` gets limits (AC7); the
    callback never raises for a limit the platform refuses to lower.
    """
    if resource is None or os.name != "posix":
        return None
    if memory_limit_bytes is None and cpu_seconds is None:
        return None

    def apply() -> None:
        if memory_limit_bytes is not None:
            _set_rlimit(resource.RLIMIT_AS, memory_limit_bytes)
        if cpu_seconds is not None:
            _set_rlimit(resource.RLIMIT_CPU, cpu_seconds)

    return apply


def _set_rlimit(which: int, limit: int) -> None:
    """Lower one rlimit to ``limit`` without ever raising the hard limit."""
    assert resource is not None  # only called from _limit_preexec
    _, hard = resource.getrlimit(which)
    ceiling = limit if hard in (resource.RLIM_INFINITY, -1) else min(limit, hard)
    try:
        resource.setrlimit(which, (ceiling, hard))
    except (OSError, ValueError):
        logger.debug("rlimit %d not applied", which, exc_info=True)


def _kill_process_group(process: subprocess.Popen[str]) -> None:
    """Kill the sandbox process -- the whole group on POSIX, else the child."""
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
            return
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        process.kill()
    except (OSError, ValueError):  # already gone
        pass


def _chomp(line: str) -> str:
    """Strip the single trailing newline a streamed line carries."""
    return line[:-1] if line.endswith("\n") else line


def _remove(path: Path) -> None:
    """Delete ``path`` if it still exists; cleanup must never mask the result."""
    try:
        path.unlink()
    except OSError:
        pass
