"""Verify temporary full-integration Git telemetry, retained failures and bounded evidence."""

import hashlib
import json
import subprocess
from collections import deque
from pathlib import Path
from unittest import mock

import pytest

from scripts import windows_git_diagnostic as diagnostic

pytestmark = pytest.mark.unit
QUERY_COMMAND = ["git", "-C", "fixture", "worktree", "list", "--porcelain", "-z"]


def force_trace_slots(observer: diagnostic.GitObserver, count: int) -> None:
    """Force on assignments only in tests of trace storage pressure.

    Args:
        observer: Collector whose rare trace pressure path is under test.
        count: Number of controlled assignments; no Git calls are added.
    """
    observer.assignment_pairs["worktrees"] = deque([True] * count)


def test_attribution_keeps_clock_boundaries_separate() -> None:
    """Report independent parent/Git durations without attributing pipe delay to Git."""
    record = {
        "popen_seconds": 0.4,
        "popen_start_wall": 100.0,
        "communicate": [{"seconds": 2.0, "end_wall": 104.0}, {"seconds": 0.1, "end_wall": 105.0}],
    }
    events = [
        {"event": "start", "time": "1970-01-01T00:01:41Z"},
        {"event": "exit", "time": "1970-01-01T00:01:43Z", "t_abs": 1.0},
        {"event": "atexit", "time": "1970-01-01T00:01:43.100000Z", "t_abs": 1.1},
    ]
    # Independently chosen clocks expose creation, child execution and parent-tail intervals.
    result = diagnostic.timing_attribution(record, events)
    assert result["popen_seconds"] == 0.4
    assert result["communicate_seconds"] == pytest.approx(2.1)
    assert result["git_start_after_popen_begin_seconds"] == 1.0
    assert result["git_elapsed_seconds"] == 1.1
    assert result["last_git_event_to_communicate_end_seconds"] == pytest.approx(1.9)
    assert diagnostic.timing_attribution(record, []) == {
        "popen_seconds": 0.4,
        "communicate_seconds": 2.1,
    }


def test_artifacts_bound_text_without_corrupting_coverage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Bound text evidence, retain failure metadata and leave binary coverage intact.

    Args:
        monkeypatch: Scoped small evidence limits for a cheap boundary probe.
        tmp_path: Dedicated fake diagnostic artifact directory.
    """
    monkeypatch.setattr(diagnostic, "MAX_TRACE_BYTES", 32)
    monkeypatch.setattr(diagnostic, "MAX_OUTPUT_BYTES", 64)
    (tmp_path / "git-0000.trace2.jsonl").write_text("x" * 100)
    (tmp_path / "pytest-output.txt").write_text("failure output" * 100)
    binary = b"coverage-sentinel" * 100
    (tmp_path / ".coverage").write_bytes(binary)
    manifest = {"exit_code": 1}
    records = [{"trace": "git-0000.trace2.jsonl", "popen_seconds": 2.5}]
    diagnostic.retain_artifacts(tmp_path, records, manifest)
    assert (tmp_path / "git-0000.trace2.jsonl").stat().st_size == 32
    assert (tmp_path / "pytest-output.txt").stat().st_size == 64
    assert (tmp_path / ".coverage").read_bytes() == binary
    retained = json.loads((tmp_path / "manifest.json").read_text())
    assert retained["exit_code"] == 1
    assert set(retained["truncated"]) == {"git-0000.trace2.jsonl", "pytest-output.txt"}
    assert records[0]["git_events"] == []
    assert "git_elapsed_seconds" not in records[0]["attribution"]


def test_metadata_queries_and_tracked_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify bounded Git metadata and detect tracked-byte changes independently of HEAD.

    Args:
        monkeypatch: Scoped subprocess and checkout boundary replacement.
        tmp_path: Disposable tracked input file.
    """
    # Keep the original bounded subprocess contract visible at the metadata boundary.
    process = mock.Mock(return_value=subprocess.CompletedProcess([], 0, stdout="git metadata"))
    monkeypatch.setattr(diagnostic.subprocess, "run", process)
    assert diagnostic.git_output("rev-parse", "HEAD") == "git metadata"
    assert process.call_args.kwargs["timeout"] == 10
    assert process.call_args.kwargs["check"]
    assert process.call_args.args[0][-2:] == ["rev-parse", "HEAD"]
    # Keep HEAD fixed and prove that actual tracked content changes the identity.
    monkeypatch.setattr(diagnostic, "ROOT", tmp_path)
    (tmp_path / "input.py").write_bytes(b"original")
    outputs = {
        ("ls-files", "-z"): "input.py\0",
        ("rev-parse", "HEAD"): "candidate\n",
        ("status", "--porcelain=v1", "--untracked-files=all"): "",
    }
    monkeypatch.setattr(diagnostic, "git_output", lambda *args: outputs[args])
    before = diagnostic.checkout_identity()
    (tmp_path / "input.py").write_bytes(b"changed")
    after = diagnostic.checkout_identity()
    assert before["head"] == after["head"] == "candidate"
    assert before["tracked_sha256"] != after["tracked_sha256"]


def test_retained_trace_events_and_missing_trace(tmp_path: Path) -> None:
    """Retain selected real Trace2 fields while preserving missing trace uncertainty.

    Args:
        tmp_path: Dedicated synthetic Trace2 artifact directory.
    """
    # Preserve selected Git events while ignoring unrelated and incomplete Trace2 lines.
    events = [
        {"event": "start", "time": "1970-01-01T00:01:41Z", "argv": ["git", "status"]},
        {"event": "region_enter", "time": "1970-01-01T00:01:42Z"},
        {"event": "exit", "time": "1970-01-01T00:01:43Z", "t_abs": 2.0, "code": 1},
    ]
    (tmp_path / "git-0000.trace2.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\ninvalid partial line\n"
    )
    records = [
        {
            "trace": "git-0000.trace2.jsonl",
            "popen_seconds": 0.5,
            "popen_start_wall": 100.0,
            "communicate": [],
        },
        {"trace": "missing.trace2.jsonl", "popen_seconds": 1.0},
    ]
    diagnostic.retain_artifacts(tmp_path, records, {})
    assert [event["event"] for event in records[0]["git_events"]] == ["start", "exit"]
    assert records[0]["git_events"][-1]["code"] == 1
    assert "argv" not in records[0]["git_events"][0]
    assert records[0]["attribution"]["git_elapsed_seconds"] == 2.0
    assert "last_git_event_to_communicate_end_seconds" not in records[0]["attribution"]
    assert records[1]["git_events"] == []


