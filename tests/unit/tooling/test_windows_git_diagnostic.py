"""Verify temporary Git timing attribution, retained failures and original process cleanup."""

import hashlib
import json
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
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


def test_timed_process_preserves_timeout_kill_reap_and_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep original timeout identity, cleanup calls and returned output unchanged.

    Args:
        monkeypatch: Scoped deterministic clock replacement.
    """
    error = subprocess.TimeoutExpired(["git", "status"], 2)
    process = mock.MagicMock()
    process.communicate.side_effect = [error, ("original stdout", "original stderr")]
    process.returncode = -9
    constructor = mock.Mock(return_value=process)
    monkeypatch.setattr(
        diagnostic.time, "perf_counter", mock.Mock(side_effect=[1, 1.4, 2, 4, 5, 5.1])
    )
    record = {}
    # Let the caller retain responsibility for killing and reaping after its unchanged timeout.
    with diagnostic.TimedProcess(constructor, record, ["git", "status"]) as timed:
        with pytest.raises(subprocess.TimeoutExpired) as caught:
            timed.communicate(timeout=2)
        assert caught.value is error
        timed.kill()
        assert timed.communicate() == ("original stdout", "original stderr")
    process.kill.assert_called_once_with()
    process.__exit__.assert_called_once_with(None, None, None)
    constructor.assert_called_once_with(["git", "status"])
    assert record["popen_seconds"] == pytest.approx(0.4)
    assert [attempt["timeout"] for attempt in record["communicate"]] == [2, None]
    assert record["communicate"][0]["error"]["type"] == "TimeoutExpired"
    assert record["communicate"][1]["returncode"] == -9
    assert "original stdout" not in str(record)
    assert "original stderr" not in str(record)


def test_creation_failure_is_retained_without_replacing_exception() -> None:
    """Keep process creation failures distinct from communicate timeouts."""
    error = OSError(5, "creation failed")
    record = {}
    constructor = mock.Mock(side_effect=error)
    with pytest.raises(OSError) as caught:
        diagnostic.TimedProcess(constructor, record, ["git", "status"])
    assert caught.value is error
    assert record["creation_error"]["errno"] == 5
    assert record["popen_seconds"] >= 0
    assert "communicate" not in record


def test_observer_preserves_environment_non_git_and_restores_on_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Add only Trace2 to Git options and restore the original constructor on failure.

    Args:
        monkeypatch: Scoped process constructor replacement.
        tmp_path: Per-process Trace2 destination.
    """
    constructor = mock.Mock()
    monkeypatch.setattr(subprocess, "Popen", constructor)
    records = []
    # An explicitly empty environment must stay empty except for the diagnostic trace variable.
    with pytest.raises(RuntimeError, match="observer body"):
        with diagnostic.observe_git(tmp_path, records, ["sample"]):
            subprocess.Popen(["git", "status"], env={})
            assert constructor.call_args.kwargs["env"] == {
                "GIT_TRACE2_EVENT": str(tmp_path / "git-0000.trace2.jsonl")
            }
            assert constructor.call_args.args == (["git", "status"],)
            subprocess.Popen(["python", "--version"])
            assert constructor.call_args.kwargs == {}
            raise RuntimeError("observer body")
    assert subprocess.Popen is constructor
    assert len(records) == 1
    assert records[0]["phase"] == "sample"


@pytest.mark.parametrize("failure_index", [None, 0, 2])
def test_samples_bound_count_stop_first_failure_and_cleanup(
    monkeypatch: pytest.MonkeyPatch, failure_index: int | None
) -> None:
    """Stop at the first failed query or collect exactly four hundred successful samples.

    Args:
        monkeypatch: Scoped disposable-fixture and repository boundary replacement.
        failure_index: Selected query failure; None allows the bounded run to finish.
    """
    # Supply enough results to expose any forbidden query after the selected failure.
    fixture = mock.Mock(root=Path("fixture"))
    monkeypatch.setattr("tests.support.Fixture", mock.Mock(return_value=fixture))
    results = [(Path("root"), Path("common"), [Path("root")])] * 400
    if failure_index is not None:
        results[failure_index] = subprocess.TimeoutExpired("git", 2)
    repository = mock.Mock(side_effect=results)
    monkeypatch.setattr(diagnostic.workspace, "repository", repository)
    records = []
    phase = ["initial"]
    diagnostic.run_samples(records, phase)
    # Verify the approved fixed budget and retention of the exact first failed observation.
    expected_count = 400 if failure_index is None else failure_index + 1
    assert diagnostic.SAMPLE_COUNT == 400
    assert len(records) == repository.call_count == expected_count
    assert all(record["ok"] for record in records[:failure_index])
    if failure_index is not None:
        assert not records[-1]["ok"]
        assert records[-1]["index"] == failure_index
        assert records[-1]["error"]["type"] == "TimeoutExpired"
        assert phase[0] == f"repository-sample-{failure_index:02d}"
    fixture.doCleanups.assert_called_once_with()
    # Setup failures also release cleanup ownership without inventing sample results.
    fixture.reset_mock()
    fixture.setUp.side_effect = OSError("setup failed")
    with pytest.raises(OSError, match="setup failed"):
        diagnostic.run_samples([], ["initial"])
    fixture.doCleanups.assert_called_once_with()


