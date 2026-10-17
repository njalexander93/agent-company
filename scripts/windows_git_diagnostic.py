"""Temporarily measure Windows Git delays; remove this harness after diagnosis.

These diagnostic artifacts never replace native acceptance or change production timeouts.
"""

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest import mock

import coverage
import pytest

from nplet.lifecycle import task_workspace as workspace

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION_TARGET = "tests/integration"
TRACE_SEED = 20261017
TRACE_TARGETS = ("GIT_TRACE2", "GIT_TRACE2_PERF", "GIT_TRACE2_EVENT")
QUERY_TYPES = {
    ("rev-parse", "--show-toplevel"): "toplevel",
    ("rev-parse", "--is-bare-repository"): "bare",
    ("rev-parse", "--path-format=absolute", "--git-common-dir"): "common",
    ("worktree", "list", "--porcelain", "-z"): "worktrees",
}
LATEST_LIMIT = 128
SLOW_LIMIT = 32
ERROR_LIMIT = 64
ACTIVE_TRACE_LIMIT = 32
MAX_RETAINED_TRACES = LATEST_LIMIT + SLOW_LIMIT + ERROR_LIMIT + 1 + ACTIVE_TRACE_LIMIT
SLOW_SECONDS = 0.5
MAX_TRACE_BYTES = 65536
MAX_OUTPUT_BYTES = 1048576
RETENTION_LIMITS = {
    "latest": LATEST_LIMIT,
    "slow": SLOW_LIMIT,
    "slow_measured_seconds": SLOW_SECONDS,
    "errors": ERROR_LIMIT,
    "first_failure": 1,
    "active_traces": ACTIVE_TRACE_LIMIT,
    "retained_trace_files": MAX_RETAINED_TRACES,
    "trace_bytes": MAX_TRACE_BYTES,
    "communicate_attempts_per_record": 4,
    "pytest_output_bytes": MAX_OUTPUT_BYTES,
}


def failure(error: Exception) -> dict[str, Any]:
    """Retain bounded error metadata without process output or environment values.

    Args:
        error: Observed diagnostic failure.

    Returns:
        JSON-safe exception type, message and optional errno.
    """
    return {
        "type": type(error).__name__,
        "message": str(error)[:512],
        "errno": getattr(error, "errno", None),
    }


def timeout_snapshot(process: Any) -> dict[str, Any]:
    """Observe bounded process/thread state without polling or changing returncode.

    Args:
        process: Original Popen object immediately after TimeoutExpired.

    Returns:
        PID, zero-time Windows handle wait result and reader-thread alive states.
        Unsupported or inaccessible handle facts remain explicitly unavailable.
    """
    # Observe the handle before thread metadata and record that these observations take time.
    started = time.perf_counter()
    pid = getattr(process, "pid", None)
    result: dict[str, Any] = {
        "pid": pid if isinstance(pid, int) else None,
        "handle_wait": None,
        "handle_wait_status": "unavailable",
        "readers": {},
    }
    # The Windows subprocess module already imports _winapi; avoid import/disk work here.
    api = getattr(subprocess, "_winapi", None)
    handle = getattr(process, "_handle", None)
    if api is not None and handle is not None:
        try:
            wait_result = api.WaitForSingleObject(handle, 0)
            result["handle_wait_perf"] = time.perf_counter()
            result["handle_wait_wall"] = time.time()
            result["handle_wait"] = int(wait_result)
            result["handle_wait_status"] = "observed"
        except (OSError, ValueError) as error:
            result["handle_wait_status"] = "failed"
            result["handle_wait_error"] = failure(error)
    # Inspect alive states only, never reader buffers, process output or frame locals.
    for name in ("stdout", "stderr"):
        reader = getattr(process, name + "_thread", None)
        try:
            result["readers"][name] = None if reader is None else bool(reader.is_alive())
        except RuntimeError as error:
            result["readers"][name] = failure(error)
    result["snapshot_end_perf"] = time.perf_counter()
    result["snapshot_end_wall"] = time.time()
    result["snapshot_seconds"] = result["snapshot_end_perf"] - started
    return result