def test_cli_preserves_exit_and_rejects_checkout_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forward CLI identity unchanged and prohibit diagnostic writes inside the checkout.

    Args:
        monkeypatch: Scoped command line and run boundary replacement.
    """
    # Forward CLI values and preserve the original failure status.
    original_run = diagnostic.run
    run = mock.Mock(return_value=1)
    monkeypatch.setattr(diagnostic, "run", run)
    monkeypatch.setattr(
        diagnostic.sys,
        "argv",
        ["diagnostic", "--output", "/tmp/evidence", "--expected-head", "candidate"],
    )
    assert diagnostic.main() == 1
    run.assert_called_once_with(Path("/tmp/evidence"), "candidate")
    with pytest.raises(ValueError, match="outside the checkout"):
        original_run(diagnostic.ROOT / "forbidden-diagnostic-output", "candidate")


@pytest.fixture
def observer(tmp_path: Path) -> diagnostic.GitObserver:
    """Provide separate uploaded and raw staging directories for telemetry tests.

    Args:
        tmp_path: Disposable evidence root.

    Returns:
        A real bounded observer with no real processes.
    """
    # Separate uploaded evidence from disposable raw Git writers.
    output, staging = tmp_path / "output", tmp_path / "staging"
    output.mkdir()
    staging.mkdir()
    return diagnostic.GitObserver(output, staging)


def test_late_off_timeout_after_2048_keeps_command_snapshot_phase_and_latch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Observe late calls and retain the first timeout after all ordinary/error ring evictions.

    Args:
        monkeypatch: Scoped original process replacement and cheap checkpoint seam.
        tmp_path: Disposable uploaded trace directory.
    """
    # Inject one late timeout after the former observation cutoff.
    error = subprocess.TimeoutExpired(QUERY_COMMAND, 2)

    def constructor(command: list[str], **kwargs: object) -> mock.MagicMock:
        """Supply the late control-group timeout without enabling any Trace2 target.

        Args:
            command: Original command forwarded by the observer.
            **kwargs: Original process options plus selected Trace2 path.

        Returns:
            A process double with unchanged output or the selected original timeout.
        """
        process = mock.MagicMock()
        process.returncode = 1 if "nonzero" in command else 0
        process.__exit__.return_value = None
        process.communicate.return_value = ("original", "output")
        if "worktree" in command:
            assert kwargs["env"]["GIT_TRACE2_EVENT"] == "0"
            process.pid = 4242
            process._handle = 99
            process.stdout_thread.is_alive.return_value = True
            process.stderr_thread.is_alive.return_value = False
            process.communicate.side_effect = error
        return process

    monkeypatch.setattr(subprocess, "Popen", constructor)
    api = mock.Mock()
    api.WaitForSingleObject.return_value = 258
    monkeypatch.setattr(subprocess, "_winapi", api, raising=False)
    with diagnostic.observe_git(tmp_path) as observed:
        monkeypatch.setattr(observed, "checkpoint", mock.Mock())
        observed.set_context("test_late_timeout", "call")
        observed.assignment_pairs["worktrees"] = deque([False])
        # Complete more calls than the old cutoff before injecting a real TimeoutExpired instance.
        for index in range(2050):
            with subprocess.Popen(["git", "rev-parse", str(index)]) as process:
                process.communicate(timeout=2)
        with pytest.raises(subprocess.TimeoutExpired) as caught:
            with subprocess.Popen(error.cmd) as process:
                process.communicate(timeout=2)
        assert caught.value is error
        first = observed.first_failure
        assert first["id"] == 2050
        assert first["command"] == error.cmd
        assert first["nodeid"] == "test_late_timeout" and first["phase"] == "call"
        assert first["trace"] is None and first["trace_group"] == "off"
        assert first["timeout_observation"]["pid"] == 4242
        assert first["timeout_observation"]["handle_wait"] == 258
        assert first["timeout_observation"]["readers"] == {"stdout": True, "stderr": False}
        api.WaitForSingleObject.assert_called_once_with(99, 0)
        assert observed.groups["worktrees"]["off"]["timeouts"] == 1
        # Evict both ordinary/error ring copies while retaining the first-failure anchor.
        for index in range(diagnostic.ERROR_LIMIT + diagnostic.LATEST_LIMIT + 2):
            with subprocess.Popen(["git", "nonzero", str(index)]) as process:
                process.communicate(timeout=2)
        assert first not in observed.latest and first not in observed.errors
        assert first in observed.retained()
        assert observed.error_latch
        assert observed.counts["timeouts"] == 1
        assert observed.counts["started"] == observed.counts["finished"] > 2048
        assert observed.counts["nonzero"] == diagnostic.ERROR_LIMIT + diagnostic.LATEST_LIMIT + 2
        assert observed.counts["errors_evicted"] > 0
        assert (
            len(observed.retained())
            <= diagnostic.LATEST_LIMIT + diagnostic.SLOW_LIMIT + diagnostic.ERROR_LIMIT + 1
        )
        assert not list(tmp_path.glob("*.trace2.jsonl"))
        staging = observed.staging
    assert subprocess.Popen is constructor
    assert not staging.exists()


def test_creation_and_communication_clocks_exclude_observer_io_and_lock_delay(
    monkeypatch: pytest.MonkeyPatch, observer: diagnostic.GitObserver
) -> None:
    """Keep checkpoint I/O and concurrent observer lock waits outside measured process times.

    Args:
        monkeypatch: Scoped deterministic clocks and simulated lock delay.
        observer: Real aggregate collector with writes replaced by simulated expensive I/O.
    """
    # Simulate expensive observer writes and concurrent telemetry lock contention.
    clock = {"perf": 10.0, "wall": 100.0}

    def advance(seconds: float) -> None:
        """Advance independent deterministic clocks equally.

        Args:
            seconds: Simulated interval.
        """
        clock["perf"] += seconds
        clock["wall"] += seconds

    monkeypatch.setattr(diagnostic.time, "perf_counter", lambda: clock["perf"])
    monkeypatch.setattr(diagnostic.time, "time", lambda: clock["wall"])
    checkpoint = mock.Mock(side_effect=lambda *args, **kwargs: advance(100))
    monkeypatch.setattr(observer, "checkpoint", checkpoint)
    lock = mock.MagicMock()
    lock.__enter__.side_effect = lambda: advance(20)
    lock.__exit__.return_value = None
    observer.lock = lock
    process = mock.MagicMock(returncode=0)
    process.__exit__.return_value = None
    process.communicate.side_effect = lambda **kwargs: (advance(2), "original-output")

    def constructor(*args: object, **kwargs: object) -> mock.MagicMock:
        """Simulate only the original process creation interval.

        Args:
            *args: Forwarded command arguments.
            **kwargs: Forwarded process options.

        Returns:
            The process double after a quarter-second construction interval.
        """
        advance(0.25)
        return process

    # Construction and communication end before checkpoint or lock bookkeeping.
    with diagnostic.TimedProcess(constructor, observer, ["git", "status"]) as timed:
        assert timed.record["popen_seconds"] == 0.25
        assert checkpoint.call_count == 0
        assert timed.record["popen_start_wall"] == timed.record["observed_start_wall"]
        before = clock["wall"]
        assert timed.communicate(timeout=2) == (None, "original-output")
        assert timed.record["communicate"][-1]["end_wall"] == before + 2
        timed.kill()
        assert checkpoint.call_count == 0
    assert checkpoint.call_count == 0
    assert observer.timings["popen_total"] == 0.25
    assert observer.timings["communicate_total"] == 2
    process.kill.assert_called_once_with()
    process.__exit__.assert_called_once_with(None, None, None)

    def creation_failure(*args: object, **kwargs: object) -> None:
        """Exclude error-recording lock waits from failed construction timing too.

        Args:
            *args: Unused original constructor arguments.
            **kwargs: Unused original constructor options.

        Raises:
            OSError: The synthetic constructor fails after half a second.
        """
        advance(0.5)
        raise OSError("construction failed")

    # Failure timing ends before the observer acquires its error-recording lock.
    with pytest.raises(OSError, match="construction failed"):
        diagnostic.TimedProcess(creation_failure, observer, ["git", "status"])
    assert observer.latest[-1]["popen_seconds"] == 0.5


