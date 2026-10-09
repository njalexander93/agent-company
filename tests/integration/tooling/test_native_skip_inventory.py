"""Keep exact native skip IDs aligned with real parameterized collection."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.coverage_evidence import APPROVED_NATIVE_SKIPS
from tests.support import ROOT

pytestmark = pytest.mark.integration

SELECTORS = (
    "tests/integration/adapters/test_platform_processes.py::"
    "test_windows_bootstrap_survives_literal_paths_and_json",
    "tests/integration/lifecycle/test_filesystem.py::test_windows_noncanonical_roots_are_rejected",
)


def test_parameterized_native_skip_ledger_matches_collection(tmp_path: Path) -> None:
    """A new native parameter needs its own reviewed skip ID before acceptance."""
    output = tmp_path / "collection.json"
    environment = os.environ.copy()
    environment["AGENT_COMPANY_PYTEST_EVIDENCE"] = str(output)
    environment.pop("COVERAGE_PROCESS_CONFIG", None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "scripts.pytest_evidence",
            *SELECTORS,
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    collected = json.loads(output.read_text(encoding="utf-8"))["collected"]
    actual = {item["nodeid"] for item in collected}
    approved = {nodeid for nodeid in APPROVED_NATIVE_SKIPS if nodeid.startswith(SELECTORS)}
    assert actual == approved
    assert len(actual) == 8
    for item in collected:
        assert item["skip_reasons"] == [APPROVED_NATIVE_SKIPS[item["nodeid"]][1]]