@pytest.mark.parametrize(
    "sample_ok, pytest_status, process_fault",
    [
        (False, 0, None),
        (True, 1, None),
        (True, 0, None),
        (True, 0, "timeout"),
        (True, 0, "creation"),
        (True, 0, "nonzero"),
        (True, 0, "wrong-head"),
        (True, 0, "foreign-package"),
        (True, 0, "sample-stage"),
    ],
)
def test_run_retains_nonzero_failure_and_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sample_ok: bool,
    pytest_status: int,
    process_fault: str | None,
) -> None:
    """Run pytest exactly once and never translate a failed experiment into success.

    Args:
        monkeypatch: Scoped expensive external and coverage boundaries.
        tmp_path: New diagnostic output destination.
        sample_ok: Whether all independent samples succeed.
        pytest_status: Original pytest result to retain.
        process_fault: Selected injected process, candidate or sample-setup fault.
    """
    # Isolate metadata and expensive execution while retaining real manifest and exit decisions.
    identity = {"head": "candidate", "status": "", "tracked_sha256": {"source": "a" * 64}}
    if process_fault == "wrong-head":
        identity["head"] = "different-candidate"
    if process_fault == "foreign-package":
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
    monkeypatch.setattr(
        diagnostic,
        "run_samples",
        lambda records, phase: records.extend(
            [{"ok": True, "index": index} for index in range(diagnostic.SAMPLE_COUNT)]
            if sample_ok
            else [{"ok": False, "index": 0}]
        ),
    )
    if process_fault == "sample-stage":
        monkeypatch.setattr(
            diagnostic, "run_samples", mock.Mock(side_effect=OSError("sample setup"))
        )
    pytest_main = mock.Mock(return_value=pytest_status)
    monkeypatch.setattr(diagnostic.pytest, "main", pytest_main)

    @contextmanager
    def observed(output: Path, records: list[dict[str, Any]], phase: list[str]) -> Iterator[None]:
        """Supply a retained Git failure without a real process or trace file.

        Args:
            output: Unused artifact directory supplied by the run.
            records: Process records receiving the selected hidden fault.
            phase: Unused diagnostic phase label.

        Yields:
            Control to the otherwise successful experiment.
        """
        # Distinguish construction, timeout followed by successful cleanup and nonzero exit.
        if process_fault in {"creation", "timeout", "nonzero"}:
            record = {"trace": "git-0000.trace2.jsonl", "popen_seconds": 0.1}
            if process_fault == "creation":
                record["creation_error"] = {"type": "OSError"}
            else:
                record["communicate"] = (
                    [
                        {"error": {"type": "TimeoutExpired"}, "seconds": 2},
                        {"returncode": 0, "seconds": 0.1},
                    ]
                    if process_fault == "timeout"
                    else [{"returncode": 1, "seconds": 0.1}]
                )
            records.append(record)
        yield

    monkeypatch.setattr(diagnostic, "observe_git", observed)
    output = tmp_path / "output"
    # Failure exit and original pytest exit remain separately inspectable in hashed artifacts.
    status = diagnostic.run(output, "candidate")
    assert status == int(not sample_ok or pytest_status != 0 or process_fault is not None)
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["exit_code"] == status
    assert manifest["sample_count"] == 400
    # Rejected candidates and foreign imports must not run samples or pytest at all.
    if process_fault in {"wrong-head", "foreign-package"}:
        pytest_main.assert_not_called()
        assert manifest["errors"][0]["type"] == "ValueError"
        assert "pytest_exit" not in manifest
    else:
        assert manifest["pytest_exit"] == pytest_status
        assert manifest["checkout_unchanged"]
        assert manifest["process_failures"] == (
            [0] if process_fault in {"creation", "timeout", "nonzero"} else []
        )
        pytest_main.assert_called_once_with([diagnostic.ROLLOVER, "--no-cov", "-q"])
    assert (
        manifest["artifacts"]["processes.json"]
        == hashlib.sha256((output / "processes.json").read_bytes()).hexdigest()
    )
    if process_fault in {"wrong-head", "foreign-package"}:
        collector.start.assert_not_called()
    else:
        collector.start.assert_called_once_with()
    assert collector.stop.called and collector.save.called


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


def test_record_cap_does_not_change_original_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Stop retaining telemetry at the cap without suppressing or retrying the command.

    Args:
        monkeypatch: Scoped constructor replacement and small record cap.
        tmp_path: Unused trace destination after the cap.
    """
    # Reach the telemetry cap without changing execution ownership.
    constructor = mock.Mock()
    monkeypatch.setattr(subprocess, "Popen", constructor)
    monkeypatch.setattr(diagnostic, "MAX_RECORDS", 1)
    records = [{"existing": True}]
    with diagnostic.observe_git(tmp_path, records, ["capped"]):
        assert subprocess.Popen(["git", "status"]) is constructor.return_value
    assert records == [{"existing": True}]
    constructor.assert_called_once_with(["git", "status"])


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