class GitObserver:
    """Aggregate every parent Git call while retaining bounded detailed evidence."""

    def __init__(self, output: Path, staging: Path) -> None:
        """Initialize independent counters, retained rings and the current pytest context.

        Args:
            output: Uploaded artifact directory.
            staging: Disposable raw trace directory outside uploaded artifacts.
        """
        # Keep permanent totals separate from detail that is intentionally evicted.
        self.output, self.staging = output, staging
        self.lock = threading.RLock()
        self.latest: deque[dict[str, Any]] = deque(maxlen=LATEST_LIMIT)
        self.slow: deque[dict[str, Any]] = deque(maxlen=SLOW_LIMIT)
        self.errors: deque[dict[str, Any]] = deque(maxlen=ERROR_LIMIT)
        self.first_failure: dict[str, Any] | None = None
        self.error_latch = False
        self.telemetry_errors: list[dict[str, Any]] = []
        self.context = {"nodeid": "", "phase": "initialization"}
        # Shuffle independent query-specific on/off pairs without adding any Git calls.
        self.assignment_random = {
            query: random.Random(f"{TRACE_SEED}:{query}") for query in QUERY_TYPES.values()
        }
        self.assignment_pairs: dict[str, deque[bool]] = {
            query: deque() for query in QUERY_TYPES.values()
        }
        self.groups = {
            query: {
                group: dict.fromkeys(
                    (
                        "started",
                        "finished",
                        "failed",
                        "timeouts",
                        "nonzero",
                        "creation_errors",
                        "communication_errors",
                        "cleanup_errors",
                        "trace_omitted",
                        "popen_total",
                        "popen_max",
                        "communicate_total",
                        "communicate_max",
                    ),
                    0,
                )
                for group in (("other",) if query == "other" else ("on", "off"))
            }
            for query in (*QUERY_TYPES.values(), "other")
        }
        self.active: dict[int, dict[str, Any]] = {}
        self.saved_traces: set[int] = set()
        self.counts = dict.fromkeys(
            (
                "started",
                "finished",
                "active",
                "failed",
                "timeouts",
                "nonzero",
                "creation_errors",
                "communication_errors",
                "cleanup_errors",
                "latest_evicted",
                "slow_seen",
                "slow_evicted",
                "errors_evicted",
                "trace_omitted",
                "trace_retention_omitted",
                "trace_deleted",
                "trace_truncated",
                "telemetry_errors",
            ),
            0,
        )
        self.timings = dict.fromkeys(
            ("popen_total", "popen_max", "communicate_total", "communicate_max"), 0.0
        )

    def retained(self) -> list[dict[str, Any]]:
        """Return deduplicated bounded details, including the permanent first failure.

        Returns:
            At most latest + slow + error + first-failure records, ordered by call ID.
        """
        # Deduplicate records appearing in several retention categories.
        records = {
            record["id"]: record
            for ring in (self.latest, self.slow, self.errors)
            for record in ring
        }
        if self.first_failure is not None:
            records[self.first_failure["id"]] = self.first_failure
        return [records[key] for key in sorted(records)]

    def note_error(self, record: dict[str, Any]) -> None:
        """Latch a process failure permanently before any later cleanup can succeed.

        Args:
            record: Call containing the original failure metadata.
        """
        # Retain the first error independently of both bounded error and latest rings.
        self.error_latch = True
        record["failed"] = True
        if self.first_failure is None:
            self.first_failure = record

    def begin(self, command: Any) -> dict[str, Any]:
        """Count every Git call and allocate a bounded active trace slot when available.

        Args:
            command: Original Git argument vector.

        Returns:
            This call's own bounded metadata; timing continues even without a trace slot.
        """
        # Capture the active pytest context before the original process constructor runs.
        with self.lock:
            identifier = self.counts["started"]
            self.counts["started"] += 1
            self.counts["active"] += 1
            # Only the four exact production repository queries participate in randomized pairs.
            query = (
                QUERY_TYPES.get(tuple(command[3:]), "other")
                if len(command) > 3 and str(command[1]) == "-C"
                else "other"
            )
            group = "other"
            if query != "other":
                pending = self.assignment_pairs[query]
                if not pending:
                    pair = [True, False]
                    self.assignment_random[query].shuffle(pair)
                    pending.extend(pair)
                group = "on" if pending.popleft() else "off"
            query_index = sum(row["started"] for row in self.groups[query].values())
            self.groups[query][group]["started"] += 1
            record = {
                "id": identifier,
                **self.context,
                "thread": threading.get_ident(),
                "query": query,
                "query_index": query_index,
                "trace_group": group,
                "command": [str(word)[:512] for word in command[:32]],
                "command_omitted_words": max(0, len(command) - 32),
                "observed_start_wall": time.time(),
                "communicate": [],
                "communicate_seconds": 0.0,
                "communicate_attempts": 0,
            }
            # Bound raw active traces without stopping timing or error observation.
            if group == "on" and len(self.active) < ACTIVE_TRACE_LIMIT:
                record["trace"] = f"git-{identifier:08d}.trace2.jsonl"
                self.active[identifier] = record
            else:
                record["trace"] = None
                if group == "on":
                    self.counts["trace_omitted"] += 1
                    self.groups[query][group]["trace_omitted"] += 1
            return record

    def copy_trace(self, record: dict[str, Any]) -> None:
        """Copy at most the approved trace prefix into uploaded evidence.

        Args:
            record: Selected completed or active Git call.

        Raises:
            OSError: Trace evidence cannot be read or retained.
        """
        # A process may not yet have created its trace, or may have failed before exec.
        name = record.get("trace")
        if not name:
            return
        source = self.staging / name
        if not source.exists():
            return
        # A deletion failure must not permit unbounded creation of uploaded trace files.
        if record["id"] not in self.saved_traces and len(self.saved_traces) >= MAX_RETAINED_TRACES:
            self.counts["trace_retention_omitted"] += 1
            return
        # Keep raw writers outside uploaded artifacts; uploaded prefixes are always bounded.
        with source.open("rb") as stream:
            content = stream.read(MAX_TRACE_BYTES + 1)
        (self.output / name).write_bytes(content[:MAX_TRACE_BYTES])
        self.saved_traces.add(record["id"])
        if len(content) > MAX_TRACE_BYTES and not record.get("trace_truncated"):
            record["trace_truncated"] = True
            self.counts["trace_truncated"] += 1

    def checkpoint(self, current: dict[str, Any] | None = None, *, force: bool = False) -> None:
        """Persist small progress continuously and bounded rings periodically or on error.

        Args:
            current: Call currently being created, communicated or completed.
            force: Also retain detailed rings and snapshots of active Trace2 writers.
        """
        # A failed telemetry write must not replace the production process's own outcome.
        with self.lock:
            try:
                progress = {
                    "counts": self.counts,
                    "timings": self.timings,
                    "groups": self.groups,
                    "trace_seed": TRACE_SEED,
                    "context": self.context,
                    "error_latch": self.error_latch,
                    "current": current,
                    "first_failure": self.first_failure,
                    "telemetry_errors": self.telemetry_errors,
                }
                temporary = self.output / "progress.json.tmp"
                temporary.write_text(json.dumps(progress), encoding="utf-8")
                temporary.replace(self.output / "progress.json")
                # Detailed files remain useful after a job timeout without growing with call count.
                if force or (self.counts["finished"] and self.counts["finished"] % 32 == 0):
                    for record in self.active.values():
                        self.copy_trace(record)
                    details = {
                        "records": self.retained(),
                        "active": list(self.active.values()),
                        "limits": RETENTION_LIMITS,
                    }
                    temporary = self.output / "checkpoint.json.tmp"
                    temporary.write_text(json.dumps(details), encoding="utf-8")
                    temporary.replace(self.output / "checkpoint.json")
            except OSError as error:
                self.error_latch = True
                self.counts["telemetry_errors"] += 1
                if len(self.telemetry_errors) < 8:
                    self.telemetry_errors.append(failure(error))

    def finish(self, record: dict[str, Any], returncode: int | None = None) -> None:
        """Aggregate a completed call, select bounded details and remove unselected traces.

        Args:
            record: Call's complete creation/communication/error metadata.
            returncode: Original process return code, when a process existed.
        """
        # Aggregate every call, including those beyond all detailed retention limits.
        with self.lock:
            record["returncode"] = returncode
            if returncode not in (None, 0):
                self.note_error(record)
            self.counts["finished"] += 1
            self.counts["active"] -= 1
            self.counts["failed"] += int(bool(record.get("failed")))
            self.counts["timeouts"] += int(bool(record.get("timed_out")))
            self.counts["nonzero"] += int(returncode not in (None, 0))
            self.counts["creation_errors"] += int("creation_error" in record)
            self.counts["communication_errors"] += record.get("communication_errors", 0)
            self.counts["cleanup_errors"] += int("cleanup_error" in record)
            self.timings["popen_total"] += record.get("popen_seconds", 0.0)
            self.timings["popen_max"] = max(
                self.timings["popen_max"], record.get("popen_seconds", 0.0)
            )
            self.timings["communicate_total"] += record["communicate_seconds"]
            self.timings["communicate_max"] = max(
                self.timings["communicate_max"], record["communicate_seconds"]
            )
            # Preserve group/query totals even after every detailed record has been evicted.
            group = self.groups[record["query"]][record["trace_group"]]
            group["finished"] += 1
            group["failed"] += int(bool(record.get("failed")))
            group["timeouts"] += int(bool(record.get("timed_out")))
            group["nonzero"] += int(returncode not in (None, 0))
            group["creation_errors"] += int("creation_error" in record)
            group["communication_errors"] += record.get("communication_errors", 0)
            group["cleanup_errors"] += int("cleanup_error" in record)
            group["popen_total"] += record.get("popen_seconds", 0.0)
            group["popen_max"] = max(group["popen_max"], record.get("popen_seconds", 0.0))
            group["communicate_total"] += record["communicate_seconds"]
            group["communicate_max"] = max(group["communicate_max"], record["communicate_seconds"])
            # Rings may evict details, but never the totals or permanent failure latch.
            self.counts["latest_evicted"] += int(len(self.latest) == LATEST_LIMIT)
            self.latest.append(record)
            if record.get("popen_seconds", 0.0) + record["communicate_seconds"] >= SLOW_SECONDS:
                self.counts["slow_seen"] += 1
                self.counts["slow_evicted"] += int(len(self.slow) == SLOW_LIMIT)
                self.slow.append(record)
            if record.get("failed"):
                self.counts["errors_evicted"] += int(len(self.errors) == ERROR_LIMIT)
                self.errors.append(record)
            # Copy selected bytes before deleting raw completed traces; prune prior ring evictions.
            self.active.pop(record["id"], None)
            try:
                try:
                    self.copy_trace(record)
                finally:
                    if record.get("trace"):
                        (self.staging / record["trace"]).unlink(missing_ok=True)
                selected = {row["id"] for row in self.retained()} | set(self.active)
                for identifier in self.saved_traces - selected:
                    (self.output / f"git-{identifier:08d}.trace2.jsonl").unlink(missing_ok=True)
                    self.saved_traces.remove(identifier)
                    self.counts["trace_deleted"] += 1
            except OSError as error:
                self.error_latch = True
                self.counts["telemetry_errors"] += 1
                if len(self.telemetry_errors) < 8:
                    self.telemetry_errors.append(failure(error))
            if record.get("failed") or self.counts["finished"] % 32 == 0:
                self.checkpoint(record, force=True)

    def set_context(self, nodeid: str, phase: str) -> None:
        """Checkpoint the currently active pytest test and phase.

        Args:
            nodeid: Current pytest item identity, or empty outside a test.
            phase: Collection, setup, call, teardown or session completion.
        """
        # Serialize phase changes with the active call and bounded checkpoint snapshots.
        with self.lock:
            self.context = {"nodeid": nodeid[:512], "phase": phase[:64]}
            self.checkpoint(force=True)


