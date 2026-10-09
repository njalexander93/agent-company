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
    """Windows."""
    from agent_company.lifecycle import _filesystem_windows

    return _filesystem_windows


def directory(windows: Any) -> Any:
    """Directory."""
    result = windows.Directory.__new__(windows.Directory)
    result.path = Path(r"C:\work")
    result._handles = [101]
    result.identity = [7, 13]
    return result


def test_native_layout_and_declarations(windows: Any) -> None:
    """Native layout and declarations."""
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
    """Api sets native signature."""
    function = SimpleNamespace(restype=None, argtypes=None)
    assert windows.api(SimpleNamespace(call=function), "call", c.c_void_p, c.c_int) is function
    assert function.restype is c.c_void_p
    assert function.argtypes == [c.c_int]


def test_checked_preserves_native_error(windows: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Checked preserves native error."""
    monkeypatch.setattr(windows.c, "get_last_error", lambda: 5)
    windows.checked(1)
    with pytest.raises(OSError) as captured:  # noqa: PT011 - winerror is asserted.
        windows.checked(0)
    assert captured.value.winerror == 5


def test_open_handle_uses_non_delete_sharing_and_security(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Open handle uses non delete sharing and security."""
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(windows, "CreateFile", lambda *args: calls.append(args) or 91)
    security = windows.SecurityAttributes()
    assert windows.open_handle(Path(r"C:\work\item"), windows.READ, 1, security) == 91
    assert calls[0][0] == r"\\?\C:\work\item"
    assert calls[0][1:3] == (windows.READ, 0x3)
    assert calls[0][3] is not None
    assert calls[0][4:6] == (1, 0x02200000)


@pytest.mark.parametrize("kind", ["wrong-type", "reparse", "hard-link", "zero-index"])
def test_information_rejects_unsafe_native_identity(
    windows: Any, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """Information rejects unsafe native identity."""
    attributes = 0 if kind in {"wrong-type", "hard-link"} else windows.DIRECTORY
    attributes |= windows.REPARSE if kind == "reparse" else 0
    links = 2 if kind == "hard-link" else 1
    index = 0 if kind == "zero-index" else 11

    def fill(_handle: int, address: Any) -> bool:
        info = c.cast(address, c.POINTER(windows.FileInformation)).contents
        info.attributes, info.links, info.index_low = attributes, links, index
        return True

    monkeypatch.setattr(windows, "GetFileInformation", fill)
    monkeypatch.setattr(windows, "GetFileType", lambda _handle: 1)
    with pytest.raises(WorkspaceError) as captured:
        windows.information(91, directory=kind != "hard-link")
    assert captured.value.code == "UNSAFE_PATH"


def test_information_allows_explicit_reparse_inspection(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Information allows explicit reparse inspection."""

    def fill(_handle: int, address: Any) -> bool:
        info = c.cast(address, c.POINTER(windows.FileInformation)).contents
        info.attributes, info.index_low = windows.DIRECTORY | windows.REPARSE, 44
        return True

    monkeypatch.setattr(windows, "GetFileInformation", fill)
    monkeypatch.setattr(windows, "GetFileType", lambda _handle: 1)
    assert windows.information(91, directory=True, reparse=True).index_low == 44


def test_information_surfaces_native_query_failure(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Information surfaces native query failure."""
    monkeypatch.setattr(windows, "GetFileInformation", lambda *_args: False)
    monkeypatch.setattr(windows.c, "get_last_error", lambda: 6)
    with pytest.raises(OSError) as captured:  # noqa: PT011 - winerror is asserted.
        windows.information(91, directory=True)
    assert captured.value.winerror == 6


def test_exact_handle_path_rejects_alias_and_truncation(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exact handle path rejects alias and truncation."""

    def answer(value: str, length: int | None = None) -> Any:
        def fake(_handle: int, buffer: Any, _size: int, _flags: int) -> int:
            buffer.value = value
            return length if length is not None else len(value)

        return fake

    path = Path(r"C:\work\item")
    monkeypatch.setattr(windows, "GetFinalPath", answer(r"\\?\C:\WORK\ITEM"))
    windows.exact_handle_path(91, path)
    for spelling, size in [(r"\\?\C:\WORK\other", None), (r"\\?\C:\WORK\ITEM", 32768)]:
        monkeypatch.setattr(windows, "GetFinalPath", answer(spelling, size))
        with pytest.raises(WorkspaceError) as captured:
            windows.exact_handle_path(91, path)
        assert captured.value.code == "UNSAFE_PATH"


def test_directory_init_releases_pins_on_validation_failure(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Directory init releases pins on validation failure."""
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
    with pytest.raises(WorkspaceError) as captured:
        windows.Directory(Path(r"C:\work"), [11, 12], private=True)
    assert captured.value.code == "UNSAFE_PATH"
    assert closed == [12, 11]


def test_directory_context_and_repeated_close(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Directory context and repeated close."""
    closed: list[int] = []
    monkeypatch.setattr(windows, "CloseHandle", lambda handle: closed.append(handle) or True)
    item = directory(windows)
    item._handles = [11, 12]
    with item as entered:
        assert entered is item
    item.close()
    assert closed == [12, 11]


def test_directory_entry_rejects_closed_and_case_alias(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Directory entry rejects closed and case alias."""
    item = directory(windows)
    monkeypatch.setattr(item, "names", lambda: ["Roadmap.md"])
    assert item._entry("Roadmap.md") == Path(r"C:\work\Roadmap.md")
    with pytest.raises(WorkspaceError) as captured:
        item._entry("roadmap.md")
    assert captured.value.code == "UNSAFE_PATH"
    item._handles = []
    with pytest.raises(WorkspaceError) as captured:
        item._entry("other.md")
    assert captured.value.code == "UNSAFE_PATH"


def test_names_exists_and_flush_use_pinned_directory(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Names exists and flush use pinned directory."""
    item = directory(windows)
    monkeypatch.setattr(
        windows.os, "listdir", lambda path: ["one"] if path == r"\\?\C:\work" else []
    )
    assert item.names() == ["one"]
    monkeypatch.setattr(windows.os, "lstat", lambda _path: object())
    assert item.exists("one")

    def absent(_path: str) -> None:
        raise FileNotFoundError

    monkeypatch.setattr(windows.os, "lstat", absent)
    assert not item.exists("two")
    item.flush()
    item._handles = []
    for operation in (item.names, item.flush):
        with pytest.raises(WorkspaceError) as captured:
            operation()
        assert captured.value.code == "UNSAFE_PATH"


def test_child_closes_result_when_private_acl_fails(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Child closes result when private acl fails."""
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
    with pytest.raises(WorkspaceError) as captured:
        item.child("issue", private=True)
    assert captured.value.code == "UNSAFE_PATH"
    assert closed == [True]


def test_file_releases_handle_after_identity_rejection(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """File releases handle after identity rejection."""
    item = directory(windows)
    closed: list[int] = []
    monkeypatch.setattr(item, "names", lambda: [])

    @contextmanager
    def security() -> Any:
        yield windows.SecurityAttributes()

    monkeypatch.setattr(windows, "private_security", security)
    monkeypatch.setattr(windows, "open_handle", lambda *_args, **_kwargs: 91)
    monkeypatch.setattr(
        windows,
        "information",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(WorkspaceError("UNSAFE_PATH")),
    )
    monkeypatch.setattr(windows, "CloseHandle", lambda handle: closed.append(handle) or True)
    with pytest.raises(WorkspaceError) as captured:
        item._file("bad", create=True)
    assert captured.value.code == "UNSAFE_PATH"
    assert closed == [91]


def test_read_rejects_declared_oversize_before_handle_transfer(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read rejects declared oversize before handle transfer."""
    item = directory(windows)
    closed: list[int] = []
    monkeypatch.setattr(item, "_file", lambda _name: 91)
    monkeypatch.setattr(
        windows, "information", lambda *_args, **_kwargs: SimpleNamespace(size_high=0, size_low=4)
    )
    monkeypatch.setattr(windows, "CloseHandle", lambda handle: closed.append(handle) or True)
    with pytest.raises(WorkspaceError) as captured:
        item.read("large", limit=3)
    assert captured.value.code == "SIZE_LIMIT"
    assert closed == [91]


def test_unlink_and_rmdir_validate_before_deletion(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unlink and rmdir validate before deletion."""
    item = directory(windows)
    effects: list[str] = []
    monkeypatch.setattr(item, "_file", lambda _name: 91)
    monkeypatch.setattr(windows, "CloseHandle", lambda _handle: effects.append("close") or True)
    monkeypatch.setattr(windows, "DeleteFile", lambda _path: effects.append("unlink") or True)
    monkeypatch.setattr(windows, "RemoveDirectory", lambda _path: effects.append("rmdir") or True)
    monkeypatch.setattr(item, "names", lambda: [])

    @contextmanager
    def child(_name: str, **_kwargs: Any) -> Any:
        effects.append("child")
        yield None

    monkeypatch.setattr(item, "child", child)
    item.unlink("file")
    item.rmdir("empty")
    assert effects == ["close", "unlink", "child", "rmdir"]


def test_rename_rejects_existing_target_without_move(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rename rejects existing target without move."""
    source, target = directory(windows), directory(windows)
    effects: list[str] = []
    monkeypatch.setattr(source, "names", lambda: [])
    monkeypatch.setattr(target, "names", lambda: [])
    monkeypatch.setattr(target, "exists", lambda _name: True)
    monkeypatch.setattr(windows, "MoveFile", lambda *_args: effects.append("move") or True)
    with pytest.raises(WorkspaceError) as captured:
        source.rename_directory("issue", target, "quarantine")
    assert captured.value.code == "RECOVERY_REQUIRED"
    assert effects == []


def test_lock_releases_handle_on_body_error(windows: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Lock releases handle on body error."""
    item = directory(windows)
    closed: list[int] = []
    monkeypatch.setattr(item, "_file", lambda *_args, **_kwargs: 91)
    monkeypatch.setattr(windows, "LockFile", lambda *_args: True)
    monkeypatch.setattr(windows, "CloseHandle", lambda handle: closed.append(handle) or True)
    with pytest.raises(RuntimeError, match="body"):
        with item.lock():
            raise RuntimeError("body")
    assert closed == [91]


def test_lock_timeout_keeps_persistent_inode(windows: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Lock timeout keeps persistent inode."""
    item = directory(windows)
    closed: list[int] = []
    monkeypatch.setattr(item, "_file", lambda *_args, **_kwargs: 91)
    monkeypatch.setattr(windows, "LockFile", lambda *_args: False)
    monkeypatch.setattr(windows.c, "get_last_error", lambda: 33)
    monkeypatch.setattr(windows.time, "monotonic", iter([10.0, 10.0]).__next__)
    monkeypatch.setattr(windows, "CloseHandle", lambda handle: closed.append(handle) or True)
    with pytest.raises(WorkspaceError) as captured:
        with item.lock(timeout=0):
            pass
    assert captured.value.code == "BUSY"
    assert closed == [91]
