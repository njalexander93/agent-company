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
    """Rounding and a mid-run source change cannot produce a passing receipt."""
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
        def __init__(self, **_kwargs: object) -> None:
            pass

        def combine(self, inputs: list[str], **_kwargs: object) -> None:
            assert len(inputs) == 2

        def save(self) -> None:
            pass

        def report(self) -> float:
            return total[0]

        def xml_report(self, *, outfile: str) -> None:
            Path(outfile).write_text("<coverage/>")

        def json_report(self, *, outfile: str, **_kwargs: object) -> None:
            Path(outfile).write_text("{}")

        def get_option(self, _name: str) -> object:
            return floor[0]

    monkeypatch.setattr(combine, "Coverage", Coverage)
    output = tmp_path / "out"
    monkeypatch.setattr(
        sys,
        "argv",
        ["combine_coverage.py", str(tmp_path / "native"), "--output-dir", str(output)],
    )
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
    with pytest.raises(ValueError, match="Combining checkout must be clean"):
        combine.main()
    monkeypatch.setattr(combine.dev, "checkout_state", lambda: dict(current))
    floor[0] = "80"
    with pytest.raises(ValueError, match="Coverage floor must be numeric"):
        combine.main()
