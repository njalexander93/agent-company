"""Check exact native record topology before coverage combination."""

from pathlib import Path

import pytest

from scripts import coverage_evidence as evidence

pytestmark = pytest.mark.unit


def test_matrix_rejects_missing_and_duplicate_native_suites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each platform needs one unit and one integration record, with no duplicates."""
    root = tmp_path / "native"

    def validate(directory: Path, **_kwargs: object) -> tuple[str, str, Path]:
        return directory.parent.name, directory.name, directory / ".coverage"

    monkeypatch.setattr(evidence, "valid_suite", validate)
    kwargs = {
        "sha": "candidate",
        "tracked_digest": "bytes",
        "config_digest": "config",
        "tool_versions": {},
    }
    for system in evidence.SYSTEMS:
        for suite in ("unit", "integration"):
            directory = root / system / suite
            directory.mkdir(parents=True)
            (directory / "manifest.json").write_text("{}")
    records = evidence.valid_matrix(root, **kwargs)
    assert len(records) == 6
    (root / "Linux/integration/manifest.json").unlink()
    with pytest.raises(ValueError, match="Incomplete native matrix"):
        evidence.valid_matrix(root, **kwargs)
    (root / "Linux/integration/manifest.json").write_text("{}")
    duplicate = root / "extra/Linux/unit"
    duplicate.mkdir(parents=True)
    (duplicate / "manifest.json").write_text("{}")
    with pytest.raises(ValueError, match="Duplicate native suite"):
        evidence.valid_matrix(root, **kwargs)


def test_full_suite_is_allowed_only_for_standalone_combiner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The compatibility route accepts full receipts only by explicit opt-in."""
    root = tmp_path / "native"

    def validate(directory: Path, **_kwargs: object) -> tuple[str, str, Path]:
        return directory.parent.name, "test", directory / ".coverage"

    monkeypatch.setattr(evidence, "valid_suite", validate)
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
    with pytest.raises(ValueError, match="Incomplete native matrix"):
        evidence.valid_matrix(root, **kwargs)
    assert len(evidence.valid_matrix(root, allow_full=True, **kwargs)) == 2
