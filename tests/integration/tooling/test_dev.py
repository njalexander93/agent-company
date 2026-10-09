"""Check portable validation evidence and bounded cleanup using disposable directories."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts import dev
from tests.platform_support import link_directory

pytestmark = pytest.mark.integration


def test_clean_preserves_environments_tasks_and_linked_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Remove caches while retaining live-data-shaped and externally linked fixtures.

    Args:
        tmp_path: Disposable root, containing no real task data.
        monkeypatch: Scoped developer-command root replacement.

    Raises:
        AssertionError: Cleanup follows a link or removes protected fixture contents.
    """
    root = tmp_path / "checkout"
    outside = tmp_path / "outside"
    for path in (root / ".venv", root / ".task", outside / "__pycache__"):
        path.mkdir(parents=True)
        (path / "keep").write_bytes(b"retain exactly")
    cache = root / "src" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "discard.pyc").write_bytes(b"generated")
    link_directory(root / "src" / "external", outside)
    monkeypatch.setattr(dev, "ROOT", root)
    # Repeated cleanup must remain bounded even with a native junction in the source tree.
    dev.clean()
    dev.clean()
    assert not cache.exists()
    for path in (root / ".venv", root / ".task", outside / "__pycache__"):
        assert (path / "keep").read_bytes() == b"retain exactly"


def test_check_stops_on_failure_and_records_real_subprocess_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Capture exact command failure and stop before a later command can execute.

    Args:
        tmp_path: Disposable log directory and subprocess working directory.
        monkeypatch: Scoped command plan and evidence identity replacement.

    Raises:
        AssertionError: Failed validation passes, loses its log or executes subsequent work.
    """
    evidence = tmp_path / "evidence with spaces"
    marker = tmp_path / "must-not-exist"
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    state = {"sha": "fixture", "status": "", "tracked_digest": "fixture"}
    monkeypatch.setattr(dev, "identity", lambda: dict(state))
    monkeypatch.setattr(dev, "checkout_state", lambda: dict(state))
    monkeypatch.setattr(dev, "CHECKS", ["first", "second"])
    monkeypatch.setattr(
        dev,
        "COMMANDS",
        {
            "first": [[sys.executable, "-c", "print('retained failure'); raise SystemExit(7)"]],
            "second": [
                [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"]
            ],
        },
    )
    monkeypatch.setattr(sys, "argv", ["dev.py", "check", "--evidence-dir", str(evidence)])
    # Execute a genuine child process; only the selected command plan and identity are fixtures.
    assert dev.main() == 7
    assert not marker.exists()
    manifest = json.loads((evidence / "manifest.json").read_text())
    assert len(manifest["commands"]) == 1
    assert manifest["commands"][0]["exit_code"] == 7
    assert (evidence / manifest["commands"][0]["log"]).read_text() == "retained failure\n"


def test_validation_rejects_source_changes_during_a_successful_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mark a passing command diagnostic-only when its tracked input changes during execution.

    Args:
        tmp_path: Disposable Git checkout and separate evidence directory.
        monkeypatch: Scoped command plan and Git environment isolation.

    Raises:
        AssertionError: Changed source retains valid candidate evidence.
    """
    root = tmp_path / "checkout"
    root.mkdir()
    source = root / "source.py"
    source.write_text("original\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for args in (
        ["init", "-q"],
        ["add", "source.py"],
        [
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
    ):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    monkeypatch.setattr(dev, "ROOT", root)
    monkeypatch.setattr(dev, "identity", dev.checkout_state)
    monkeypatch.setattr(dev, "CHECKS", ["mutation"])
    monkeypatch.setattr(
        dev,
        "COMMANDS",
        {
            "mutation": [
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path('source.py').write_text('changed\\n')",
                ]
            ]
        },
    )
    evidence = tmp_path / "evidence"
    monkeypatch.setattr(sys, "argv", ["dev.py", "check", "--evidence-dir", str(evidence)])
    assert dev.main() == 1
    manifest = json.loads((evidence / "manifest.json").read_text())
    assert manifest["commands"][0]["exit_code"] == 0
    assert manifest["sha"] == manifest["end_sha"]
    assert manifest["status"] == "" and manifest["end_status"]
    assert manifest["tracked_digest"] != manifest["end_tracked_digest"]
    assert manifest["candidate_unchanged"] is False


