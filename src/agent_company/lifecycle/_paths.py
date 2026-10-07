"""Validate portable direct entry names before either filesystem backend sees them."""

from agent_company.lifecycle._errors import require


def entry_name(name: str) -> None:
    """Reject traversal, alternate streams, Win32 aliases and device names on every host."""
    require(isinstance(name, str) and bool(name), "UNSAFE_PATH")
    require(
        name not in {".", ".."}
        and not any(ord(char) < 32 or char in '/\\:<>"|?*' for char in name)
        and not name.endswith((".", " ")),
        "UNSAFE_PATH",
    )
    stem = name.split(".", 1)[0].upper()
    require(
        stem not in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
        and stem
        not in {f"{prefix}{digit}" for prefix in ("COM", "LPT") for digit in "123456789¹²³"},
        "UNSAFE_PATH",
    )
