"""Check exact native record topology before coverage combination."""

import json
from pathlib import Path

import pytest
from coverage import Coverage, CoverageData

from scripts import coverage_evidence as evidence

pytestmark = pytest.mark.unit


def test_report_file_index_rejects_malformed_paths() -> None:
    """Only unambiguous, nonblank native source names may enter the lookup."""
    # Reject a coverage report whose file rows are not a mapping.
    with pytest.raises(ValueError, match="malformed coverage file rows"):
        evidence.normalized_report_files({"files": ["src/sample.py"]})
    # Reject an empty coverage source name before path normalization.
    with pytest.raises(ValueError, match="malformed coverage source path"):
        evidence.normalized_report_files({"files": {"": {}}})


def test_regeneration_retains_a_measured_file_with_no_arcs(tmp_path: Path) -> None:
    """An empty native module remains measured even when it is visited first.

    Args:
        tmp_path: Disposable directory supplied by pytest for this case.
    """
    # Create a measured source that has no executed branch arcs.
    source = tmp_path / "src/agent_company/sample.py"
    source.parent.mkdir(parents=True)
    source.write_text("value = 1\n")
    (tmp_path / "pyproject.toml").write_text("[tool.coverage.run]\nbranch = true\n")
    database = CoverageData(basename=str(tmp_path / ".coverage"))
    database.add_arcs({})
    database.touch_file("src/agent_company/sample.py")
    database.write()
    database.close()
    database = CoverageData(basename=str(tmp_path / ".coverage"))
    database.read()
    report = evidence.regenerated_report(database, tmp_path, False)
    database.close()
    row = report["files"]["src/agent_company/sample.py"]
    # Require its source row to survive report regeneration.
    assert row["executed_lines"] == []
    assert row["missing_lines"] == [1]


@pytest.mark.parametrize(
    ("measured", "reason"),
    [
        ("../outside.py", "unsafe or duplicate measured source"),
        ("src/agent_company/missing.py", "missing or unsafe source file"),
    ],
)
def test_regeneration_rejects_unsafe_or_absent_source(
    tmp_path: Path, measured: str, reason: str
) -> None:
    """A recorded arc cannot authorize a source outside the exact checkout.

    Args:
        tmp_path: Disposable directory supplied by pytest for this case.
        measured: Measured paths or arcs supplied to the fake coverage object.
        reason: Expected diagnostic for the injected defect.
    """
    # Supply measured paths that escape or are absent from the checkout.
    (tmp_path / "pyproject.toml").write_text("[tool.coverage.run]\nbranch = true\n")
    database = CoverageData(basename=str(tmp_path / ".coverage"))
    database.add_arcs({measured: [(-1, 1), (1, -1)]})
    database.write()
    database.close()
    database = CoverageData(basename=str(tmp_path / ".coverage"))
    database.read()
    # Require source validation to reject each unsafe path.
    with pytest.raises(ValueError, match=reason):
        evidence.regenerated_report(database, tmp_path, False)
    database.close()