def test_trace_slots_rings_and_checkpoint_files_stay_bounded(
    monkeypatch: pytest.MonkeyPatch, observer: diagnostic.GitObserver
) -> None:
    """Preserve all timing counters while bounding active traces, ring details and files.

    Args:
        monkeypatch: Scoped small limits for a cheap overflow proof.
        observer: Real collector and actual disposable evidence files.
    """
    monkeypatch.setattr(diagnostic, "ACTIVE_TRACE_LIMIT", 1)
    force_trace_slots(observer, 3)
    first = observer.begin(QUERY_COMMAND)
    second = observer.begin(QUERY_COMMAND)
    assert first["trace"] and second["trace"] is None
    assert observer.counts["started"] == 2 and observer.counts["trace_omitted"] == 1
    # Bound uploaded copies while preserving raw active writers outside the artifact directory.
    raw = observer.staging / first["trace"]
    raw.write_bytes(b"x" * (diagnostic.MAX_TRACE_BYTES + 1))
    observer.checkpoint(first, force=True)
    assert (observer.output / first["trace"]).stat().st_size == diagnostic.MAX_TRACE_BYTES
    assert observer.counts["trace_truncated"] == 1
    for record in (first, second):
        record.update(popen_seconds=0.6, communicate_seconds=0.1)
        observer.finish(record, 0)
    assert not raw.exists()
    assert observer.counts["finished"] == 2 and observer.counts["slow_seen"] == 2
    checkpoint = json.loads((observer.output / "checkpoint.json").read_text())
    assert len(checkpoint["active"]) <= diagnostic.ACTIVE_TRACE_LIMIT
    assert (observer.output / "progress.json").exists()
    # Completed trace retention can also hit its bound without stopping timing/error observation.
    monkeypatch.setattr(diagnostic, "MAX_RETAINED_TRACES", 1)
    third = observer.begin(QUERY_COMMAND)
    (observer.staging / third["trace"]).write_text("bounded trace")
    third["popen_seconds"] = 0.1
    observer.finish(third, 0)
    assert observer.counts["trace_retention_omitted"] == 1
    assert observer.counts["finished"] == 3
    assert len(list(observer.output.glob("*.trace2.jsonl"))) == 1


def test_process_errors_cleanup_and_bounded_communication_history(
    monkeypatch: pytest.MonkeyPatch, observer: diagnostic.GitObserver
) -> None:
    """Retain original errors despite successful cleanup and later communication history eviction.

    Args:
        monkeypatch: Scoped cheap checkpoint replacement.
        observer: Original aggregate observer.
    """
    monkeypatch.setattr(observer, "checkpoint", mock.Mock())
    error = OSError(5, "creation failed")
    with pytest.raises(OSError, match="creation failed") as caught:
        diagnostic.TimedProcess(mock.Mock(side_effect=error), observer, ["git", "status"])
    assert caught.value is error
    assert observer.counts["creation_errors"] == 1
    process = mock.MagicMock(returncode=0)
    timeout = subprocess.TimeoutExpired("git", 2)
    process.communicate.side_effect = [timeout] + [("out", "err")] * 5
    process.__exit__.side_effect = OSError("cleanup failed")
    # Exercise communication before isolating the original cleanup error itself.
    timed = diagnostic.TimedProcess(mock.Mock(return_value=process), observer, ["git", "status"])
    timed.__enter__()
    try:
        with pytest.raises(subprocess.TimeoutExpired) as caught:
            timed.communicate(timeout=2)
        assert caught.value is timeout
        timed.kill()
        for _ in range(5):
            assert timed.communicate() == ("out", "err")
        assert len(timed.record["communicate"]) == 4
        assert not any("error" in attempt for attempt in timed.record["communicate"])
    # The caller keeps ownership of kill and original cleanup errors.
    finally:
        with pytest.raises(OSError, match="cleanup failed"):
            timed.__exit__(None, None, None)
    assert observer.error_latch and observer.counts["timeouts"] == 1
    assert observer.counts["communication_errors"] == 1 and observer.counts["cleanup_errors"] == 1
    assert observer.counts["active"] == 0