@pytest.mark.parametrize("task", ["check", "check-local"])
def test_local_collection_is_explicit_and_does_not_weaken_full_check(
    task: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep aggregate enforcement on ordinary checks and label local collection as pending.

    Args:
        task: Ordinary acceptance check or explicit local-only check.
        tmp_path: Disposable evidence output directory.
        monkeypatch: Scoped command runner fixture and fixed checkout identity.

    Raises:
        AssertionError: Default checking bypasses the floor or local collection claims acceptance.
    """
    state = {"sha": "fixture", "status": "", "tracked_digest": "fixture"}
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    monkeypatch.setattr(dev, "identity", lambda: dict(state))
    monkeypatch.setattr(dev, "checkout_state", lambda: dict(state))
    monkeypatch.setattr(dev, "CHECKS", ["test"])
    monkeypatch.setattr(
        dev, "COMMANDS", {"test": [[sys.executable, "-c", "print('fixture check')"]]}
    )
    evidence = tmp_path / "evidence"
    monkeypatch.setattr(sys, "argv", ["dev.py", task, "--evidence-dir", str(evidence)])
    assert dev.main() == 0
    manifest = json.loads((evidence / "manifest.json").read_text())
    command = manifest["commands"][0]["command"]
    assert ("--cov-fail-under=0" in command) is (task == "check-local")
    assert ("coverage_gate" in manifest) is (task == "check-local")
    assert manifest["candidate_unchanged"] is True


def test_failed_instrumentation_still_runs_suite_for_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retain a suite result after the independent child-only smoke fails."""
    state = {"sha": "fixture", "status": "", "tracked_digest": "fixture"}
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    monkeypatch.setattr(dev, "identity", lambda: dict(state))
    monkeypatch.setattr(dev, "checkout_state", lambda: dict(state))
    monkeypatch.setattr(
        dev,
        "COMMANDS",
        {"test-unit": [[sys.executable, "-c", "print('suite executed')"]]},
    )
    real_run = dev.run_command

    def smoke_fails(
        command: list[str], environment: dict[str, str], log: Path | None = None
    ) -> int:
        if "coverage_smoke.py" in " ".join(command):
            assert log is not None
            log.write_text("smoke failed\n")
            return 1
        return real_run(command, environment, log)

    monkeypatch.setattr(dev, "run_command", smoke_fails)
    evidence = tmp_path / "unit"
    monkeypatch.setattr(
        sys,
        "argv",
        ["dev.py", "test-unit", "--platform-coverage", "--evidence-dir", str(evidence)],
    )
    assert dev.main() == 1
    manifest = json.loads((evidence / "manifest.json").read_text())
    assert manifest["instrumentation_exit_code"] == 1
    assert manifest["commands"][0]["exit_code"] == 0
    assert "suite executed" in (evidence / "test-unit-0.log").read_text()


def test_command_stream_retains_partial_output_before_child_exit(tmp_path: Path) -> None:
    """Flush unterminated progress output while the actual child process is still running.

    Args:
        tmp_path: Disposable release signal and evidence log.

    Raises:
        AssertionError: Console or evidence output waits for the entire child to finish.
    """
    log = tmp_path / "partial.log"
    release = tmp_path / "release"
    child = (
        "import time; from pathlib import Path\n"
        "print('partial', end='', flush=True)\n"
        f"while not Path({str(release)!r}).exists(): time.sleep(0.01)\n"
        "print(' done', flush=True)\n"
    )
    parent = (
        "import os; from pathlib import Path; from scripts.dev import run_command; "
        f"raise SystemExit(run_command({[sys.executable, '-c', child]!r}, "
        f"os.environ.copy(), Path({str(log)!r})))"
    )
    with subprocess.Popen(
        [sys.executable, "-c", parent],
        cwd=dev.ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        try:
            deadline = time.monotonic() + 5
            while (not log.exists() or not log.read_bytes()) and time.monotonic() < deadline:
                time.sleep(0.01)
            assert log.read_bytes() == b"partial"
            assert process.poll() is None
        finally:
            release.touch()
        output, error = process.communicate(timeout=10)
    assert process.returncode == 0, error
    assert output == "partial done\n"
    assert log.read_text() == output


def test_command_stream_normalizes_split_crlf_and_preserves_raw_log(tmp_path: Path) -> None:
    """Keep split UTF-8 and CRLF intact without double Windows console translation.

    Args:
        tmp_path: Disposable raw log and synchronization signal.

    Raises:
        AssertionError: Forwarding corrupts Unicode, adds newlines or changes logged bytes.
    """
    log = tmp_path / "crlf.log"
    release = tmp_path / "release"
    newline_release = tmp_path / "newline-release"
    child = (
        "import sys,time; from pathlib import Path\n"
        "sys.stdout.buffer.write(b'caf\\xc3'); sys.stdout.flush()\n"
        f"while not Path({str(release)!r}).exists(): time.sleep(0.01)\n"
        "sys.stdout.buffer.write(b'\\xa9\\r'); sys.stdout.flush()\n"
        f"while not Path({str(newline_release)!r}).exists(): time.sleep(0.01)\n"
        "sys.stdout.buffer.write(b'\\n'); sys.stdout.flush()\n"
    )
    # Explicit Windows newline translation reproduces the boundary on every native OS.
    parent = (
        "import os,sys; from pathlib import Path; from scripts.dev import run_command; "
        "sys.stdout.reconfigure(encoding='utf-8', newline='\\r\\n'); "
        f"raise SystemExit(run_command({[sys.executable, '-c', child]!r}, "
        f"os.environ.copy(), Path({str(log)!r})))"
    )
    with subprocess.Popen(
        [sys.executable, "-c", parent],
        cwd=dev.ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as process:
        try:
            deadline = time.monotonic() + 5
            while (not log.exists() or not log.read_bytes()) and time.monotonic() < deadline:
                time.sleep(0.01)
            assert log.read_bytes() == b"caf\xc3"
            assert process.poll() is None
            release.touch()
            deadline = time.monotonic() + 5
            while log.read_bytes() != "café\r".encode("utf-8") and time.monotonic() < deadline:
                time.sleep(0.01)
            assert log.read_bytes() == "café\r".encode("utf-8")
        finally:
            release.touch()
            newline_release.touch()
        output, error = process.communicate(timeout=10)
    assert process.returncode == 0, error
    assert output == "café\r\n".encode("utf-8")
    assert log.read_bytes() == output
