"""Check the standalone combiner's unrounded floor decision."""

import json
import sys
from pathlib import Path

import pytest

from scripts import combine_coverage as combine

pytestmark = pytest.mark.unit


def test_combiner_requires_unrounded_floor_and_unchanged_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rounding and a mid-run source change cannot produce a passing receipt.

    Args:
        tmp_path: Disposable directory supplied by pytest for this case.
        monkeypatch: Pytest fixture that restores patched dependencies after this case.
    """
    # Create a clean candidate and fake native coverage reports around the configured floor.
    root = tmp_path / "candidate"
    root.mkdir()
    (root / "pyproject.toml").write_text("[tool.coverage.report]\nfail_under = 80\n")
    monkeypatch.setattr(combine, "ROOT", root)
    monkeypatch.setattr(combine.os, "chdir", lambda _path: None)
    monkeypatch.setattr(
        combine,
        "valid_matrix",
        lambda *_args, **_kwargs: {
            ("Linux", "unit"): tmp_path / "Linux/unit/.coverage",
            ("Windows", "unit"): tmp_path / "Windows/unit/.coverage",
        },
    )
    current = {"sha": "candidate", "status": "", "tracked_digest": "bytes"}
    monkeypatch.setattr(combine.dev, "checkout_state", lambda: dict(current))
    total = [79.999]
    floor: list[object] = [80]

    class Coverage:
        """Stand in for coverage reporting while exposing the values this case checks."""

        def __init__(self, **_kwargs: object) -> None:
            """Accept Coverage constructor options while fixing the aggregate result.

            Args:
                _kwargs: Keyword inputs ignored by this test fake.
            """
            pass

        def combine(self, inputs: list[str], **_kwargs: object) -> None:
            """Write a successful combined-coverage receipt for this case.

            Args:
                inputs: Native coverage inputs passed to the fake combiner.
                _kwargs: Keyword inputs ignored by this test fake.
            """
            assert len(inputs) == 2

        def save(self) -> None:
            """Persist the mutated receipt and refresh its declared artifact hashes."""
            pass

        def report(self) -> float:
            """Return the controlled aggregate coverage percentage.

            Returns:
                Controlled aggregate percentage used to test the floor.
            """
            return total[0]

        def xml_report(self, *, outfile: str) -> None:
            """Write the fake combined XML report.

            Args:
                outfile: Destination for the generated report.
            """
            Path(outfile).write_text("<coverage/>")

        def json_report(self, *, outfile: str, **_kwargs: object) -> None:
            """Write the fake combined JSON report.

            Args:
                outfile: Destination for the generated report.
                _kwargs: Keyword inputs ignored by this test fake.
            """
            Path(outfile).write_text("{}")

        def get_option(self, _name: str) -> object:
            """Return the configured aggregate coverage floor.

            Args:
                _name: Coverage option name, unused by this fake.

            Returns:
                Controlled coverage floor for the aggregate decision.
            """
            return floor[0]

    monkeypatch.setattr(combine, "Coverage", Coverage)
    output = tmp_path / "out"
    monkeypatch.setattr(
        sys,
        "argv",
        ["combine_coverage.py", str(tmp_path / "native"), "--output-dir", str(output)],
    )
    # Vary the unrounded total and checkout state to verify the final decision.
    assert combine.main() == 1
    report = json.loads((output / "combined.json").read_text())
    assert report["coverage_percent"] == 79.999
    assert report["passed"] is False
    total[0] = 80.0
    assert combine.main() == 0
    assert json.loads((output / "combined.json").read_text())["passed"] is True
    states = iter([dict(current), {**current, "tracked_digest": "changed"}])
    monkeypatch.setattr(combine.dev, "checkout_state", lambda: next(states))
    assert combine.main() == 1
    assert json.loads((output / "combined.json").read_text())["passed"] is False
    monkeypatch.setattr(combine.dev, "checkout_state", lambda: {**current, "status": " M file.py"})
    # Reject a dirty combining checkout before reading native coverage inputs.
    with pytest.raises(ValueError, match="Combining checkout must be clean"):
        combine.main()
    monkeypatch.setattr(combine.dev, "checkout_state", lambda: dict(current))
    floor[0] = "80"
    # Reject a nonnumeric coverage floor instead of coercing it silently.
    with pytest.raises(ValueError, match="Coverage floor must be numeric"):
        combine.main()
