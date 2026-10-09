"""Model ownership and ACL decisions at the Windows kernel boundary."""

from __future__ import annotations

import ctypes as c
import struct
import sys
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


def test_sid_text_frees_native_string(windows: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sid text frees native string."""
    allocated = c.create_unicode_buffer("S-1-5-21-42")
    freed: list[int] = []

    def convert(_sid: Any, address: Any) -> bool:
        c.cast(address, c.POINTER(windows.P))[0] = c.cast(allocated, windows.P)
        return True

    monkeypatch.setattr(windows, "SidToString", convert)
    monkeypatch.setattr(windows, "LocalFree", lambda value: freed.append(value.value) or None)
    assert windows.sid_text(101) == "S-1-5-21-42"
    assert freed == [c.addressof(allocated)]


def test_token_sid_uses_requested_class_and_closes_token(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Token sid uses requested class and closes token."""
    classes: list[int] = []
    closed: list[int] = []

    def open_token(_process: Any, _rights: int, address: Any) -> bool:
        c.cast(address, c.POINTER(windows.w.HANDLE))[0] = windows.w.HANDLE(81)
        return True

    def token_info(
        _token: Any, information_class: int, buffer: Any, _size: Any, length: Any
    ) -> bool:
        classes.append(information_class)
        c.cast(length, c.POINTER(windows.w.DWORD))[0] = windows.w.DWORD(c.sizeof(windows.P))
        if buffer is not None:
            c.cast(buffer, c.POINTER(windows.P))[0] = windows.P(99)
        return buffer is not None

    monkeypatch.setattr(windows, "GetCurrentProcess", lambda: 17)
    monkeypatch.setattr(windows, "OpenProcessToken", open_token)
    monkeypatch.setattr(windows, "GetTokenInformation", token_info)
    monkeypatch.setattr(windows, "sid_text", lambda _sid: "S-1-5-21-42")
    monkeypatch.setattr(
        windows, "CloseHandle", lambda handle: closed.append(int(handle.value)) or True
    )
    assert windows.token_sid(4) == "S-1-5-21-42"
    assert classes == [4, 4]
    assert closed == [81]


def test_private_security_protects_descriptor_and_frees_it(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Private security protects descriptor and frees it."""
    descriptors: list[str] = []
    freed: list[int] = []

    def convert(sddl: str, _version: int, address: Any, _size: Any) -> bool:
        descriptors.append(sddl)
        c.cast(address, c.POINTER(windows.P))[0] = windows.P(123)
        return True

    monkeypatch.setattr(windows, "token_sid", lambda: "S-1-5-21-42")
    monkeypatch.setattr(windows, "ConvertSecurity", convert)
    monkeypatch.setattr(windows, "LocalFree", lambda value: freed.append(value.value) or None)
    with windows.private_security() as attributes:
        assert attributes.length == c.sizeof(windows.SecurityAttributes)
        assert attributes.descriptor == 123
        assert attributes.inherit == 0
    assert descriptors == [
        "O:S-1-5-21-42D:P(A;OICI;FA;;;S-1-5-21-42)(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"
    ]
    assert freed == [123]


@pytest.mark.parametrize(
    ("owner", "private", "ace_kind", "ace_flags", "ace_mask", "ace_sid", "expected"),
    [
        ("user", False, 0, 0, 0, "foreign", None),
        ("owner", True, 0, 0, 0x40000000, "user", None),
        ("user", True, 0, 0, 0x40000000, "foreign", "UNSAFE_PATH"),
        ("user", True, 0, 0x08, 0x40000000, "foreign", None),
        ("user", True, 0, 0, 0x00000001, "foreign", None),
        ("user", True, 1, 0, 0x40000000, "foreign", None),
        ("user", True, 9, 0, 0, "foreign", "UNSAFE_PATH"),
        ("foreign", False, 0, 0, 0, "foreign", "UNSAFE_PATH"),
    ],
)
def test_validate_security_owner_and_ace_policy(
    windows: Any,
    monkeypatch: pytest.MonkeyPatch,
    owner: str,
    private: bool,
    ace_kind: int,
    ace_flags: int,
    ace_mask: int,
    ace_sid: str,
    expected: str | None,
) -> None:
    """Validate security owner and ace policy."""
    acl = c.create_string_buffer(16)
    acl[4:6] = struct.pack("<H", 1)
    ace = c.create_string_buffer(
        struct.pack("<BBHI", ace_kind, ace_flags, 12, ace_mask) + b"\0" * 4
    )
    freed: list[int] = []

    def security(
        _handle: int,
        _kind: int,
        _flags: int,
        owner_out: Any,
        _group: Any,
        dacl_out: Any,
        _sacl: Any,
        descriptor_out: Any,
    ) -> int:
        c.cast(owner_out, c.POINTER(windows.P))[0] = windows.P(111)
        c.cast(dacl_out, c.POINTER(windows.P))[0] = windows.P(c.addressof(acl))
        c.cast(descriptor_out, c.POINTER(windows.P))[0] = windows.P(333)
        return 0

    def sid_text(value: Any) -> str:
        pointer = int(value.value) if isinstance(value, windows.P) else int(value)
        if pointer == 111:
            return {"user": "S-1-5-21-42", "owner": "S-1-5-21-43", "foreign": "S-1-5-21-99"}[owner]
        return {"user": "S-1-5-21-42", "foreign": "S-1-5-21-99"}[ace_sid]

    def get_ace(_acl: Any, _index: int, address: Any) -> bool:
        c.cast(address, c.POINTER(windows.P))[0] = windows.P(c.addressof(ace))
        return True

    monkeypatch.setattr(windows, "GetSecurityInfo", security)
    monkeypatch.setattr(windows, "GetAce", get_ace)
    monkeypatch.setattr(windows, "sid_text", sid_text)
    monkeypatch.setattr(
        windows,
        "token_sid",
        lambda information_class=1: "S-1-5-21-43" if information_class == 4 else "S-1-5-21-42",
    )
    monkeypatch.setattr(windows, "LocalFree", lambda pointer: freed.append(pointer.value) or None)
    if expected:
        with pytest.raises(WorkspaceError) as captured:
            windows.validate_security(91, private)
        assert captured.value.code == expected
    else:
        windows.validate_security(91, private)
    assert freed == [333]


def test_validate_security_rejects_null_dacl(windows: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Validate security rejects null dacl."""

    def security(
        _handle: int,
        _kind: int,
        _flags: int,
        owner_out: Any,
        _group: Any,
        _dacl_out: Any,
        _sacl: Any,
        descriptor_out: Any,
    ) -> int:
        c.cast(owner_out, c.POINTER(windows.P))[0] = windows.P(111)
        c.cast(descriptor_out, c.POINTER(windows.P))[0] = windows.P(333)
        return 0

    freed: list[int] = []
    monkeypatch.setattr(windows, "GetSecurityInfo", security)
    monkeypatch.setattr(windows, "token_sid", lambda information_class=1: "S-1-5-21-42")
    monkeypatch.setattr(windows, "sid_text", lambda _pointer: "S-1-5-21-42")
    monkeypatch.setattr(windows, "LocalFree", lambda pointer: freed.append(pointer.value) or None)
    with pytest.raises(WorkspaceError) as captured:
        windows.validate_security(91, True)
    assert captured.value.code == "UNSAFE_PATH"
    assert freed == [333]


def test_validate_security_reports_native_query_code(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GetSecurityInfo returns an error code directly."""
    monkeypatch.setattr(windows, "GetSecurityInfo", lambda *_args: 5)
    with pytest.raises(OSError) as captured:  # noqa: PT011 - winerror is asserted.
        windows.validate_security(91, True)
    assert captured.value.winerror == 5


def test_validate_security_releases_descriptor_when_ace_read_fails(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bad ACE read leaves no allocated descriptor behind."""
    acl = c.create_string_buffer(16)
    acl[4:6] = struct.pack("<H", 1)
    freed: list[int] = []

    def security(
        _handle: int,
        _kind: int,
        _flags: int,
        owner_out: Any,
        _group: Any,
        dacl_out: Any,
        _sacl: Any,
        descriptor_out: Any,
    ) -> int:
        c.cast(owner_out, c.POINTER(windows.P))[0] = windows.P(111)
        c.cast(dacl_out, c.POINTER(windows.P))[0] = windows.P(c.addressof(acl))
        c.cast(descriptor_out, c.POINTER(windows.P))[0] = windows.P(333)
        return 0

    monkeypatch.setattr(windows, "GetSecurityInfo", security)
    monkeypatch.setattr(windows, "token_sid", lambda information_class=1: "S-1-5-21-42")
    monkeypatch.setattr(windows, "sid_text", lambda _sid: "S-1-5-21-42")
    monkeypatch.setattr(windows, "GetAce", lambda *_args: False)
    monkeypatch.setattr(windows.c, "get_last_error", lambda: 87)
    monkeypatch.setattr(windows, "LocalFree", lambda pointer: freed.append(pointer.value) or None)
    with pytest.raises(OSError) as captured:  # noqa: PT011 - winerror is asserted.
        windows.validate_security(91, True)
    assert captured.value.winerror == 87
    assert freed == [333]


def test_token_sid_closes_token_after_information_failure(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed token detail query releases the process token."""
    closed: list[int] = []

    def open_token(_process: Any, _rights: int, address: Any) -> bool:
        c.cast(address, c.POINTER(windows.w.HANDLE))[0] = windows.w.HANDLE(81)
        return True

    def token_info(_token: Any, _class: int, buffer: Any, _size: Any, length: Any) -> bool:
        c.cast(length, c.POINTER(windows.w.DWORD))[0] = windows.w.DWORD(8)
        return buffer is None

    monkeypatch.setattr(windows, "GetCurrentProcess", lambda: 17)
    monkeypatch.setattr(windows, "OpenProcessToken", open_token)
    monkeypatch.setattr(windows, "GetTokenInformation", token_info)
    monkeypatch.setattr(windows.c, "get_last_error", lambda: 5)
    monkeypatch.setattr(windows, "CloseHandle", lambda handle: closed.append(handle.value) or True)
    with pytest.raises(OSError) as captured:  # noqa: PT011 - winerror is asserted.
        windows.token_sid()
    assert captured.value.winerror == 5
    assert closed == [81]


def test_private_security_rejects_descriptor_conversion_failure(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed conversion cannot yield an unprotected creation descriptor."""
    monkeypatch.setattr(windows, "token_sid", lambda: "S-1-5-21-42")
    monkeypatch.setattr(windows, "ConvertSecurity", lambda *_args: False)
    monkeypatch.setattr(windows.c, "get_last_error", lambda: 87)
    with pytest.raises(OSError) as captured:  # noqa: PT011 - winerror is asserted.
        with windows.private_security():
            pass
    assert captured.value.winerror == 87


def test_sid_text_rejects_conversion_failure_without_free(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No allocation is released when SID conversion never succeeded."""
    freed: list[int] = []
    monkeypatch.setattr(windows, "SidToString", lambda *_args: False)
    monkeypatch.setattr(windows.c, "get_last_error", lambda: 87)
    monkeypatch.setattr(windows, "LocalFree", lambda _value: freed.append(1))
    with pytest.raises(OSError) as captured:  # noqa: PT011 - winerror is asserted.
        windows.sid_text(windows.P(91))
    assert captured.value.winerror == 87
    assert freed == []


def test_token_sid_rejects_token_open_failure_without_close(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed token open has no acquired handle to close."""
    closed: list[int] = []
    monkeypatch.setattr(windows, "GetCurrentProcess", lambda: 17)
    monkeypatch.setattr(windows, "OpenProcessToken", lambda *_args: False)
    monkeypatch.setattr(windows.c, "get_last_error", lambda: 5)
    monkeypatch.setattr(windows, "CloseHandle", lambda _handle: closed.append(1))
    with pytest.raises(OSError) as captured:  # noqa: PT011 - winerror is asserted.
        windows.token_sid()
    assert captured.value.winerror == 5
    assert closed == []
