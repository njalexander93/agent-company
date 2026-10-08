"""Verify coverage transport using synthetic data, never as native platform evidence."""

import json
from pathlib import Path

import pytest
from coverage import Coverage, CoverageData

from scripts.combine_coverage import verified_inputs
from tests.support import ROOT

pytestmark = pytest.mark.integration


def test_combination_normalizes_windows_paths_and_preserves_contexts(tmp_path: Path) -> None:
    """Combine synthetic separators and contexts through the real locked coverage library.

    Args:
        tmp_path: Disposable coverage databases unrelated to platform acceptance.

    Raises:
        AssertionError: Windows filenames remain separate or context labels are lost.
    """
    files = []
    for system, name in (
        ("synthetic-posix", "src/agent_company/lifecycle/task_workspace.py"),
        ("synthetic-windows", "src\\agent_company\\lifecycle\\task_workspace.py"),
    ):
        path = tmp_path / system
        data = CoverageData(basename=str(path))
        data.set_context(system)
        data.add_arcs({name: [(-1, 1), (1, -1)]})
        data.write()
        files.append(str(path))
    coverage = Coverage(
        data_file=str(tmp_path / "combined"), config_file=str(ROOT / "pyproject.toml")
    )
    coverage.combine(files, strict=True, keep=True)
    data = coverage.get_data()
    assert data.measured_files() == {str(Path("src/agent_company/lifecycle/task_workspace.py"))}
    assert data.measured_contexts() == {"synthetic-posix", "synthetic-windows"}
    assert all(Path(path).exists() for path in files)


@pytest.mark.parametrize(
    "defect",
    [
        "missing-platform",
        "wrong-sha",
        "failed-tests",
        "dirty",
        "changed-source",
        "changed-commit",
        "different-bytes",
    ],
)
def test_native_evidence_rejects_incomplete_or_mismatched_inputs(
    tmp_path: Path, defect: str
) -> None:
    """Refuse aggregate acceptance when a native result is absent or belongs to other code.

    Args:
        tmp_path: Disposable synthetic artifact directory.
        defect: Invalid provenance or test result to introduce.

    Raises:
        AssertionError: The combined gate accepts invalid platform input metadata.
    """
    for system in ("Windows", "Linux"):
        if defect == "missing-platform" and system == "Linux":
            continue
        root = tmp_path / system
        root.mkdir()
        manifest = {
            "sha": "candidate",
            "system": system,
            "end_sha": "candidate",
            "end_status": "",
            "candidate_unchanged": True,
            "tracked_digest": "before",
            "end_tracked_digest": "before",
            "status": "",
            "commands": [
                {"task": "test", "exit_code": 0},
            ],
        }
        if system == "Windows":
            if defect == "wrong-sha":
                manifest["sha"] = "another-candidate"
            elif defect == "failed-tests":
                manifest["commands"][0]["exit_code"] = 1
            elif defect == "changed-source":
                manifest["end_tracked_digest"] = "after"
            elif defect == "changed-commit":
                manifest["end_sha"] = "another-candidate"
            elif defect == "different-bytes":
                manifest["tracked_digest"] = manifest["end_tracked_digest"] = "other"
            elif defect == "dirty":
                manifest["status"] = " M source.py"
        (root / "manifest.json").write_text(json.dumps(manifest))
        (root / ".coverage").write_bytes(b"not used: provenance must fail first")
    with pytest.raises(ValueError):
        verified_inputs(tmp_path, "candidate", "before")


