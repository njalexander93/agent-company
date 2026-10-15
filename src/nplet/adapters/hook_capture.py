"""Capture bounded Codex metadata after the supervised adapter has produced its result.

The private opt-in never changes admission, identity, readiness, or hook trust.
Payloads and unknown fields are omitted. A separate bounded writer may fail without
replacing the original adapter response. Native callback delivery remains unproven
until this observer is invoked by the installed host.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ENVIRONMENT = "NPLET_CODEX_CAPTURE_DIR"
BYTE_LIMIT = 8192
FILE_LIMIT = 1024
TIME_LIMIT = 0.25
FIELDS = (
    "session_id",
    "agent_id",
    "agent_type",
    "turn_id",
    "tool_use_id",
    "hook_event_name",
    "tool_name",
    "cwd",
    "model",
    "permission_mode",
    "timestamp",
)
DECISIONS = {"allow", "deny", "ask", "block", "approve"}


def response_shape(value: object) -> dict[str, Any]:
    """Describe known completion fields without retaining tool output or arbitrary keys.

    Args:
        value: Native response whose contents must remain private.

    Returns:
        Fixed field names and their types, plus typed terminal status facts.
    """
    # Inspect a bounded serialized object only; malformed or deep payloads remain opaque.
    shape: dict[str, Any] = {"type": type(value).__name__}
    if isinstance(value, str) and len(value) <= 65536:
        try:
            value = json.loads(value)
            shape["decoded_type"] = type(value).__name__
        # Invalid serialized responses expose only their original type.
        except (ValueError, RecursionError):
            return shape
    # Never traverse arbitrary output subtrees or record their user-controlled keys.
    if isinstance(value, dict):
        for key in (
            "exit_code",
            "exitCode",
            "stdout",
            "stderr",
            "interrupted",
            "isError",
            "output",
            "content",
            "text",
            "result",
            "session_id",
        ):
            # Retain only known field types and explicitly permitted terminal facts.
            if key in value:
                shape[key] = {"type": type(value[key]).__name__}
                if key in {"exit_code", "exitCode"} and type(value[key]) is int:
                    shape[key]["value"] = value[key]
                elif key in {"interrupted", "isError"} and type(value[key]) is bool:
                    shape[key]["value"] = value[key]
    # Lists reveal only a bounded prefix of element types, never element contents.
    elif isinstance(value, list):
        shape["item_types"] = [type(item).__name__ for item in value[:4]]
    return shape


def metadata(event: dict[str, Any], result: dict[str, Any], status: int) -> dict[str, Any]:
    """Select fixed top-level metadata without traversing provider or task payloads.

    Args:
        event: Parsed native hook event; nested payloads remain private.
        result: Original adapter response; only known decision enum values survive.
        status: Original supervised adapter exit status.

    Returns:
        A bounded flat observation with native IDs when the event supplies them.
    """
    # Only published common/event metadata is retained; absent IDs remain absent.
    native = {
        key: value
        for key in FIELDS
        if isinstance(value := event.get(key), str) and len(value) <= 512
    }
    # Decision fields are accepted only at the response boundary and as known enums.
    decisions: dict[str, str | bool] = {}
    specific = result.get("hookSpecificOutput")
    if isinstance(specific, dict):
        value = specific.get("permissionDecision")
        if isinstance(value, str) and value in DECISIONS:
            decisions["permissionDecision"] = value
    # Preserve only recognized top-level admission decisions.
    value = result.get("decision")
    if isinstance(value, str) and value in DECISIONS:
        decisions["decision"] = value
    # The continuation fact is recorded only when the host supplied a real boolean.
    value = result.get("continue")
    if type(value) is bool:
        decisions["continue"] = value
    # Fixed omission markers convey privacy boundaries without recording arbitrary keys.
    observation = {
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "native": native,
        "adapter_decisions": decisions,
        "adapter_exit_code": status,
        "response_shape": response_shape(event.get("tool_response")),
        "payloads": "omitted",
        "unknown_fields": "omitted",
        "identity_limit": "No child identity is inferred from a parent session ID.",
    }
    return observation


def capture(event: dict[str, Any], result: dict[str, Any], status: int) -> None:
    """Best-effort record an already-completed Codex decision within a separate budget.

    Args:
        event: Native Codex event already evaluated by the supervised adapter.
        result: Original adapter result, never modified by this diagnostic.
        status: Original adapter exit status, never modified by this diagnostic.
    """
    # An explicit private path enables only this Codex diagnostic; other hosts stay unchanged.
    path = os.environ.get(ENVIRONMENT)
    if not path or os.name != "posix":
        return
    # Contain formatting, process, and writer failures without replacing the guard result.
    try:
        data = json.dumps(metadata(event, result, status), separators=(",", ":")).encode()
        # Oversized metadata is discarded before any writer process starts.
        if len(data) > BYTE_LIMIT:
            return
        # Isolate diagnostic I/O from the completed lifecycle adapter.
        with subprocess.Popen(
            [sys.executable, "-m", "nplet.adapters.hook_capture", "--write", path],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ) as writer:
            # The original mutation worker is reaped; this writer has no lifecycle authority.
            try:
                writer.communicate(data, timeout=TIME_LIMIT)
            # Terminate and reap a failed or timed-out writer before propagating its failure.
            except BaseException:
                writer.kill()
                writer.communicate()
                raise
    except Exception:
        # All ordinary diagnostic failures are non-authoritative, including malformed metadata.
        return


def write_record(path: Path, data: bytes) -> None:
    """Reserve one private fixed slot and write at most the diagnostic byte budget.

    Args:
        path: Existing private absolute directory explicitly selected by the human.
        data: Already-redacted metadata bounded by the caller and rechecked here.

    Raises:
        OSError: The directory is unsafe, unavailable, or full.
        ValueError: The record exceeds its byte budget or the path is not absolute.
    """
    # Require an existing private POSIX directory and reject a symlink at its boundary.
    if not path.is_absolute() or len(data) > BYTE_LIMIT:
        raise ValueError("Invalid capture boundary")
    # Open the selected directory without following a symlink and validate its permissions.
    directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if os.fstat(directory).st_mode & 0o077:
            raise OSError("Capture directory must be private")
        # Atomic fixed slots bound aggregate bytes and prevent concurrent records overwriting.
        for slot in range(FILE_LIMIT):
            try:
                fd = os.open(
                    f"event-{slot:04d}.json",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=directory,
                )
            # Occupied slots belong to earlier observations and must never be overwritten.
            except FileExistsError:
                continue
            # Transfer slot ownership only after the stream constructor succeeds.
            try:
                stream = os.fdopen(fd, "wb")
            # A failed constructor leaves the raw descriptor owned by this writer.
            except BaseException:
                os.close(fd)
                raise
            # Close failures remain in this disposable writer, never in the original adapter.
            with stream:
                stream.write(data)
            return
        # Exhaustion ends this disposable capture without extending the storage budget.
        raise OSError("Capture slots exhausted")
    # Release the directory descriptor on both successful writes and all failure paths.
    finally:
        os.close(directory)


def main() -> int:
    """Write one bounded observation in the disposable diagnostic process.

    Returns:
        Zero after capture, or two for any rejected diagnostic operation.
    """
    # Accept only the internal writer invocation; do not install or activate any hooks.
    if len(sys.argv) != 3 or sys.argv[1] != "--write" or os.name != "posix":
        return 2
    # Read at most one bounded record and delegate safe storage to the writer boundary.
    try:
        data = sys.stdin.buffer.read(BYTE_LIMIT + 1)
        write_record(Path(sys.argv[2]), data)
    # Report diagnostic failure without changing the already-completed adapter decision.
    except Exception:
        return 2
    # Successful storage is the only zero-status writer outcome.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
