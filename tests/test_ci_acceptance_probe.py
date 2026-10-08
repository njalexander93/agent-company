"""Exercise a deliberate native-test failure for AGENT-24 acceptance."""

import pytest


@pytest.mark.unit
def test_ci_acceptance_probe() -> None:
    """Fail intentionally until the acceptance probe is removed."""
    assert False, "AGENT-24 deliberate native-test failure"
