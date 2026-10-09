"""Focused unit contracts for the portable contributor command runner."""

import hashlib
import json
import os
import runpy
import sys
from pathlib import Path

import pytest

from scripts import dev

pytestmark = pytest.mark.unit


def test_clean_removes_named_outputs_and_nested_caches_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Generated output is removed without traversing protected or linked trees."""
    root = tmp_path / "checkout"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("untouched")
    (root / ".task").mkdir()
    (root / ".task" / "keep").write_text("task")
    (root / ".venv").mkdir()
    (root / ".venv" / "keep").write_text("environment")
    for name in ("build", "dist", "htmlcov", ".pytest_cache"):
        (root / name).mkdir()
        (root / name / "old").write_text("remove")
    for name in ("coverage.xml", "coverage.json", ".coverage", ".coverage.worker"):
        (root / name).write_text("remove")
    cache = root / "scripts" / "nested" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "old.pyc").write_bytes(b"remove")
    (root / "scripts" / "nested" / "keep.py").write_text("keep")
    (root / "scripts" / "external").symlink_to(outside, target_is_directory=True)
    (root / "src").symlink_to(outside, target_is_directory=True)
    (root / ".ruff_cache").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(dev, "ROOT", root)

    dev.clean()
    dev.clean()

    assert not cache.exists()
    assert not any((root / name).exists() for name in ("build", "dist", "htmlcov", ".pytest_cache"))
    assert not any(
        (root / name).exists()
        for name in ("coverage.xml", "coverage.json", ".coverage", ".coverage.worker")
    )
    assert (root / "scripts" / "nested" / "keep.py").read_text() == "keep"
    assert (root / ".task" / "keep").read_text() == "task"
    assert (root / ".venv" / "keep").read_text() == "environment"
    assert (outside / "keep").read_text() == "untouched"
    assert not (root / ".ruff_cache").is_symlink()
    assert (root / "scripts" / "external").is_symlink()
    assert (root / "src").is_symlink()


def test_checkout_state_distinguishes_link_target_and_missing_tracked_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the tracked link target, not its referent bytes, affects the digest."""
    root = tmp_path / "checkout"
    root.mkdir()
    target = root / "actual"
    target.write_bytes(b"same contents")
    link = root / "tracked"
    link.symlink_to("actual")
    monkeypatch.setattr(dev, "ROOT", root)

    def git_output(command: list[str], **kwargs: object) -> bytes | str:
        if command[1] == "ls-files":
            return b"tracked\0"
        if command[1] == "rev-parse":
            return "abc123\n"
        return " M tracked\n"

    monkeypatch.setattr(dev.subprocess, "check_output", git_output)
    original = dev.checkout_state()
    assert original["sha"] == "abc123"
    assert original["status"] == " M tracked\n"
    target.write_bytes(b"changed target bytes")
    assert dev.checkout_state()["tracked_digest"] == original["tracked_digest"]
    link.unlink()
    link.symlink_to("another-target")
    changed_link = dev.checkout_state()["tracked_digest"]
    assert changed_link != original["tracked_digest"]
    link.unlink()
    assert dev.checkout_state()["tracked_digest"] != changed_link


