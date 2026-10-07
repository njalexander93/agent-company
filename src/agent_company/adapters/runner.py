"""Supervise one hook without platform-specific signals or surviving mutation workers."""

from __future__ import annotations

import importlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Callable

from agent_company.lifecycle import task_workspace as core

INPUT_LIMIT = 1024 * 1024
DEADLINE_SECONDS = 2.0


def run(
    handler: Callable[[core.JSONObject], core.JSONObject],
    failure: Callable[[str, str], core.JSONObject],
    error_status: int = 2,
) -> int:
    """Read bounded input and reap the mutation worker before reporting a timeout.

    Args:
        handler: Selected host handler; only its known module name selects the worker.
        failure: Host-specific failure response renderer.
        error_status: Existing host exit convention for protocol failures.

    Returns:
        Zero on success, or the selected host's failure exit code.
    """
    deadline = time.monotonic() + DEADLINE_SECONDS
    incoming: queue.Queue[bytes | Exception] = queue.Queue(maxsize=1)

    def read_input() -> None:
        """Read stdin without letting an open pipe delay the supervisor deadline."""
        try:
            chunks: list[bytes] = []
            size = 0
            while size <= INPUT_LIMIT:
                chunk = os.read(sys.stdin.fileno(), min(65536, INPUT_LIMIT + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            incoming.put(b"".join(chunks))
        except Exception as error:
            incoming.put(error)

    # This daemon only reads bytes. All lifecycle mutation happens in the child process.
    threading.Thread(target=read_input, daemon=True).start()
    name = ""
    try:
        raw = incoming.get(timeout=max(0.001, deadline - time.monotonic()))
        if isinstance(raw, Exception):
            raise raw
        core.require(len(raw) <= INPUT_LIMIT, "SIZE_LIMIT")
        event = core.strict_json(raw)
        core.require(isinstance(event, dict), "INVALID_REQUEST")
        event_name = event.get("hook_event_name", "")
        core.require(isinstance(event_name, str), "INVALID_REQUEST")
        name = core.token(event_name)
        # File entry points use __main__; the source filename still identifies the host.
        module = handler.__module__.rsplit(".", 1)[-1]
        if module == "__main__":
            from pathlib import Path

            module = Path(handler.__globals__["__file__"]).stem
        core.require(module in {"codex", "claude", "cursor"}, "INVALID_REQUEST")
        with subprocess.Popen(
            [sys.executable, "-m", "agent_company.adapters.runner", "--worker", module],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ) as worker:
            try:
                output, errors = worker.communicate(
                    raw, timeout=max(0.001, deadline - time.monotonic())
                )
            except BaseException:
                # TerminateProcess on Windows; SIGKILL on POSIX. Reap before any response.
                worker.kill()
                worker.communicate()
                raise
            core.require(worker.returncode in {0, 2}, "RECOVERY_REQUIRED")
            status = error_status if worker.returncode == 2 else 0
        core.require(len(output) <= INPUT_LIMIT, "SIZE_LIMIT")
        result = core.strict_json(output)
        core.require(isinstance(result, dict), "INVALID_REQUEST")
        if errors:
            sys.stderr.write(errors.decode("utf-8", errors="replace"))
    except Exception as error:
        code = (
            "BUSY"
            if isinstance(error, (queue.Empty, subprocess.TimeoutExpired))
            else error.code
            if isinstance(error, core.WorkspaceError)
            else "RECOVERY_REQUIRED"
        )
        result = failure(name, code)
        status = error_status
        print("TASK_WORKSPACE_NOT_READY: " + code, file=sys.stderr)
    print(json.dumps(result, separators=(",", ":")))
    return status


def main() -> int:
    """Run the selected handler in the disposable, supervised mutation process.

    Returns:
        Zero after a valid handler response; two for an invalid worker invocation.
    """
    if (
        len(sys.argv) != 3
        or sys.argv[1] != "--worker"
        or sys.argv[2] not in {"codex", "claude", "cursor"}
    ):
        return 2
    adapter = importlib.import_module("agent_company.adapters." + sys.argv[2])
    event = core.strict_json(sys.stdin.buffer.read(INPUT_LIMIT + 1))
    status = 0
    try:
        result = adapter.handle(event)
    except Exception as error:
        code = error.code if isinstance(error, core.WorkspaceError) else "RECOVERY_REQUIRED"
        result = adapter.failure(event.get("hook_event_name", ""), code)
        status = 2
    output = json.dumps(result, separators=(",", ":")).encode("utf-8")
    if len(output) > INPUT_LIMIT:
        output = json.dumps(
            adapter.failure(event.get("hook_event_name", ""), "SIZE_LIMIT")
        ).encode()
        status = 2
    sys.stdout.buffer.write(output + b"\n")
    return status


if __name__ == "__main__":
    sys.exit(main())
