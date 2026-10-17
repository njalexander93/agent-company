"""Temporarily measure Windows Git delays; remove this harness after diagnosis.

These diagnostic artifacts never replace native acceptance or change production timeouts.
"""

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import time
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
ROLLOVER = (
    "tests/integration/lifecycle/test_independent_rollover.py::IndependentRolloverTests::"
    "test_crash_retry_all_publication_boundaries_and_global_chain"
)
MAX_RECORDS = 2048
SAMPLE_COUNT = 400
MAX_TRACE_BYTES = 65536
MAX_OUTPUT_BYTES = 1048576


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


class TimedProcess:
    """Delegate a real Git process while separating creation and communication timing."""

    def __init__(
        self,
        constructor: Callable[..., Any],
        record: dict[str, Any],
        args: Any,
        *positional: Any,
        **kwargs: Any,
    ) -> None:
        """Construct the original process without changing arguments or exception behavior.

        Args:
            constructor: Original subprocess.Popen implementation.
            record: Bounded telemetry record owned by the observer.
            args: Original command passed to Popen.
            *positional: Remaining original process arguments.
            **kwargs: Original process options plus this process's Trace2 destination.

        Raises:
            Exception: The original process construction fails.
        """
        # Time process construction separately from any later communicate timeout.
        self.record = record
        record["popen_start_wall"] = time.time()
        started = time.perf_counter()
        try:
            self.process = constructor(args, *positional, **kwargs)
        except Exception as error:
            record["creation_error"] = failure(error)
            raise
        finally:
            record["popen_seconds"] = time.perf_counter() - started

    def communicate(self, *args: Any, **kwargs: Any) -> Any:
        """Measure each original communicate attempt, including timeout cleanup calls.

        Args:
            *args: Original communicate positional arguments.
            **kwargs: Original options, including the unchanged timeout.

        Returns:
            The original stdout/stderr pair, never copied into telemetry.

        Raises:
            Exception: The original communicate call fails or times out.
        """
        # Preserve each attempt and rethrow the original error so subprocess.run owns cleanup.
        attempt: dict[str, Any] = {"timeout": kwargs.get("timeout")}
        started = time.perf_counter()
        try:
            result = self.process.communicate(*args, **kwargs)
            attempt["returncode"] = self.process.returncode
            return result
        except Exception as error:
            attempt["error"] = failure(error)
            raise
        finally:
            attempt["seconds"] = time.perf_counter() - started
            attempt["end_wall"] = time.time()
            self.record.setdefault("communicate", []).append(attempt)

    def __enter__(self) -> "TimedProcess":
        """Enter the original process context and retain the timed communicate surface.

        Returns:
            This delegating process wrapper.
        """
        self.process.__enter__()
        return self

    def __exit__(self, *args: Any) -> Any:
        """Delegate process reaping and pipe cleanup to the original context manager.

        Args:
            *args: Original exception context.

        Returns:
            The original context manager's result.
        """
        return self.process.__exit__(*args)

    def __getattr__(self, name: str) -> Any:
        """Expose unchanged process attributes such as kill, poll, args and returncode.

        Args:
            name: Original process attribute requested by subprocess.run.

        Returns:
            The original attribute.
        """
        return getattr(self.process, name)


@contextmanager
def observe_git(output: Path, records: list[dict[str, Any]], phase: list[str]) -> Iterator[None]:
    """Time Git Popen calls only and restore subprocess construction on every exit.

    Args:
        output: Dedicated artifact directory for per-process Trace2 files.
        records: Bounded process records appended by this observer.
        phase: One mutable label identifying the current independent sample or test.

    Yields:
        Control with the delegating Git process observer active.
    """
    original = subprocess.Popen

    def popen(args: Any, *positional: Any, **kwargs: Any) -> Any:
        """Wrap bounded Git calls while leaving all other process launches unchanged.

        Args:
            args: Original command, normally an argument list.
            *positional: Original Popen positional options.
            **kwargs: Original Popen keyword options.

        Returns:
            A timed Git process or the original non-Git process.
        """
        # Bound telemetry and never rewrite non-Git commands or shell strings.
        if (
            not isinstance(args, (list, tuple))
            or not args
            or Path(str(args[0])).name.lower() not in {"git", "git.exe"}
            or len(records) >= MAX_RECORDS
        ):
            return original(args, *positional, **kwargs)
        # Assign a unique trace destination without changing other supplied environment values.
        trace = output / f"git-{len(records):04d}.trace2.jsonl"
        record = {
            "phase": phase[0],
            "command": [str(word)[:512] for word in args],
            "trace": trace.name,
        }
        records.append(record)
        environment = kwargs.get("env")
        kwargs["env"] = {
            **(os.environ if environment is None else environment),
            "GIT_TRACE2_EVENT": str(trace),
        }
        return TimedProcess(original, record, args, *positional, **kwargs)

    # Patch only during this diagnostic run; original process ownership remains authoritative.
    with mock.patch.object(subprocess, "Popen", popen):
        yield


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
        "communicate_seconds": sum(attempt["seconds"] for attempt in attempts),
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


