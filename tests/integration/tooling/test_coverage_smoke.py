"""Prove child-only instrumentation succeeds and its disabled control fails."""

import pytest

from scripts.coverage_smoke import probe

pytestmark = pytest.mark.integration


def test_child_only_smoke_distinguishes_enabled_and_disabled_patch() -> None:
    """A passing nested test cannot disguise a child-only coverage miss."""
    # Run the child-only probe with normal, isolated, and disabled startup hooks.
    enabled = probe()
    isolated = probe(isolated_child=True)
    disabled = probe(instrumented=False)
    # Require branch evidence only when instrumentation is active.
    assert enabled["test_exit_code"] == disabled["test_exit_code"] == 0
    assert enabled["passed"] is True
    assert isolated["passed"] is True
    assert disabled["passed"] is False
    assert {1, 2, 3, 4} <= set(enabled["child_lines"])
    assert disabled["child_lines"] == []
