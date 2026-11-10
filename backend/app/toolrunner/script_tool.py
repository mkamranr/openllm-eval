"""Script tools: one fresh, resource-limited Python process per call.

The process gets the call's arguments as JSON on stdin and must print one JSON value on
stdout. It runs with:

- an empty environment;
- isolated mode (``python -I -S``: no user site, no ``PYTHON*`` variables, no site imports);
- a private temporary working directory, deleted afterwards;
- its own process group (session), killed as a whole on timeout;
- resource limits (POSIX): CPU seconds, address space, file size, open files, no core dumps.

The limits are set by a tiny launcher that then ``exec``s the script, so they apply to the
script process itself and are inherited by anything it starts.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.toolrunner.limits import ToolResult, truncate_text

STDERR_CAP = 4096
# Sets the limits, then replaces itself with the script (limits survive exec).
_LAUNCHER = """
import os, sys
try:
    import resource
except ImportError:
    resource = None
if resource is not None:
    cpu, mem, fsize = (int(v) for v in sys.argv[1:4])
    def lim(name, value):
        res = getattr(resource, name, None)
        if res is None:
            return
        try:
            resource.setrlimit(res, (value, value))
        except (ValueError, OSError):
            pass
    lim("RLIMIT_CPU", cpu)
    lim("RLIMIT_AS", mem)
    lim("RLIMIT_FSIZE", fsize)
    lim("RLIMIT_NOFILE", 64)
    lim("RLIMIT_CORE", 0)
if os.name == "nt":  # no real exec on Windows: run the script in this (already isolated) process
    import runpy
    sys.argv = [sys.argv[4]]
    runpy.run_path(sys.argv[0], run_name="__main__")
else:
    os.execv(sys.executable, [sys.executable, "-I", "-S", sys.argv[4]])
"""


@dataclass(frozen=True)
class ScriptLimits:
    memory_mb: int = 256
    file_size_bytes: int = 16 * 1024 * 1024
    temp_root: str | None = None


async def _read_capped(stream: asyncio.StreamReader, cap: int) -> tuple[bytes, bool]:
    """Read everything, keep the first ``cap`` bytes."""
    kept = bytearray()
    truncated = False
    while chunk := await stream.read(65_536):
        room = cap - len(kept)
        if room > 0:
            kept.extend(chunk[:room])
        if len(chunk) > max(room, 0):
            truncated = True
    return bytes(kept), truncated


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError):
        pass


async def run_script(
    script: Path,
    arguments: dict[str, Any],
    *,
    timeout: float,  # noqa: ASYNC109 - kills the process group, not just the await
    max_output_bytes: int,
    limits: ScriptLimits | None = None,
) -> ToolResult:
    limits = limits or ScriptLimits()
    workdir = tempfile.mkdtemp(prefix="tool-", dir=limits.temp_root)
    started = time.monotonic()
    cpu_seconds = max(1, int(timeout) + 1)
    command = [
        sys.executable,
        "-I",
        "-S",
        "-c",
        _LAUNCHER,
        str(cpu_seconds),
        str(limits.memory_mb * 1024 * 1024),
        str(limits.file_size_bytes),
        str(script),
    ]
    kwargs: dict[str, Any] = {}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    else:  # Windows needs SYSTEMROOT to start Python at all; nothing else is passed.
        kwargs["env"] = {"SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\Windows")}
    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workdir,
            **({"env": {}} | kwargs),
        )
    except OSError as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        return ToolResult.failure(f"The script could not be started: {exc}", started)

    if proc.stdin is None or proc.stdout is None or proc.stderr is None:  # pragma: no cover
        _kill_group(proc)
        shutil.rmtree(workdir, ignore_errors=True)
        return ToolResult.failure("The script's pipes could not be opened.", started)
    payload = json.dumps(arguments).encode("utf-8")

    async def communicate() -> tuple[tuple[bytes, bool], tuple[bytes, bool]]:
        async def feed() -> None:
            try:
                proc.stdin.write(payload)  # type: ignore[union-attr]
                await proc.stdin.drain()  # type: ignore[union-attr]
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                proc.stdin.close()  # type: ignore[union-attr]

        _, out, err, _ = await asyncio.gather(
            feed(),
            _read_capped(proc.stdout, max_output_bytes),  # type: ignore[arg-type]
            _read_capped(proc.stderr, STDERR_CAP),  # type: ignore[arg-type]
            proc.wait(),
        )
        return out, err

    try:
        (stdout, truncated), (stderr, _) = await asyncio.wait_for(communicate(), timeout)
    except TimeoutError:
        _kill_group(proc)
        await proc.wait()
        return ToolResult.failure(f"The tool timed out after {timeout:g} s.", started)
    finally:
        _kill_group(proc)  # anything the script left behind in its session
        shutil.rmtree(workdir, ignore_errors=True)

    if proc.returncode != 0:
        detail = truncate_text(stderr.decode("utf-8", "replace").strip(), 500)
        if proc.returncode is not None and proc.returncode < 0:
            reason = f"killed by signal {-proc.returncode}"
            if -proc.returncode in (signal.SIGKILL, getattr(signal, "SIGXCPU", -1)):
                reason += " (a resource limit was reached)"
        else:
            reason = f"exit code {proc.returncode}"
        message = f"The script failed ({reason})" + (f": {detail}" if detail else ".")
        return ToolResult.failure(message, started)
    if truncated:
        return ToolResult.failure(
            f"The script printed more than {max_output_bytes} bytes.", started, truncated=True
        )
    text = stdout.decode("utf-8", "replace").strip()
    try:
        result = json.loads(text)
    except ValueError:
        return ToolResult.failure("The script did not print a JSON value.", started)
    return ToolResult.success(result, started)
