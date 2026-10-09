"""Exercise POSIX descriptor decisions with bounded syscall doubles."""

from __future__ import annotations

import os
import stat
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_company.lifecycle import _filesystem_posix as posix
from agent_company.lifecycle._errors import WorkspaceError

pytestmark = pytest.mark.unit


def directory(fd: int = 10) -> posix.Directory:
    """Build a descriptor owner without opening a real filesystem entry."""
    result = posix.Directory.__new__(posix.Directory)
    result.fd = fd
    result.identity = [1, 2]
    return result


def test_absolute_walks_components_without_following_and_closes_previous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Open each absolute component relative to its predecessor and retain only the last."""
    opened: list[tuple[str, int | None, int]] = []
    closed: list[int] = []

    def open_entry(name: str, flags: int, *, dir_fd: int | None = None) -> int:
        """Record component-local opens and provide unique descriptor identities."""
        opened.append((name, dir_fd, flags))
        return 20 + len(opened)

    monkeypatch.setattr(posix.os, "open", open_entry)
    monkeypatch.setattr(posix.os, "close", closed.append)
    monkeypatch.setattr(posix.Directory, "__init__", lambda self, fd: setattr(self, "fd", fd))
    result = posix.Directory.absolute(Path("/one/two"))
    assert result.fd == 23
    assert [(name, parent) for name, parent, _flags in opened] == [
        ("/", None),
        ("one", 21),
        ("two", 22),
    ]
    assert all(flags & os.O_NOFOLLOW for _name, _parent, flags in opened[1:])
    assert closed == [21, 22]


def test_absolute_closes_active_descriptor_after_component_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Close the current path descriptor if a later component cannot open."""
    closed: list[int] = []

    def open_entry(name: str, _flags: int, *, dir_fd: int | None = None) -> int:
        """Fail at the second component after ownership passed to its parent."""
        if name == "two":
            assert dir_fd == 22
            raise FileNotFoundError(name)
        return 21 if name == "/" else 22

    monkeypatch.setattr(posix.os, "open", open_entry)
    monkeypatch.setattr(posix.os, "close", closed.append)
    with pytest.raises(FileNotFoundError, match="two"):
        posix.Directory.absolute("/one/two")
    assert closed == [21, 22]


def test_child_creation_flushes_parent_and_uses_nofollow_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Make child creation durable before opening and validating its descriptor."""
    events: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        posix.os,
        "mkdir",
        lambda name, mode, *, dir_fd: events.append(("mkdir", name, mode, dir_fd)),
    )
    monkeypatch.setattr(posix.os, "fsync", lambda fd: events.append(("fsync", fd)))

    def open_entry(name: str, flags: int, *, dir_fd: int) -> int:
        """Require a no-follow, directory-only child open."""
        events.append(("open", name, flags, dir_fd))
        return 11

    monkeypatch.setattr(posix.os, "open", open_entry)
    monkeypatch.setattr(
        posix.os,
        "fstat",
        lambda _fd: SimpleNamespace(
            st_mode=stat.S_IFDIR | 0o700, st_uid=os.getuid(), st_dev=1, st_ino=2
        ),
    )
    child = directory().child("issue", create=True)
    assert child.fd == 11
    assert events[:2] == [("mkdir", "issue", 0o700, 10), ("fsync", 10)]
    assert events[2][0] == "open" and events[2][1] == "issue"
    assert events[2][2] & os.O_NOFOLLOW
    assert events[2][3] == 10


def test_read_rejects_oversized_file_before_byte_read_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject an oversized owned file before acquiring a Python stream."""
    closed: list[int] = []
    monkeypatch.setattr(posix.os, "open", lambda *_args, **_kwargs: 11)
    monkeypatch.setattr(
        posix.os,
        "fstat",
        lambda _fd: SimpleNamespace(
            st_mode=stat.S_IFREG | 0o600,
            st_nlink=1,
            st_uid=os.getuid(),
            st_size=5,
        ),
    )
    monkeypatch.setattr(posix.os, "fdopen", lambda *_args, **_kwargs: pytest.fail("opened stream"))
    monkeypatch.setattr(posix.os, "close", closed.append)
    with pytest.raises(WorkspaceError) as captured:
        directory().read("note", limit=4)
    assert captured.value.code == "SIZE_LIMIT"
    assert closed == [11]


