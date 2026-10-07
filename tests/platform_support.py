"""Create portable disposable fixture links without changing live host configuration."""

import os
import subprocess
from pathlib import Path


def environment_python(environment: Path) -> Path:
    """Locate a virtual environment interpreter using the current native OS layout.

    Args:
        environment: Virtual environment directory.

    Returns:
        Native interpreter path, without assuming the interpreter already exists.
    """
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def link_directory(link: Path, target: Path) -> None:
    """Link a disposable directory using a native junction or POSIX symbolic link.

    Args:
        link: New fixture link path.
        target: Existing directory that fixture processes need to access.

    Raises:
        OSError: Native link creation fails.
        subprocess.CalledProcessError: Windows junction creation fails.
    """
    # Junction creation needs no Developer Mode change and preserves native Windows path behavior.
    if os.name == "nt":
        command = f'"{os.environ.get("COMSPEC", "cmd.exe")}" /d /c mklink /J "{link}" "{target}"'
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    else:
        link.symlink_to(target, target_is_directory=True)


def unlink_directory(link: Path) -> None:
    """Remove only a fixture directory link, never its target.

    Args:
        link: Existing junction or symbolic link.

    Raises:
        ValueError: The supplied path is not a directory link.
    """
    if link.is_junction():
        link.rmdir()
    elif link.is_symlink():
        link.unlink()
    else:
        raise ValueError(f"Refusing to unlink an ordinary fixture directory: {link}")


def shell_command(shell: str, command: str) -> list[str]:
    """Execute an exact host command through its native shell without rewriting it.

    Args:
        shell: Explicit test shell, required to exist on the selected native runner.
        command: Checked-in hook command or canonical bootstrap command.

    Returns:
        Subprocess argv for the selected real shell.
    """
    if shell in {"powershell.exe", "pwsh.exe"}:
        return [shell, "-NoProfile", "-NonInteractive", "-Command", command]
    if shell == "cmd.exe":
        return [shell, "/d", "/c", command]
    return [shell, "-c", command]
