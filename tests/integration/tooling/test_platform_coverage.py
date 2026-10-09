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
        data.close()
        files.append(str(path))
    coverage = Coverage(
        data_file=str(tmp_path / "combined"), config_file=str(ROOT / "pyproject.toml")
    )
    coverage.combine(files, strict=True, keep=True)
    data = coverage.get_data()
    assert data.measured_files() == {str(Path("src/agent_company/lifecycle/task_workspace.py"))}
    assert data.measured_contexts() == {"synthetic-posix", "synthetic-windows"}
    assert all(Path(path).exists() for path in files)


@pytest.mark.parametrize("split", [False, True])
@pytest.mark.parametrize(
    "mutation",
    ["none", "dirty-policy", "dirty-source", "during-report", "below-floor", "floor-repaired"],
)
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
            data.close()
            assert not (evidence / "host.json").exists()
    initial = dev.checkout_state()
    input_files = verified_inputs(artifacts, initial["sha"], initial["tracked_digest"])
    assert len(input_files) == (4 if split else 2)
    # This fixture tests combination and checkout fencing. The dedicated native
    # evidence tests exercise strict receipt/database validation separately.
    monkeypatch.setattr(
        combine_coverage,
        "valid_matrix",
        lambda *args, **kwargs: {str(index): Path(path) for index, path in enumerate(input_files)},
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
    elif mutation in {"below-floor", "floor-repaired"}:
        reported = 79.999 if mutation == "below-floor" else 80.0
        monkeypatch.setattr(Coverage, "report", lambda self: reported)
    if mutation.startswith("dirty"):
        with pytest.raises(ValueError, match="must be clean"):
            combine_coverage.main()
        assert not (output / "combined.json").exists()
    else:
        assert combine_coverage.main() == (1 if mutation in {"during-report", "below-floor"} else 0)
        result = json.loads((output / "combined.json").read_text())
        assert result["required_percent"] == 80
        if mutation in {"below-floor", "floor-repaired"}:
            assert result["coverage_percent"] == reported
        assert result["passed"] is (mutation in {"none", "floor-repaired"})
        assert result["candidate_unchanged"] is (mutation != "during-report")
