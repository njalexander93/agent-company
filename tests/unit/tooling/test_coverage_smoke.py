"""Check the child-coverage probe controller and receipt output."""

import json
import sys
from pathlib import Path

import pytest

from scripts import coverage_smoke as smoke

pytestmark = pytest.mark.unit


def test_probe_reports_child_line_arc_proof_and_negative_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The controller reports observed child data and flags absent instrumentation."""
    root = tmp_path / "candidate"
    root.mkdir()
    (root / "pyproject.toml").write_text("[tool.coverage.run]\nbranch = true\n")
    monkeypatch.setattr(smoke, "ROOT", root)
    observed: list[tuple[bool, bool]] = []

    class Completed:
        returncode = 0
        stdout = "1 passed\n"
        stderr = ""

    def run(command: list[str], **kwargs: object) -> Completed:
        test_source = Path(command[-1]).read_text()
        isolated = "'-I'" in test_source
        disabled = "disabled.toml" in " ".join(command)
        observed.append((isolated, disabled))
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        assert "COVERAGE_PROCESS_CONFIG" not in environment
        return Completed()

    class Data:
        def measured_files(self) -> set[str]:
            return {"child_only.py"}

        def arcs(self, _path: str) -> list[tuple[int, int]]:
            return [] if observed[-1][1] else [(2, 3), (2, 4)]

        def lines(self, _path: str) -> list[int]:
            return [] if observed[-1][1] else [1, 2, 3, 4]

        def has_arcs(self) -> bool:
            return True

    class Coverage:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def load(self) -> None:
            pass

        def get_data(self) -> Data:
            return Data()

    monkeypatch.setattr(smoke.subprocess, "run", run)
    monkeypatch.setattr(smoke, "Coverage", Coverage)
    positive = smoke.probe(isolated_child=True)
    assert positive["passed"] is True
    assert positive["child_lines"] == [1, 2, 3, 4]
    assert positive["child_arcs"] == [[2, 3], [2, 4]]
    negative = smoke.probe(instrumented=False)
    assert negative["passed"] is False
    assert negative["instrumented"] is False
    assert observed == [(True, False), (False, True)]


def test_smoke_main_writes_failed_and_repaired_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI returns failure when child proof is absent and writes the receipt."""
    output = tmp_path / "reports" / "smoke.json"
    monkeypatch.setattr(sys, "argv", ["coverage_smoke.py", "--output", str(output)])
    monkeypatch.setattr(smoke, "probe", lambda: {"passed": False, "child_lines": []})
    assert smoke.main() == 1
    assert json.loads(output.read_text())["passed"] is False
    monkeypatch.setattr(smoke, "probe", lambda: {"passed": True, "child_lines": [1, 2]})
    assert smoke.main() == 0
    assert json.loads(output.read_text())["child_lines"] == [1, 2]
