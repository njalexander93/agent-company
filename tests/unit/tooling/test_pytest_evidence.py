"""Check the pytest receipt emitted for native quality validation."""

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import pytest_evidence as evidence

pytestmark = pytest.mark.unit


def test_receipt_keeps_collection_errors_and_subtest_outcomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A partial run retains exact nodes, markers, subtests, and failure causes."""
    destination = tmp_path / "receipt.json"
    monkeypatch.setenv("AGENT_COMPANY_PYTEST_EVIDENCE", str(destination))
    # pytest imports plugins before pytest-cov starts tracing the test body.
    importlib.reload(evidence)
    evidence.pytest_sessionstart(SimpleNamespace())

    class Item:
        nodeid = "tests/unit/tooling/test_sample.py::test_case"
        path = Path("tests/unit/tooling/test_sample.py")

        def iter_markers(self) -> list[SimpleNamespace]:
            return [
                SimpleNamespace(name="unit", kwargs={}),
                SimpleNamespace(name="skipif", kwargs={"reason": "native only"}),
                SimpleNamespace(name="slow", kwargs={}),
            ]

    evidence.pytest_collection_finish(SimpleNamespace(items=[Item()]))
    evidence.pytest_collectreport(SimpleNamespace(failed=False, longrepr="ignored"))
    evidence.pytest_collectreport(SimpleNamespace(failed=True, longrepr="import failed"))
    node = Item.nodeid
    evidence.pytest_runtest_logreport(
        SimpleNamespace(nodeid=node, when="setup", outcome="passed", duration=0.01, skipped=False)
    )
    evidence.pytest_runtest_logreport(
        SimpleNamespace(
            nodeid=node,
            when="call",
            outcome="failed",
            duration=0.02,
            skipped=False,
            context="subtest",
            head_line="case 1",
            wasxfail="expected failure",
        )
    )
    evidence.pytest_runtest_logreport(
        SimpleNamespace(
            nodeid=node,
            when="call",
            outcome="skipped",
            duration=0.03,
            skipped=True,
            context="subtest",
            head_line="case 2",
            longrepr="skip reason",
        )
    )
    evidence.pytest_sessionfinish(SimpleNamespace(), 1)
    receipt = json.loads(destination.read_text())
    assert receipt["exit_code"] == 1
    assert receipt["collection_errors"] == ["import failed"]
    assert receipt["collected"][0]["suite_markers"] == ["unit"]
    assert receipt["collected"][0]["skip_reasons"] == ["native only"]
    assert receipt["reports"][node][1]["subtest_index"] == 0
    assert receipt["reports"][node][1]["wasxfail"] == "expected failure"
    assert receipt["reports"][node][2]["subtest_index"] == 1
    assert receipt["reports"][node][2]["reason"] == "skip reason"

    evidence.pytest_sessionstart(SimpleNamespace())
    monkeypatch.delenv("AGENT_COMPANY_PYTEST_EVIDENCE")
    evidence.pytest_sessionfinish(SimpleNamespace(), 0)
    assert evidence._items == []
    assert evidence._reports == {}
    assert evidence._collection_errors == []
