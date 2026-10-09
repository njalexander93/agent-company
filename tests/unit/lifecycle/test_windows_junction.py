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
    item = windows.Directory.__new__(windows.Directory)
    # Pin the modeled path and handle identity for native checks.
    item.path = Path(r"C:\work")
    item._handles = [101]
    item.identity = [7, 13]
    return item


def target_scope(windows: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a temporary target directory for junction scenarios.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """

    @contextmanager
    def absolute(_cls: Any, _target: str) -> Any:
        """Yield a controlled resource for target scope.

        Args:
            _cls: Unused cls argument accepted by this fake.
            _target: Unused target argument accepted by this fake.

        Returns:
            Context manager for the simulated absolute directory.
        """
        yield None

    # Install fake absolute calls.
    monkeypatch.setattr(windows.Directory, "absolute", classmethod(absolute))


def test_issue_view_rejects_nonjunction_and_closes_handle(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue view rejects nonjunction and closes handle.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Construct the fake directory with its pinned handle identity.
    item = directory(windows)
    # Install the exact target path expected in the mount-point payload.
    target_scope(windows, monkeypatch)
    # Install fake _entry, exists, open_handle and related calls.
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
    # Attempt junction publication with the selected native response.
    with pytest.raises(WorkspaceError) as captured:
        item.issue_view("AGENT-30", r"C:\store\AGENT-30")
    # Confirm the rejected operation reports UNSAFE_PATH.
    assert captured.value.code == "UNSAFE_PATH"
    assert closed == [91]


def test_issue_view_rejects_foreign_reparse_payload(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue view rejects foreign reparse payload.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Construct the fake directory with its pinned handle identity.
    item = directory(windows)
    # Install the exact target path expected in the mount-point payload.
    target_scope(windows, monkeypatch)
    # Install fake _entry, exists, open_handle and related calls.
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
        """Write a controlled native reparse or path response.

        Args:
            _handle: Unused handle argument accepted by this fake.
            control: Windows device-control code supplied to the fake.
            _input: Unused input argument accepted by this fake.
            _input_len: Unused input len argument accepted by this fake.
            output: Native output buffer filled by the fake.
            _output_len: Unused output len argument accepted by this fake.
            returned: Native output parameter for the byte count.
            _overlapped: Unused overlapped argument accepted by this fake.

        Returns:
            Success flag after writing the selected reparse data.
        """
        calls.append(control)
        c.memmove(output, b"foreign", 7)
        c.cast(returned, c.POINTER(windows.w.DWORD))[0] = windows.w.DWORD(7)
        return True

    # Install fake DeviceIoControl calls.
    monkeypatch.setattr(windows, "DeviceIoControl", io_control)
    # Attempt junction publication with the selected native response.
    with pytest.raises(WorkspaceError) as captured:
        item.issue_view("AGENT-30", r"C:\store\AGENT-30")
    # Confirm the rejected operation reports UNSAFE_PATH.
    assert captured.value.code == "UNSAFE_PATH"
    assert calls == [0x900A8]
    assert closed == [91]


def test_issue_view_accepts_exact_mount_point_data(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue view accepts exact mount point data.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Construct the fake directory with its pinned handle identity.
    item = directory(windows)
    # Install the exact target path expected in the mount-point payload.
    target_scope(windows, monkeypatch)
    # Install fake _entry, exists, open_handle and related calls.
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
        """Write a controlled native reparse or path response.

        Args:
            _handle: Unused handle argument accepted by this fake.
            control: Windows device-control code supplied to the fake.
            _input: Unused input argument accepted by this fake.
            _input_len: Unused input len argument accepted by this fake.
            output: Native output buffer filled by the fake.
            _output_len: Unused output len argument accepted by this fake.
            returned: Native output parameter for the byte count.
            _overlapped: Unused overlapped argument accepted by this fake.

        Returns:
            Success flag after writing the selected reparse data.
        """
        assert control == 0x900A8
        # Copy the controlled reparse payload into the native output buffer.
        c.memmove(output, native_payload, len(native_payload))
        c.cast(returned, c.POINTER(windows.w.DWORD))[0] = windows.w.DWORD(len(native_payload))
        return True

    # Install fake DeviceIoControl calls.
    monkeypatch.setattr(windows, "DeviceIoControl", io_control)
    # Publish and validate the exact target junction.
    item.issue_view("AGENT-30", target)
    # Check recorded closed against the required side effects.
    assert closed == [91]


def test_issue_view_creation_race_removes_only_temporary_junction(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A collision leaves the existing view for exact payload validation.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Construct the fake directory with its pinned handle identity.
    item = directory(windows)
    # Install the exact target path expected in the mount-point payload.
    target_scope(windows, monkeypatch)
    # Start an effect log for the required operation order.
    effects: list[str] = []

    @contextmanager
    def child(_name: str, **_kwargs: Any) -> Any:
        """Yield a controlled resource for issue view creation race removes only temporary junction.

        Args:
            _name: Unused name argument accepted by this fake.
            **_kwargs: Extra options accepted to match the collaborator signature.

        Returns:
            Fake child directory or issue context for the caller.
        """
        effects.append("temporary-directory")
        yield None

    # Install fake _entry, child, exists and related calls.
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
        """Write a controlled native reparse or path response.

        Args:
            _handle: Unused handle argument accepted by this fake.
            control: Windows device-control code supplied to the fake.
            _input: Unused input argument accepted by this fake.
            _input_len: Unused input len argument accepted by this fake.
            _output: Unused output argument accepted by this fake.
            _output_len: Unused output len argument accepted by this fake.
            _returned: Unused returned argument accepted by this fake.
            _overlapped: Unused overlapped argument accepted by this fake.

        Returns:
            Success flag after writing the selected reparse data.
        """
        effects.append("set" if control == 0x900A4 else "get")
        return control == 0x900A4

    # Install fake DeviceIoControl calls.
    monkeypatch.setattr(windows, "DeviceIoControl", io_control)
    # Attempt junction publication with the selected native response.
    with pytest.raises(OSError) as captured:  # noqa: PT011 - native error is asserted.
        item.issue_view("AGENT-30", r"C:\store\AGENT-30")
    # Check recorded effects against the required side effects.
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
    """A noncollision rename failure is reported and its temp junction is removed.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Construct the fake directory with its pinned handle identity.
    item = directory(windows)
    # Install the exact target path expected in the mount-point payload.
    target_scope(windows, monkeypatch)
    # Start an effect log for the required operation order.
    effects: list[str] = []

    @contextmanager
    def child(_name: str, **_kwargs: Any) -> Any:
        """Yield a controlled resource for issue view rejects unexpected publish failure and
        removes temp.

        Args:
            _name: Unused name argument accepted by this fake.
            **_kwargs: Extra options accepted to match the collaborator signature.

        Returns:
            Fake child directory or issue context for the caller.
        """
        effects.append("create-temp")
        yield None

    # Install fake _entry, child, exists and related calls.
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
    # Attempt junction publication with the selected native response.
    with pytest.raises(OSError) as captured:  # noqa: PT011 - native code is asserted.
        item.issue_view("AGENT-30", r"C:\store\AGENT-30")
    # Check recorded effects against the required side effects.
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
    """Successful publication still rejects an entry lacking reparse status.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Construct the fake directory with its pinned handle identity.
    item = directory(windows)
    # Install the exact target path expected in the mount-point payload.
    target_scope(windows, monkeypatch)
    # Start an effect log for the required operation order.
    effects: list[str] = []
    published = False

    @contextmanager
    def child(_name: str, **_kwargs: Any) -> Any:
        """Yield a controlled resource for issue view after publish checks new view without
        unlinking target.

        Args:
            _name: Unused name argument accepted by this fake.
            **_kwargs: Extra options accepted to match the collaborator signature.

        Returns:
            Fake child directory or issue context for the caller.
        """
        effects.append("create-temp")
        yield None

    def exists(name: str) -> bool:
        """Model the native resource state for issue view after publish checks new view without
        unlinking target.

        Args:
            name: Selected filesystem name or parameterized input.

        Returns:
            Whether the requested test entry exists.
        """
        return name.startswith(".view-") and not published

    def move(*_args: Any) -> bool:
        """Model the native resource state for issue view after publish checks new view without
        unlinking target.

        Args:
            *_args: Extra arguments accepted to match the collaborator signature.

        Returns:
            Whether publishing the temporary entry succeeded.
        """
        # Track whether the temporary junction was published.
        nonlocal published
        published = True
        effects.append("publish")
        return True

    def info(*_args: Any, **_kwargs: Any) -> Any:
        """Model the native resource state for issue view after publish checks new view without
        unlinking target.

        Args:
            *_args: Extra arguments accepted to match the collaborator signature.
            **_kwargs: Extra options accepted to match the collaborator signature.

        Returns:
            Native attributes for the temporary or published entry.
        """
        # Check the published outcome and its alternative.
        if published:
            # Report ordinary directory attributes after publication.
            return SimpleNamespace(attributes=windows.DIRECTORY)
        return SimpleNamespace(attributes=windows.DIRECTORY | windows.REPARSE)

    # Install fake _entry, child, exists and related calls.
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
    # Attempt junction publication with the selected native response.
    with pytest.raises(WorkspaceError) as captured:
        item.issue_view("AGENT-30", r"C:\store\AGENT-30")
    # Confirm the rejected operation reports UNSAFE_PATH.
    assert captured.value.code == "UNSAFE_PATH"
    assert effects == ["create-temp", "open", "set", "close", "publish", "open", "close"]
