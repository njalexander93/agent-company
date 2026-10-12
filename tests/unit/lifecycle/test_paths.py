"""Validate portable entry names without filesystem or process access."""

import pytest

from nplet.lifecycle._errors import WorkspaceError
from nplet.lifecycle._paths import entry_name

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "name",
    [
        "../outside",
        "x/y",
        "x\\y",
        "x:stream",
        "..",
        ".",
        "",
        "note.md.",
        "note.md ",
        "CON",
        "con.md",
        "aux.md",
        "NUL",
        "COM1.md",
        "LPT9",
        "COM¹",
        "x\0y",
        "x?y",
    ],
)
def test_portable_name_rejection(name: str) -> None:
    """Reject traversal, device names and platform aliases as pure input validation."""
    with pytest.raises(WorkspaceError, match="UNSAFE_PATH"):
        entry_name(name)