def test_non_git_options_and_original_constructor_restored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Preserve non-Git launches and explicitly empty Git environments on every exit.

    Args:
        monkeypatch: Scoped process constructor replacement.
        tmp_path: Disposable uploaded trace root.
    """
    # Use a real process-shaped double and verify only its construction options change.
    constructor = mock.MagicMock()
    constructor.return_value.returncode = 0
    constructor.return_value.__exit__.return_value = None
    monkeypatch.setattr(subprocess, "Popen", constructor)
    scope = diagnostic.observe_git(tmp_path)
    observed = scope.__enter__()
    try:
        with subprocess.Popen(["git", "status"], env={}):
            assert constructor.call_args.kwargs["env"] == dict.fromkeys(
                diagnostic.TRACE_TARGETS, "0"
            )
        assert subprocess.Popen(["python", "--version"]) is constructor.return_value
        assert constructor.call_args.kwargs == {}
        staging = observed.staging
    # Inject the body error at context exit and require that it remains unsuppressed.
    finally:
        assert scope.__exit__(RuntimeError, RuntimeError("body failed"), None) is False
    assert subprocess.Popen is constructor
    assert not staging.exists()


@pytest.mark.parametrize(
    "fault",
    [None, "pytest", "timeout", "nonzero", "wrong-head", "foreign-package", "final-checkpoint"],
)
def test_run_once_preserves_failures_and_final_checkpoint_latch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fault: str | None
) -> None:
    """Run full integration once and retain original failure or final telemetry failure status.

    Args:
        monkeypatch: Scoped expensive metadata, coverage and pytest boundaries.
        tmp_path: New uploaded evidence directory.
        fault: Selected experiment failure, including a final checkpoint write error.
    """
    # Isolate execution boundaries while preserving real final manifest and latch decisions.
    identity = {"head": "candidate", "status": "", "tracked_sha256": {"source": "a" * 64}}
    if fault == "wrong-head":
        identity["head"] = "other"
    if fault == "foreign-package":
        monkeypatch.setattr(diagnostic.workspace, "__file__", str(tmp_path / "foreign.py"))
    monkeypatch.setattr(diagnostic.platform, "platform", lambda: "fixture-platform")
    monkeypatch.setattr(diagnostic, "checkout_identity", lambda: identity.copy())
    monkeypatch.setattr(diagnostic, "git_output", lambda *args: "git fixture")
    monkeypatch.setattr(
        diagnostic.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, stdout="Poetry 2.4.2"),
    )
    collector = mock.Mock()
    collector.get_option.return_value = "ctrace"
    monkeypatch.setattr(diagnostic.coverage, "Coverage", mock.Mock(return_value=collector))
    observed = []

    def pytest_main(arguments: list[str], plugins: list[diagnostic.PhasePlugin]) -> int:
        """Exercise phase hooks and one selected Git outcome without launching pytest again.

        Args:
            arguments: Exact full integration invocation.
            plugins: Harness phase observer passed to pytest.

        Returns:
            The original selected pytest result.
        """
        assert arguments == ["tests/integration", "--no-cov", "-q"]
        plugin = plugins[0]
        item = mock.Mock(nodeid="test_module.py::test_body")
        plugin.pytest_runtest_setup(item)
        plugin.pytest_runtest_call(item)
        observer = plugin.observer
        observed.append(observer)
        if fault in {"timeout", "nonzero"}:
            record = observer.begin(["git", "worktree", "list"])
            record["popen_seconds"] = 0.1
            if fault == "timeout":
                record["timed_out"] = True
                observer.note_error(record)
            observer.finish(record, 1 if fault == "nonzero" else 0)
        plugin.pytest_runtest_teardown(item)
        if fault == "final-checkpoint":
            original = observer.checkpoint
            calls = []

            def checkpoint(*args: object, **kwargs: object) -> None:
                """Latch an error only on the final post-context checkpoint.

                Args:
                    *args: Original checkpoint arguments.
                    **kwargs: Original checkpoint options.
                """
                calls.append(True)
                original(*args, **kwargs)
                if len(calls) == 3:
                    observer.error_latch = True
                    observer.counts["telemetry_errors"] += 1

            monkeypatch.setattr(observer, "checkpoint", checkpoint)
        return 1 if fault == "pytest" else 0

    main = mock.Mock(side_effect=pytest_main)
    monkeypatch.setattr(diagnostic.pytest, "main", main)
    output = tmp_path / "output"
    status = diagnostic.run(output, "candidate")
    manifest = json.loads((output / "manifest.json").read_text())
    assert status == manifest["exit_code"] == int(fault is not None)
    assert manifest["target"] == "tests/integration"
    if fault in {"wrong-head", "foreign-package"}:
        main.assert_not_called()
        collector.start.assert_not_called()
    else:
        main.assert_called_once()
        assert manifest["pytest_exit"] == int(fault == "pytest")
        assert manifest["error_latch"] == (fault in {"timeout", "nonzero", "final-checkpoint"})
        assert observed[0].context["phase"] == "session-finished"
        collector.start.assert_called_once_with()
    assert collector.stop.called and collector.save.called
    assert (
        manifest["artifacts"]["processes.json"]
        == hashlib.sha256((output / "processes.json").read_bytes()).hexdigest()
    )


@pytest.mark.parametrize("full_error_list", [False, True])
def test_checkpoint_io_errors_stay_bounded_and_do_not_replace_process_outcomes(
    monkeypatch: pytest.MonkeyPatch, observer: diagnostic.GitObserver, full_error_list: bool
) -> None:
    """Latch telemetry failures without raising them through the original Git process.

    Args:
        monkeypatch: Scoped failed evidence-write boundary.
        observer: Real aggregate observer.
        full_error_list: Whether the trace failure must preserve an already full error list.
    """
    # A broken checkpoint path can repeat without growing the retained error list.
    original = Path.write_text

    def write(path: Path, *args: object, **kwargs: object) -> int:
        """Fail only the observer checkpoint destination.

        Args:
            path: Evidence path being written.
            *args: Original text arguments.
            **kwargs: Original text encoding options.

        Returns:
            The original successful write count for unrelated files.

        Raises:
            OSError: The checkpoint write is intentionally denied.
        """
        if path.name == "progress.json.tmp":
            raise OSError("checkpoint denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", write)
    for _ in range(10):
        observer.checkpoint(force=True)
    assert observer.error_latch and observer.counts["telemetry_errors"] == 10
    assert len(observer.telemetry_errors) == 8
    # Trace-copy failure still removes the completed raw writer and active ownership.
    force_trace_slots(observer, 1)
    record = observer.begin(QUERY_COMMAND)
    (observer.staging / record["trace"]).write_bytes(b"trace")
    record["popen_seconds"] = 0.1
    monkeypatch.setattr(observer, "copy_trace", mock.Mock(side_effect=OSError("trace denied")))
    if not full_error_list:
        observer.telemetry_errors.clear()
    observer.finish(record, 0)
    assert observer.counts["finished"] == 1
    assert not observer.active
    assert not (observer.staging / record["trace"]).exists()


def test_slow_ring_eviction_deletes_unselected_trace_files(
    monkeypatch: pytest.MonkeyPatch, observer: diagnostic.GitObserver
) -> None:
    """Prune completed traces while keeping aggregate timing and slow overflow counts.

    Args:
        monkeypatch: Scoped cheap checkpoint seam.
        observer: Real collector with actual trace copy/deletion boundaries.
    """
    # Complete enough slow calls to evict early records from every non-error retention ring.
    monkeypatch.setattr(observer, "checkpoint", mock.Mock())
    first_name = None
    force_trace_slots(observer, diagnostic.LATEST_LIMIT + 2)
    for index in range(diagnostic.LATEST_LIMIT + 2):
        record = observer.begin(QUERY_COMMAND)
        if first_name is None:
            first_name = record["trace"]
        (observer.staging / record["trace"]).write_text("trace")
        record["popen_seconds"] = 0.5
        observer.finish(record, 0)
    assert len(observer.latest) == diagnostic.LATEST_LIMIT
    assert len(observer.slow) == diagnostic.SLOW_LIMIT
    assert observer.counts["slow_evicted"] == diagnostic.LATEST_LIMIT + 2 - diagnostic.SLOW_LIMIT
    assert observer.counts["trace_deleted"] == 2
    assert not (observer.output / first_name).exists()
    assert len(list(observer.output.glob("*.trace2.jsonl"))) == diagnostic.LATEST_LIMIT
    assert observer.timings["popen_total"] == 0.5 * (diagnostic.LATEST_LIMIT + 2)


def test_active_trace_overflow_still_times_process_and_preserves_other_errors(
    monkeypatch: pytest.MonkeyPatch, observer: diagnostic.GitObserver
) -> None:
    """Keep late process errors observable when every active trace slot is occupied.

    Args:
        monkeypatch: Scoped single trace slot and cheap checkpoint seam.
        observer: Real collector.
    """
    # Occupy the sole raw-trace slot without stopping later process timing or communication.
    monkeypatch.setattr(diagnostic, "ACTIVE_TRACE_LIMIT", 1)
    checkpoint = mock.Mock()
    monkeypatch.setattr(observer, "checkpoint", checkpoint)
    force_trace_slots(observer, 2)
    active = observer.begin(QUERY_COMMAND)
    process = mock.MagicMock(returncode=0)
    process.__exit__.return_value = None
    error = OSError("communication failed")
    process.communicate.side_effect = error
    constructor = mock.Mock(return_value=process)
    with diagnostic.TimedProcess(
        constructor, observer, QUERY_COMMAND, env={"KEEP": "value"}
    ) as timed:
        assert constructor.call_args.kwargs["env"] == {
            "KEEP": "value",
            **dict.fromkeys(diagnostic.TRACE_TARGETS, "0"),
        }
        before = checkpoint.call_count
        with pytest.raises(OSError, match="communication failed") as caught:
            timed.communicate(timeout=2)
        assert caught.value is error
        assert checkpoint.call_count == before
    active["popen_seconds"] = 0.1
    observer.finish(active, 0)
    assert observer.counts["finished"] == 2
    assert observer.counts["communication_errors"] == 1
    assert observer.counts["timeouts"] == 0
    assert observer.error_latch


def test_query_assignments_are_balanced_reproducible_and_independent(tmp_path: Path) -> None:
    """Balance each query prefix without duplicates and reproduce it across interleavings.

    Args:
        tmp_path: Unused paths for observers with checkpoint writes disabled.
    """
    # Two collectors see different cross-query order but the same per-query sequence.
    observers = [diagnostic.GitObserver(tmp_path / str(i), tmp_path / f"raw-{i}") for i in range(2)]
    sequences = []
    for index, observer in enumerate(observers):
        observer.checkpoint = mock.Mock()
        choices = {query: [] for query in diagnostic.QUERY_TYPES.values()}
        queries = list(diagnostic.QUERY_TYPES.items())
        for ordinal in range(20):
            for arguments, query in queries if index == 0 else reversed(queries):
                record = observer.begin(["git", "-C", "fixture", *arguments])
                choices[query].append(record["trace_group"])
                assert abs(choices[query].count("on") - choices[query].count("off")) <= 1
                record["popen_seconds"] = 0.1
                observer.finish(record, 0)
        assert observer.counts["started"] == observer.counts["finished"] == 80
        for query in choices:
            assert choices[query].count("on") == choices[query].count("off") == 10
            assert observer.groups[query]["on"]["finished"] == 10
            assert observer.groups[query]["off"]["finished"] == 10
            assert observer.groups[query]["on"]["popen_total"] == pytest.approx(1)
        sequences.append(choices)
    assert sequences[0] == sequences[1]
    assert len({tuple(values) for values in sequences[0].values()}) > 1


@pytest.mark.parametrize("group", ["on", "off", "other"])
def test_trace_targets_and_expected_absence(
    monkeypatch: pytest.MonkeyPatch, observer: diagnostic.GitObserver, group: str
) -> None:
    """Override inherited Trace2 targets, enabling only the assigned event destination.

    Args:
        monkeypatch: Scoped process construction replacement.
        observer: Real bounded collector and trace paths.
        group: Assigned metadata group or unrelated Git command.
    """
    # Explicit inherited targets must never leak into control or unrelated Git calls.
    inherited = {"KEEP": "value", **dict.fromkeys(diagnostic.TRACE_TARGETS, "inherited-target")}
    observer.assignment_pairs["worktrees"] = deque([group == "on"])
    command = ["git", "status"] if group == "other" else QUERY_COMMAND
    process = mock.MagicMock(returncode=0)
    process.__exit__.return_value = None
    process.communicate.return_value = ("stdout", "stderr")
    constructor = mock.Mock(return_value=process)
    with diagnostic.TimedProcess(constructor, observer, command, env=inherited) as timed:
        environment = constructor.call_args.kwargs["env"]
        assert constructor.call_args.args[0] is command
        assert environment["KEEP"] == "value"
        assert environment["GIT_TRACE2"] == environment["GIT_TRACE2_PERF"] == "0"
        if group == "on":
            path = observer.staging / timed.record["trace"]
            assert environment["GIT_TRACE2_EVENT"] == str(path)
            path.write_text('{"event":"start","time":"1970-01-01T00:01:41Z"}\n')
        else:
            assert environment["GIT_TRACE2_EVENT"] == "0" and timed.record["trace"] is None
        assert timed.communicate(timeout=2) == ("stdout", "stderr")
    assert all(inherited[target] == "inherited-target" for target in diagnostic.TRACE_TARGETS)
    records = observer.retained()
    diagnostic.retain_artifacts(observer.output, records, {})
    assert records[0]["trace_group"] == group
    assert records[0]["trace_status"] == ("retained" if group == "on" else "disabled")
    assert observer.counts["trace_omitted"] == 0 and not observer.error_latch


def test_checkpoint_cadence_is_uniform_and_never_per_call(
    monkeypatch: pytest.MonkeyPatch, observer: diagnostic.GitObserver
) -> None:
    """Checkpoint every thirty-two completions, errors and phases equally across groups.

    Args:
        monkeypatch: Scoped checkpoint boundary replacement.
        observer: Real aggregate and grouping implementation.
    """
    # Successful on/off assignment alone must never trigger a disk checkpoint.
    checkpoint = mock.Mock()
    monkeypatch.setattr(observer, "checkpoint", checkpoint)
    for index in range(64):
        record = observer.begin(QUERY_COMMAND)
        assert checkpoint.call_count == index // 32
        record["popen_seconds"] = 0.1
        observer.finish(record, 0)
        assert checkpoint.call_count == (index + 1) // 32
    observer.set_context("module::test", "call")
    assert checkpoint.call_count == 3
    # Error observation remains in memory until original process cleanup has completed.
    record = observer.begin(QUERY_COMMAND)
    record["popen_seconds"] = 0.1
    observer.note_error(record)
    assert checkpoint.call_count == 3
    observer.finish(record, 0)
    assert checkpoint.call_count == 4
    assert observer.groups["worktrees"]["on"]["started"] - observer.groups["worktrees"]["off"][
        "started"
    ] in {-1, 1}


def test_timeout_endpoint_precedes_handle_and_thread_snapshot_without_poll(
    monkeypatch: pytest.MonkeyPatch, observer: diagnostic.GitObserver
) -> None:
    """Preserve the original timeout clock while separately timing bounded state observations.

    Args:
        monkeypatch: Scoped deterministic clock and Windows handle API doubles.
        observer: Real bounded collector with disk writes replaced.
    """
    # Simulated expensive metadata must not inflate the original communicate duration.
    clock = {"perf": 10.0, "wall": 100.0}
    order = []

    def advance(label: str, seconds: float, result: object) -> object:
        """Advance the fake clock and record operation order.

        Args:
            label: Observed operation name.
            seconds: Simulated operation duration.
            result: Original operation return value.

        Returns:
            The supplied operation result.
        """
        order.append(label)
        clock["perf"] += seconds
        clock["wall"] += seconds
        return result

    monkeypatch.setattr(diagnostic.time, "perf_counter", lambda: clock["perf"])
    monkeypatch.setattr(diagnostic.time, "time", lambda: clock["wall"])
    checkpoint = mock.Mock()
    monkeypatch.setattr(observer, "checkpoint", checkpoint)
    api = mock.Mock()
    api.WaitForSingleObject.side_effect = lambda *args: advance("handle", 50, 258)
    monkeypatch.setattr(subprocess, "_winapi", api, raising=False)
    observer.assignment_pairs["worktrees"] = deque([False])
    process = mock.MagicMock(pid=123, returncode=None)
    process._handle = 77
    process.__exit__.return_value = None
    process.stdout_thread.is_alive.side_effect = lambda: advance("stdout", 50, True)
    process.stderr_thread.is_alive.side_effect = lambda: advance("stderr", 50, False)
    error = subprocess.TimeoutExpired(QUERY_COMMAND, 2)

    def communicate(**kwargs: object) -> None:
        """Raise the original timeout after two simulated seconds.

        Args:
            **kwargs: Original timeout options.

        Raises:
            subprocess.TimeoutExpired: The exact injected original timeout.
        """
        advance("communicate", 2, None)
        raise error

    process.communicate.side_effect = communicate
    with diagnostic.TimedProcess(mock.Mock(return_value=process), observer, QUERY_COMMAND) as timed:
        with pytest.raises(subprocess.TimeoutExpired) as caught:
            timed.communicate(timeout=2)
        assert caught.value is error
        snapshot = timed.record["timeout_observation"]
        assert snapshot["perf"] == 12 and snapshot["wall"] == 102
        assert snapshot["handle_wait_perf"] == 62
        assert snapshot["snapshot_end_perf"] == 162 and snapshot["snapshot_seconds"] == 150
        assert snapshot["pid"] == 123 and snapshot["handle_wait"] == 258
        assert snapshot["readers"] == {"stdout": True, "stderr": False}
        assert timed.record["communicate_seconds"] == 2
        assert checkpoint.call_count == 0
        assert process.returncode is None
        process.poll.assert_not_called()
        process.kill()
    assert order == ["communicate", "handle", "stdout", "stderr"]
    api.WaitForSingleObject.assert_called_once_with(77, 0)
    process.kill.assert_called_once_with()
    assert observer.groups["worktrees"]["off"]["timeouts"] == 1


def test_timeout_snapshot_unavailable_metadata_does_not_replace_original_error(
    monkeypatch: pytest.MonkeyPatch, observer: diagnostic.GitObserver
) -> None:
    """Bound failed handle/thread observations while keeping missing state explicit.

    Args:
        monkeypatch: Scoped failed Windows handle observation.
        observer: Real collector used to verify progress-only checkpoint behavior.
    """
    # Snapshot failures are metadata, never process polling or replacement exceptions.
    api = mock.Mock()
    api.WaitForSingleObject.side_effect = OSError("handle inaccessible")
    monkeypatch.setattr(subprocess, "_winapi", api, raising=False)
    process = mock.Mock(pid="unavailable", returncode=None)
    process._handle = 1
    process.stdout_thread.is_alive.side_effect = RuntimeError("thread unavailable")
    process.stderr_thread = None
    snapshot = diagnostic.timeout_snapshot(process)
    assert snapshot["pid"] is None
    assert snapshot["handle_wait_status"] == "failed"
    assert snapshot["handle_wait_error"]["type"] == "OSError"
    assert snapshot["readers"]["stdout"]["type"] == "RuntimeError"
    assert snapshot["readers"]["stderr"] is None
    assert process.returncode is None
    process.poll.assert_not_called()
    # The low-level checkpoint can retain progress without forcing detailed ring output.
    observer.checkpoint()
    assert (observer.output / "progress.json").exists()
    assert not (observer.output / "checkpoint.json").exists()


@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "query",
        "collectors",
        "stop",
        "cancel",
        "start",
        "start-timeout",
        "etl",
        "active",
        "ambiguous",
        "status-error",
        "admin",
        "versions",
        "profiles",
        "init",
        "wrong-head",
        "missing",
        "foreign",
        "raw-cleanup",
    ],
)
def test_wpr_capability_owns_only_its_recording(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fault: str, request: pytest.FixtureRequest
) -> None:
    """Keep probe mutations scoped, preserve failures and publish only bounded metadata.

    Args:
        monkeypatch: Native platform and deterministic command boundary replacement.
        tmp_path: External disposable artifact directory.
        fault: One recorder/discovery/query failure or successful capture.
        request: Cleanup registration that also runs if a survival assertion fails.
    """
    # Simulate native tool availability and a clean exact candidate without launching a suite.
    monkeypatch.setattr(
        diagnostic.platform, "system", lambda: "Linux" if fault == "foreign" else "Windows"
    )
    monkeypatch.setattr(
        diagnostic.shutil, "which", lambda name: None if fault == "missing" else name
    )
    identity = {"head": "other" if fault == "wrong-head" else "candidate", "status": ""}
    monkeypatch.setattr(diagnostic, "checkout_identity", lambda: identity)
    # Preserve command evidence even if the operating system leaves a locked raw sidecar.
    if fault == "raw-cleanup":
        original_cleanup = diagnostic.tempfile.TemporaryDirectory._rmtree

        def failed_cleanup(*args: object, **kwargs: object) -> None:
            """Model deletion denial while evidence publication is under test.

            Args:
                *args: Original temporary-tree deletion arguments.
                **kwargs: Original deletion policy options.

            Raises:
                OSError: A simulated locked recorder sidecar.
            """
            request.addfinalizer(
                lambda: original_cleanup(*args, **kwargs) if Path(str(args[0])).exists() else None
            )
            raise OSError("locked recorder sidecar")

        monkeypatch.setattr(diagnostic.tempfile.TemporaryDirectory, "_rmtree", failed_cleanup)
    calls: dict[str, list[str]] = {}

    def command(arguments: list[str], timeout: int) -> dict:
        """Model bounded native results and create only disposable raw trace data.

        Args:
            arguments: Explicit native invocation.
            timeout: Required command communication bound.

        Returns:
            Synthetic completed command metadata.
        """
        # Classify invocations using their actual native command option.
        name = {
            "-Command": "versions",
            "-profiles": "profiles",
            "-profiledetails": "profile",
            "init": "init",
            "-start": "start",
            "worktree": "query",
            "-stop": "stop",
            "-cancel": "cancel",
        }
        phase = next(
            (value for option, value in name.items() if option in arguments),
            "collectors" if "collectors" in arguments else "status",
        )
        calls[phase] = arguments
        result = {"returncode": 0, "stdout": "", "stderr": ""}
        if phase == "versions":
            result["stdout"] = (
                "invalid"
                if fault == "versions"
                else json.dumps({"admin": fault != "admin", "wpr": "10", "tracerpt": "10"})
            )
        if phase == "status":
            result["stdout"] = (
                "WPR recording is in progress"
                if fault == "active"
                else "unknown"
                if fault == "ambiguous"
                else "WPR is not recording"
            )
            if fault == "status-error":
                result["error"] = {"type": "TimeoutExpired"}
        if phase == fault or (fault == "cancel" and phase == "stop"):
            result["returncode"] = 1
        if phase == "start" and fault == "start-timeout":
            result.pop("returncode")
            result["error"] = {"type": "TimeoutExpired"}
        if phase == "stop" and fault != "etl":
            Path(arguments[2]).write_bytes(b"raw system trace")
            Path(arguments[2]).with_suffix(".sidecar").write_bytes(b"raw sidecar")
        if phase in ("init", "query"):
            assert timeout == 2
        return result

    monkeypatch.setattr(diagnostic, "capability_command", command)
    suite = mock.Mock(side_effect=AssertionError("Capability probe must not run pytest"))
    monkeypatch.setattr(diagnostic.pytest, "main", suite)
    # Inspect exact ownership, artifact allowlist and unconditional raw evidence cleanup.
    output = tmp_path / "published"
    assert diagnostic.wpr_capability(output, "candidate") == int(fault != "none")
    result = json.loads((output / "capability.json").read_text())
    assert result["raw_scratch_removed"] == (fault != "raw-cleanup")
    assert set(path.name for path in output.iterdir()) == {"capability.json", "manifest.json"}
    assert not result["raw_trace_uploaded"]
    suite.assert_not_called()
    for phase in ("start", "query", "collectors", "stop", "cancel"):
        if phase in calls and phase != "query":
            assert calls[phase][-2:] == ["-instancename", result["instance"]]
    assert ("cancel" in calls) == (fault in {"stop", "cancel"})
    assert result["cleanup_uncertain"] == (fault in {"cancel", "start-timeout", "raw-cleanup"})
    if fault == "none":
        assert result["etl"] == {
            "bytes": 16,
            "sha256": hashlib.sha256(b"raw system trace").hexdigest(),
        }
    if "stop" in calls:
        raw_tree = Path(calls["stop"][2]).parent
        if fault == "raw-cleanup":
            assert raw_tree.exists()
            assert result["errors"][-1]["phase"] == "raw-scratch-cleanup"
            original_cleanup(str(raw_tree))
        assert not raw_tree.exists()
        assert "-skipPdbGen" in calls["stop"] and "-compress" in calls["stop"]
    if "start" in calls:
        assert "-filemode" not in calls["start"]


@pytest.mark.parametrize("fault", ["none", "oserror", "timeout"])
def test_capability_command_bounds_streams_and_preserves_faults(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Bound retained recorder text while keeping command errors and communication limits.

    Args:
        monkeypatch: Real subprocess runner boundary replacement.
        fault: Successful process, unavailable executable or timeout.
    """

    # Write beyond retained prefixes without creating an unbounded in-memory capture.
    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        """Write synthetic native output or raise the selected process fault.

        Args:
            command: Explicit executable vector.
            **kwargs: Native runner stream and timeout options.

        Returns:
            Original native exit status.

        Raises:
            OSError: The fake executable is missing.
            subprocess.TimeoutExpired: The fake command exceeds its bound.
        """
        assert kwargs["timeout"] == 2
        kwargs["stdout"].write(b"a" * 20000)
        kwargs["stderr"].write(b"b" * 19000)
        if fault == "oserror":
            raise OSError("missing")
        if fault == "timeout":
            raise subprocess.TimeoutExpired(command, 2)
        return subprocess.CompletedProcess(command, 7)

    monkeypatch.setattr(diagnostic.subprocess, "run", run)
    # Faults retain bounded partial streams and never become successful results.
    result = diagnostic.capability_command(["native", "-status"], 2)
    assert len(result["stdout"]) == len(result["stderr"]) == 16384
    assert result["stdout_bytes"] == 20000 and result["stderr_bytes"] == 19000
    assert result["seconds"] >= 0
    if fault == "none":
        assert result["returncode"] == 7
    else:
        assert "returncode" not in result and "error" in result


