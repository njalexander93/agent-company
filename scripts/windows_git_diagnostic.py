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
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime
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


def main() -> int:
    """Parse the explicit candidate and artifact destination for this temporary experiment.

    Returns:
        The diagnostic run's original nonzero status on failure.
    """
    # Parse the explicit event candidate and external artifact destination.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    args = parser.parse_args()
    # Preserve the experiment's nonzero status rather than treating retained artifacts as success.
    return run(args.output, args.expected_head)


if __name__ == "__main__":
    raise SystemExit(main())
