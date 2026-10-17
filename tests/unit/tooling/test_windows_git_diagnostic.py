"""Verify temporary full-integration Git telemetry, retained failures and bounded evidence."""

import hashlib
import json
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from scripts import windows_git_diagnostic as diagnostic

pytestmark = pytest.mark.unit


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


def test_late_timeout_after_2048_keeps_command_trace_phase_and_latch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Observe late calls and retain the first timeout after all ordinary/error ring evictions.

    Args:
        monkeypatch: Scoped original process replacement and cheap checkpoint seam.
        tmp_path: Disposable uploaded trace directory.
    """
    # Inject one late timeout after the former observation cutoff.
    error = subprocess.TimeoutExpired(["git", "worktree", "list", "--porcelain", "-z"], 2)

    def constructor(command: list[str], **kwargs: object) -> mock.MagicMock:
        """Supply real Trace2 bytes only for the injected late timeout.

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
            trace = Path(kwargs["env"]["GIT_TRACE2_EVENT"])
            trace.write_text('{"event":"start","time":"1970-01-01T00:01:41Z"}\n')
            process.communicate.side_effect = error
        return process

    monkeypatch.setattr(subprocess, "Popen", constructor)
    with diagnostic.observe_git(tmp_path) as observed:
        monkeypatch.setattr(observed, "checkpoint", mock.Mock())
        observed.set_context("test_late_timeout", "call")
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
        assert (tmp_path / first["trace"]).read_text().startswith('{"event":"start"')
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
        assert (tmp_path / first["trace"]).exists()
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

    # A hundred seconds of checkpoint delay must not become attributed process creation time.
    with diagnostic.TimedProcess(constructor, observer, ["git", "status"]) as timed:
        assert timed.record["popen_seconds"] == 0.25
        assert checkpoint.call_count == 1
        assert timed.record["popen_start_wall"] > timed.record["observed_start_wall"] + 99
        before = clock["wall"]
        assert timed.communicate(timeout=2) == (None, "original-output")
        assert timed.record["communicate"][-1]["end_wall"] == before + 2
        timed.kill()
        assert checkpoint.call_count == 1
    assert checkpoint.call_count == 2
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
    first = observer.begin(["git", "status"])
    second = observer.begin(["git", "status"])
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
    third = observer.begin(["git", "worktree", "list"])
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
    with pytest.raises(OSError) as caught:
        diagnostic.TimedProcess(mock.Mock(side_effect=error), observer, ["git", "status"])
    assert caught.value is error
    assert observer.counts["creation_errors"] == 1
    process = mock.MagicMock(returncode=0)
    timeout = subprocess.TimeoutExpired("git", 2)
    process.communicate.side_effect = [timeout] + [("out", "err")] * 5
    process.__exit__.side_effect = OSError("cleanup failed")
    # The caller keeps ownership of kill and original cleanup errors.
    with pytest.raises(OSError, match="cleanup failed"):
        with diagnostic.TimedProcess(
            mock.Mock(return_value=process), observer, ["git", "status"]
        ) as timed:
            with pytest.raises(subprocess.TimeoutExpired) as caught:
                timed.communicate(timeout=2)
            assert caught.value is timeout
            timed.kill()
            for _ in range(5):
                assert timed.communicate() == ("out", "err")
            assert len(timed.record["communicate"]) == 4
            assert not any("error" in attempt for attempt in timed.record["communicate"])
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
    with pytest.raises(RuntimeError, match="body failed"):
        with diagnostic.observe_git(tmp_path) as observed:
            with subprocess.Popen(["git", "status"], env={}):
                assert set(constructor.call_args.kwargs["env"]) == {"GIT_TRACE2_EVENT"}
            assert subprocess.Popen(["python", "--version"]) is constructor.return_value
            assert constructor.call_args.kwargs == {}
            staging = observed.staging
            raise RuntimeError("body failed")
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
    record = observer.begin(["git", "status"])
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
    for index in range(diagnostic.LATEST_LIMIT + 2):
        record = observer.begin(["git", "status", str(index)])
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
    active = observer.begin(["git", "status"])
    process = mock.MagicMock(returncode=0)
    process.__exit__.return_value = None
    error = OSError("communication failed")
    process.communicate.side_effect = error
    constructor = mock.Mock(return_value=process)
    with diagnostic.TimedProcess(
        constructor, observer, ["git", "status"], env={"KEEP": "value"}
    ) as timed:
        assert constructor.call_args.kwargs["env"] == {"KEEP": "value"}
        before = checkpoint.call_count
        with pytest.raises(OSError) as caught:
            timed.communicate(timeout=2)
        assert caught.value is error
        assert checkpoint.call_count == before
    active["popen_seconds"] = 0.1
    observer.finish(active, 0)
    assert observer.counts["finished"] == 2
    assert observer.counts["communication_errors"] == 1
    assert observer.counts["timeouts"] == 0
    assert observer.error_latch