def test_capability_cli_and_output_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Select only capability mode and reject candidate-contained artifacts.

    Args:
        monkeypatch: Scoped CLI and capability dispatch replacement.
    """
    # Reject writes inside the checkout before creating evidence or discovering tools.
    with pytest.raises(ValueError, match="outside the candidate checkout"):
        diagnostic.wpr_capability(diagnostic.ROOT / "forbidden-probe", "candidate")
    probe = mock.Mock(return_value=1)
    monkeypatch.setattr(diagnostic, "wpr_capability", probe)
    monkeypatch.setattr(
        diagnostic.sys,
        "argv",
        [
            "probe",
            "--mode",
            "wpr-capability",
            "--output",
            "/tmp/probe",
            "--expected-head",
            "candidate",
        ],
    )
    assert diagnostic.main() == 1
    probe.assert_called_once_with(Path("/tmp/probe"), "candidate")


@pytest.mark.parametrize("fault", ["none", "timeout", "communication", "creation"])
def test_scheduling_query_captures_native_identity_and_original_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Disable Trace2 while preserving the actual process identity and kill/reap sequence.

    Args:
        tmp_path: Disposable repository argument.
        monkeypatch: Controlled Popen and identity boundary replacements.
        fault: Original process creation/communication outcome.
    """
    # Model actual Windows reader thread names and immutable native IDs.
    process = mock.MagicMock(pid=20, returncode=0)
    process.__enter__.return_value = process
    process.__exit__.return_value = None
    process.stdout_thread.native_id, process.stderr_thread.native_id = 12, 13
    process.communicate.side_effect = (
        [subprocess.TimeoutExpired("git", 2), (b"", b"")]
        if fault == "timeout"
        else OSError("communication")
        if fault == "communication"
        else [(b"out", b"err")]
    )
    constructor = mock.Mock(return_value=process)
    if fault == "creation":
        constructor.side_effect = OSError("creation")
    monkeypatch.setattr(diagnostic.subprocess, "Popen", constructor)
    monkeypatch.setattr(diagnostic.os, "getpid", lambda: 10)
    monkeypatch.setattr(diagnostic.threading, "get_native_id", lambda: 11)
    # Original cleanup happens before the helper returns any retained metadata.
    result = diagnostic.scheduling_query(tmp_path)
    assert result["python_pid"] == 10 and result["calling_tid"] == 11
    assert all(
        constructor.call_args.kwargs["env"][name] == "0" for name in diagnostic.TRACE_TARGETS
    )
    assert process.kill.call_count == int(fault == "timeout")
    if fault == "creation":
        assert "git_pid" not in result and "error" in result
    else:
        assert result["git_pid"] == 20 and result["reader_tids"] == [12, 13]
        assert result["start_perf_ns"] <= result["end_perf_ns"]
        assert process.communicate.call_args_list[0] == mock.call(timeout=2)
        if fault == "timeout":
            assert result["timeout"]
            assert process.communicate.call_args_list[1] == mock.call()


