"""Model Windows handle and directory decisions without touching native resources."""

from __future__ import annotations

import ctypes as c
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_company.lifecycle._errors import WorkspaceError

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(
        sys.platform != "win32",
        reason="Windows native CreateFileW and NTFS handle APIs are unavailable on POSIX",
    ),
]


@pytest.fixture
def windows() -> Any:
    """Import the Windows filesystem module for native contract tests.

    Returns:
        Imported Windows filesystem module.
    """
    from agent_company.lifecycle import _filesystem_windows

    return _filesystem_windows


def directory(windows: Any) -> Any:
    """Create a fake Windows directory with pinned handle identity.

    Args:
        windows: Windows filesystem module being exercised.

    Returns:
        Fake Windows directory with pinned identity and handles.
    """
    # Allocate a directory without opening a native handle.
    result = windows.Directory.__new__(windows.Directory)
    # Pin the modeled path and handle identity for native checks.
    result.path = Path(r"C:\work")
    result._handles = [101]
    result.identity = [7, 13]
    return result


def test_native_layout_and_declarations(windows: Any) -> None:
    """Native layout and declarations.

    Args:
        windows: Windows filesystem module being exercised.
    """
    assert windows.CreateFile.restype is windows.w.HANDLE
    assert windows.CreateFile.argtypes[0] is windows.w.LPCWSTR
    assert windows.CreateFile.argtypes[-1] is windows.w.HANDLE
    assert windows.GetFinalPath.restype is windows.w.DWORD
    assert windows.LockFile.argtypes[-1] is windows.P
    assert windows.FileInformation.attributes.offset == 0
    assert windows.FileInformation.index_low.offset > windows.FileInformation.volume.offset
    assert windows.SecurityAttributes.descriptor.offset >= c.sizeof(windows.w.DWORD)
    assert windows.Overlapped.event.offset > windows.Overlapped.offset_high.offset


def test_api_sets_native_signature(windows: Any) -> None:
    """Api sets native signature.

    Args:
        windows: Windows filesystem module being exercised.
    """
    # Supply a fake native function whose signature is inspected.
    function = SimpleNamespace(restype=None, argtypes=None)
    # Confirm the declaration uses the expected native argument and return types.
    assert windows.api(SimpleNamespace(call=function), "call", c.c_void_p, c.c_int) is function
    assert function.restype is c.c_void_p
    assert function.argtypes == [c.c_int]


