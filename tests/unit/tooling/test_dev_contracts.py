"""Check the local runner's tooling-only evidence contract."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import dev

pytestmark = pytest.mark.unit


def test_checkout_state_digests_tracked_bytes_and_reports_dirty_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Untracked noise cannot change the digest; a tracked edit must change it."""
    root = tmp_path / "checkout"
    root.mkdir()
    tracked = root / "tracked.py"
    tracked.write_text("value = 1\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for command in (
        ["init", "-q"],
        ["add", "tracked.py"],
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
        subprocess.run(["git", *command], cwd=root, check=True, capture_output=True)
    monkeypatch.setattr(dev, "ROOT", root)
    baseline = dev.checkout_state()
    assert baseline["status"] == ""
    (root / "untracked.txt").write_text("noise")
    untracked = dev.checkout_state()
    assert untracked["tracked_digest"] == baseline["tracked_digest"]
    assert "untracked.txt" in untracked["status"]
    tracked.write_text("value = 2\n")
    changed = dev.checkout_state()
    assert changed["sha"] == baseline["sha"]
    assert changed["tracked_digest"] != baseline["tracked_digest"]


def test_tooling_unit_run_has_separate_script_coverage_and_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tooling unit task cannot claim production instrumentation or coverage."""
    state = {"sha": "candidate", "status": "", "tracked_digest": "bytes"}
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    monkeypatch.setattr(dev, "identity", lambda: dict(state))
    monkeypatch.setattr(dev, "checkout_state", lambda: dict(state))
    monkeypatch.setattr(dev, "COMMANDS", {"test-tooling-unit": [["pytest", "tests/unit/tooling"]]})
    seen: list[list[str]] = []

    def run(command: list[str], environment: dict[str, str], log: Path | None) -> int:
        seen.append(command)
        assert log is not None
        assert environment["COVERAGE_FILE"] == str(tmp_path / "evidence/.coverage")
        assert environment["AGENT_COMPANY_PYTEST_EVIDENCE"] == str(
            tmp_path / "evidence/pytest-evidence.json"
        )
        log.write_text("tooling unit passed\n")
        return 0

    monkeypatch.setattr(dev, "run_command", run)
    evidence = tmp_path / "evidence"
    monkeypatch.setattr(
        sys, "argv", ["dev.py", "test-tooling-unit", "--evidence-dir", str(evidence)]
    )
    assert dev.main() == 0
    assert len(seen) == 1
    command = seen[0]
    assert command[:2] == ["pytest", "tests/unit/tooling"]
    assert "--cov=scripts" in command
    assert "--cov-fail-under=0" in command
    assert not any("coverage_smoke.py" in item for item in command)
    receipt = json.loads((evidence / "manifest.json").read_text())
    assert receipt["commands"][0]["task"] == "test-tooling-unit"
    assert receipt["candidate_unchanged"] is True
    assert "instrumentation.json" not in receipt["artifacts_sha256"]