def run_samples(records: list[dict[str, Any]], phase: list[str]) -> None:
    """Run bounded repository discoveries, stopping at the first failure without retrying.

    Args:
        records: Per-sample results, retaining every failure without retries.
        phase: Mutable instrumentation label.
    """
    # Load the checkout's test support when invoked as a script outside pytest's import setup.
    sys.path.insert(0, str(ROOT))
    from tests.support import Fixture

    # Register fixture cleanup even if its initial Git or lifecycle setup fails.
    fixture = Fixture()
    try:
        phase[0] = "sample-fixture-setup"
        fixture.setUp()
        for index in range(SAMPLE_COUNT):
            phase[0] = f"repository-sample-{index:02d}"
            started = time.perf_counter()
            sample: dict[str, Any] = {"index": index}
            try:
                root, common, trees = workspace.repository(fixture.root)
                sample.update(root=str(root), common=str(common), worktrees=len(trees), ok=True)
            except Exception as error:
                sample.update(ok=False, error=failure(error))
            finally:
                sample["seconds"] = time.perf_counter() - started
                records.append(sample)
            # Preserve the failed sample and leave the independent rollover run eligible.
            if not sample["ok"]:
                break
    # Reap and remove only the fixture's own disposable repositories.
    finally:
        fixture.doCleanups()


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
        trace = output / record["trace"]
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
        Zero only when every sample, the single rollover test and checkout verification succeed.

    Raises:
        OSError: The artifact directory cannot be created or written.
        ValueError: The artifact destination is inside the checkout.
    """
    # Keep evidence outside the checkout and refuse to overwrite prior diagnostic runs.
    output = output.resolve()
    if output.is_relative_to(ROOT):
        raise ValueError("Diagnostic output must be outside the checkout")
    output.mkdir(parents=True, exist_ok=False)
    processes: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    phase = ["initialization"]
    manifest: dict[str, Any] = {
        "expected_head": expected_head,
        "diagnostic_only": True,
        "python": sys.version,
        "platform": platform.platform(),
        "sample_count": SAMPLE_COUNT,
        "rollover": ROLLOVER,
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
        # Use the repository's real ctrace/subprocess coverage settings for samples and one test.
        collector.start()
        manifest["coverage_core"] = collector.get_option("run:core")
        manifest["active_tracer"] = type(sys.gettrace()).__name__
        with observe_git(output, processes, phase):
            try:
                run_samples(samples, phase)
            except Exception as error:
                manifest["errors"].append({"phase": phase[0], **failure(error)})
            phase[0] = "rollover-test"
            with (output / "pytest-output.txt").open("w", encoding="utf-8") as stream:
                with redirect_stdout(stream), redirect_stderr(stream):
                    manifest["pytest_exit"] = int(pytest.main([ROLLOVER, "--no-cov", "-q"]))
        collector.stop()
        collector.save()
        # Verify candidate bytes after measurement; diagnostics are never acceptance receipts.
        after = checkout_identity()
        manifest["after"] = after
        manifest["checkout_unchanged"] = before == after
        # An expected-failure assertion can mask a Git fault; retain that fault as run failure.
        manifest["process_failures"] = [
            index
            for index, record in enumerate(processes)
            if record.get("creation_error")
            or any(
                "error" in attempt or attempt.get("returncode", 0) != 0
                for attempt in record.get("communicate", [])
            )
        ]
        status = int(
            not (
                before == after
                and len(samples) == SAMPLE_COUNT
                and all(s["ok"] for s in samples)
                and manifest["pytest_exit"] == 0
                and not manifest["errors"]
                and not manifest["process_failures"]
                and len(processes) < MAX_RECORDS
            )
        )
    except Exception as error:
        manifest["errors"].append({"phase": phase[0], **failure(error)})
    # Always restore coverage and retain whatever evidence exists after an experimental failure.
    finally:
        collector.stop()
        collector.save()
        manifest["exit_code"] = status
        manifest["samples"] = samples
        retain_artifacts(output, processes, manifest)
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
