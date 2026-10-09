"""Validate standalone legacy full/split receipt topology."""

import json
from pathlib import Path

import pytest

from scripts.combine_coverage import verified_inputs

pytestmark = pytest.mark.unit


def test_legacy_input_rejects_nonobject_manifest(tmp_path: Path) -> None:
    """A syntactically valid JSON array is not a native suite receipt."""
    directory = tmp_path / "Linux"
    directory.mkdir()
    (directory / "manifest.json").write_text("[]")
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
    expected = (
        "Both native Windows and Linux"
        if defect == "missing-platform"
        else "Missing, malformed or failed commands"
        if defect == "failed-tests"
        else "Incomplete or mismatched platform evidence"
    )
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
        with pytest.raises(ValueError, match=expected):
            verified_inputs(tmp_path, "candidate", "bytes")