def test_unlink_flushes_parent_after_deletion(monkeypatch: pytest.MonkeyPatch) -> None:
    """Persist the namespace deletion only after unlink succeeds."""
    events: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        posix.os,
        "unlink",
        lambda name, *, dir_fd: events.append(("unlink", name, dir_fd)),
    )
    monkeypatch.setattr(posix.os, "fsync", lambda fd: events.append(("fsync", fd)))
    directory().unlink("note")
    assert events == [("unlink", "note", 10), ("fsync", 10)]


def test_rename_refuses_occupied_quarantine_before_open_or_move(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not inspect or move source when quarantine destination already exists."""
    source = directory(10)
    destination = directory(12)
    monkeypatch.setattr(posix.Directory, "exists", lambda self, _name: self is destination)
    monkeypatch.setattr(
        posix.Directory, "child", lambda *_args, **_kwargs: pytest.fail("opened source")
    )
    monkeypatch.setattr(posix.os, "rename", lambda *_args, **_kwargs: pytest.fail("renamed"))
    with pytest.raises(WorkspaceError) as captured:
        source.rename_directory("issue", destination, "quarantine")
    assert captured.value.code == "RECOVERY_REQUIRED"


def test_issue_view_creates_exact_link_and_flushes_before_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Publish one issue link then verify its no-follow type and exact target."""
    events: list[tuple[object, ...]] = []
    monkeypatch.setattr(posix.Directory, "exists", lambda _self, _name: False)
    monkeypatch.setattr(
        posix.os,
        "symlink",
        lambda target, name, *, dir_fd: events.append(("symlink", target, name, dir_fd)),
    )
    monkeypatch.setattr(posix.os, "fsync", lambda fd: events.append(("fsync", fd)))

    def stat_entry(name: str, *, dir_fd: int, follow_symlinks: bool) -> SimpleNamespace:
        """Require a no-follow inspection of the published view entry."""
        events.append(("stat", name, dir_fd, follow_symlinks))
        return SimpleNamespace(st_mode=stat.S_IFLNK)

    monkeypatch.setattr(posix.os, "stat", stat_entry)
    monkeypatch.setattr(
        posix.os,
        "readlink",
        lambda name, *, dir_fd: events.append(("readlink", name, dir_fd)) or "/target/issue",
    )
    directory().issue_view("AGENT-30", "/target/issue")
    assert events == [
        ("symlink", "/target/issue", "AGENT-30", 10),
        ("fsync", 10),
        ("stat", "AGENT-30", 10, False),
        ("readlink", "AGENT-30", 10),
    ]


def test_write_cleans_unpublished_temp_after_replace_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Release the staged file and remove its name when atomic publication fails."""
    events: list[tuple[object, ...]] = []
    existing = {"note": False, "temp": True}
    monkeypatch.setattr(posix.uuid, "uuid4", lambda: SimpleNamespace(hex="fixed"))
    monkeypatch.setattr(
        posix.Directory,
        "exists",
        lambda _self, name: existing["note"] if name == "note" else existing["temp"],
    )
    monkeypatch.setattr(
        posix.os,
        "open",
        lambda name, flags, mode, *, dir_fd: (
            events.append(("open", name, flags, mode, dir_fd)) or 11
        ),
    )

    class RecordingStream(BytesIO):
        """Retain staged bytes for assertion after the write context closes."""

        def close(self) -> None:
            """Record the exact staged payload before closing."""
            events.append(("bytes", self.getvalue()))
            super().close()

    stream = RecordingStream()
    monkeypatch.setattr(posix.os, "fdopen", lambda _fd, _mode, *, closefd: stream)
    monkeypatch.setattr(posix.os, "fsync", lambda fd: events.append(("fsync", fd)))

    def fail_replace(*_args: object, **_kwargs: object) -> None:
        """Model failed atomic publication after the staged content was flushed."""
        events.append(("replace",))
        raise OSError("replace failed")

    monkeypatch.setattr(posix.os, "replace", fail_replace)
    monkeypatch.setattr(posix.os, "close", lambda fd: events.append(("close", fd)))
    monkeypatch.setattr(
        posix.os, "unlink", lambda name, *, dir_fd: events.append(("unlink", name, dir_fd))
    )
    with pytest.raises(OSError, match="replace failed"):
        directory().write("note", b"new bytes")
    assert stream.closed
    assert events[0][0:2] == ("open", ".write-fixed")
    assert events[1:] == [
        ("fsync", 11),
        ("bytes", b"new bytes"),
        ("replace",),
        ("close", 11),
        ("unlink", ".write-fixed", 10),
    ]


def test_lock_rejects_unsafe_existing_lock_and_releases_descriptor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject a multi-link lock file before flock and release the opened handle."""
    closed: list[int] = []

    def open_lock(_name: str, flags: int, *_args: object, **_kwargs: object) -> int:
        """Model an existing lock after exclusive creation fails."""
        if flags & os.O_EXCL:
            raise FileExistsError("lock")
        return 11

    monkeypatch.setattr(posix.os, "open", open_lock)
    monkeypatch.setattr(
        posix.os,
        "fstat",
        lambda _fd: SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_nlink=2, st_uid=os.getuid()),
    )
    monkeypatch.setattr(posix.os, "close", closed.append)
    monkeypatch.setattr(posix.fcntl, "flock", lambda *_args: pytest.fail("locked unsafe file"))
    with pytest.raises(WorkspaceError) as captured:
        with directory().lock():
            pytest.fail("entered unsafe lock")
    assert captured.value.code == "UNSAFE_PATH"
    assert closed == [11]


