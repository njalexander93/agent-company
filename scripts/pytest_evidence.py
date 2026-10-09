"""Capture exact pytest collection and outcomes for native suite evidence."""

import json
import os
from pathlib import Path

import pytest

_items: list[dict[str, object]] = []
_reports: dict[str, list[dict[str, object]]] = {}
_collection_errors: list[str] = []


def pytest_sessionstart(session: pytest.Session) -> None:
    """Reset process-local records before collection begins.

    Args:
        session: Active pytest session whose collection or outcome is recorded.
    """
    # Discard state from any earlier pytest session in this process.
    _items.clear()
    _reports.clear()
    _collection_errors.clear()


def pytest_collection_finish(session: pytest.Session) -> None:
    """Retain every collected node and its suite markers.

    Args:
        session: Active pytest session whose collection or outcome is recorded.
    """
    # Capture exact collected node IDs, suite markers, and skip reasons.
    _items.extend(
        {
            "nodeid": item.nodeid,
            "path": str(item.path),
            "suite_markers": sorted(
                {
                    marker.name
                    for marker in item.iter_markers()
                    if marker.name in {"unit", "integration"}
                }
            ),
            "skip_reasons": [
                str(marker.kwargs.get("reason", ""))
                for marker in item.iter_markers()
                if marker.name in {"skip", "skipif"}
            ],
        }
        for item in session.items
    )


def pytest_collectreport(report: pytest.CollectReport) -> None:
    """Retain import and collection failures even when no test executes.

    Args:
        report: Coverage report or pytest hook report to inspect.
    """
    # Record import and collection failures even without executed cases.
    if report.failed:
        _collection_errors.append(str(report.longrepr))


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Retain setup, call and teardown outcomes per node.

    Args:
        report: Coverage report or pytest hook report to inspect.
    """
    # Attach this report to the collected node and phase history.
    previous = _reports.setdefault(report.nodeid, [])
    entry: dict[str, object] = {
        "phase": report.when,
        "outcome": report.outcome,
        "seconds": round(report.duration, 6),
    }
    # Retain subtest context when pytest supplies it.
    if hasattr(report, "context"):
        entry["subtest_index"] = sum("subtest_index" in row for row in previous)
        entry["subtest"] = report.head_line
    # Retain expected-failure metadata for policy validation.
    if hasattr(report, "wasxfail"):
        entry["wasxfail"] = str(report.wasxfail)
    # Retain native skip information for the reviewed skip inventory.
    if report.skipped:
        entry["reason"] = str(report.longrepr)
    previous.append(entry)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Write a partial-safe receipt even when pytest exits nonzero.

    Args:
        session: Active pytest session whose collection or outcome is recorded.
        exitstatus: Final pytest exit status written into the receipt.
    """
    # Honor the configured receipt path only when evidence was requested.
    destination = os.environ.get("AGENT_COMPANY_PYTEST_EVIDENCE")
    # Leave ordinary pytest sessions without a generated receipt.
    if not destination:
        return
    # Persist a receipt even when collection or execution failed.
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "exit_code": int(exitstatus),
                "collected": _items,
                "reports": _reports,
                "collection_errors": _collection_errors,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