def test_checked_preserves_native_error(windows: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Checked preserves native error.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake get_last_error calls.
    monkeypatch.setattr(windows.c, "get_last_error", lambda: 5)
    # Convert a successful native status into the expected result.
    windows.checked(1)
    # Surface the native last-error code on failure.
    with pytest.raises(OSError) as captured:  # noqa: PT011 - winerror is asserted.
        windows.checked(0)
    assert captured.value.winerror == 5


def test_open_handle_uses_non_delete_sharing_and_security(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Open handle uses non delete sharing and security.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake CreateFile calls.
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(windows, "CreateFile", lambda *args: calls.append(args) or 91)
    # Build private creation attributes from the security descriptor.
    security = windows.SecurityAttributes()
    # Confirm the native call receives the exact child path.
    assert windows.open_handle(Path(r"C:\work\item"), windows.READ, 1, security) == 91
    assert calls[0][0] == r"\\?\C:\work\item"
    assert calls[0][1:3] == (windows.READ, 0x3)
    assert calls[0][3] is not None
    assert calls[0][4:6] == (1, 0x02200000)


@pytest.mark.parametrize("kind", ["wrong-type", "reparse", "hard-link", "zero-index"])
def test_information_rejects_unsafe_native_identity(
    windows: Any, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """Information rejects unsafe native identity.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
        kind: Native object kind requested by the call.
    """
    # Select native attributes, link count, and index for validation.
    attributes = 0 if kind in {"wrong-type", "hard-link"} else windows.DIRECTORY
    attributes |= windows.REPARSE if kind == "reparse" else 0
    links = 2 if kind == "hard-link" else 1
    index = 0 if kind == "zero-index" else 11

    def fill(_handle: int, address: Any) -> bool:
        """Populate native file or volume information output fields.

        Args:
            _handle: Unused handle argument accepted by this fake.
            address: Native output pointer filled by the fake.

        Returns:
            Success flag after writing native file information.
        """
        # Write the selected identity fields into the native output buffer.
        info = c.cast(address, c.POINTER(windows.FileInformation)).contents
        info.attributes, info.links, info.index_low = attributes, links, index
        return True

    # Install fake GetFileInformation, GetFileType calls.
    monkeypatch.setattr(windows, "GetFileInformation", fill)
    monkeypatch.setattr(windows, "GetFileType", lambda _handle: 1)
    # Reject metadata with an unsafe file identity.
    with pytest.raises(WorkspaceError) as captured:
        windows.information(91, directory=kind != "hard-link")
    # Confirm the rejected operation reports UNSAFE_PATH.
    assert captured.value.code == "UNSAFE_PATH"


def test_information_allows_explicit_reparse_inspection(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Information allows explicit reparse inspection.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """

    def fill(_handle: int, address: Any) -> bool:
        """Populate native file or volume information output fields.

        Args:
            _handle: Unused handle argument accepted by this fake.
            address: Native output pointer filled by the fake.

        Returns:
            Success flag after writing native file information.
        """
        # Write the selected identity fields into the native output buffer.
        info = c.cast(address, c.POINTER(windows.FileInformation)).contents
        info.attributes, info.index_low = windows.DIRECTORY | windows.REPARSE, 44
        return True

    # Install fake GetFileInformation, GetFileType calls.
    monkeypatch.setattr(windows, "GetFileInformation", fill)
    monkeypatch.setattr(windows, "GetFileType", lambda _handle: 1)
    # Confirm the returned index, attributes, and link count.
    assert windows.information(91, directory=True, reparse=True).index_low == 44


def test_information_surfaces_native_query_failure(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Information surfaces native query failure.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake GetFileInformation, get_last_error calls.
    monkeypatch.setattr(windows, "GetFileInformation", lambda *_args: False)
    monkeypatch.setattr(windows.c, "get_last_error", lambda: 6)
    # Reject metadata with an unsafe file identity.
    with pytest.raises(OSError) as captured:  # noqa: PT011 - winerror is asserted.
        windows.information(91, directory=True)
    assert captured.value.winerror == 6


def test_exact_handle_path_rejects_alias_and_truncation(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exact handle path rejects alias and truncation.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """

    def answer(value: str, length: int | None = None) -> Any:
        """Write a controlled native reparse or path response.

        Args:
            value: Input value provided to the fake or tested operation.
            length: Length reported by the fake native call.

        Returns:
            Native response function that writes the requested path.
        """

        def fake(_handle: int, buffer: Any, _size: int, _flags: int) -> int:
            """Write a controlled native reparse or path response.

            Args:
                _handle: Unused handle argument accepted by this fake.
                buffer: Native output buffer filled by the fake.
                _size: Unused size argument accepted by this fake.
                _flags: Unused flags argument accepted by this fake.

            Returns:
                Native path response writer used by the case.
            """
            # Write the native final path into the output buffer.
            buffer.value = value
            return length if length is not None else len(value)

        return fake

    # Install fake GetFinalPath calls.
    path = Path(r"C:\work\item")
    monkeypatch.setattr(windows, "GetFinalPath", answer(r"\\?\C:\WORK\ITEM"))
    # Resolve the final path for the opened handle.
    windows.exact_handle_path(91, path)
    # Repeat the check for each (spelling, size) value.
    for spelling, size in [(r"\\?\C:\WORK\other", None), (r"\\?\C:\WORK\ITEM", 32768)]:
        # Install fake GetFinalPath calls.
        monkeypatch.setattr(windows, "GetFinalPath", answer(spelling, size))
        # Reject aliases or truncation in the native final path.
        with pytest.raises(WorkspaceError) as captured:
            windows.exact_handle_path(91, path)
        # Confirm the rejected operation reports UNSAFE_PATH.
        assert captured.value.code == "UNSAFE_PATH"


def test_directory_init_releases_pins_on_validation_failure(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Directory init releases pins on validation failure.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake information, validate_security, CloseHandle calls.
    closed: list[int] = []
    monkeypatch.setattr(
        windows,
        "information",
        lambda *_args, **_kwargs: SimpleNamespace(volume=7, index_high=0, index_low=13),
    )
    monkeypatch.setattr(
        windows,
        "validate_security",
        lambda *_args: (_ for _ in ()).throw(WorkspaceError("UNSAFE_PATH")),
    )
    monkeypatch.setattr(windows, "CloseHandle", lambda handle: closed.append(handle) or True)
    # Reject unsafe directory metadata and release pinned handles.
    with pytest.raises(WorkspaceError) as captured:
        windows.Directory(Path(r"C:\work"), [11, 12], private=True)
    # Confirm the rejected operation reports UNSAFE_PATH.
    assert captured.value.code == "UNSAFE_PATH"
    assert closed == [12, 11]


def test_directory_context_and_repeated_close(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Directory context and repeated close.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake CloseHandle calls.
    closed: list[int] = []
    monkeypatch.setattr(windows, "CloseHandle", lambda handle: closed.append(handle) or True)
    item = directory(windows)
    item._handles = [11, 12]
    # Use the fake pinned directory as a context manager.
    with item as entered:
        assert entered is item
    # Close each pinned handle once, in reverse order.
    item.close()
    # Check recorded closed against the required side effects.
    assert closed == [12, 11]


def test_directory_entry_rejects_closed_and_case_alias(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Directory entry rejects closed and case alias.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake names calls.
    item = directory(windows)
    monkeypatch.setattr(item, "names", lambda: ["Roadmap.md"])
    # Confirm a case-only alias is rejected before opening it.
    assert item._entry("Roadmap.md") == Path(r"C:\work\Roadmap.md")
    # Reject a closed handle or case-alias entry before opening it.
    with pytest.raises(WorkspaceError) as captured:
        item._entry("roadmap.md")
    # Confirm the rejected operation reports UNSAFE_PATH.
    assert captured.value.code == "UNSAFE_PATH"
    # Replace the pinned-handle set to model closed state.
    item._handles = []
    # Reject a closed handle or case-alias entry before opening it.
    with pytest.raises(WorkspaceError) as captured:
        item._entry("other.md")
    # Confirm the rejected operation reports UNSAFE_PATH.
    assert captured.value.code == "UNSAFE_PATH"


def test_names_exists_and_flush_use_pinned_directory(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Names exists and flush use pinned directory.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake listdir calls.
    item = directory(windows)
    monkeypatch.setattr(
        windows.os, "listdir", lambda path: ["one"] if path == r"\\?\C:\work" else []
    )
    # Confirm native enumeration returns only canonical child names.
    assert item.names() == ["one"]
    # Install fake lstat calls.
    monkeypatch.setattr(windows.os, "lstat", lambda _path: object())
    # Confirm absence and presence are reported from native metadata.
    assert item.exists("one")

    def absent(_path: str) -> None:
        """Raise file absence at the simulated lookup boundary.

        Args:
            _path: Unused path argument accepted by this fake.

        Raises:
            FileNotFoundError: When the simulated entry is absent.
        """
        raise FileNotFoundError

    # Install fake lstat calls.
    monkeypatch.setattr(windows.os, "lstat", absent)
    # Confirm absence and presence are reported from native metadata.
    assert not item.exists("two")
    # Flush through the directory handle after validating it.
    item.flush()
    # Replace the pinned-handle set to model closed state.
    item._handles = []
    # Repeat the check for each operation value.
    for operation in (item.names, item.flush):
        # Exercise operation and capture the expected failure.
        with pytest.raises(WorkspaceError) as captured:
            operation()
        # Confirm the rejected operation reports UNSAFE_PATH.
        assert captured.value.code == "UNSAFE_PATH"


def test_child_closes_result_when_private_acl_fails(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Child closes result when private acl fails.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake absolute, close, validate_security and related calls.
    item = directory(windows)
    child = directory(windows)
    closed: list[bool] = []
    monkeypatch.setattr(windows.Directory, "absolute", classmethod(lambda _cls, _path: child))
    monkeypatch.setattr(child, "close", lambda: closed.append(True))
    monkeypatch.setattr(
        windows,
        "validate_security",
        lambda *_args: (_ for _ in ()).throw(WorkspaceError("UNSAFE_PATH")),
    )
    monkeypatch.setattr(item, "names", lambda: [])
    # Reject a child whose private ACL validation fails.
    with pytest.raises(WorkspaceError) as captured:
        item.child("issue", private=True)
    # Confirm the rejected operation reports UNSAFE_PATH.
    assert captured.value.code == "UNSAFE_PATH"
    assert closed == [True]


def test_file_releases_handle_after_identity_rejection(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """File releases handle after identity rejection.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake names calls.
    item = directory(windows)
    closed: list[int] = []
    monkeypatch.setattr(item, "names", lambda: [])

    @contextmanager
    def security() -> Any:
        """Yield a controlled resource for file releases handle after identity rejection.

        Returns:
            Context manager yielding the selected security attributes.
        """
        yield windows.SecurityAttributes()

    # Install fake private_security, open_handle, information and related calls.
    monkeypatch.setattr(windows, "private_security", security)
    monkeypatch.setattr(windows, "open_handle", lambda *_args, **_kwargs: 91)
    monkeypatch.setattr(
        windows,
        "information",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(WorkspaceError("UNSAFE_PATH")),
    )
    monkeypatch.setattr(windows, "CloseHandle", lambda handle: closed.append(handle) or True)
    # Reject the file and close its native handle after identity failure.
    with pytest.raises(WorkspaceError) as captured:
        item._file("bad", create=True)
    # Confirm the rejected operation reports UNSAFE_PATH.
    assert captured.value.code == "UNSAFE_PATH"
    assert closed == [91]


def test_read_rejects_declared_oversize_before_handle_transfer(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read rejects declared oversize before handle transfer.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake _file, information, CloseHandle calls.
    item = directory(windows)
    closed: list[int] = []
    monkeypatch.setattr(item, "_file", lambda _name: 91)
    monkeypatch.setattr(
        windows, "information", lambda *_args, **_kwargs: SimpleNamespace(size_high=0, size_low=4)
    )
    monkeypatch.setattr(windows, "CloseHandle", lambda handle: closed.append(handle) or True)
    # Reject oversized content before transferring its handle.
    with pytest.raises(WorkspaceError) as captured:
        item.read("large", limit=3)
    # Confirm the rejected operation reports SIZE_LIMIT.
    assert captured.value.code == "SIZE_LIMIT"
    assert closed == [91]


def test_unlink_and_rmdir_validate_before_deletion(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unlink and rmdir validate before deletion.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake _file, CloseHandle, DeleteFile and related calls.
    item = directory(windows)
    effects: list[str] = []
    monkeypatch.setattr(item, "_file", lambda _name: 91)
    monkeypatch.setattr(windows, "CloseHandle", lambda _handle: effects.append("close") or True)
    monkeypatch.setattr(windows, "DeleteFile", lambda _path: effects.append("unlink") or True)
    monkeypatch.setattr(windows, "RemoveDirectory", lambda _path: effects.append("rmdir") or True)
    monkeypatch.setattr(item, "names", lambda: [])

    @contextmanager
    def child(_name: str, **_kwargs: Any) -> Any:
        """Yield a controlled resource for unlink and rmdir validate before deletion.

        Args:
            _name: Unused name argument accepted by this fake.
            **_kwargs: Extra options accepted to match the collaborator signature.

        Returns:
            Fake child directory or issue context for the caller.
        """
        effects.append("child")
        yield None

    # Install fake child calls.
    monkeypatch.setattr(item, "child", child)
    # Delete only after validating the existing entry.
    item.unlink("file")
    item.rmdir("empty")
    # Check recorded effects against the required side effects.
    assert effects == ["close", "unlink", "child", "rmdir"]


def test_rename_rejects_existing_target_without_move(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rename rejects existing target without move.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake names, exists and related calls.
    source, target = directory(windows), directory(windows)
    effects: list[str] = []
    monkeypatch.setattr(source, "names", lambda: [])
    monkeypatch.setattr(target, "names", lambda: [])
    monkeypatch.setattr(target, "exists", lambda _name: True)
    monkeypatch.setattr(windows, "MoveFile", lambda *_args: effects.append("move") or True)
    # Reject an existing destination before issuing the move.
    with pytest.raises(WorkspaceError) as captured:
        source.rename_directory("issue", target, "quarantine")
    # Confirm the rejected operation reports RECOVERY_REQUIRED.
    assert captured.value.code == "RECOVERY_REQUIRED"
    assert effects == []


def test_lock_releases_handle_on_body_error(windows: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Lock releases handle on body error.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake _file, LockFile, CloseHandle calls.
    item = directory(windows)
    closed: list[int] = []
    monkeypatch.setattr(item, "_file", lambda *_args, **_kwargs: 91)
    monkeypatch.setattr(windows, "LockFile", lambda *_args: True)
    monkeypatch.setattr(windows, "CloseHandle", lambda handle: closed.append(handle) or True)
    # Exercise lock releases handle on body error and capture the expected failure.
    with pytest.raises(RuntimeError, match="body"):
        # Hold a native lock while injecting a body failure.
        with item.lock():
            raise RuntimeError("body")
    # Check recorded closed against the required side effects.
    assert closed == [91]


def test_lock_timeout_keeps_persistent_inode(windows: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Lock timeout keeps persistent inode.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake _file, LockFile, get_last_error and related calls.
    item = directory(windows)
    closed: list[int] = []
    monkeypatch.setattr(item, "_file", lambda *_args, **_kwargs: 91)
    monkeypatch.setattr(windows, "LockFile", lambda *_args: False)
    monkeypatch.setattr(windows.c, "get_last_error", lambda: 33)
    monkeypatch.setattr(windows.time, "monotonic", iter([10.0, 10.0]).__next__)
    monkeypatch.setattr(windows, "CloseHandle", lambda handle: closed.append(handle) or True)
    # Exercise lock timeout keeps persistent inode and capture the expected failure.
    with pytest.raises(WorkspaceError) as captured:
        # Require immediate contention to time out on the persistent lock.
        with item.lock(timeout=0):
            pass
    # Confirm the rejected operation reports BUSY.
    assert captured.value.code == "BUSY"
    assert closed == [91]
