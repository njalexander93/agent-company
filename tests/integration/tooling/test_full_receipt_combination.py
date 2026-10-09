"""Exercise strict full/check-local receipts through the real standalone combiner."""

import json
from pathlib import Path

import pytest

from scripts.coverage_evidence import digest, valid_matrix
from tests.unit.tooling.test_coverage_evidence_records import TOOLS, record

pytestmark = pytest.mark.integration


def test_standalone_combiner_accepts_only_complete_full_native_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preserve the older full-suite input after strict receipt validation.

    Args:
        tmp_path: Disposable directory supplied by pytest for this case.
        monkeypatch: Pytest fixture that restores patched dependencies after this case.
    """
    # Build complete legacy full-suite records for both aggregate systems.
    for system in ("Linux", "Windows"):
        directory = record(tmp_path, system, "test")
        outcome = directory / "pytest-evidence.json"
        content = json.loads(outcome.read_text())
        old = content["collected"][0]["nodeid"]
        phases = content["reports"].pop(old)
        unit = "tests/unit/test_sample.py::test_unit"
        integration = "tests/integration/test_sample.py::test_integration"
        content["collected"] = [
            {"nodeid": unit, "suite_markers": ["unit"], "skip_reasons": []},
            {"nodeid": integration, "suite_markers": ["integration"], "skip_reasons": []},
        ]
        content["reports"] = {unit: phases, integration: phases}
        outcome.write_text(json.dumps(content))
        (directory / "tests.xml").write_text(
            '<testsuites><testsuite tests="2"><testcase name="test_unit"/>'
            '<testcase name="test_integration"/></testsuite></testsuites>'
        )
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        (directory / "test-test-0.log").rename(directory / "test-0.log")
        manifest["commands"][0]["task"] = "test"
        manifest["commands"][0]["log"] = "test-0.log"
        manifest["artifacts_sha256"].pop("test-test-0.log")
        manifest["artifacts_sha256"]["test-0.log"] = digest(directory / "test-0.log")
        # Rehash the altered outcome artifacts so semantic validation must detect the defect.
        for name in ("pytest-evidence.json", "tests.xml"):
            manifest["artifacts_sha256"][name] = digest(directory / name)
        manifest_path.write_text(json.dumps(manifest))
    kwargs = {
        "sha": "candidate",
        "tracked_digest": "bytes",
        "config_digest": "config",
        "tool_versions": TOOLS,
        "systems": {"Linux", "Windows"},
        "allow_full": True,
        "source_root": tmp_path,
    }
    # Verify the complete native shape before exercising the standalone combiner.
    assert set(valid_matrix(tmp_path, **kwargs)) == {("Linux", "test"), ("Windows", "test")}
    windows = tmp_path / "Windows/test"
    check_local = tmp_path / "Windows/.coverage.local"
    windows.rename(check_local)
    missing = check_local / "manifest.json"
    manifest = json.loads(missing.read_text())
    tasks = ["validate-config", "validate-config", "format-check", "lint", "type-check", "test"]
    counts: dict[str, int] = {}
    commands = []
    # Generate each requested native suite through the real contributor runner.
    for task in tasks:
        log = f"{task}-{counts.get(task, 0)}.log"
        counts[task] = counts.get(task, 0) + 1
        (check_local / log).write_text("passed\n")
        manifest["artifacts_sha256"][log] = digest(check_local / log)
        commands.append({"task": task, "exit_code": 0, "seconds": 0.1, "log": log})
    manifest["commands"] = commands
    missing.write_text(json.dumps(manifest))
    assert set(valid_matrix(tmp_path, **kwargs)) == {("Linux", "test"), ("Windows", "test")}
    original = missing.read_bytes()
    missing.unlink()
    # Reject the complete-suite pair when the stricter three-OS matrix is required.
    with pytest.raises(ValueError, match="Incomplete native matrix"):
        valid_matrix(tmp_path, **kwargs)
    missing.write_bytes(original)
    assert set(valid_matrix(tmp_path, **kwargs)) == {("Linux", "test"), ("Windows", "test")}
    # The actual combiner must consume both strict full receipts, including check-local.
    import hashlib
    import os
    import subprocess
    import sys

    from scripts import combine_coverage, dev

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    source = checkout / "src/agent_company/sample.py"
    source.parent.mkdir(parents=True)
    source.write_text("value = 1\n")
    policy = checkout / "pyproject.toml"
    policy.write_text(
        "[tool.coverage.run]\nbranch = true\nrelative_files = true\n"
        "[tool.coverage.report]\nfail_under = 80\n"
    )
    (checkout / ".gitattributes").write_text("* text=auto eol=lf\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    # Initialize a clean disposable checkout before measuring a full suite.
    for command in (
        ["init", "-q"],
        ["add", "."],
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
        subprocess.run(["git", *command], cwd=checkout, check=True, capture_output=True)
    monkeypatch.chdir(checkout)
    monkeypatch.setattr(dev, "ROOT", checkout)
    monkeypatch.setattr(combine_coverage, "ROOT", checkout)
    state = dev.checkout_state()
    config = hashlib.sha256(policy.read_bytes()).hexdigest()
    # Verify each retained complete-suite receipt is independently accepted.
    for directory in (tmp_path / "Linux/test", check_local):
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest.update(
            {
                "sha": state["sha"],
                "end_sha": state["sha"],
                "tracked_digest": state["tracked_digest"],
                "end_tracked_digest": state["tracked_digest"],
                "config_sha256": config,
            }
        )
        smoke_path = directory / "instrumentation.json"
        smoke = json.loads(smoke_path.read_text())
        smoke["config_sha256"] = config
        smoke_path.write_text(json.dumps(smoke))
        manifest["artifacts_sha256"]["instrumentation.json"] = digest(smoke_path)
        manifest_path.write_text(json.dumps(manifest))
    output = tmp_path / "combined"
    monkeypatch.setattr(
        sys, "argv", ["combine_coverage.py", str(tmp_path), "--output-dir", str(output)]
    )
    assert combine_coverage.main() == 0
    combined = json.loads((output / "combined.json").read_text())
    assert combined["passed"] is True
    assert combined["required_percent"] == 80
