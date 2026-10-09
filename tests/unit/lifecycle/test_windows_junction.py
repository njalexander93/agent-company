"""Model junction publication and exact reparse payload validation."""

from __future__ import annotations

import ctypes as c
import struct
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
    item = windows.Directory.__new__(windows.Directory)
    item.path = Path(r"C:\work")
    item._handles = [101]
    item.identity = [7, 13]
    return item


def target_scope(windows: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Target scope."""

    @contextmanager
    def absolute(_cls: Any, _target: str) -> Any:
        yield None

    monkeypatch.setattr(windows.Directory, "absolute", classmethod(absolute))


def test_issue_view_rejects_nonjunction_and_closes_handle(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue view rejects nonjunction and closes handle."""
    item = directory(windows)
    target_scope(windows, monkeypatch)
    closed: list[int] = []
    monkeypatch.setattr(item, "_entry", lambda _name: Path(r"C:\work\AGENT-30"))
    monkeypatch.setattr(item, "exists", lambda _name: True)
    monkeypatch.setattr(windows, "open_handle", lambda *_args, **_kwargs: 91)
    monkeypatch.setattr(
        windows,
        "information",
        lambda *_args, **_kwargs: SimpleNamespace(attributes=windows.DIRECTORY),
    )
    monkeypatch.setattr(windows, "CloseHandle", lambda handle: closed.append(handle) or True)
    with pytest.raises(WorkspaceError) as captured:
        item.issue_view("AGENT-30", r"C:\store\AGENT-30")
    assert captured.value.code == "UNSAFE_PATH"
    assert closed == [91]


def test_issue_view_rejects_foreign_reparse_payload(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue view rejects foreign reparse payload."""
    item = directory(windows)
    target_scope(windows, monkeypatch)
    closed: list[int] = []
    calls: list[int] = []
    monkeypatch.setattr(item, "_entry", lambda _name: Path(r"C:\work\AGENT-30"))
    monkeypatch.setattr(item, "exists", lambda _name: True)
    monkeypatch.setattr(windows, "open_handle", lambda *_args, **_kwargs: 91)
    monkeypatch.setattr(
        windows,
        "information",
        lambda *_args, **_kwargs: SimpleNamespace(attributes=windows.DIRECTORY | windows.REPARSE),
    )
    monkeypatch.setattr(windows, "validate_security", lambda *_args: None)
    monkeypatch.setattr(windows, "CloseHandle", lambda handle: closed.append(handle) or True)

    def io_control(
        _handle: int,
        control: int,
        _input: Any,
        _input_len: int,
        output: Any,
        _output_len: int,
        returned: Any,
        _overlapped: Any,
    ) -> bool:
        calls.append(control)
        c.memmove(output, b"foreign", 7)
        c.cast(returned, c.POINTER(windows.w.DWORD))[0] = windows.w.DWORD(7)
        return True

    monkeypatch.setattr(windows, "DeviceIoControl", io_control)
    with pytest.raises(WorkspaceError) as captured:
        item.issue_view("AGENT-30", r"C:\store\AGENT-30")
    assert captured.value.code == "UNSAFE_PATH"
    assert calls == [0x900A8]
    assert closed == [91]


def test_issue_view_accepts_exact_mount_point_data(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue view accepts exact mount point data."""
    item = directory(windows)
    target_scope(windows, monkeypatch)
    target = r"C:\store\AGENT-30"
    substitute = ("\\??\\" + target).encode("utf-16-le")
    printable = target.encode("utf-16-le")
    paths = substitute + b"\0\0" + printable + b"\0\0"
    body = struct.pack("<HHHH", 0, len(substitute), len(substitute) + 2, len(printable)) + paths
    native_payload = struct.pack("<IHH", 0xA0000003, len(body), 0) + body
    closed: list[int] = []
    monkeypatch.setattr(item, "_entry", lambda _name: Path(r"C:\work\AGENT-30"))
    monkeypatch.setattr(item, "exists", lambda _name: True)
    monkeypatch.setattr(windows, "open_handle", lambda *_args, **_kwargs: 91)
    monkeypatch.setattr(
        windows,
        "information",
        lambda *_args, **_kwargs: SimpleNamespace(attributes=windows.DIRECTORY | windows.REPARSE),
    )
    monkeypatch.setattr(windows, "validate_security", lambda *_args: None)
    monkeypatch.setattr(windows, "CloseHandle", lambda handle: closed.append(handle) or True)

    def io_control(
        _handle: int,
        control: int,
        _input: Any,
        _input_len: int,
        output: Any,
        _output_len: int,
        returned: Any,
        _overlapped: Any,
    ) -> bool:
        assert control == 0x900A8
        c.memmove(output, native_payload, len(native_payload))
        c.cast(returned, c.POINTER(windows.w.DWORD))[0] = windows.w.DWORD(len(native_payload))
        return True

    monkeypatch.setattr(windows, "DeviceIoControl", io_control)
    item.issue_view("AGENT-30", target)
    assert closed == [91]


def test_issue_view_creation_race_removes_only_temporary_junction(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A collision leaves the existing view for exact payload validation."""
    item = directory(windows)
    target_scope(windows, monkeypatch)
    effects: list[str] = []

    @contextmanager
    def child(_name: str, **_kwargs: Any) -> Any:
        effects.append("temporary-directory")
        yield None

    monkeypatch.setattr(item, "_entry", lambda _name: Path(r"C:\work\AGENT-30"))
    monkeypatch.setattr(item, "child", child)
    monkeypatch.setattr(item, "exists", lambda name: name.startswith(".view-"))
    monkeypatch.setattr(windows, "open_handle", lambda *_args, **_kwargs: 91)
    monkeypatch.setattr(
        windows,
        "information",
        lambda *_args, **_kwargs: SimpleNamespace(attributes=windows.DIRECTORY | windows.REPARSE),
    )
    monkeypatch.setattr(windows, "validate_security", lambda *_args: None)
    monkeypatch.setattr(windows, "CloseHandle", lambda _handle: effects.append("close") or True)
    monkeypatch.setattr(windows, "MoveFile", lambda *_args: effects.append("collision") or False)
    monkeypatch.setattr(windows.c, "get_last_error", lambda: 183)
    monkeypatch.setattr(
        windows, "RemoveDirectory", lambda _path: effects.append("remove-temporary") or True
    )

    def io_control(
        _handle: int,
        control: int,
        _input: Any,
        _input_len: int,
        _output: Any,
        _output_len: int,
        _returned: Any,
        _overlapped: Any,
    ) -> bool:
        effects.append("set" if control == 0x900A4 else "get")
        return control == 0x900A4

    monkeypatch.setattr(windows, "DeviceIoControl", io_control)
    with pytest.raises(OSError) as captured:  # noqa: PT011 - native error is asserted.
        item.issue_view("AGENT-30", r"C:\store\AGENT-30")
    assert captured.value.winerror == 183
    assert effects == [
        "temporary-directory",
        "set",
        "close",
        "collision",
        "remove-temporary",
        "get",
        "close",
    ]


def test_issue_view_rejects_unexpected_publish_failure_and_removes_temp(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A noncollision rename failure is reported and its temp junction is removed."""
    item = directory(windows)
    target_scope(windows, monkeypatch)
    effects: list[str] = []

    @contextmanager
    def child(_name: str, **_kwargs: Any) -> Any:
        effects.append("create-temp")
        yield None

    monkeypatch.setattr(item, "_entry", lambda _name: Path(r"C:\work\AGENT-30"))
    monkeypatch.setattr(item, "child", child)
    monkeypatch.setattr(item, "exists", lambda name: name.startswith(".view-"))
    monkeypatch.setattr(
        windows, "open_handle", lambda *_args, **_kwargs: effects.append("open") or 91
    )
    monkeypatch.setattr(windows, "information", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(windows, "DeviceIoControl", lambda *_args: effects.append("set") or True)
    monkeypatch.setattr(windows, "CloseHandle", lambda _handle: effects.append("close") or True)
    monkeypatch.setattr(
        windows, "MoveFile", lambda *_args: effects.append("publish-failed") or False
    )
    monkeypatch.setattr(windows.c, "get_last_error", lambda: 5)
    monkeypatch.setattr(
        windows,
        "RemoveDirectory",
        lambda _path: effects.append("remove-temp") or True,
    )
    with pytest.raises(OSError) as captured:  # noqa: PT011 - native code is asserted.
        item.issue_view("AGENT-30", r"C:\store\AGENT-30")
    assert captured.value.winerror == 5
    assert effects == [
        "create-temp",
        "open",
        "set",
        "close",
        "publish-failed",
        "remove-temp",
    ]


def test_issue_view_after_publish_checks_new_view_without_unlinking_target(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Successful publication still rejects an entry lacking reparse status."""
    item = directory(windows)
    target_scope(windows, monkeypatch)
    effects: list[str] = []
    published = False

    @contextmanager
    def child(_name: str, **_kwargs: Any) -> Any:
        effects.append("create-temp")
        yield None

    def exists(name: str) -> bool:
        return name.startswith(".view-") and not published

    def move(*_args: Any) -> bool:
        nonlocal published
        published = True
        effects.append("publish")
        return True

    def info(*_args: Any, **_kwargs: Any) -> Any:
        if published:
            return SimpleNamespace(attributes=windows.DIRECTORY)
        return SimpleNamespace(attributes=windows.DIRECTORY | windows.REPARSE)

    monkeypatch.setattr(item, "_entry", lambda _name: Path(r"C:\work\AGENT-30"))
    monkeypatch.setattr(item, "child", child)
    monkeypatch.setattr(item, "exists", exists)
    monkeypatch.setattr(
        windows, "open_handle", lambda *_args, **_kwargs: effects.append("open") or 91
    )
    monkeypatch.setattr(windows, "information", info)
    monkeypatch.setattr(windows, "DeviceIoControl", lambda *_args: effects.append("set") or True)
    monkeypatch.setattr(windows, "CloseHandle", lambda _handle: effects.append("close") or True)
    monkeypatch.setattr(windows, "MoveFile", move)
    monkeypatch.setattr(
        windows,
        "RemoveDirectory",
        lambda _path: (_ for _ in ()).throw(AssertionError("published target must remain")),
    )
    with pytest.raises(WorkspaceError) as captured:
        item.issue_view("AGENT-30", r"C:\store\AGENT-30")
    assert captured.value.code == "UNSAFE_PATH"
    assert effects == ["create-temp", "open", "set", "close", "publish", "open", "close"]