def test_directory_context_and_names_use_owned_descriptor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """List and release only the descriptor retained by this directory handle."""
    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(posix.os, "listdir", lambda fd: calls.append(("list", fd)) or ["note"])
    monkeypatch.setattr(posix.os, "close", lambda fd: calls.append(("close", fd)))
    owned = directory()
    with owned as opened:
        assert opened is owned
        assert opened.names() == ["note"]
    assert calls == [("list", 10), ("close", 10)]


def test_read_returns_exact_bounded_owned_regular_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read one more than the limit and close the descriptor after valid bytes."""
    opened: list[tuple[str, int, int]] = []
    closed: list[int] = []
    monkeypatch.setattr(
        posix.os,
        "open",
        lambda name, flags, *, dir_fd: opened.append((name, flags, dir_fd)) or 11,
    )
    monkeypatch.setattr(
        posix.os,
        "fstat",
        lambda _fd: SimpleNamespace(
            st_mode=stat.S_IFREG | 0o600,
            st_nlink=1,
            st_uid=os.getuid(),
            st_size=4,
        ),
    )
    monkeypatch.setattr(posix.os, "fdopen", lambda _fd, _mode, *, closefd: BytesIO(b"data"))
    monkeypatch.setattr(posix.os, "close", closed.append)
    assert directory().read("note", limit=4) == b"data"
    assert opened[0][0] == "note"
    assert opened[0][1] & os.O_NOFOLLOW
    assert opened[0][2] == 10
    assert closed == [11]


def test_rmdir_validates_direct_child_then_flushes_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validate the child handle before removing an empty directory."""
    calls: list[tuple[object, ...]] = []

    class Child:
        """Expose a validated direct child context."""

        def __enter__(self) -> Child:
            """Record validation before removal."""
            calls.append(("child-enter",))
            return self

        def __exit__(self, *_args: object) -> None:
            """Release the validated direct child."""
            calls.append(("child-exit",))

    monkeypatch.setattr(
        posix.Directory,
        "child",
        lambda _self, name, *, private: calls.append(("child", name, private)) or Child(),
    )
    monkeypatch.setattr(
        posix.os,
        "rmdir",
        lambda name, *, dir_fd: calls.append(("rmdir", name, dir_fd)),
    )
    monkeypatch.setattr(posix.os, "fsync", lambda fd: calls.append(("fsync", fd)))
    directory().rmdir("old")
    assert calls == [
        ("child", "old", False),
        ("child-enter",),
        ("child-exit",),
        ("rmdir", "old", 10),
        ("fsync", 10),
    ]