def test_regeneration_rejects_output_path_aliases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reporter emitting absolute and relative aliases cannot double-count one file.

    Args:
        tmp_path: Disposable directory supplied by pytest for this case.
        monkeypatch: Pytest fixture that restores patched dependencies after this case.
    """
    # Create source and branch data with colliding native path spellings.
    source = tmp_path / "src/agent_company/sample.py"
    source.parent.mkdir(parents=True)
    source.write_text("value = 1\n")
    (tmp_path / "pyproject.toml").write_text("[tool.coverage.run]\nbranch = true\n")
    database = CoverageData(basename=str(tmp_path / ".coverage"))
    database.add_arcs({"src/agent_company/sample.py": [(-1, 1), (1, -1)]})
    database.write()
    database.close()
    database = CoverageData(basename=str(tmp_path / ".coverage"))
    database.read()
    original = Coverage.json_report

    def alias_report(self: Coverage, *, outfile: str, **kwargs: object) -> float:
        """Duplicate one measured source under a conflicting normalized report path.

        Args:
            self: Coverage reporter whose JSON rows are being aliased.
            outfile: Destination for the generated report.
            kwargs: Extra library options accepted by the test fake.

        Returns:
            The original report percentage after its file rows are changed.
        """
        result = original(self, outfile=outfile, **kwargs)
        path = Path(outfile)
        report = json.loads(path.read_text())
        row = next(iter(report["files"].values()))
        report["files"]["src/agent_company/sample.py"] = row
        path.write_text(json.dumps(report))
        return result

    monkeypatch.setattr(Coverage, "json_report", alias_report)
    # Require regeneration to reject aliases before accepting report rows.
    with pytest.raises(ValueError, match="duplicate regenerated source"):
        evidence.regenerated_report(database, tmp_path, False)
    database.close()


def test_matrix_rejects_missing_and_duplicate_native_suites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each platform needs one unit and one integration record, with no duplicates.

    Args:
        tmp_path: Disposable directory supplied by pytest for this case.
        monkeypatch: Pytest fixture that restores patched dependencies after this case.
    """
    # Populate split native records with one deliberate matrix defect.
    root = tmp_path / "native"

    def validate(directory: Path, **_kwargs: object) -> tuple[str, str, Path]:
        """Return the native suite identity expected by this matrix case.

        Args:
            directory: Directory containing the candidate evidence or test output.
            _kwargs: Keyword inputs ignored by this test fake.

        Returns:
            Native system, suite name, and database path.
        """
        return directory.parent.name, directory.name, directory / ".coverage"

    monkeypatch.setattr(evidence, "valid_suite", validate)
    kwargs = {
        "sha": "candidate",
        "tracked_digest": "bytes",
        "config_digest": "config",
        "tool_versions": {},
    }
    # Construct native suite records for all supported operating systems.
    for system in evidence.SYSTEMS:
        # Write independent unit and integration record locations for this platform.
        for suite in ("unit", "integration"):
            directory = root / system / suite
            directory.mkdir(parents=True)
            (directory / "manifest.json").write_text("{}")
    records = evidence.valid_matrix(root, **kwargs)
    # Confirm the complete matrix before removing or duplicating a suite.
    assert len(records) == 6
    (root / "Linux/integration/manifest.json").unlink()
    # Reject the matrix after removing one required native suite.
    with pytest.raises(ValueError, match="Incomplete native matrix"):
        evidence.valid_matrix(root, **kwargs)
    (root / "Linux/integration/manifest.json").write_text("{}")
    duplicate = root / "extra/Linux/unit"
    duplicate.mkdir(parents=True)
    (duplicate / "manifest.json").write_text("{}")
    # Reject duplicated evidence for an already represented platform/suite pair.
    with pytest.raises(ValueError, match="Duplicate native suite"):
        evidence.valid_matrix(root, **kwargs)


def test_full_suite_is_allowed_only_for_standalone_combiner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The compatibility route accepts full receipts only by explicit opt-in.

    Args:
        tmp_path: Disposable directory supplied by pytest for this case.
        monkeypatch: Pytest fixture that restores patched dependencies after this case.
    """
    # Build a legacy full-suite receipt for each requested system.
    root = tmp_path / "native"

    def validate(directory: Path, **_kwargs: object) -> tuple[str, str, Path]:
        """Return the native suite identity expected by this matrix case.

        Args:
            directory: Directory containing the candidate evidence or test output.
            _kwargs: Keyword inputs ignored by this test fake.

        Returns:
            Native system, suite name, and database path.
        """
        return directory.parent.name, "test", directory / ".coverage"

    monkeypatch.setattr(evidence, "valid_suite", validate)
    # Construct the historical two-platform full-suite input shape.
    for system in ("Linux", "Windows"):
        directory = root / system / "full"
        directory.mkdir(parents=True)
        (directory / "manifest.json").write_text("{}")
    kwargs = {
        "sha": "candidate",
        "tracked_digest": "bytes",
        "config_digest": "config",
        "tool_versions": {},
        "systems": {"Linux", "Windows"},
    }
    # Require explicit permission for full receipts and reject mixed shapes.
    with pytest.raises(ValueError, match="Incomplete native matrix"):
        evidence.valid_matrix(root, **kwargs)
    assert len(evidence.valid_matrix(root, allow_full=True, **kwargs)) == 2
