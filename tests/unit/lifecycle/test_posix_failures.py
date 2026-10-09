"""Model POSIX failure exits; native filesystem behavior is tested separately."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_company.lifecycle._errors import WorkspaceError

if os.name == "posix":
    from agent_company.lifecycle import _filesystem_posix as posix
else:
    posix = None

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(
        os.name != "posix",
        reason="POSIX fcntl and descriptor APIs are unavailable on Windows",
    ),
]


def handle() -> posix.Directory:
    """Create a non-owning test handle for injected syscall outcomes."""
    directory = posix.Directory.__new__(posix.Directory)
    directory.fd = 10
    directory.identity = [1, 2]
    return directory


def test_directory_constructor_closes_unsafe_fd(monkeypatch: pytest.MonkeyPatch) -> None:
    """Release a supplied descriptor when ownership validation fails."""
    closed: list[int] = []
    monkeypatch.setattr(
        posix.os, "fstat", lambda _fd: SimpleNamespace(st_mode=stat.S_IFREG, st_uid=os.getuid())
    )
    monkeypatch.setattr(posix.os, "close", closed.append)
    with pytest.raises(WorkspaceError) as captured:
        posix.Directory(11)
    assert captured.value.code == "UNSAFE_PATH"
    assert closed == [11]


def test_absolute_rejects_relative_or_parent_path_before_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject traversal without opening even the root directory."""
    monkeypatch.setattr(posix.os, "open", lambda *_args, **_kwargs: pytest.fail("opened path"))
    for path in (Path("relative"), Path("/tmp/../outside")):
        with pytest.raises(WorkspaceError) as captured:
            posix.Directory.absolute(path)
        assert captured.value.code == "UNSAFE_PATH"


def test_exists_reports_absent_entry_without_following_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use no-follow stat and distinguish absence from other errors."""
    observed: list[tuple[str, bool]] = []

    def stat_entry(name: str, *, dir_fd: int, follow_symlinks: bool) -> object:
        """Record direct lookup flags and model an absent entry."""
        assert dir_fd == 10
        observed.append((name, follow_symlinks))
        raise FileNotFoundError(name)

    monkeypatch.setattr(posix.os, "stat", stat_entry)
    assert handle().exists("note.md") is False
    assert observed == [("note.md", False)]


def test_read_rejects_unsafe_file_and_closes_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject a multi-link file before reading bytes and release its descriptor."""
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
    with pytest.raises(WorkspaceError) as captured:
        handle().read("note.md")
    assert captured.value.code == "UNSAFE_PATH"
    assert closed == [11]


def test_write_refuses_unsafe_existing_target_before_temp_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Perform no replacement when existing target validation fails."""
    monkeypatch.setattr(posix.Directory, "exists", lambda _self, _name: True)

    def reject(_self: posix.Directory, _name: str) -> bytes:
        """Model the prior target failing ownership or link validation."""
        raise WorkspaceError("UNSAFE_PATH")

    monkeypatch.setattr(posix.Directory, "read", reject)
    monkeypatch.setattr(posix.os, "open", lambda *_args, **_kwargs: pytest.fail("created temp"))
    with pytest.raises(WorkspaceError) as captured:
        handle().write("note.md", b"replacement")
    assert captured.value.code == "UNSAFE_PATH"


def test_issue_view_rejects_existing_link_to_different_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve an existing issue link when its target differs."""
    monkeypatch.setattr(posix.Directory, "exists", lambda _self, _name: True)
    monkeypatch.setattr(
        posix.os, "stat", lambda *_args, **_kwargs: SimpleNamespace(st_mode=stat.S_IFLNK)
    )
    monkeypatch.setattr(posix.os, "readlink", lambda *_args, **_kwargs: "/other/issue")
    monkeypatch.setattr(posix.os, "symlink", lambda *_args, **_kwargs: pytest.fail("replaced link"))
    with pytest.raises(WorkspaceError) as captured:
        handle().issue_view("AGENT-30", "/expected/issue")
    assert captured.value.code == "UNSAFE_PATH"
