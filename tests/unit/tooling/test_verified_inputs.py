"""Validate standalone legacy full/split receipt topology."""

import json
from pathlib import Path

import pytest

from scripts.combine_coverage import verified_inputs

pytestmark = pytest.mark.unit


def test_legacy_input_rejects_nonobject_manifest(tmp_path: Path) -> None:
    """A syntactically valid JSON array is not a native suite receipt.

    Args:
        tmp_path: Disposable directory supplied by pytest for this case.
    """
    # Write a syntactically valid but non-object native manifest.
    directory = tmp_path / "Linux"
    directory.mkdir()
    (directory / "manifest.json").write_text("[]")
    # Require the legacy input validator to reject its shape.
    with pytest.raises(ValueError, match="Malformed platform evidence"):
        verified_inputs(tmp_path, "candidate", "bytes")


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
    # Create candidate native inputs with a missing or stale receipt.
    for system in ("Windows", "Linux"):
        # Omit Linux evidence to prove a single native platform cannot satisfy the aggregate.
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
        # Apply the selected corruption to Windows while leaving Linux as the valid control.
        if system == "Windows":
            # Substitute a different candidate commit in one platform receipt.
            if defect == "wrong-sha":
                manifest["sha"] = "another-candidate"
            # Record a failing suite command instead of successful native execution.
            elif defect == "failed-tests":
                manifest["commands"][0]["exit_code"] = 1
            # Change the end-of-run tracked digest to expose source mutation.
            elif defect == "changed-source":
                manifest["end_tracked_digest"] = "after"
            # Change the ending commit to expose a revision switch during measurement.
            elif defect == "changed-commit":
                manifest["end_sha"] = "another-candidate"
            # Give one platform different source bytes despite an otherwise matching commit.
            elif defect == "different-bytes":
                manifest["tracked_digest"] = manifest["end_tracked_digest"] = "other"
            # Mark the native checkout dirty before accepting its evidence.
            elif defect == "dirty":
                manifest["status"] = " M source.py"
        (root / "manifest.json").write_text(json.dumps(manifest))
        (root / ".coverage").write_bytes(b"not used: provenance must fail first")
    expected = (
        "Both native Windows and Linux"
        if defect == "missing-platform"
        else "Missing, malformed or failed commands"
        if defect == "failed-tests"
        else "Incomplete or mismatched platform evidence"
    )
    # Require exact candidate matching before any aggregate database is opened.
    with pytest.raises(ValueError, match=expected):
        verified_inputs(tmp_path, "candidate", "before")


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
    # Create Linux and Windows split suite records with controlled defects.
    for system in ("Windows", "Linux"):
        # Generate both required suites for the selected native platform.
        for suite in ("unit", "integration"):
            # Omit exactly the selected Windows suite to test matrix completeness.
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
            # Apply corruption only to the selected Windows receipt.
            if system == "Windows":
                # Fail the selected split-suite command without altering the other suite.
                if defect == f"failed-{suite}":
                    manifest["commands"][0]["exit_code"] = 1
                # Apply identity and shape corruptions to the Windows integration record.
                if suite == "integration":
                    # Assign a different candidate commit to the integration receipt.
                    if defect == "wrong-sha":
                        manifest["sha"] = "other"
                    # Assign a different tracked-byte digest to the integration receipt.
                    elif defect == "wrong-digest":
                        manifest["tracked_digest"] = "other"
                    # Mark the integration checkout dirty at collection time.
                    elif defect == "dirty":
                        manifest["end_status"] = " M sample.py"
                    # Relabel integration evidence as another unit suite to create a duplicate.
                    elif defect == "duplicate-suite":
                        manifest["commands"][0]["task"] = "test-unit"
                    # Mix full-suite and split-suite command shapes within one native platform.
                    elif defect == "mixed-full-suite":
                        manifest["commands"][0]["task"] = "test"
                    # Repeat the integration command to violate unique suite evidence.
                    elif defect == "repeated-command":
                        manifest["commands"] *= 2
                    # Replace the command record with malformed data.
                    elif defect == "malformed-command":
                        manifest["commands"] = [None]
            (root / "manifest.json").write_text(json.dumps(manifest))
            # Retain databases except for the deliberately missing Windows unit database.
            if not (system == "Windows" and suite == "unit" and defect == "missing-database"):
                (root / ".coverage").touch()
    # Accept only the complete, unmodified split-suite evidence fixture.
    if defect == "none":
        assert len(verified_inputs(tmp_path, "candidate", "bytes")) == 4
    else:
        # Select the precise rejection expected for the injected split-suite defect.
        expected = (
            "Missing, malformed or failed commands"
            if defect in {"failed-unit", "failed-integration", "malformed-command"}
            else "Incomplete or mismatched platform evidence"
            if defect in {"wrong-sha", "wrong-digest", "dirty"}
            else "Duplicate native test suite"
            if defect == "duplicate-suite"
            else "Missing coverage database"
            if defect == "missing-database"
            else "Unexpected platform or test suite"
            if defect == "repeated-command"
            else "Both native Windows and Linux"
            if defect in {"missing-unit", "missing-integration", "mixed-full-suite"}
            else "Missing, malformed or failed commands"
        )
        # Require the invalid split-suite fixture to fail with its specific diagnostic.
        with pytest.raises(ValueError, match=expected):
            verified_inputs(tmp_path, "candidate", "bytes")