def test_lock_creates_private_persistent_file_and_releases_after_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flush a private lock entry before acquiring flock and close after the body."""
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        posix.os,
        "open",
        lambda name, flags, mode, *, dir_fd: (
            calls.append(("open", name, flags, mode, dir_fd)) or 11
        ),
    )
    monkeypatch.setattr(posix.os, "fsync", lambda fd: calls.append(("fsync", fd)))
    monkeypatch.setattr(
        posix.os,
        "fstat",
        lambda _fd: SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_nlink=1, st_uid=os.getuid()),
    )
    monkeypatch.setattr(
        posix.fcntl,
        "flock",
        lambda fd, flags: calls.append(("flock", fd, flags)),
    )
    monkeypatch.setattr(posix.os, "close", lambda fd: calls.append(("close", fd)))
    with directory().lock("issue.lock"):
        calls.append(("body",))
    assert calls[0][0:2] == ("open", "issue.lock")
    assert calls[0][2] & os.O_EXCL
    assert calls[0][3:] == (0o600, 10)
    assert calls[1:] == [
        ("fsync", 10),
        ("flock", 11, posix.fcntl.LOCK_EX | posix.fcntl.LOCK_NB),
        ("body",),
        ("close", 11),
    ]


def test_rename_directory_validates_source_then_flushes_both_parents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Quarantine one direct child only after validation and flush both namespaces."""
    source, destination = directory(10), directory(12)
    calls: list[tuple[object, ...]] = []

    class Child:
        """Model one validated direct source directory."""

        def __enter__(self) -> Child:
            """Record source validation before rename."""
            calls.append(("child-enter",))
            return self

        def __exit__(self, *_args: object) -> None:
            """Release the source directory before rename."""
            calls.append(("child-exit",))

    monkeypatch.setattr(posix.Directory, "exists", lambda _self, _name: False)
    monkeypatch.setattr(
        posix.Directory,
        "child",
        lambda _self, name, *, private: calls.append(("child", name, private)) or Child(),
    )
    monkeypatch.setattr(
        posix.os,
        "rename",
        lambda name, new_name, *, src_dir_fd, dst_dir_fd: calls.append(
            ("rename", name, new_name, src_dir_fd, dst_dir_fd)
        ),
    )
    monkeypatch.setattr(posix.os, "fsync", lambda fd: calls.append(("fsync", fd)))
    source.rename_directory("issue", destination, "quarantine")
    assert calls == [
        ("child", "issue", False),
        ("child-enter",),
        ("child-exit",),
        ("rename", "issue", "quarantine", 10, 12),
        ("fsync", 10),
        ("fsync", 12),
    ]


def test_exists_reports_present_direct_entry_without_following_symlink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Count a direct link or file entry as present using no-follow stat."""
    looked_up: list[tuple[str, int, bool]] = []
    monkeypatch.setattr(
        posix.os,
        "stat",
        lambda name, *, dir_fd, follow_symlinks: (
            looked_up.append((name, dir_fd, follow_symlinks))
            or SimpleNamespace(st_mode=stat.S_IFLNK)
        ),
    )
    assert directory().exists("view") is True
    assert looked_up == [("view", 10, False)]


def test_child_reuses_existing_private_directory_after_create_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Open and validate an existing child after exclusive mkdir reports a race."""
    calls: list[str] = []
    monkeypatch.setattr(
        posix.os,
        "mkdir",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileExistsError("existing")),
    )
    monkeypatch.setattr(posix.os, "fsync", lambda _fd: pytest.fail("flushed absent create"))
    monkeypatch.setattr(
        posix.os,
        "open",
        lambda name, flags, *, dir_fd: (
            calls.append(name)
            or (11 if flags & os.O_NOFOLLOW and dir_fd == 10 else pytest.fail("unsafe open"))
        ),
    )
    monkeypatch.setattr(
        posix.os,
        "fstat",
        lambda _fd: SimpleNamespace(
            st_mode=stat.S_IFDIR | 0o700, st_uid=os.getuid(), st_dev=1, st_ino=2
        ),
    )
    assert directory().child("existing", create=True).fd == 11
    assert calls == ["existing"]


