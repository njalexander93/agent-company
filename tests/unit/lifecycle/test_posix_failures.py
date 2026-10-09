"""Model POSIX failure exits; native filesystem behavior is tested separately."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_company.lifecycle._errors import WorkspaceError

# Select the native POSIX backend only when available.
if os.name == "posix":
    # Import POSIX descriptor operations for failure injection.
    from agent_company.lifecycle import _filesystem_posix as posix
else:
    # Keep the backend absent on unsupported hosts.
    posix = None

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(
        os.name != "posix",
        reason="POSIX fcntl and descriptor APIs are unavailable on Windows",
    ),
]


def handle() -> posix.Directory:
    """Create a non-owning test handle for injected syscall outcomes.

    Returns:
        Native handle supplied by the fake open call.
    """
    # Supply a descriptor whose ownership check will fail.
    directory = posix.Directory.__new__(posix.Directory)
    directory.fd = 10
    directory.identity = [1, 2]
    return directory


def test_directory_constructor_closes_unsafe_fd(monkeypatch: pytest.MonkeyPatch) -> None:
    """Release a supplied descriptor when ownership validation fails.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake fstat, close calls.
    closed: list[int] = []
    monkeypatch.setattr(
        posix.os, "fstat", lambda _fd: SimpleNamespace(st_mode=stat.S_IFREG, st_uid=os.getuid())
    )
    monkeypatch.setattr(posix.os, "close", closed.append)
    # Reject the unsafe descriptor and verify it is closed.
    with pytest.raises(WorkspaceError) as captured:
        posix.Directory(11)
    # Confirm the rejected operation reports UNSAFE_PATH.
    assert captured.value.code == "UNSAFE_PATH"
    assert closed == [11]


def test_absolute_rejects_relative_or_parent_path_before_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject traversal without opening even the root directory.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake open calls.
    monkeypatch.setattr(posix.os, "open", lambda *_args, **_kwargs: pytest.fail("opened path"))
    # Repeat the check for each path value.
    for path in (Path("relative"), Path("/tmp/../outside")):
        # Reject traversal before opening the root path.
        with pytest.raises(WorkspaceError) as captured:
            posix.Directory.absolute(path)
        # Confirm the rejected operation reports UNSAFE_PATH.
        assert captured.value.code == "UNSAFE_PATH"


def test_exists_reports_absent_entry_without_following_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use no-follow stat and distinguish absence from other errors.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Record collaborator calls for the assertion.
    observed: list[tuple[str, bool]] = []

    def stat_entry(name: str, *, dir_fd: int, follow_symlinks: bool) -> object:
        """Record direct lookup flags and model an absent entry.

        Args:
            name: Requested file, directory, or control-entry name.
            dir_fd: Directory descriptor at the POSIX boundary.
            follow_symlinks: Whether the native call follows symbolic links.

        Returns:
            Native metadata supplied by the fake stat call.

        Raises:
            FileNotFoundError: When the simulated entry is absent.
        """
        assert dir_fd == 10
        observed.append((name, follow_symlinks))
        # Model an absent entry at the native lookup boundary.
        raise FileNotFoundError(name)

    # Install fake stat calls.
    monkeypatch.setattr(posix.os, "stat", stat_entry)
    # Check recorded observed against the required side effects.
    assert handle().exists("note.md") is False
    assert observed == [("note.md", False)]


def test_read_rejects_unsafe_file_and_closes_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject a multi-link file before reading bytes and release its descriptor.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake open, fstat, fdopen and related calls.
    closed: list[int] = []
    monkeypatch.setattr(posix.os, "open", lambda *_args, **_kwargs: 11)
    monkeypatch.setattr(
        posix.os,
        "fstat",
        lambda _fd: SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_nlink=2, st_uid=os.getuid()),
    )
    monkeypatch.setattr(
        posix.os, "fdopen", lambda *_args, **_kwargs: pytest.fail("read unsafe file")
    )
    monkeypatch.setattr(posix.os, "close", closed.append)
    # Reject a multi-link file before reading its bytes.
    with pytest.raises(WorkspaceError) as captured:
        handle().read("note.md")
    # Confirm the rejected operation reports UNSAFE_PATH.
    assert captured.value.code == "UNSAFE_PATH"
    assert closed == [11]


def test_write_refuses_unsafe_existing_target_before_temp_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Perform no replacement when existing target validation fails.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake exists calls.
    monkeypatch.setattr(posix.Directory, "exists", lambda _self, _name: True)

    def reject(_self: posix.Directory, _name: str) -> bytes:
        """Model the prior target failing ownership or link validation.

        Args:
            _self: Unused self argument accepted by this fake.
            _name: Unused name argument accepted by this fake.

        Raises:
            WorkspaceError: When target validation rejects the unsafe path.
        """
        raise WorkspaceError("UNSAFE_PATH")

    # Install fake read, open calls.
    monkeypatch.setattr(posix.Directory, "read", reject)
    monkeypatch.setattr(posix.os, "open", lambda *_args, **_kwargs: pytest.fail("created temp"))
    # Reject the unsafe existing target before creating a temp file.
    with pytest.raises(WorkspaceError) as captured:
        handle().write("note.md", b"replacement")
    # Confirm the rejected operation reports UNSAFE_PATH.
    assert captured.value.code == "UNSAFE_PATH"


def test_issue_view_rejects_existing_link_to_different_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve an existing issue link when its target differs.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake exists, stat, readlink and related calls.
    monkeypatch.setattr(posix.Directory, "exists", lambda _self, _name: True)
    monkeypatch.setattr(
        posix.os, "stat", lambda *_args, **_kwargs: SimpleNamespace(st_mode=stat.S_IFLNK)
    )
    monkeypatch.setattr(posix.os, "readlink", lambda *_args, **_kwargs: "/other/issue")
    monkeypatch.setattr(posix.os, "symlink", lambda *_args, **_kwargs: pytest.fail("replaced link"))
    # Preserve the existing link when its target differs.
    with pytest.raises(WorkspaceError) as captured:
        handle().issue_view("AGENT-30", "/expected/issue")
    # Confirm the rejected operation reports UNSAFE_PATH.
    assert captured.value.code == "UNSAFE_PATH"