@pytest.mark.parametrize(
    "fault", ["none", "deadline", "overflow", "late-overflow", "oserror", "running"]
)
def test_private_export_bounds_and_reaps_owned_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Keep raw export output private and terminate owned exporters on bounded overflow.

    Args:
        tmp_path: Private export staging directory.
        monkeypatch: Native process, clock and raw-size controls.
        fault: Selected export deadline/disk/native failure.
    """
    # A private writer may exceed limits before or immediately after process completion.
    process = mock.MagicMock()
    process.__enter__.return_value = process
    process.wait.return_value = 0
    constructor = mock.Mock(return_value=process)
    monkeypatch.setattr(diagnostic.subprocess, "Popen", constructor)
    if fault == "oserror":
        constructor.side_effect = OSError("sensitive raw error")
    if fault == "running":
        process.wait.side_effect = [subprocess.TimeoutExpired("xperf", 0.05), 0]
    if fault == "deadline":
        monkeypatch.setattr(diagnostic.time, "monotonic", mock.Mock(side_effect=[0, 91, 92]))
    if fault == "overflow":
        monkeypatch.setattr(diagnostic, "RAW_LIMIT_BYTES", -1)
    if fault == "late-overflow":
        monkeypatch.setattr(diagnostic, "RAW_LIMIT_BYTES", 1)
        process.wait.side_effect = lambda **kwargs: (tmp_path / "huge.csv").write_bytes(b"xx") and 0
    # Publish numeric outcomes only, never native export stdout/stderr or exception messages.
    result = diagnostic.bounded_export(["xperf", "private.etl"], tmp_path)
    assert "sensitive" not in json.dumps(result)
    assert "stdout" not in result and "stderr" not in result
    assert process.kill.call_count == int(fault in {"deadline", "overflow"})
    assert ("error" in result) == (fault not in {"none", "running"})


def test_scheduling_cli_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dispatch only the explicitly selected bounded scheduling roundtrip.

    Args:
        monkeypatch: CLI arguments and probe replacement.
    """
    # Preserve candidate identity and the scheduling capability flag.
    probe = mock.Mock(return_value=1)
    monkeypatch.setattr(diagnostic, "wpr_capability", probe)
    monkeypatch.setattr(
        diagnostic.sys,
        "argv",
        [
            "probe",
            "--mode",
            "wpr-scheduling",
            "--output",
            "/tmp/probe",
            "--expected-head",
            "candidate",
        ],
    )
    assert diagnostic.main() == 1
    probe.assert_called_once_with(Path("/tmp/probe"), "candidate", scheduling=True)