def test_write_publishes_staged_bytes_then_flushes_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replace the destination only after staged byte flush and descriptor fsync."""
    events: list[tuple[object, ...]] = []
    monkeypatch.setattr(posix.uuid, "uuid4", lambda: SimpleNamespace(hex="fixed"))
    monkeypatch.setattr(posix.Directory, "exists", lambda _self, _name: False)
    monkeypatch.setattr(
        posix.os,
        "open",
        lambda name, flags, mode, *, dir_fd: events.append(("open", name, mode, dir_fd)) or 11,
    )

    class Stream(BytesIO):
        """Record exact staged bytes before stream close."""

        def close(self) -> None:
            """Capture flushed bytes before atomic replacement."""
            events.append(("bytes", self.getvalue()))
            super().close()

    monkeypatch.setattr(posix.os, "fdopen", lambda _fd, _mode, *, closefd: Stream())
    monkeypatch.setattr(posix.os, "fsync", lambda fd: events.append(("fsync", fd)))
    monkeypatch.setattr(
        posix.os,
        "replace",
        lambda source, target, *, src_dir_fd, dst_dir_fd: events.append(
            ("replace", source, target, src_dir_fd, dst_dir_fd)
        ),
    )
    monkeypatch.setattr(posix.os, "close", lambda fd: events.append(("close", fd)))
    directory().write("note", b"published")
    assert events == [
        ("open", ".write-fixed", 0o600, 10),
        ("fsync", 11),
        ("bytes", b"published"),
        ("replace", ".write-fixed", "note", 10, 10),
        ("fsync", 10),
        ("close", 11),
    ]


def test_lock_retries_blocked_flock_then_enters_with_same_descriptor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wait within the bounded deadline and retain one persistent lock handle."""
    calls: list[str] = []

    def open_lock(_name: str, flags: int, *_args: object, **_kwargs: object) -> int:
        """Model existing lock after exclusive creation loses a race."""
        if flags & os.O_EXCL:
            raise FileExistsError("existing")
        return 11

    monkeypatch.setattr(posix.os, "open", open_lock)
    monkeypatch.setattr(
        posix.os,
        "fstat",
        lambda _fd: SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_nlink=1, st_uid=os.getuid()),
    )
    attempts = 0

    def flock(fd: int, _flags: int) -> None:
        """Block once, then grant the same descriptor's exclusive lock."""
        nonlocal attempts
        assert fd == 11
        attempts += 1
        if attempts == 1:
            raise BlockingIOError("busy")
        calls.append("locked")

    monkeypatch.setattr(posix.fcntl, "flock", flock)
    monkeypatch.setattr(posix.time, "sleep", lambda _delay: calls.append("wait"))
    monkeypatch.setattr(posix.os, "close", lambda _fd: calls.append("closed"))
    with directory().lock(timeout=1):
        calls.append("body")
    assert attempts == 2
    assert calls == ["wait", "locked", "body", "closed"]


def test_issue_view_validates_racing_existing_exact_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accept only the exact preexisting target after symlink creation races."""
    monkeypatch.setattr(posix.Directory, "exists", lambda _self, _name: False)
    monkeypatch.setattr(
        posix.os,
        "symlink",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileExistsError("raced")),
    )
    monkeypatch.setattr(posix.os, "fsync", lambda _fd: pytest.fail("flushed failed creation"))
    monkeypatch.setattr(
        posix.os,
        "stat",
        lambda *_args, **_kwargs: SimpleNamespace(st_mode=stat.S_IFLNK),
    )
    monkeypatch.setattr(posix.os, "readlink", lambda *_args, **_kwargs: "/exact")
    assert directory().issue_view("AGENT-30", "/exact") is None


def test_child_without_create_never_attempts_mkdir_or_parent_flush(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Open an existing direct child without modifying parent metadata."""
    monkeypatch.setattr(posix.os, "mkdir", lambda *_args, **_kwargs: pytest.fail("created"))
    monkeypatch.setattr(posix.os, "fsync", lambda _fd: pytest.fail("flushed"))
    monkeypatch.setattr(posix.os, "open", lambda *_args, **_kwargs: 11)
    monkeypatch.setattr(
        posix.os,
        "fstat",
        lambda _fd: SimpleNamespace(
            st_mode=stat.S_IFDIR | 0o700, st_uid=os.getuid(), st_dev=1, st_ino=2
        ),
    )
    assert directory().child("existing", create=False).fd == 11


def test_lock_reports_busy_when_existing_file_disappears_until_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End a create/open race at the bounded deadline without leaking a handle."""
    calls: list[str] = []

    def open_lock(_name: str, flags: int, *_args: object, **_kwargs: object) -> int:
        """Model repeated disappearance after exclusive creation races."""
        calls.append("create" if flags & os.O_EXCL else "existing")
        if flags & os.O_EXCL:
            raise FileExistsError("raced")
        raise FileNotFoundError("disappeared")

    ticks = iter([0.0, 2.0])
    monkeypatch.setattr(posix.os, "open", open_lock)
    monkeypatch.setattr(posix.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(posix.os, "close", lambda _fd: pytest.fail("closed unopened file"))
    with pytest.raises(WorkspaceError) as captured:
        with directory().lock(timeout=1):
            pytest.fail("entered absent lock")
    assert captured.value.code == "BUSY"
    assert calls == ["create", "existing"]
