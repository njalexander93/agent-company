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
    """The controller reports observed child data and flags absent instrumentation.

    Args:
        tmp_path: Disposable directory supplied by pytest for this case.
        monkeypatch: Pytest fixture that restores patched dependencies after this case.
    """
    # Install fake child coverage and subprocess outcomes for both configurations.
    root = tmp_path / "candidate"
    root.mkdir()
    (root / "pyproject.toml").write_text("[tool.coverage.run]\nbranch = true\n")
    monkeypatch.setattr(smoke, "ROOT", root)
    observed: list[tuple[bool, bool]] = []

    class Completed:
        """Represent the controlled subprocess result for this case."""

        returncode = 0
        stdout = "1 passed\n"
        stderr = ""

    def run(command: list[str], **kwargs: object) -> Completed:
        """Record child launch options and return the matching probe outcome.

        Args:
            command: Exact command arguments supplied to the child or fake runner.
            kwargs: Extra library options accepted by the test fake.

        Returns:
            Controlled subprocess result with output and exit status.
        """
        test_source = Path(command[-1]).read_text()
        isolated = "'-I'" in test_source
        disabled = "disabled.toml" in " ".join(command)
        observed.append((isolated, disabled))
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        assert "COVERAGE_PROCESS_CONFIG" not in environment
        return Completed()

    class Data:
        """Represent child-only measured coverage for this case."""

        def measured_files(self) -> set[str]:
            """Expose the child-only source to the fake coverage database.

            Returns:
                The child-only source name expected in the probe receipt.
            """
            return {"child_only.py"}

        def arcs(self, _path: str) -> list[tuple[int, int]]:
            """Expose child branch arcs unless instrumentation is disabled.

            Args:
                _path: Measured path, unused by this fake.

            Returns:
                The fixture records consumed by the test.
            """
            return [] if observed[-1][1] else [(2, 3), (2, 4)]

        def lines(self, _path: str) -> list[int]:
            """Expose child lines unless instrumentation is disabled.

            Args:
                _path: Measured path, unused by this fake.

            Returns:
                The fixture records consumed by the test.
            """
            return [] if observed[-1][1] else [1, 2, 3, 4]

        def has_arcs(self) -> bool:
            """Report branch-capable fake coverage data.

            Returns:
                Boolean decision exercised by this test.
            """
            return True

    class Coverage:
        """Stand in for coverage reporting while exposing the values this case checks."""

        def __init__(self, **_kwargs: object) -> None:
            """Accept Coverage constructor options for the fake child database.

            Args:
                _kwargs: Keyword inputs ignored by this test fake.
            """
            pass

        def load(self) -> None:
            """Accept the coverage database load requested by the probe."""
            pass

        def get_data(self) -> Data:
            """Return the fake child-only coverage database.

            Returns:
                Child-only coverage rows supplied to the probe.
            """
            return Data()

    monkeypatch.setattr(smoke.subprocess, "run", run)
    monkeypatch.setattr(smoke, "Coverage", Coverage)
    positive = smoke.probe(isolated_child=True)
    # Compare the positive instrumentation proof with the disabled control.
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
    """The CLI returns failure when child proof is absent and writes the receipt.

    Args:
        tmp_path: Disposable directory supplied by pytest for this case.
        monkeypatch: Pytest fixture that restores patched dependencies after this case.
    """
    # Run the smoke entry point with a controlled failing probe.
    output = tmp_path / "reports" / "smoke.json"
    monkeypatch.setattr(sys, "argv", ["coverage_smoke.py", "--output", str(output)])
    monkeypatch.setattr(smoke, "probe", lambda: {"passed": False, "child_lines": []})
    # Repair the probe and compare persisted status receipts.
    assert smoke.main() == 1
    assert json.loads(output.read_text())["passed"] is False
    monkeypatch.setattr(smoke, "probe", lambda: {"passed": True, "child_lines": [1, 2]})
    assert smoke.main() == 0
    assert json.loads(output.read_text())["child_lines"] == [1, 2]
