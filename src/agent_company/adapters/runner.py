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
    # Establish one deadline for input collection and child execution.
    deadline = time.monotonic() + DEADLINE_SECONDS
    incoming: queue.Queue[bytes | Exception] = queue.Queue(maxsize=1)

    def read_input() -> None:
        """Collect bounded stdin bytes for the supervisor queue.

        The daemon may remain blocked on an open pipe. Only the child process mutates
        lifecycle state, and the supervisor applies its own deadline.
        """
        # Read bounded chunks until EOF or the input budget is exhausted.
        try:
            # Accumulate bytes without parsing or touching lifecycle state.
            chunks: list[bytes] = []
            size = 0
            # Include one byte beyond the limit so oversize input is detectable.
            while size <= INPUT_LIMIT:
                # Read only the remaining budget plus the sentinel byte.
                chunk = os.read(sys.stdin.fileno(), min(65536, INPUT_LIMIT + 1 - size))
                # Stop when the writer closes the stream.
                if not chunk:
                    # EOF completes input collection for the worker.
                    break
                chunks.append(chunk)
                size += len(chunk)
            incoming.put(b"".join(chunks))
        except Exception as error:
            # Return read failures through the queue for bounded reporting.
            incoming.put(error)

    # This daemon only reads bytes. All lifecycle mutation happens in the child process.
    threading.Thread(target=read_input, daemon=True).start()
    name = ""
    # Validate the host event before starting a mutation worker.
    try:
        # Wait only until the shared deadline for the daemon's input result.
        raw = incoming.get(timeout=max(0.001, deadline - time.monotonic()))
        # Propagate a captured stdin read failure to the host error response.
        if isinstance(raw, Exception):
            # The outer handler maps this failure to a bounded diagnostic.
            raise raw
        core.require(len(raw) <= INPUT_LIMIT, "SIZE_LIMIT")
        event = core.strict_json(raw)
        core.require(isinstance(event, dict), "INVALID_REQUEST")
        event_name = event.get("hook_event_name", "")
        core.require(isinstance(event_name, str), "INVALID_REQUEST")
        name = core.token(event_name)
        # File entry points use __main__; the source filename still identifies the host.
        module = handler.__module__.rsplit(".", 1)[-1]
        # Resolve script execution to the same allowlisted adapter module.
        if module == "__main__":
            # Derive the adapter name from the handler's source file.
            from pathlib import Path

            module = Path(handler.__globals__["__file__"]).stem
        core.require(module in {"codex", "claude", "cursor"}, "INVALID_REQUEST")
        # Isolate lifecycle mutation in a disposable child process.
        with subprocess.Popen(
            [sys.executable, "-m", "agent_company.adapters.runner", "--worker", module],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ) as worker:
            # Wait for a bounded response, killing and reaping on any interruption.
            try:
                # Send the validated raw event with the remaining deadline.
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
        # Relay worker diagnostics without exposing them in the JSON response.
        if errors:
            # Decode invalid bytes defensively for stderr only.
            sys.stderr.write(errors.decode("utf-8", errors="replace"))
    except Exception as error:
        # Convert timeout and runtime failures into bounded host diagnostics.
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
    # Reject any worker invocation outside the allowlisted adapter protocol.
    if (
        len(sys.argv) != 3
        or sys.argv[1] != "--worker"
        or sys.argv[2] not in {"codex", "claude", "cursor"}
    ):
        # Signal a bad worker command without loading an adapter.
        return 2
    # Parse one bounded event and execute only its selected adapter.
    adapter = importlib.import_module("agent_company.adapters." + sys.argv[2])
    event = core.strict_json(sys.stdin.buffer.read(INPUT_LIMIT + 1))
    status = 0
    # Keep adapter exceptions inside the worker's bounded response shape.
    try:
        # Invoke the host adapter on the parsed event.
        result = adapter.handle(event)
    except Exception as error:
        # Render a host-specific failure without leaking the exception body.
        code = error.code if isinstance(error, core.WorkspaceError) else "RECOVERY_REQUIRED"
        result = adapter.failure(event.get("hook_event_name", ""), code)
        status = 2
    output = json.dumps(result, separators=(",", ":")).encode("utf-8")
    # Replace an oversized response with a bounded SIZE_LIMIT diagnostic.
    if len(output) > INPUT_LIMIT:
        # Preserve the worker's failure status when truncation is required.
        output = json.dumps(
            adapter.failure(event.get("hook_event_name", ""), "SIZE_LIMIT")
        ).encode()
        status = 2
    sys.stdout.buffer.write(output + b"\n")
    return status


# Enter the worker or supervisor mode only for direct script execution.
if __name__ == "__main__":
    # Return the selected mode's exit status to the host.
    sys.exit(main())