@pytest.mark.parametrize("split", [False, True])
@pytest.mark.parametrize("mutation", ["none", "dirty-policy", "dirty-source", "during-report"])
def test_collector_to_combiner_fences_checkout_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str, split: bool
) -> None:
    """Exercise collection and real reporting in a disposable Git checkout.

    Args:
        tmp_path: Disposable source and synthetic platform evidence.
        monkeypatch: Scoped collector commands and platform labels; never native evidence.
        mutation: Policy or source mutation before or during report generation.
        split: Collect unit and integration data independently instead of a full-suite run.

    Raises:
        AssertionError: Collection requires a sidecar or changed bytes receive acceptance.
    """
    import os
    import subprocess
    import sys

    from scripts import combine_coverage, dev

    root = tmp_path / "checkout"
    root.mkdir()
    source = root / "sample.py"
    source.write_text("value = 1\n", encoding="utf-8")
    policy = root / "pyproject.toml"
    policy.write_text("[tool.coverage.report]\nfail_under = 80\n", encoding="utf-8")
    (root / ".gitattributes").write_text("* text=auto eol=lf\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
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
        subprocess.run(["git", *command], cwd=root, check=True, capture_output=True)
    monkeypatch.chdir(root)
    monkeypatch.setattr(dev, "ROOT", root)
    monkeypatch.setattr(combine_coverage, "ROOT", root)
    monkeypatch.setattr(dev, "CHECKS", ["test"])
    monkeypatch.setattr(
        dev, "COMMANDS", {task: [[sys.executable, "-c", "pass"]] for task in dev.TEST_TASKS}
    )
    artifacts = tmp_path / "artifacts"
    # Only labels and check commands are synthetic. The collector writes its actual schema.
    for system in ("Windows", "Linux"):
        monkeypatch.setattr(
            dev,
            "identity",
            lambda system=system: {
                **dev.checkout_state(),
                "system": system,
            },
        )
        for task in ("test-unit", "test-integration") if split else ("test",):
            evidence = artifacts / system / task
            monkeypatch.setattr(
                sys,
                "argv",
                ["dev.py", task, "--platform-coverage", "--evidence-dir", str(evidence)],
            )
            assert dev.main() == 0
            data = CoverageData(basename=str(evidence / ".coverage"))
            data.add_lines({str(source): {1}})
            data.write()
            assert not (evidence / "host.json").exists()
    initial = dev.checkout_state()
    assert len(verified_inputs(artifacts, initial["sha"], initial["tracked_digest"])) == (
        4 if split else 2
    )
    output = tmp_path / "combined"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "combine_coverage.py",
            str(artifacts),
            "--output-dir",
            str(output),
        ],
    )
    if mutation == "dirty-policy":
        policy.write_text("[tool.coverage.report]\nfail_under = 0\n", encoding="utf-8")
    elif mutation == "dirty-source":
        source.write_text("value = 2\n", encoding="utf-8")
    elif mutation == "during-report":
        original = Coverage.report

        def changing_report(self: Coverage, *args: object, **kwargs: object) -> float:
            """Mutate tracked policy after the real report reads its configuration."""
            result = original(self, *args, **kwargs)
            policy.write_text("[tool.coverage.report]\nfail_under = 0\n", encoding="utf-8")
            return result

        monkeypatch.setattr(Coverage, "report", changing_report)
    if mutation.startswith("dirty"):
        with pytest.raises(ValueError, match="must be clean"):
            combine_coverage.main()
        assert not (output / "combined.json").exists()
    else:
        assert combine_coverage.main() == (1 if mutation == "during-report" else 0)
        result = json.loads((output / "combined.json").read_text())
        assert result["required_percent"] == 80
        assert result["passed"] is (mutation == "none")
        assert result["candidate_unchanged"] is (mutation == "none")


@pytest.mark.parametrize(
    "defect",
    [
        "none",
        "missing-unit",
        "missing-integration",
        "failed-unit",
        "failed-integration",
        "duplicate-suite",
        "repeated-command",
        "mixed-full-suite",
        "wrong-sha",
        "wrong-digest",
        "dirty",
        "missing-database",
        "malformed-command",
    ],
)
def test_split_suite_evidence_requires_each_successful_native_suite(
    tmp_path: Path, defect: str
) -> None:
    """Reject incomplete, repeated or conflicting split coverage before opening databases.

    Args:
        tmp_path: Disposable synthetic evidence, never native acceptance evidence.
        defect: One invalid property to inject into the Windows evidence.

    Raises:
        AssertionError: Missing or invalid suite evidence receives combined acceptance.
    """
    for system in ("Windows", "Linux"):
        for suite in ("unit", "integration"):
            if system == "Windows" and defect == f"missing-{suite}":
                continue
            root = tmp_path / system / suite
            root.mkdir(parents=True)
            manifest = {
                "sha": "candidate",
                "end_sha": "candidate",
                "system": system,
                "status": "",
                "end_status": "",
                "tracked_digest": "bytes",
                "end_tracked_digest": "bytes",
                "candidate_unchanged": True,
                "commands": [{"task": f"test-{suite}", "exit_code": 0}],
            }
            if system == "Windows":
                if defect == f"failed-{suite}":
                    manifest["commands"][0]["exit_code"] = 1
                if suite == "integration":
                    if defect == "wrong-sha":
                        manifest["sha"] = "other"
                    elif defect == "wrong-digest":
                        manifest["tracked_digest"] = "other"
                    elif defect == "dirty":
                        manifest["end_status"] = " M sample.py"
                    elif defect == "duplicate-suite":
                        manifest["commands"][0]["task"] = "test-unit"
                    elif defect == "mixed-full-suite":
                        manifest["commands"][0]["task"] = "test"
                    elif defect == "repeated-command":
                        manifest["commands"] *= 2
                    elif defect == "malformed-command":
                        manifest["commands"] = [None]
            (root / "manifest.json").write_text(json.dumps(manifest))
            if not (system == "Windows" and suite == "unit" and defect == "missing-database"):
                (root / ".coverage").touch()
    if defect == "none":
        assert len(verified_inputs(tmp_path, "candidate", "bytes")) == 4
    else:
        with pytest.raises(ValueError):
            verified_inputs(tmp_path, "candidate", "bytes")