@pytest.mark.parametrize("system", ["Darwin", "Linux"])
def test_identity_records_actual_runtime_and_tool_versions(
    system: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The receipt identifies the launcher, checkout and tool interpreter."""
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    (tmp_path / "pyproject.toml").write_bytes(b"[tool.poetry]\n")
    monkeypatch.setattr(
        dev, "checkout_state", lambda: {"sha": "head", "status": "", "tracked_digest": "tracked"}
    )
    monkeypatch.setattr(dev.platform, "platform", lambda: "fixture-platform")
    monkeypatch.setattr(dev.platform, "system", lambda: system)
    monkeypatch.setattr(dev.platform, "machine", lambda: "fixture-machine")
    monkeypatch.setattr(dev.sys, "platform", "linux" if system == "Linux" else "darwin")
    monkeypatch.setattr(dev.platform, "freedesktop_os_release", lambda: {"ID": "fixture"})
    tool = {
        "version": "3.x",
        "executable": "/tool/python",
        "tool_versions": {"coverage": "7", "pytest": "9", "pytest-cov": "7"},
    }
    seen: list[list[str]] = []

    def output(command: list[str], **kwargs: object) -> str:
        seen.append(command)
        assert kwargs["cwd"] == tmp_path
        return json.dumps(tool)

    monkeypatch.setattr(dev.subprocess, "check_output", output)
    result = dev.identity()
    assert result["sha"] == "head"
    assert result["config_sha256"] == hashlib.sha256(b"[tool.poetry]\n").hexdigest()
    assert result["system"] == system
    assert result["launcher_executable"] == sys.executable
    assert result["tool_python"] == tool
    assert result["tool_versions"] == tool["tool_versions"]
    assert ("distribution" in result) is (system == "Linux")
    assert seen[0][:3] == ["poetry", "run", "python"]


def test_run_command_returns_child_status_and_keeps_raw_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The child status and raw bytes survive console newline decoding."""
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    child = [
        sys.executable,
        "-c",
        "import os,sys; sys.stdout.buffer.write(b'one\\r\\ntwo\\xff'); "
        "sys.exit(4 if os.getenv('PYTHONUNBUFFERED') == '1' else 8)",
    ]
    log = tmp_path / "run.log"
    assert dev.run_command(child, os.environ.copy(), log) == 4
    assert log.read_bytes() == b"one\r\ntwo\xff"
    assert capsys.readouterr().out == "one\ntwo\ufffd"
    assert dev.run_command([sys.executable, "-c", "raise SystemExit(6)"], os.environ.copy()) == 6


@pytest.mark.parametrize("with_log", [False, True])
def test_run_command_reports_start_failure_and_keeps_diagnostic(
    with_log: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A missing executable returns 127 and retains a useful error."""
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    log = tmp_path / "start.log" if with_log else None
    assert dev.run_command([str(tmp_path / "does-not-exist")], {}, log) == 127
    error = capsys.readouterr().err
    assert ("[WinError 2]" if os.name == "nt" else "[Errno 2]") in error
    if log:
        assert log.read_text() == error


def test_main_help_clean_and_argument_rejection_have_no_child_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Help, cleanup and rejected flags do not start contributor checks."""
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    monkeypatch.setattr(dev, "run_command", lambda *_args, **_kwargs: pytest.fail("child started"))
    monkeypatch.setattr(sys, "argv", ["dev.py"])
    assert dev.main() == 0
    assert "Tasks:" in capsys.readouterr().out
    generated = tmp_path / "coverage.xml"
    generated.write_text("old")
    monkeypatch.setattr(sys, "argv", ["dev.py", "clean"])
    assert dev.main() == 0
    assert not generated.exists()
    monkeypatch.setattr(
        sys,
        "argv",
        ["dev.py", "lint", "--platform-coverage", "--evidence-dir", str(tmp_path / "evidence")],
    )
    with pytest.raises(SystemExit) as error:
        dev.main()
    assert error.value.code == 2
    assert not (tmp_path / "evidence").exists()


def test_main_build_runs_validation_before_build_and_stops_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed configuration check prevents package construction."""
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    monkeypatch.setattr(dev, "COMMANDS", {"validate-config": [["check"]], "build": [["package"]]})
    seen: list[list[str]] = []

    def run(command: list[str], environment: dict[str, str], log: Path | None) -> int:
        seen.append(command)
        assert log is None
        assert "COVERAGE_FILE" not in environment
        return 9

    monkeypatch.delenv("COVERAGE_FILE", raising=False)
    monkeypatch.setattr(dev, "run_command", run)
    monkeypatch.setattr(sys, "argv", ["dev.py", "build"])
    assert dev.main() == 9
    assert seen == [["check"]]


def test_main_evidence_replaces_stale_files_and_hashes_completed_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Evidence reflects this run and preserves unrelated output."""
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    for name in (
        "manifest.json",
        ".coverage",
        "coverage.xml",
        "coverage.json",
        "tests.xml",
        "pytest-evidence.json",
        "instrumentation.json",
        "instrumentation.log",
        ".coverage.old",
    ):
        (evidence / name).write_text("stale")
    (evidence / "outside.txt").write_text("keep")
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    state = {"sha": "head", "status": "", "tracked_digest": "bytes"}
    monkeypatch.setattr(dev, "identity", lambda: dict(state))
    monkeypatch.setattr(dev, "checkout_state", lambda: dict(state))
    monkeypatch.setattr(
        dev, "COMMANDS", {"test-tooling": [["pytest", "tests/integration/tooling"]]}
    )

    def run(command: list[str], environment: dict[str, str], log: Path | None) -> int:
        assert environment["COVERAGE_FILE"] == str(evidence / ".coverage")
        assert environment["AGENT_COMPANY_PYTEST_EVIDENCE"] == str(
            evidence / "pytest-evidence.json"
        )
        assert "--cov=scripts" in command
        assert any(arg.startswith("--junitxml=") for arg in command)
        assert log is not None
        log.write_text("completed")
        (evidence / ".coverage").write_bytes(b"coverage")
        (evidence / "pytest-evidence.json").write_text("outcomes")
        return 0

    monkeypatch.setattr(dev, "run_command", run)
    monkeypatch.setattr(sys, "argv", ["dev.py", "test-tooling", "--evidence-dir", str(evidence)])
    assert dev.main() == 0
    manifest = json.loads((evidence / "manifest.json").read_text())
    assert manifest["candidate_unchanged"] is True
    assert manifest["artifacts_sha256"][".coverage"] == hashlib.sha256(b"coverage").hexdigest()
    assert (
        manifest["artifacts_sha256"]["test-tooling-0.log"]
        == hashlib.sha256(b"completed").hexdigest()
    )
    assert "coverage.xml" not in manifest["artifacts_sha256"]
    assert not (evidence / ".coverage.old").exists()
    assert (evidence / "outside.txt").read_text() == "keep"


def test_main_preserves_directory_shard_and_runs_check_local_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Local collection uses its default directory and preserves directories."""
    root = tmp_path / "checkout"
    root.mkdir()
    evidence = root / ".coverage.local"
    evidence.mkdir()
    shard = evidence / ".coverage.directory"
    shard.mkdir()
    (shard / "keep").write_text("outside generated files")
    monkeypatch.setattr(dev, "ROOT", root)
    state = {
        "sha": "head",
        "status": "",
        "tracked_digest": "bytes",
        "tool_python": {"executable": "/tool/python"},
    }
    monkeypatch.setattr(dev, "identity", lambda: dict(state))
    monkeypatch.setattr(
        dev,
        "checkout_state",
        lambda: {key: state[key] for key in ("sha", "status", "tracked_digest")},
    )
    monkeypatch.setattr(dev, "CHECKS", ["test"])
    monkeypatch.setattr(dev, "COMMANDS", {"test": [["pytest", "tests"]]})
    seen: list[list[str]] = []

    def run(command: list[str], environment: dict[str, str], log: Path | None) -> int:
        seen.append(command)
        assert environment["COVERAGE_FILE"] == str(evidence / ".coverage")
        assert log is not None
        log.write_text("finished")
        return 0

    monkeypatch.setattr(dev, "run_command", run)
    monkeypatch.setattr(sys, "argv", ["dev.py", "check-local"])
    assert dev.main() == 0
    assert seen[0][0] == "/tool/python"
    assert "--cov-report=term-missing" not in seen[1]
    assert "--cov-fail-under=0" in seen[1]
    assert (shard / "keep").read_text() == "outside generated files"
    manifest = json.loads((evidence / "manifest.json").read_text())
    assert manifest["coverage_gate"] == "pending native Windows + Linux combination"


def test_main_unit_evidence_without_platform_gate_has_outcomes_but_no_coverage_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plain unit run records outcomes without claiming platform coverage."""
    evidence = tmp_path / "unit"
    monkeypatch.delenv("AGENT_COMPANY_COVERAGE_CONTEXT", raising=False)
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    state = {"sha": "head", "status": "", "tracked_digest": "bytes"}
    monkeypatch.setattr(dev, "identity", lambda: dict(state))
    monkeypatch.setattr(dev, "checkout_state", lambda: dict(state))
    monkeypatch.setattr(dev, "COMMANDS", {"test-unit": [["pytest", "tests/unit"]]})

    def run(command: list[str], environment: dict[str, str], log: Path | None) -> int:
        assert "-p" in command and "scripts.pytest_evidence" in command
        assert not any(arg.startswith("--cov-report=") for arg in command)
        assert "AGENT_COMPANY_COVERAGE_CONTEXT" not in environment
        assert log is not None
        log.write_text("unit passed")
        return 0

    monkeypatch.setattr(dev, "run_command", run)
    monkeypatch.setattr(sys, "argv", ["dev.py", "test-unit", "--evidence-dir", str(evidence)])
    assert dev.main() == 0
    assert json.loads((evidence / "manifest.json").read_text())["commands"][0]["exit_code"] == 0


def test_script_entry_exits_after_help_without_running_checks(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The executable entry propagates the help command exit code."""
    monkeypatch.setattr(sys, "argv", ["dev.py", "help"])
    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(Path(dev.__file__)), run_name="__main__")
    assert result.value.code == 0
    assert "Tasks:" in capsys.readouterr().out


def test_main_platform_smoke_falls_back_to_launcher_and_keeps_suite_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed child smoke leaves the later suite result visible."""
    evidence = tmp_path / "unit"
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    monkeypatch.setattr(
        dev,
        "identity",
        lambda: {
            "sha": "head",
            "status": "",
            "tracked_digest": "bytes",
            "tool_python": {"executable": None},
        },
    )
    monkeypatch.setattr(
        dev, "checkout_state", lambda: {"sha": "head", "status": "", "tracked_digest": "bytes"}
    )
    monkeypatch.setattr(dev, "COMMANDS", {"test-unit": [["pytest", "tests/unit"]]})
    calls: list[list[str]] = []

    def run(command: list[str], environment: dict[str, str], log: Path | None) -> int:
        calls.append(command)
        assert log is not None
        assert "AGENT_COMPANY_COVERAGE_CONTEXT" in environment
        log.write_text("failed smoke" if len(calls) == 1 else "suite complete")
        return 5 if len(calls) == 1 else 0

    monkeypatch.setattr(dev, "run_command", run)
    monkeypatch.setattr(
        sys, "argv", ["dev.py", "test-unit", "--platform-coverage", "--evidence-dir", str(evidence)]
    )
    assert dev.main() == 5
    assert calls[0][0] == sys.executable
    assert "coverage_smoke.py" in calls[0][1]
    assert "--cov-fail-under=0" in calls[1]
    assert "--cov-report=term-missing" in calls[1]
    manifest = json.loads((evidence / "manifest.json").read_text())
    assert manifest["instrumentation_exit_code"] == 5
    assert manifest["commands"][0]["exit_code"] == 0
    assert manifest["coverage_gate"] == "pending native Windows + Linux combination"


def test_main_records_changed_candidate_and_fails_even_when_command_passes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Changed tracked input invalidates a passing check receipt."""
    evidence = tmp_path / "evidence"
    state = {"sha": "head", "status": "", "tracked_digest": "before"}
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    monkeypatch.setattr(dev, "identity", lambda: dict(state))
    monkeypatch.setattr(dev, "checkout_state", lambda: {**state, "tracked_digest": "after"})
    monkeypatch.setattr(dev, "COMMANDS", {"test": [["pytest"]]})

    def run(command: list[str], environment: dict[str, str], log: Path | None) -> int:
        assert log is not None
        log.write_text("passed")
        return 0

    monkeypatch.setattr(dev, "run_command", run)
    monkeypatch.setattr(sys, "argv", ["dev.py", "test", "--evidence-dir", str(evidence)])
    assert dev.main() == 1
    manifest = json.loads((evidence / "manifest.json").read_text())
    assert manifest["commands"][0]["exit_code"] == 0
    assert manifest["candidate_unchanged"] is False
    assert manifest["end_tracked_digest"] == "after"
