"""Deliberately fail AGENT-24's CI acceptance probe; removed after validation."""

import pytest

pytestmark = pytest.mark.unit


def test_required_gate_rejects_failed_tests() -> None:
    """Fail deliberately so the native test checks must report failure."""
    assert False, "Deliberate AGENT-24 required-check acceptance probe"
