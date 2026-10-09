"""Capture exact pytest collection and outcomes for native suite evidence."""

import json
import os
from pathlib import Path

import pytest

_items: list[dict[str, object]] = []
_reports: dict[str, list[dict[str, object]]] = {}
_collection_errors: list[str] = []


def pytest_sessionstart(session: pytest.Session) -> None:
    """Reset process-local records before collection begins."""
    _items.clear()
    _reports.clear()
    _collection_errors.clear()


def pytest_collection_finish(session: pytest.Session) -> None:
    """Retain every collected node and its suite markers."""
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
    """Retain import and collection failures even when no test executes."""
    if report.failed:
        _collection_errors.append(str(report.longrepr))


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Retain setup, call and teardown outcomes per node."""
    previous = _reports.setdefault(report.nodeid, [])
    entry: dict[str, object] = {
        "phase": report.when,
        "outcome": report.outcome,
        "seconds": round(report.duration, 6),
    }
    if hasattr(report, "context"):
        entry["subtest_index"] = sum("subtest_index" in row for row in previous)
        entry["subtest"] = report.head_line
    if hasattr(report, "wasxfail"):
        entry["wasxfail"] = str(report.wasxfail)
    if report.skipped:
        entry["reason"] = str(report.longrepr)
    previous.append(entry)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Write a partial-safe receipt even when pytest exits nonzero."""
    destination = os.environ.get("AGENT_COMPANY_PYTEST_EVIDENCE")
    if not destination:
        return
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