class TimedProcess:
    """Delegate the original Git process while collecting bounded parent-side timing."""

    def __init__(
        self,
        constructor: Callable[..., Any],
        observer: GitObserver,
        command: Any,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Measure original construction without changing its arguments or exceptions.

        Args:
            constructor: Original Popen implementation.
            observer: Permanent aggregate and bounded detail collector.
            command: Original Git argument vector.
            *args: Original positional process options.
            **kwargs: Original keyword process options.

        Raises:
            Exception: Original process construction fails.
        """
        # Allocate bounded call metadata before starting the original construction clocks.
        self.observer = observer
        self.record = observer.begin(command)
        # Override all Trace2 targets equally; only an assigned on-group event file is enabled.
        environment = kwargs.get("env")
        kwargs["env"] = {
            **(os.environ if environment is None else environment),
            **dict.fromkeys(TRACE_TARGETS, "0"),
        }
        if self.record["trace"]:
            kwargs["env"]["GIT_TRACE2_EVENT"] = str(observer.staging / self.record["trace"])
        # Start both construction clocks after bounded assignment and environment preparation.
        self.record["popen_start_wall"] = time.time()
        started = time.perf_counter()
        ended = None
        try:
            self.process = constructor(command, *args, **kwargs)
        except Exception as error:
            ended = time.perf_counter()
            with observer.lock:
                self.record["creation_error"] = failure(error)
                observer.note_error(self.record)
            raise
        finally:
            elapsed = (time.perf_counter() if ended is None else ended) - started
            with observer.lock:
                self.record["popen_seconds"] = elapsed
                if "creation_error" in self.record:
                    observer.finish(self.record)

    def communicate(self, *args: Any, **kwargs: Any) -> Any:
        """Preserve communicate results, timeouts and caller-owned kill/reap behavior.

        Args:
            *args: Original communicate positional arguments.
            **kwargs: Original communicate options, including the unchanged timeout.

        Returns:
            The original stdout/stderr pair; telemetry never stores output.

        Raises:
            Exception: The original communicate call fails.
        """
        # End timing before formatting errors or acquiring the observer's telemetry lock.
        attempt: dict[str, Any] = {"timeout": kwargs.get("timeout")}
        started = time.perf_counter()
        ended = None
        try:
            return self.process.communicate(*args, **kwargs)
        except Exception as error:
            ended = (time.perf_counter(), time.time())
            # Capture observation endpoints first, then inspect bounded state in memory only.
            if isinstance(error, subprocess.TimeoutExpired):
                attempt["timed_out"] = True
                attempt["timeout_observation"] = {
                    "perf": ended[0],
                    "wall": ended[1],
                    **timeout_snapshot(self.process),
                }
            attempt["error"] = failure(error)
            raise
        finally:
            end_perf, end_wall = (time.perf_counter(), time.time()) if ended is None else ended
            elapsed = end_perf - started
            with self.observer.lock:
                attempt.update(seconds=elapsed, end_wall=end_wall)
                if attempt.get("timed_out"):
                    self.record["timed_out"] = True
                    self.record["timeout_observation"] = attempt["timeout_observation"]
                self.record["communicate_seconds"] += attempt["seconds"]
                self.record["communicate_attempts"] += 1
                self.record["communicate"].append(attempt)
                del self.record["communicate"][:-4]
                if "error" in attempt:
                    self.record["communication_errors"] = (
                        self.record.get("communication_errors", 0) + 1
                    )
                    self.observer.note_error(self.record)

    def __enter__(self) -> "TimedProcess":
        """Enter the original process context and return the timed communication surface.

        Returns:
            This delegating wrapper.
        """
        self.process.__enter__()
        return self

    def __exit__(self, *args: Any) -> Any:
        """Preserve original cleanup errors and aggregate the call after reaping.

        Args:
            *args: Original exception context.

        Returns:
            The original context manager result.

        Raises:
            Exception: Original pipe cleanup or process reaping fails.
        """
        # Let the original context reap the process before committing diagnostics to disk.
        try:
            return self.process.__exit__(*args)
        except Exception as error:
            with self.observer.lock:
                self.record["cleanup_error"] = failure(error)
                self.observer.note_error(self.record)
            raise
        finally:
            self.observer.finish(self.record, self.process.returncode)

    def __getattr__(self, name: str) -> Any:
        """Expose original process properties and caller-owned kill/poll/wait methods.

        Args:
            name: Requested original process attribute.

        Returns:
            The original attribute unchanged.
        """
        return getattr(self.process, name)


@contextmanager
def observe_git(output: Path) -> Iterator[GitObserver]:
    """Observe every parent Git call, restoring Popen and deleting raw staging on exit.

    Args:
        output: Dedicated uploaded artifact directory.

    Yields:
        Aggregate observer and bounded selected process evidence.
    """
    original = subprocess.Popen
    # Raw files are never uploaded unbounded; only selected prefixes enter output.
    with tempfile.TemporaryDirectory(prefix="nplet-git-trace-") as directory:
        observer = GitObserver(output, Path(directory))

        def popen(command: Any, *args: Any, **kwargs: Any) -> Any:
            """Observe all Git calls without changing non-Git process construction.

            Args:
                command: Original command or shell string.
                *args: Original positional options.
                **kwargs: Original keyword options.

            Returns:
                A timed Git wrapper or unchanged non-Git process.
            """
            # Retention limits never bypass timing or error detection for later Git calls.
            if (
                isinstance(command, (list, tuple))
                and command
                and Path(str(command[0])).name.lower() in {"git", "git.exe"}
            ):
                return TimedProcess(original, observer, command, *args, **kwargs)
            return original(command, *args, **kwargs)

        with mock.patch.object(subprocess, "Popen", popen):
            try:
                yield observer
            finally:
                observer.checkpoint(force=True)


class PhasePlugin:
    """Tag parent Git calls with the active pytest node and phase without altering outcomes."""

    def __init__(self, observer: GitObserver) -> None:
        """Bind the experiment's parent-process observer.

        Args:
            observer: Collector whose current test context will be updated.
        """
        self.observer = observer

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_setup(self, item: pytest.Item) -> None:
        """Checkpoint setup context before fixture setup.

        Args:
            item: Test whose setup is starting.
        """
        self.observer.set_context(item.nodeid, "setup")

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_call(self, item: pytest.Item) -> None:
        """Checkpoint call context before the test body.

        Args:
            item: Test whose body is starting.
        """
        self.observer.set_context(item.nodeid, "call")

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_teardown(self, item: pytest.Item) -> None:
        """Checkpoint teardown context before fixture cleanup.

        Args:
            item: Test whose teardown is starting.
        """
        self.observer.set_context(item.nodeid, "teardown")


def timing_attribution(record: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    """Separate measured creation, communication and Git-internal clock observations.

    Args:
        record: One process's parent-side timing observations.
        events: Selected Trace2 start/exit/atexit records.

    Returns:
        Durations with explicit clock boundaries; missing Git events remain unknown.
    """
    # Keep parent monotonic durations distinct from Git's own elapsed clock.
    attempts = record.get("communicate", [])
    result = {
        "popen_seconds": record["popen_seconds"],
        "communicate_seconds": record.get(
            "communicate_seconds", sum(attempt["seconds"] for attempt in attempts)
        ),
    }
    starts = [event for event in events if event.get("event") == "start"]
    exits = [event for event in events if event.get("event") in {"exit", "atexit"}]
    # Wall differences include scheduling and pipe delay; they cannot identify either alone.
    if starts:
        start_wall = datetime.fromisoformat(starts[0]["time"].replace("Z", "+00:00")).timestamp()
        result["git_start_after_popen_begin_seconds"] = start_wall - record["popen_start_wall"]
    if exits:
        result["git_elapsed_seconds"] = exits[-1].get("t_abs")
        if attempts:
            exit_wall = datetime.fromisoformat(exits[-1]["time"].replace("Z", "+00:00")).timestamp()
            result["last_git_event_to_communicate_end_seconds"] = (
                attempts[-1]["end_wall"] - exit_wall
            )
    return result


def git_output(*args: str) -> str:
    """Run bounded metadata queries outside the measured production query path.

    Args:
        *args: Fixed Git metadata arguments.

    Returns:
        Captured UTF-8 stdout.

    Raises:
        OSError: Git cannot start.
        subprocess.SubprocessError: Metadata collection fails or exceeds ten seconds.
    """
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    ).stdout


def checkout_identity() -> dict[str, Any]:
    """Snapshot exact HEAD, working-tree status and every tracked file's bytes.

    Returns:
        Candidate identity and tracked byte hashes for before/after comparison.

    Raises:
        OSError: A tracked file cannot be read.
        subprocess.SubprocessError: Git metadata cannot be queried.
    """
    # Hash tracked inputs rather than treating an unchanged HEAD as proof of unchanged bytes.
    files = git_output("ls-files", "-z").split("\0")
    hashes = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in files if name
    }
    return {
        "head": git_output("rev-parse", "HEAD").strip(),
        "status": git_output("status", "--porcelain=v1", "--untracked-files=all"),
        "tracked_sha256": hashes,
    }


def retain_artifacts(output: Path, records: list[dict[str, Any]], manifest: dict[str, Any]) -> None:
    """Bound raw artifacts, attach Trace2 attribution and write a hashed final manifest.

    Args:
        output: Dedicated diagnostic artifact directory.
        records: Parent-side Git timing records.
        manifest: Diagnostic identity, outcomes and collection errors.

    Raises:
        OSError: Artifacts cannot be retained.
    """
    # Preserve bounded raw Trace2 and pytest text, explicitly recording truncation.
    truncated = []
    for path in sorted(output.iterdir()):
        limit = MAX_TRACE_BYTES if path.name.endswith(".trace2.jsonl") else MAX_OUTPUT_BYTES
        bounded_text = path.name.endswith(".trace2.jsonl") or path.name == "pytest-output.txt"
        if bounded_text and path.is_file() and path.stat().st_size > limit:
            with path.open("rb") as stream:
                content = stream.read(limit)
            path.write_bytes(content)
            truncated.append(path.name)
    # Retain Git start/exit/atexit facts without inventing absent timing observations.
    for record in records:
        trace = output / (record.get("trace") or "uncollected.trace2.jsonl")
        events = []
        if trace.exists():
            for line in trace.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("event") in {"start", "exit", "atexit"}:
                    events.append(
                        {
                            key: event[key]
                            for key in ("event", "time", "t_abs", "code")
                            if key in event
                        }
                    )
        record["git_events"] = events
        record["trace_status"] = (
            "disabled"
            if record.get("trace_group") in {"off", "other"}
            else "retained"
            if trace.exists()
            else "not-created"
            if record.get("creation_error")
            else "omitted"
            if not record.get("trace")
            else "missing"
        )
        record["attribution"] = timing_attribution(record, events)
    (output / "processes.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    # Hash every retained artifact independently from the manifest that describes it.
    manifest["truncated"] = truncated
    manifest["artifacts"] = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(output.iterdir())
        if path.is_file()
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def run(output: Path, expected_head: str) -> int:
    """Collect one bounded diagnostic experiment and return failure without retrying it.

    Args:
        output: New artifact directory outside the checkout.
        expected_head: Exact pull-request head SHA selected by the workflow event.

    Returns:
        Zero only when the integration suite, every observed Git call and checkout checks succeed.

    Raises:
        OSError: The artifact directory cannot be created or written.
        ValueError: The artifact destination is inside the checkout.
    """
    # Keep evidence outside the checkout and refuse to overwrite prior diagnostic runs.
    output = output.resolve()
    if output.is_relative_to(ROOT):
        raise ValueError("Diagnostic output must be outside the checkout")
    output.mkdir(parents=True, exist_ok=False)
    observer: GitObserver | None = None
    manifest: dict[str, Any] = {
        "expected_head": expected_head,
        "diagnostic_only": True,
        "python": sys.version,
        "platform": platform.platform(),
        "target": INTEGRATION_TARGET,
        "observed_processes": "parent Git calls only; child-process Git is not instrumented",
        "exit_semantics": (
            "Git nonzero statuses include intentional probes; diagnostic exit is not "
            "acceptance or timeout reproduction"
        ),
        "instrumentation_caveat": (
            "Trace2 and checkpoint work can alter suite scheduling. Constructor and communicate "
            "segments exclude observer checkpoint I/O; their sum is not total parent elapsed."
        ),
        "retention_limits": RETENTION_LIMITS,
        "trace_assignment": {
            "seed": TRACE_SEED,
            "method": "independent shuffled on/off pairs per query",
            "queries": {name: list(args) for args, name in QUERY_TYPES.items()},
            "other": "all Trace2 targets disabled",
        },
        "errors": [],
    }
    collector = coverage.Coverage(
        config_file=str(ROOT / "pyproject.toml"), data_file=str(output / ".coverage")
    )
    status = 1
    try:
        before = checkout_identity()
        manifest["before"] = before
        if before["head"] != expected_head or before["status"]:
            raise ValueError("Expected exact clean PR head before diagnostic execution")
        manifest["git"] = git_output("--version").strip()
        manifest["poetry"] = subprocess.run(
            ["poetry", "--version"], check=True, capture_output=True, text=True, timeout=10
        ).stdout.strip()
        manifest["python_executable"] = sys.executable
        manifest["package_source"] = str(Path(workspace.__file__).resolve())
        if not Path(workspace.__file__).resolve().is_relative_to(ROOT):
            raise ValueError("Imported package does not belong to this candidate checkout")
        manifest["tools"] = {
            name: importlib.metadata.version(name) for name in ("coverage", "pytest", "pytest-cov")
        }
        # Use the repository's real ctrace/subprocess coverage settings for the integration suite.
        collector.start()
        manifest["coverage_core"] = collector.get_option("run:core")
        manifest["active_tracer"] = type(sys.gettrace()).__name__
        with observe_git(output) as observer:
            observer.set_context("", "collection")
            with (output / "pytest-output.txt").open("w", encoding="utf-8") as stream:
                with redirect_stdout(stream), redirect_stderr(stream):
                    manifest["pytest_exit"] = int(
                        pytest.main(
                            [INTEGRATION_TARGET, "--no-cov", "-q"], plugins=[PhasePlugin(observer)]
                        )
                    )
            observer.set_context("", "session-finished")
        collector.stop()
        collector.save()
        # Verify candidate bytes after measurement; diagnostics are never acceptance receipts.
        after = checkout_identity()
        manifest["after"] = after
        manifest["checkout_unchanged"] = before == after
        # Ring eviction and expected-failure assertions never clear a recorded Git fault.
        status = int(
            not (
                before == after
                and manifest["pytest_exit"] == 0
                and not observer.error_latch
                and not manifest["errors"]
            )
        )
    except Exception as error:
        manifest["errors"].append({"phase": "diagnostic-run", **failure(error)})
    # Always restore coverage and retain whatever evidence exists after an experimental failure.
    finally:
        collector.stop()
        collector.save()
        if observer is not None:
            observer.checkpoint(force=True)
            status = int(status != 0 or observer.error_latch)
            manifest["counts"] = observer.counts
            manifest["timings"] = observer.timings
            manifest["groups"] = observer.groups
            manifest["error_latch"] = observer.error_latch
            manifest["telemetry_errors"] = observer.telemetry_errors
        manifest["exit_code"] = status
        retain_artifacts(output, observer.retained() if observer is not None else [], manifest)
    return status


def capability_command(command: list[str], timeout: int) -> dict[str, Any]:
    """Run one bounded capability command without retaining unbounded output in memory.

    Args:
        command: Explicit native executable and arguments; never a shell expression.
        timeout: Maximum communication duration in seconds, without retries.

    Returns:
        Exit/error metadata and at most 16 KiB from each command stream.

    Raises:
        OSError: Temporary stream creation, reading or cleanup fails.
    """
    # Keep raw streams outside the artifact directory and delete them on every path.
    result: dict[str, Any] = {"command": command, "timeout": timeout}
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        started = time.perf_counter()
        try:
            completed = subprocess.run(command, stdout=stdout, stderr=stderr, timeout=timeout)
            result["returncode"] = completed.returncode
        except (OSError, subprocess.SubprocessError) as error:
            result["error"] = failure(error)
        result["seconds"] = time.perf_counter() - started
        # Retain prefixes and original lengths, never raw recorder output files.
        for name, stream in (("stdout", stdout), ("stderr", stderr)):
            result[f"{name}_bytes"] = stream.tell()
            stream.seek(0)
            result[name] = stream.read(16384).decode("utf-8", errors="replace")
    return result


# This exact profile excludes stacks, other providers, inheritance and file mode.
SCHEDULING_PROFILE = """<?xml version="1.0" encoding="utf-8"?>
<WindowsPerformanceRecorder Version="1.0" Author="Nplet">
<Profiles>
<SystemCollector Id="SchedulingCollector" Name="Nplet scheduling">
<BufferSize Value="1024"/><Buffers Value="64"/></SystemCollector>
<SystemProvider Id="SchedulingProvider"><Keywords>
<Keyword Value="ProcessThread"/><Keyword Value="CSwitch"/><Keyword Value="ReadyThread"/>
</Keywords></SystemProvider>
<Profile Id="Scheduling.Verbose.Memory" Name="Scheduling" Description="Bounded schema probe"
LoggingMode="Memory" DetailLevel="Verbose"><Collectors>
<SystemCollectorId Value="SchedulingCollector"><SystemProviderId Value="SchedulingProvider"/>
</SystemCollectorId></Collectors></Profile>
</Profiles></WindowsPerformanceRecorder>"""
RAW_LIMIT_BYTES = 256 * 1024 * 1024
XML_LIMIT_BYTES = 128 * 1024 * 1024
EVENT_LIMIT = 1000000
SANITIZED_LIMIT = 4096
PROCESS_GUID = "3d6fa8d0-fe05-11d0-9dda-00c04fd7ba7c"
THREAD_GUID = "3d6fa8d1-fe05-11d0-9dda-00c04fd7ba7c"


def scheduling_query(repository: Path) -> dict[str, Any]:
    """Observe native identities around one original two-second Git communication.

    Args:
        repository: Disposable initialized repository, with setup outside recording.

    Returns:
        Numeric process/thread identities, original outcome and wall/performance endpoints.
    """
    # Disable every Trace2 target and capture the calling thread before process construction.
    result: dict[str, Any] = {"python_pid": os.getpid(), "calling_tid": threading.get_native_id()}
    environment = {**os.environ, **dict.fromkeys(TRACE_TARGETS, "0")}
    command = ["git", "-C", str(repository), "worktree", "list", "--porcelain", "-z"]
    result.update(start_wall_ns=time.time_ns(), start_perf_ns=time.perf_counter_ns())
    try:
        with subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment
        ) as process:
            result["git_pid"] = process.pid
            try:
                stdout, stderr = process.communicate(timeout=2)
                ended = (time.time_ns(), time.perf_counter_ns())
                result.update(
                    returncode=process.returncode,
                    stdout_bytes=len(stdout),
                    stderr_bytes=len(stderr),
                )
            except subprocess.TimeoutExpired:
                # Observe the original endpoint before diagnostic work; preserve kill and reap.
                ended = (time.time_ns(), time.perf_counter_ns())
                result["timeout"] = True
                process.kill()
                process.communicate()
            except (OSError, subprocess.SubprocessError):
                ended = (time.time_ns(), time.perf_counter_ns())
                raise
            finally:
                # Windows Popen owns these reader threads; never substitute unrelated identities.
                result["reader_tids"] = [
                    thread.native_id
                    for name in ("stdout_thread", "stderr_thread")
                    if (thread := getattr(process, name, None)) is not None
                    and thread.native_id is not None
                ]
                result.update(end_wall_ns=ended[0], end_perf_ns=ended[1])
    except (OSError, subprocess.SubprocessError) as error:
        result["error"] = failure(error)
    return result


def bounded_export(command: list[str], scratch: Path) -> dict[str, Any]:
    """Export privately with a ninety-second deadline and aggregate raw-disk bound.

    Args:
        command: Explicit tracerpt XML export invocation.
        scratch: Owned raw directory, never an upload source.

    Returns:
        Numeric exit/timing/size metadata and fixed failure categories only.
    """
    # Export command output stays private; no arbitrary ETL-derived text is published.
    result: dict[str, Any] = {}
    started = time.monotonic()
    try:
        with (
            tempfile.TemporaryFile() as stream,
            subprocess.Popen(command, cwd=scratch, stdout=stream, stderr=stream) as process,
        ):
            try:
                # Poll file sizes, not process IDs or output contents; abort without retry.
                while True:
                    size = sum(path.stat().st_size for path in scratch.rglob("*") if path.is_file())
                    result["raw_bytes"] = size
                    if size > RAW_LIMIT_BYTES:
                        result["error"] = "raw-disk-limit"
                        break
                    if time.monotonic() - started >= 90:
                        result["error"] = "export-deadline"
                        break
                    try:
                        result["returncode"] = process.wait(timeout=0.05)
                        break
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                # Only terminate the export process owned by this helper, then reap it.
                if "returncode" not in result:
                    process.kill()
                    process.wait()
            result["raw_bytes"] = sum(
                path.stat().st_size for path in scratch.rglob("*") if path.is_file()
            )
            if result["raw_bytes"] > RAW_LIMIT_BYTES:
                result["error"] = "raw-disk-limit"
    except (OSError, subprocess.SubprocessError) as error:
        result["error"] = type(error).__name__
    result["seconds"] = time.monotonic() - started
    return result


def scheduling_events(path: Path) -> Iterator[dict[str, Any]]:
    """Stream private XML into strictly typed known schema fields or structural metadata.

    Args:
        path: Private tracerpt XML file with bounded size.

    Yields:
        Known numeric fields, structural schemas or fixed decode failure codes/field names.

    Raises:
        ValueError: XML size or event/field bounds are invalid.
        OSError: Private export cannot be read.
    """
    # Reject oversized exports and entity declarations before parsing any event payload.
    if path.stat().st_size > XML_LIMIT_BYTES:
        raise ValueError("xml-size-limit")
    with path.open("rb") as stream:
        carry = b""
        while chunk := stream.read(65536):
            scan = (carry + chunk).replace(b"\0", b"")
            if b"<!DOCTYPE" in scan.upper() or b"<!ENTITY" in scan.upper():
                raise ValueError("xml-declaration-rejected")
            carry = scan[-16:]
    # Clear completed events from their root to keep memory independent of export length.
    try:
        context = ET.iterparse(path, events=("start", "end"))
        _, root = next(context)
        count = 0
        for action, element in context:
            if action != "end" or element.tag.rsplit("}", 1)[-1] != "Event":
                continue
            count += 1
            if count > EVENT_LIMIT:
                raise ValueError("event-count-limit")
            children = {child.tag.rsplit("}", 1)[-1]: child for child in element}
            system = children.get("System")
            data = children.get("EventData")
            # UserData is a different export layout; inventory names but never infer its values.
            fields = [] if data is None else list(data)
            if len(fields) > 64:
                raise ValueError("event-field-limit")
            names = [field.get("Name", "") for field in fields]
            structural: dict[str, Any] = {
                "fields": sorted(
                    {name for name in names if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name)}
                )[:32]
            }
            header = (
                {} if system is None else {child.tag.rsplit("}", 1)[-1]: child for child in system}
            )
            provider = header.get("Provider")
            guid = "" if provider is None else provider.get("Guid", "").strip("{}").lower()
            structural["provider"] = guid if re.fullmatch(r"[0-9a-f-]{36}", guid) else "unknown"
            version = header.get("Version")
            opcode = header.get("Opcode")
            ver = (
                int(version.text)
                if version is not None and re.fullmatch(r"\d{1,3}", version.text or "")
                else -1
            )
            op = (
                int(opcode.text)
                if opcode is not None and re.fullmatch(r"\d{1,3}", opcode.text or "")
                else -1
            )
            structural.update(version=ver, opcode=op)
            allowed: tuple[str, ...] = ()
            if ver in (2, 3, 4) and guid == PROCESS_GUID and op in (1, 2, 3, 4):
                allowed = ("ProcessId", "ParentId", "ExitStatus")
            elif guid == THREAD_GUID:
                if ver in (2, 3) and op in (1, 2, 3, 4):
                    allowed = ("ProcessId", "TThreadId")
                elif ver in (2, 3, 4) and op == 36:
                    allowed = (
                        "NewThreadId",
                        "OldThreadId",
                        "OldThreadState",
                        "OldThreadWaitReason",
                        "OldThreadWaitMode",
                    )
                elif ver == 2 and op == 50:
                    allowed = ("TThreadId", "AdjustReason", "AdjustIncrement", "Flag")
            values = {field.get("Name"): field.text or "" for field in fields}
            # A schema is supported only with every documented field and a unique payload mapping.
            known = (
                bool(allowed)
                and all(name in values for name in allowed)
                and len(names) == len(set(names))
            )
            record: dict[str, Any] = {"schema": structural, "known": known}
            if known:
                try:
                    timestamp = header.get("TimeCreated")
                    stamp = "" if timestamp is None else timestamp.get("SystemTime", "")
                    match = re.fullmatch(
                        r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?Z", stamp
                    )
                    error_code, error_field = "timestamp-format", "SystemTime"
                    if match is None:
                        raise ValueError("unsupported-event-timestamp")
                    error_code = "timestamp-value"
                    seconds = int(
                        datetime.fromisoformat(match[1]).replace(tzinfo=timezone.utc).timestamp()
                    )
                    record["time_ns"] = seconds * 1000000000 + int((match[2] or "").ljust(9, "0"))
                    numeric = {}
                    for name in allowed:
                        error_code, error_field = "numeric-format", name
                        value = values[name]
                        if not re.fullmatch(r"(?:-?\d{1,10}|0[xX][0-9a-fA-F]{1,8})", value):
                            raise ValueError("invalid-numeric-payload")
                        number = int(value, 16 if value.lower().startswith("0x") else 10)
                        minimum = (
                            -(2**31)
                            if name == "ExitStatus"
                            else -128
                            if name == "AdjustIncrement"
                            else 0
                        )
                        maximum = 2**32 if name.endswith("Id") or name == "ExitStatus" else 256
                        error_code = "numeric-range"
                        if not minimum <= number < maximum:
                            raise ValueError("numeric-payload-range")
                        numeric[name] = number
                    record["values"] = numeric
                    processor = header.get("Execution")
                    cpu = "" if processor is None else processor.get("ProcessorID", "")
                    if cpu.isdecimal() and len(cpu) <= 5:
                        record["cpu"] = int(cpu)
                except ValueError:
                    # Preserve only schema and an allowlisted failure location.
                    record = {
                        "schema": structural,
                        "known": False,
                        "decode_error": {"code": error_code, "field": error_field},
                    }
            yield record
            root.clear()
    except ET.ParseError:
        # A later malformed element cannot erase schemas already yielded by this stream.
        yield {
            "schema": {"provider": "unknown", "version": -1, "opcode": -1, "fields": []},
            "known": False,
            "decode_error": {"code": "xml-parse", "field": "XML"},
        }


def decode_scheduling(path: Path, query: dict[str, Any]) -> dict[str, Any]:
    """Establish target lifetimes, then retain only target numeric scheduling evidence.

    Args:
        path: Private bounded XML export, read twice without retaining unrelated events.
        query: Actual Python/Git/native thread identities and query wall-clock window.

    Returns:
        Bounded sanitized records, structural schemas and explicit capability gaps.

    Raises:
        ValueError: Ambiguous/reused target identities or export bounds.
        OSError: Export reading fails.
    """
    # First pass inventories schemas and discovers target process/thread lifetimes by payload IDs.
    parent, git = query["python_pid"], query["git_pid"]
    tids = {query["calling_tid"], *query["reader_tids"]}
    process_events: dict[int, list[tuple[int, int]]] = {parent: [], git: []}
    thread_events: dict[int, list[tuple[int, int, int]]] = {}
    schemas: list[dict[str, Any]] = []
    unknown = 0
    decode_errors: list[dict[str, Any]] = []
    error_count = 0
    for record in scheduling_events(path):
        schema = record["schema"]
        if schema not in schemas and len(schemas) < 16:
            schemas.append(schema)
        if "decode_error" in record:
            error_count += 1
            if len(decode_errors) < 32:
                decode_errors.append({"schema": schema, **record["decode_error"]})
            continue
        if not record["known"]:
            unknown += 1
            continue
        values, op, stamp = record["values"], schema["opcode"], record["time_ns"]
        if schema["provider"] == PROCESS_GUID and values["ProcessId"] in process_events:
            process_events[values["ProcessId"]].append((op, stamp))
            if len(process_events[values["ProcessId"]]) > 8:
                raise ValueError("target-process-lifetime-limit")
        if schema["provider"] == THREAD_GUID and op in (1, 2, 3, 4):
            tid, pid = values["TThreadId"], values["ProcessId"]
            if tid in tids or pid == git:
                thread_events.setdefault(tid, []).append((op, stamp, pid))
                if len(thread_events[tid]) > 8:
                    raise ValueError("target-thread-lifetime-limit")
                if len(thread_events) > 128:
                    raise ValueError("target-thread-limit")
    # Multiple births for a target PID/TID make reuse attribution ambiguous: fail closed.
    for events in process_events.values():
        if sum(op in (1, 3) for op, _ in events) > 1:
            raise ValueError("ambiguous-process-lifetime")
    for thread_life in thread_events.values():
        if (
            len({pid for _, _, pid in thread_life}) > 1
            or sum(op in (1, 3) for op, _, _ in thread_life) > 1
        ):
            raise ValueError("ambiguous-thread-lifetime")
    start, end = query["start_wall_ns"], query["end_wall_ns"]
    git_life = process_events[git]
    births = [stamp for op, stamp in git_life if op == 1]
    deaths = [stamp for op, stamp in git_life if op == 2]
    valid_git = len(births) == len(deaths) == 1 and start <= births[0] <= deaths[0] <= end
    thread_windows = {}
    for tid, life in thread_events.items():
        born = [stamp for op, stamp, _ in life if op in (1, 3)]
        dead = [stamp for op, stamp, _ in life if op in (2, 4)]
        owner = life[0][2]
        if len(dead) > 1:
            raise ValueError("ambiguous-thread-lifetime")
        if len(born) == len(dead) == 1 and born[0] <= dead[0] and owner in (parent, git):
            thread_windows[tid] = (born[0], dead[0], owner)
    mapping = all(tid in thread_windows and thread_windows[tid][2] == parent for tid in tids)
    mapping = mapping and any(owner == git for _, _, owner in thread_windows.values())
    # Second pass filters the time window and redacts unrelated CSwitch counterparty IDs.
    retained: list[dict[str, Any]] = []
    counts = {"process": 0, "thread": 0, "cswitch": 0, "ready": 0}
    overflow = 0
    cpu_missing = False
    for record in scheduling_events(path):
        if not record["known"] or not start <= record["time_ns"] <= end:
            continue
        schema, values = record["schema"], record["values"]
        op, provider = schema["opcode"], schema["provider"]
        target_tids = {
            tid
            for tid, (born, dead, _) in thread_windows.items()
            if born <= record["time_ns"] <= dead
        }
        selected = {}
        kind = ""
        if provider == PROCESS_GUID and values["ProcessId"] in (parent, git):
            selected = {
                name: value
                for name, value in values.items()
                if name != "ParentId" or value in (parent, git)
            }
            kind = "process"
        elif provider == THREAD_GUID and op in (1, 2, 3, 4):
            if values["TThreadId"] in target_tids and values["ProcessId"] in (parent, git):
                selected, kind = values, "thread"
        elif provider == THREAD_GUID and op == 36:
            if values["NewThreadId"] in target_tids or values["OldThreadId"] in target_tids:
                selected = {
                    name: value
                    for name, value in values.items()
                    if name not in ("NewThreadId", "OldThreadId") or value in target_tids
                }
                kind = "cswitch"
        elif provider == THREAD_GUID and op == 50 and values["TThreadId"] in target_tids:
            selected, kind = values, "ready"
        if kind:
            if kind in ("cswitch", "ready") and "cpu" not in record:
                cpu_missing = True
            counts[kind] += 1
            if len(retained) < SANITIZED_LIMIT:
                retained.append(
                    {
                        "kind": kind,
                        "opcode": op,
                        "time_ns": record["time_ns"],
                        "values": selected,
                        **({"cpu": record["cpu"]} if "cpu" in record else {}),
                    }
                )
            else:
                overflow += 1
    gaps = []
    if error_count:
        gaps.append("event-decode")
    if not valid_git:
        gaps.append("git-start-end-window")
    if not mapping or len(query["reader_tids"]) != 2:
        gaps.append("python-thread-mapping")
    if not counts["cswitch"] or not counts["ready"]:
        gaps.append("scheduling-events")
    if cpu_missing:
        gaps.append("scheduler-cpu")
    if overflow:
        gaps.append("sanitized-record-limit")
    return {
        "schemas": schemas,
        "decode_errors": decode_errors,
        "decode_error_count": error_count,
        "unknown_events": unknown,
        "counts": counts,
        "events": retained,
        "omitted": overflow,
        "gaps": gaps,
    }


def wpr_capability(output: Path, expected_head: str, scheduling: bool = False) -> int:
    """Probe native recording capability once, without running integration tests.

    Args:
        output: New external directory receiving metadata only.
        expected_head: Exact clean checkout required by the triggering PR event.
        scheduling: Use the narrow custom profile and private sanitized schema roundtrip.

    Returns:
        Zero only when tool discovery, the owned recording and the query succeed.

    Raises:
        ValueError: The destination is inside the candidate checkout.
        OSError: The external artifact directory cannot be created or written.
    """
    # Validate the exact candidate before creating disposable system-wide trace evidence.
    output = output.resolve()
    if output.is_relative_to(ROOT):
        raise ValueError("Diagnostic output must be outside the candidate checkout")
    output.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, Any] = {
        "mode": "wpr-scheduling" if scheduling else "wpr-capability",
        "expected_head": expected_head,
        "purpose": "Capability only; not root-cause reproduction or acceptance",
        "commands": {},
        "errors": [],
        "recording_started": False,
        "cleanup_uncertain": False,
        "raw_trace_uploaded": False,
        "stream_limit_bytes": 16384,
    }
    commands = manifest["commands"]
    owned = False
    status = 1
    instance = f"nplet-capability-{uuid.uuid4()}"
    manifest["instance"] = instance
    # The temporary tree owns ETL and sidecars, even when stop or hashing fails.
    scratch: Path | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="nplet-wpr-capability-") as directory:
            scratch = Path(directory)
            wpr = shutil.which("wpr.exe")
            tracerpt = shutil.which("tracerpt.exe")
            powershell = shutil.which("powershell.exe")
            manifest["tools"] = {"wpr": wpr, "tracerpt": tracerpt, "powershell": powershell}
            try:
                before = checkout_identity()
                manifest["before"] = before
                if before["head"] != expected_head or before["status"]:
                    raise ValueError("Candidate is not the exact clean event head")
                if (
                    platform.system() != "Windows"
                    or wpr is None
                    or tracerpt is None
                    or powershell is None
                ):
                    raise ValueError("Native Windows WPR/tracerpt/version tools unavailable")
                # Query only executable versions and token elevation.
                quoted_wpr = wpr.replace("'", "''")
                quoted_tracerpt = tracerpt.replace("'", "''")
                version_script = (
                    "[pscustomobject]@{admin=([Security.Principal.WindowsPrincipal]"
                    "[Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole("
                    "[Security.Principal.WindowsBuiltInRole]::Administrator);"
                    f"wpr=(Get-Item -LiteralPath '{quoted_wpr}').VersionInfo.FileVersion;"
                    f"tracerpt=(Get-Item -LiteralPath '{quoted_tracerpt}').VersionInfo.FileVersion"
                    "}|ConvertTo-Json -Compress"
                )
                commands["versions_admin"] = capability_command(
                    [powershell, "-NoProfile", "-NonInteractive", "-Command", version_script], 15
                )
                profile = "CPU"
                if scheduling:
                    # Generate only the reviewed collector configuration in owned scratch.
                    profile_path = scratch / "scheduling.wprp"
                    profile_path.write_text(SCHEDULING_PROFILE, encoding="utf-8")
                    profile = f"{profile_path}!Scheduling"
                    manifest["profile_sha256"] = hashlib.sha256(
                        SCHEDULING_PROFILE.encode()
                    ).hexdigest()
                commands["profiles"] = capability_command([wpr, "-profiles"], 15)
                commands["cpu_profile"] = capability_command([wpr, "-profiledetails", profile], 15)
                commands["global_status"] = capability_command([wpr, "-status"], 15)
                # Ambiguous/localized/preexisting state blocks mutation conservatively.
                global_status = commands["global_status"]
                idle = (
                    "wpr is not recording"
                    in (global_status["stdout"] + global_status["stderr"]).lower()
                )
                manifest["global_idle_confirmed"] = idle
                if "error" in global_status or not idle:
                    raise ValueError("Existing or ambiguous WPR recording state; recording skipped")
                versions = json.loads(commands["versions_admin"]["stdout"])
                manifest["versions_admin"] = versions
                if not versions["admin"]:
                    raise ValueError("Administrative recording capability unavailable")
                if any(
                    result.get("returncode") != 0
                    for name, result in commands.items()
                    if name != "global_status"
                ):
                    raise ValueError("Native recorder discovery command failed")
                # Initialize before recording, then execute exactly one unchanged two-second query.
                repository = scratch / "repository"
                commands["git_init"] = capability_command(
                    ["git", "init", "--quiet", str(repository)], 2
                )
                if commands["git_init"].get("returncode") != 0:
                    raise ValueError("Disposable repository initialization failed")
                commands["start"] = capability_command(
                    [wpr, "-start", profile, "-instancename", instance], 30
                )
                if commands["start"].get("returncode") != 0:
                    manifest["cleanup_uncertain"] = "error" in commands["start"]
                    raise ValueError("Recording start did not establish ownership")
                owned = True
                manifest["recording_started"] = True
                commands["query"] = (
                    scheduling_query(repository)
                    if scheduling
                    else capability_command(
                        ["git", "-C", str(repository), "worktree", "list", "--porcelain", "-z"], 2
                    )
                )
                commands["collectors"] = capability_command(
                    [wpr, "-status", "collectors", "-details", "-instancename", instance], 15
                )
                etl = scratch / "capability.etl"
                commands["stop"] = capability_command(
                    [wpr, "-stop", str(etl), "-skipPdbGen", "-compress", "-instancename", instance],
                    90,
                )
                if commands["stop"].get("returncode") != 0:
                    raise ValueError("Owned recording did not stop successfully")
                owned = False
                # Bound completed recorder output before hashing or starting an export.
                if (
                    scheduling
                    and sum(path.stat().st_size for path in scratch.rglob("*") if path.is_file())
                    > RAW_LIMIT_BYTES
                ):
                    raise ValueError("raw-disk-limit")
                # Stream large ETL hashes; the raw trace never enters the upload allowlist.
                with etl.open("rb") as stream:
                    manifest["etl"] = {
                        "bytes": etl.stat().st_size,
                        "sha256": hashlib.file_digest(stream, "sha256").hexdigest(),
                    }
                if scheduling:
                    # Export privately; raw text never enters command metadata.
                    xml = scratch / "events.xml"
                    manifest["export"] = bounded_export(
                        [tracerpt, str(etl), "-o", str(xml), "-of", "XML", "-y"], scratch
                    )
                    report: dict[str, Any] = {"gaps": []}
                    manifest["scheduling"] = report
                    try:
                        if (
                            manifest["export"].get("returncode") != 0
                            or "error" in manifest["export"]
                        ):
                            raise ValueError("private-export-failed")
                        with xml.open("rb") as stream:
                            manifest["xml"] = {
                                "bytes": xml.stat().st_size,
                                "sha256": hashlib.file_digest(stream, "sha256").hexdigest(),
                            }
                        report.update(decode_scheduling(xml, commands["query"]))
                    except (OSError, ValueError, KeyError, ET.ParseError) as error:
                        # Export payload values and parser exception text are never published.
                        report["decoder_error"] = type(error).__name__
                        report["gaps"].append("private-xml-decode")
                    # Confirm actual collector settings and both recorder loss counters.
                    collector = commands["collectors"].get("stdout", "")
                    profile_text = commands["cpu_profile"].get("stdout", "")
                    configuration = all(
                        re.search(pattern, collector)
                        for pattern in (
                            r"Logging mode\s*:\s*Memory",
                            r"Buffer Size \(KB\)\s*:\s*1024\b",
                            r"Number of Buffers\s*:\s*64\b",
                        )
                    )
                    loss = all(
                        re.search(pattern, collector)
                        for pattern in (r"Events Lost\s*:\s*0\b", r"Dropped event\s*:\s*0\b")
                    )
                    keyword_section = re.search(
                        r"System Keywords\r?\n((?:[ \t]+[^\r\n]*\r?\n)+)", collector
                    )
                    keywords = (
                        set()
                        if keyword_section is None
                        else {
                            line.strip() for line in keyword_section[1].splitlines() if line.strip()
                        }
                    )
                    configuration = configuration and (
                        collector.count("Collector Name") == 1
                        and "System Stacks" not in collector
                        and "CaptureState Providers" not in collector
                        and "Event Collector" not in collector
                        and "Scheduling.Verbose.Memory" in profile_text
                    )
                    report.update(
                        configuration_verified=bool(configuration),
                        loss_verified=bool(loss),
                        keywords_verified=keywords == {"ProcessThread", "CSwitch", "ReadyThread"},
                    )
                    if (
                        not configuration
                        or not loss
                        or keywords != {"ProcessThread", "CSwitch", "ReadyThread"}
                    ):
                        report["gaps"].append("collector-configuration-or-loss")
                after = checkout_identity()
                manifest["checkout_unchanged"] = before == after
                status = int(
                    before != after
                    or bool(manifest.get("scheduling", {}).get("gaps"))
                    or any(
                        result.get("returncode") != 0
                        for name, result in commands.items()
                        if name != "global_status"
                    )
                )
            except Exception as error:
                manifest["errors"].append(failure(error))
            finally:
                # Cancel only the successfully started, uniquely named instance.
                if owned:
                    commands["cancel"] = capability_command(
                        [str(wpr), "-cancel", "-instancename", instance], 30
                    )
                    manifest["cleanup_uncertain"] = commands["cancel"].get("returncode") != 0
    except OSError as error:
        # A locked raw sidecar must not erase already collected recorder/cleanup evidence.
        manifest["errors"].append({"phase": "raw-scratch-cleanup", **failure(error)})
        manifest["cleanup_uncertain"] = True
        status = 1
    # Publish metadata only and report whether raw evidence cleanup completed.
    manifest["raw_scratch_removed"] = scratch is None or not scratch.exists()
    manifest["exit_code"] = status
    payload = json.dumps(manifest, indent=2).encode()
    (output / "capability.json").write_bytes(payload)
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "mode": manifest["mode"],
                "exit_code": status,
                "artifacts": {"capability.json": hashlib.sha256(payload).hexdigest()},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return status


def main() -> int:
    """Parse the explicit candidate and artifact destination for this temporary experiment.

    Returns:
        The diagnostic run's original nonzero status on failure.
    """
    # Parse the explicit event candidate and external artifact destination.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument(
        "--mode", choices=("integration", "wpr-capability", "wpr-scheduling"), default="integration"
    )
    args = parser.parse_args()
    # Preserve the experiment's nonzero status rather than treating retained artifacts as success.
    if args.mode == "wpr-scheduling":
        return wpr_capability(args.output, args.expected_head, scheduling=True)
    if args.mode == "wpr-capability":
        return wpr_capability(args.output, args.expected_head)
    return run(args.output, args.expected_head)


if __name__ == "__main__":
    raise SystemExit(main())