@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "utf16",
        "missing",
        "incomplete",
        "incomplete-secret",
        "malformed-secret",
        "data-error",
        "duplicate",
        "empty",
        "bad-order",
        "row-in-header",
        "csv",
        "line",
        "records",
        "headers",
        "fields",
        "name-length",
        "size",
        "decode",
        "absent",
    ],
)
def test_xperf_header_boundaries_counts_and_privacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Require explicit complete headers and never retain CSV content values.

    Args:
        tmp_path: Private xperf CSV destination.
        monkeypatch: Scoped small hard-limit controls.
        fault: Selected format, encoding, absence or bound failure.
    """
    # Native header markers remain an unverified expectation, exercised with synthetic schemas.
    path = tmp_path / "events.csv"
    content = (
        "BeginHeader\nCSwitch,TimeStamp,New Thread Id,Old Thread Id\n"
        "ReadyThread,TimeStamp,Thread Id\nEndHeader\n\n"
        "CSwitch,1,SECRET_PROCESS,SECRET_COMMAND\n"
        "ReadyThread,2,SECRET_THREAD\nUnknown,SECRET_OTHER\n"
    )
    if fault == "missing":
        content = "CSwitch,1,SECRET_PROCESS\n"
    elif fault == "incomplete":
        content = "BeginHeader\nCSwitch,TimeStamp\n"
    elif fault == "incomplete-secret":
        content = "BeginHeader\nSECRET_CONTENT,PRIVATE_VALUE\n"
    elif fault == "malformed-secret":
        content = "BeginHeader\nCSwitch,TimeStamp\nSECRET_CONTENT,123\nEndHeader\n"
    elif fault == "data-error":
        content += 'CSwitch,"SECRET_UNCLOSED\n'
    elif fault == "duplicate":
        content += "BeginHeader\nCSwitch,TimeStamp\nEndHeader\n"
    elif fault == "empty":
        content = "BeginHeader\nEndHeader\n"
    elif fault == "bad-order":
        content += "EndHeader\n"
    elif fault == "row-in-header":
        content = "BeginHeader\nCSwitch,123,SECRET_PROCESS\nEndHeader\n"
    elif fault == "csv":
        content = 'BeginHeader\nCSwitch,"SECRET_UNCLOSED\n'
    elif fault == "line":
        monkeypatch.setattr(diagnostic, "CSV_LINE_LIMIT", 3)
    elif fault == "records":
        monkeypatch.setattr(diagnostic, "CSV_RECORD_LIMIT", 1)
    elif fault == "headers":
        monkeypatch.setattr(diagnostic, "HEADER_LIMIT", 1)
    elif fault == "fields":
        monkeypatch.setattr(diagnostic, "HEADER_FIELD_LIMIT", 2)
    elif fault == "name-length":
        monkeypatch.setattr(diagnostic, "HEADER_NAME_LIMIT", 3)
    elif fault == "size":
        monkeypatch.setattr(diagnostic, "EXPORT_LIMIT_BYTES", 1)
    path.write_text(content, encoding="utf-16" if fault == "utf16" else "utf-8")
    if fault == "decode":
        path.write_bytes(b"\xffSECRET_INVALID_ENCODING")
    if fault == "absent":
        path.unlink()
    # Only marker-delimited schemas and literal event counters enter published metadata.
    report = diagnostic.xperf_schema(path)
    assert "SECRET" not in json.dumps(report)
    assert bool(report["errors"]) == (fault not in {"none", "utf16"})
    assert len(report["headers"]) <= diagnostic.HEADER_LIMIT
    if fault in {"none", "utf16"}:
        assert report["header_sections"] == 1 and len(report["headers"]) == 2
        assert report["event_counts"]["CSwitch"] == report["event_counts"]["ReadyThread"] == 1
        assert report["unknown_rows"] == 1
    if fault in {"incomplete-secret", "malformed-secret"}:
        assert not report["headers"] and report["header_sections"] == 0
    if fault == "data-error":
        assert report["headers"] and report["header_sections"] == 1
    if fault == "missing":
        assert not report["headers"] and report["errors"] == ["header-marker-missing"]


@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "xperf-missing",
        "help",
        "dumper",
        "cswitch",
        "csv",
        "missing-file",
        "oversize",
        "raw-limit",
        "config",
        "cleanup",
    ],
)
def test_xperf_roundtrip_preserves_private_exports_and_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest, fault: str
) -> None:
    """Export one owned ETL twice and publish schemas instead of arbitrary native report text.

    Args:
        tmp_path: External metadata publication directory.
        monkeypatch: Deterministic native platform/tool/process boundaries.
        request: Cleanup registration even if raw-survival assertions fail.
        fault: Selected installed-tool/export/format/configuration/cleanup gap.
    """
    # Reuse native recording guards while ensuring no integration suite or dependency installation.
    if fault == "raw-limit":
        monkeypatch.setattr(diagnostic, "RAW_LIMIT_BYTES", 1)
    monkeypatch.setattr(diagnostic.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        diagnostic.shutil,
        "which",
        lambda name: None if fault == "xperf-missing" and name == "xperf.exe" else name,
    )
    original_is_file = Path.is_file
    monkeypatch.setattr(
        diagnostic.Path,
        "is_file",
        lambda path: (
            False if "Windows Performance Toolkit" in str(path) else original_is_file(path)
        ),
    )
    monkeypatch.setattr(
        diagnostic, "checkout_identity", lambda: {"head": "candidate", "status": ""}
    )
    query = mock.Mock(
        return_value={
            "python_pid": 10,
            "git_pid": 20,
            "calling_tid": 11,
            "reader_tids": [12, 13],
            "returncode": 0,
        }
    )
    monkeypatch.setattr(diagnostic, "scheduling_query", query)
    if fault == "cleanup":
        original_cleanup = diagnostic.tempfile.TemporaryDirectory._rmtree

        def denied_cleanup(*args: object, **kwargs: object) -> None:
            """Register test-owned raw cleanup before simulating a locked sidecar.

            Args:
                *args: Original private-tree deletion arguments.
                **kwargs: Original deletion policy arguments.

            Raises:
                OSError: Simulated recorder sidecar remains locked.
            """
            request.addfinalizer(
                lambda: original_cleanup(*args, **kwargs) if Path(str(args[0])).exists() else None
            )
            raise OSError("locked sidecar")

        monkeypatch.setattr(diagnostic.tempfile.TemporaryDirectory, "_rmtree", denied_cleanup)
    calls = []
    exports = []
    collectors = (
        "Dropped event : 0\nLogging mode : Memory\nCollector Name : owned\n"
        "Buffer Size (KB) : 1024\nNumber of Buffers : 64\nEvents Lost : 0\n"
        "System Keywords\n\tProcessThread\n\tCSwitch\n\tReadyThread\n"
    )
    if fault == "config":
        collectors = collectors.replace("64", "818")

    def command(arguments: list[str], timeout: int) -> dict:
        """Return static recorder/help metadata and create only a private synthetic ETL.

        Args:
            arguments: Explicit native recorder/help invocation.
            timeout: Command communication bound.

        Returns:
            Original command outcome without trace content.
        """
        calls.append(arguments)
        result = {"returncode": 0, "stdout": "", "stderr": ""}
        if "-Command" in arguments:
            result["stdout"] = json.dumps(
                {"admin": True, "wpr": "10", "tracerpt": "10", "xperf": "10"}
            )
        elif "-help" in arguments:
            result["stdout"] = "Static native help"
            result["returncode"] = int(fault == "help")
        elif "-profiledetails" in arguments:
            result["stdout"] = "Profile : Scheduling.Verbose.Memory"
        elif "collectors" in arguments:
            result["stdout"] = collectors
        elif "-status" in arguments:
            result["stdout"] = "WPR is not recording"
        elif "-stop" in arguments:
            Path(arguments[2]).write_bytes(b"PRIVATE_ETL")
        return result

    def export(arguments: list[str], scratch: Path) -> dict:
        """Create separate private dumper/report outputs from the same ETL.

        Args:
            arguments: Exact xperf export command.
            scratch: External private staging directory.

        Returns:
            Original exporter outcome.
        """
        exports.append(arguments)
        action = arguments[arguments.index("-a") + 1]
        path = Path(arguments[arguments.index("-o") + 1])
        if fault != "missing-file":
            if action == "dumper":
                path.write_text(
                    "SECRET_NO_MARKERS"
                    if fault == "csv"
                    else (
                        "BeginHeader\nCSwitch,TimeStamp,Thread Id\nEndHeader\n"
                        "CSwitch,123,SECRET_PROCESS\n"
                    )
                )
            else:
                path.write_text("SECRET_THREAD_REPORT SECRET_COMMAND_LINE")
        if fault == "oversize":
            monkeypatch.setattr(diagnostic, "EXPORT_LIMIT_BYTES", 1)
        return {"returncode": int(fault == action), "seconds": 0.1}

    monkeypatch.setattr(diagnostic, "capability_command", command)
    monkeypatch.setattr(diagnostic, "bounded_export", export)
    # Both original outcomes, private-file hashes and uncertain cleanup remain reviewable.
    output = tmp_path / "published"
    assert diagnostic.wpr_capability(output, "candidate", scheduling=True) == int(fault != "none")
    text = (output / "capability.json").read_text()
    assert "SECRET" not in text and "PRIVATE_ETL" not in text
    report = json.loads(text)
    assert report["raw_scratch_removed"] == (fault != "cleanup")
    assert set(path.name for path in output.iterdir()) == {"manifest.json", "capability.json"}
    if fault not in {"help", "xperf-missing", "raw-limit"}:
        query.assert_called_once()
        assert len(exports) == 2 and exports[0][2] == exports[1][2]
        assert exports[0][-2:] == ["-a", "dumper"]
        assert exports[1][-3:] == ["-a", "cswitch", "-thread"]
        assert report["schema_only"]
        assert report["xperf_exports"]["dumper"]["etl_sha256"] == report["etl"]["sha256"]
        assert report["xperf_exports"]["cswitch"]["etl_sha256"] == report["etl"]["sha256"]
    else:
        if fault == "raw-limit":
            query.assert_called_once()
        else:
            query.assert_not_called()
        assert not exports
