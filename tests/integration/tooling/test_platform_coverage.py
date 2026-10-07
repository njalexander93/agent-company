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
    assert data.measured_files() == {"src/agent_company/lifecycle/task_workspace.py"}
    assert data.measured_contexts() == {"synthetic-posix", "synthetic-windows"}
    assert all(Path(path).exists() for path in files)


@pytest.mark.parametrize(
    "defect",
    ["missing-platform", "wrong-sha", "failed-tests", "dirty", "changed-source", "changed-commit"],
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
        host = {"sha": "candidate", "system": system}
        if system == "Windows":
            if defect == "wrong-sha":
                manifest["sha"] = "another-candidate"
            elif defect == "failed-tests":
                manifest["commands"][0]["exit_code"] = 1
            elif defect == "changed-source":
                manifest["end_tracked_digest"] = "after"
            elif defect == "changed-commit":
                manifest["end_sha"] = "another-candidate"
            elif defect == "dirty":
                manifest["status"] = " M source.py"
        (root / "manifest.json").write_text(json.dumps(manifest))
        (root / "host.json").write_text(json.dumps(host))
        (root / ".coverage").write_bytes(b"not used: provenance must fail first")
    with pytest.raises(ValueError):
        verified_inputs(tmp_path, "candidate")
