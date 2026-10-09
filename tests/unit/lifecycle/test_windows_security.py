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
    """Import the Windows filesystem module for native contract tests.

    Returns:
        Imported Windows filesystem module.
    """
    from agent_company.lifecycle import _filesystem_windows

    return _filesystem_windows


def test_sid_text_frees_native_string(windows: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sid text frees native string.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Track native allocation and release of the SID string.
    allocated = c.create_unicode_buffer("S-1-5-21-42")
    freed: list[int] = []

    def convert(_sid: Any, address: Any) -> bool:
        """Simulate Windows token or descriptor access for sid text frees native string.

        Args:
            _sid: Unused sid argument accepted by this fake.
            address: Native output pointer filled by the fake.

        Returns:
            Success flag after writing an allocated descriptor pointer.
        """
        # Write the allocated string pointer through the native output.
        c.cast(address, c.POINTER(windows.P))[0] = c.cast(allocated, windows.P)
        return True

    # Install fake SidToString, LocalFree calls.
    monkeypatch.setattr(windows, "SidToString", convert)
    monkeypatch.setattr(windows, "LocalFree", lambda value: freed.append(value.value) or None)
    # Confirm the native SID conversion returns the expected user SID.
    assert windows.sid_text(101) == "S-1-5-21-42"
    assert freed == [c.addressof(allocated)]


def test_token_sid_uses_requested_class_and_closes_token(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Token sid uses requested class and closes token.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Track token-handle closure across success and failure.
    classes: list[int] = []
    closed: list[int] = []

    def open_token(_process: Any, _rights: int, address: Any) -> bool:
        """Simulate Windows token or descriptor access for token sid uses requested class and
        closes token.

        Args:
            _process: Unused process argument accepted by this fake.
            _rights: Unused rights argument accepted by this fake.
            address: Native output pointer filled by the fake.

        Returns:
            Success flag after writing a fake token handle.
        """
        # Write the acquired token handle into the native output parameter.
        c.cast(address, c.POINTER(windows.w.HANDLE))[0] = windows.w.HANDLE(81)
        return True

    def token_info(
        _token: Any, information_class: int, buffer: Any, _size: Any, length: Any
    ) -> bool:
        """Simulate Windows token or descriptor access for token sid uses requested class and
        closes token.

        Args:
            _token: Unused token argument accepted by this fake.
            information_class: Token information class requested by the code under test.
            buffer: Native output buffer filled by the fake.
            _size: Unused size argument accepted by this fake.
            length: Length reported by the fake native call.

        Returns:
            Whether the fake token information query succeeded.
        """
        classes.append(information_class)
        c.cast(length, c.POINTER(windows.w.DWORD))[0] = windows.w.DWORD(c.sizeof(windows.P))
        # Check the buffer is not None outcome and its alternative.
        if buffer is not None:
            # Write the token SID only when the caller supplied a buffer.
            c.cast(buffer, c.POINTER(windows.P))[0] = windows.P(99)
        return buffer is not None

    # Install fake GetCurrentProcess, OpenProcessToken, GetTokenInformation and related calls.
    monkeypatch.setattr(windows, "GetCurrentProcess", lambda: 17)
    monkeypatch.setattr(windows, "OpenProcessToken", open_token)
    monkeypatch.setattr(windows, "GetTokenInformation", token_info)
    monkeypatch.setattr(windows, "sid_text", lambda _sid: "S-1-5-21-42")
    monkeypatch.setattr(
        windows, "CloseHandle", lambda handle: closed.append(int(handle.value)) or True
    )
    # Confirm the native SID conversion returns the expected user SID.
    assert windows.token_sid(4) == "S-1-5-21-42"
    assert classes == [4, 4]
    assert closed == [81]


def test_private_security_protects_descriptor_and_frees_it(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Private security protects descriptor and frees it.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Track security descriptor creation and release.
    descriptors: list[str] = []
    freed: list[int] = []

    def convert(sddl: str, _version: int, address: Any, _size: Any) -> bool:
        """Simulate Windows token or descriptor access for private security protects descriptor and
        frees it.

        Args:
            sddl: Security descriptor text passed to the Windows API fake.
            _version: Unused version argument accepted by this fake.
            address: Native output pointer filled by the fake.
            _size: Unused size argument accepted by this fake.

        Returns:
            Success flag after writing an allocated descriptor pointer.
        """
        descriptors.append(sddl)
        c.cast(address, c.POINTER(windows.P))[0] = windows.P(123)
        return True

    # Install fake token_sid, ConvertSecurity, LocalFree calls.
    monkeypatch.setattr(windows, "token_sid", lambda: "S-1-5-21-42")
    monkeypatch.setattr(windows, "ConvertSecurity", convert)
    monkeypatch.setattr(windows, "LocalFree", lambda value: freed.append(value.value) or None)
    # Enter the private descriptor scope and inspect its native attributes.
    with windows.private_security() as attributes:
        # Confirm the native attribute size and inherited-handle policy.
        assert attributes.length == c.sizeof(windows.SecurityAttributes)
        assert attributes.descriptor == 123
        assert attributes.inherit == 0
    # Verify O:S-1-5-21-42D:P(A;OICI;FA;;;S-1-5-21-42)(A;OICI;FA;;;SY)(A;OICI;FA;;;BA) and the
    # associated result.
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
    """Validate security owner and ace policy.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
        owner: SID to publish as the descriptor owner.
        private: Whether the resource should be private to this issue.
        ace_kind: Access-control entry type to encode.
        ace_flags: Inheritance flags to encode in the entry.
        ace_mask: Permission mask to encode in the entry.
        ace_sid: SID to encode in the access-control entry.
        expected: Expected result for this input.
    """
    # Encode an ACL entry with the selected owner, flags, and mask.
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
        """Yield a controlled resource for validate security owner and ace policy.

        Args:
            _handle: Unused handle argument accepted by this fake.
            _kind: Unused kind argument accepted by this fake.
            _flags: Unused flags argument accepted by this fake.
            owner_out: Output pointer for the descriptor owner.
            _group: Unused group argument accepted by this fake.
            dacl_out: Output pointer for the access-control list.
            _sacl: Unused sacl argument accepted by this fake.
            descriptor_out: Output pointer for the allocated security descriptor.

        Returns:
            Context manager yielding the selected security attributes.
        """
        # Write the controlled security pointer into native output.
        c.cast(owner_out, c.POINTER(windows.P))[0] = windows.P(111)
        c.cast(dacl_out, c.POINTER(windows.P))[0] = windows.P(c.addressof(acl))
        c.cast(descriptor_out, c.POINTER(windows.P))[0] = windows.P(333)
        return 0

    def sid_text(value: Any) -> str:
        """Simulate Windows token or descriptor access for validate security owner and ace policy.

        Args:
            value: Input value provided to the fake or tested operation.

        Returns:
            SID string resolved from the supplied pointer.
        """
        # Decode the supplied SID pointer for the fake conversion.
        pointer = int(value.value) if isinstance(value, windows.P) else int(value)
        # Check the pointer == 111 outcome and its alternative.
        if pointer == 111:
            # Resolve the owner SID from the descriptor-owner pointer.
            return {"user": "S-1-5-21-42", "owner": "S-1-5-21-43", "foreign": "S-1-5-21-99"}[owner]
        return {"user": "S-1-5-21-42", "foreign": "S-1-5-21-99"}[ace_sid]

    def get_ace(_acl: Any, _index: int, address: Any) -> bool:
        """Simulate Windows token or descriptor access for validate security owner and ace policy.

        Args:
            _acl: Unused acl argument accepted by this fake.
            _index: Unused index argument accepted by this fake.
            address: Native output pointer filled by the fake.

        Returns:
            Success flag after writing an access-control entry pointer.
        """
        # Write the controlled security pointer into native output.
        c.cast(address, c.POINTER(windows.P))[0] = windows.P(c.addressof(ace))
        return True

    # Install fake GetSecurityInfo, GetAce, sid_text and related calls.
    monkeypatch.setattr(windows, "GetSecurityInfo", security)
    monkeypatch.setattr(windows, "GetAce", get_ace)
    monkeypatch.setattr(windows, "sid_text", sid_text)
    monkeypatch.setattr(
        windows,
        "token_sid",
        lambda information_class=1: "S-1-5-21-43" if information_class == 4 else "S-1-5-21-42",
    )
    monkeypatch.setattr(windows, "LocalFree", lambda pointer: freed.append(pointer.value) or None)
    # Check the expected outcome and its alternative.
    if expected:
        # Reject the unsafe descriptor while retaining native diagnostics.
        with pytest.raises(WorkspaceError) as captured:
            windows.validate_security(91, private)
        # Confirm the native API failure code reaches the caller.
        assert captured.value.code == expected
    else:
        # Handle the alternative to expected.
        windows.validate_security(91, private)
    assert freed == [333]


def test_validate_security_rejects_null_dacl(windows: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Validate security rejects null dacl.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """

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
        """Yield a controlled resource for validate security rejects null dacl.

        Args:
            _handle: Unused handle argument accepted by this fake.
            _kind: Unused kind argument accepted by this fake.
            _flags: Unused flags argument accepted by this fake.
            owner_out: Output pointer for the descriptor owner.
            _group: Unused group argument accepted by this fake.
            _dacl_out: Unused dacl out argument accepted by this fake.
            _sacl: Unused sacl argument accepted by this fake.
            descriptor_out: Output pointer for the allocated security descriptor.

        Returns:
            Context manager yielding the selected security attributes.
        """
        # Write the controlled security pointer into native output.
        c.cast(owner_out, c.POINTER(windows.P))[0] = windows.P(111)
        c.cast(descriptor_out, c.POINTER(windows.P))[0] = windows.P(333)
        return 0

    # Install fake GetSecurityInfo, token_sid, sid_text and related calls.
    freed: list[int] = []
    monkeypatch.setattr(windows, "GetSecurityInfo", security)
    monkeypatch.setattr(windows, "token_sid", lambda information_class=1: "S-1-5-21-42")
    monkeypatch.setattr(windows, "sid_text", lambda _pointer: "S-1-5-21-42")
    monkeypatch.setattr(windows, "LocalFree", lambda pointer: freed.append(pointer.value) or None)
    # Reject the unsafe descriptor while retaining native diagnostics.
    with pytest.raises(WorkspaceError) as captured:
        windows.validate_security(91, True)
    # Confirm the rejected operation reports UNSAFE_PATH.
    assert captured.value.code == "UNSAFE_PATH"
    assert freed == [333]


def test_validate_security_reports_native_query_code(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GetSecurityInfo returns an error code directly.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake GetSecurityInfo calls.
    monkeypatch.setattr(windows, "GetSecurityInfo", lambda *_args: 5)
    # Reject the unsafe descriptor while retaining native diagnostics.
    with pytest.raises(OSError) as captured:  # noqa: PT011 - winerror is asserted.
        windows.validate_security(91, True)
    assert captured.value.winerror == 5


def test_validate_security_releases_descriptor_when_ace_read_fails(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bad ACE read leaves no allocated descriptor behind.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Build the malformed ACL and track descriptor release.
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
        """Yield a controlled resource for validate security releases descriptor when ace read
        fails.

        Args:
            _handle: Unused handle argument accepted by this fake.
            _kind: Unused kind argument accepted by this fake.
            _flags: Unused flags argument accepted by this fake.
            owner_out: Output pointer for the descriptor owner.
            _group: Unused group argument accepted by this fake.
            dacl_out: Output pointer for the access-control list.
            _sacl: Unused sacl argument accepted by this fake.
            descriptor_out: Output pointer for the allocated security descriptor.

        Returns:
            Context manager yielding the selected security attributes.
        """
        # Write the controlled security pointer into native output.
        c.cast(owner_out, c.POINTER(windows.P))[0] = windows.P(111)
        c.cast(dacl_out, c.POINTER(windows.P))[0] = windows.P(c.addressof(acl))
        c.cast(descriptor_out, c.POINTER(windows.P))[0] = windows.P(333)
        return 0

    # Install fake GetSecurityInfo, token_sid, sid_text and related calls.
    monkeypatch.setattr(windows, "GetSecurityInfo", security)
    monkeypatch.setattr(windows, "token_sid", lambda information_class=1: "S-1-5-21-42")
    monkeypatch.setattr(windows, "sid_text", lambda _sid: "S-1-5-21-42")
    monkeypatch.setattr(windows, "GetAce", lambda *_args: False)
    monkeypatch.setattr(windows.c, "get_last_error", lambda: 87)
    monkeypatch.setattr(windows, "LocalFree", lambda pointer: freed.append(pointer.value) or None)
    # Reject the unsafe descriptor while retaining native diagnostics.
    with pytest.raises(OSError) as captured:  # noqa: PT011 - winerror is asserted.
        windows.validate_security(91, True)
    assert captured.value.winerror == 87
    assert freed == [333]


def test_token_sid_closes_token_after_information_failure(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed token detail query releases the process token.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Track token-handle closure across success and failure.
    closed: list[int] = []

    def open_token(_process: Any, _rights: int, address: Any) -> bool:
        """Simulate Windows token or descriptor access for token sid closes token after information
        failure.

        Args:
            _process: Unused process argument accepted by this fake.
            _rights: Unused rights argument accepted by this fake.
            address: Native output pointer filled by the fake.

        Returns:
            Success flag after writing a fake token handle.
        """
        # Write the acquired token handle into the native output parameter.
        c.cast(address, c.POINTER(windows.w.HANDLE))[0] = windows.w.HANDLE(81)
        return True

    def token_info(_token: Any, _class: int, buffer: Any, _size: Any, length: Any) -> bool:
        """Simulate Windows token or descriptor access for token sid closes token after information
        failure.

        Args:
            _token: Unused token argument accepted by this fake.
            _class: Unused class argument accepted by this fake.
            buffer: Native output buffer filled by the fake.
            _size: Unused size argument accepted by this fake.
            length: Length reported by the fake native call.

        Returns:
            Whether the fake token information query succeeded.
        """
        # Write the required token buffer length into native output.
        c.cast(length, c.POINTER(windows.w.DWORD))[0] = windows.w.DWORD(8)
        return buffer is None

    # Install fake GetCurrentProcess, OpenProcessToken, GetTokenInformation and related calls.
    monkeypatch.setattr(windows, "GetCurrentProcess", lambda: 17)
    monkeypatch.setattr(windows, "OpenProcessToken", open_token)
    monkeypatch.setattr(windows, "GetTokenInformation", token_info)
    monkeypatch.setattr(windows.c, "get_last_error", lambda: 5)
    monkeypatch.setattr(windows, "CloseHandle", lambda handle: closed.append(handle.value) or True)
    # Reject the failed token query and close only acquired handles.
    with pytest.raises(OSError) as captured:  # noqa: PT011 - winerror is asserted.
        windows.token_sid()
    # Check recorded closed against the required side effects.
    assert captured.value.winerror == 5
    assert closed == [81]


def test_private_security_rejects_descriptor_conversion_failure(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed conversion cannot yield an unprotected creation descriptor.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake token_sid, ConvertSecurity, get_last_error calls.
    monkeypatch.setattr(windows, "token_sid", lambda: "S-1-5-21-42")
    monkeypatch.setattr(windows, "ConvertSecurity", lambda *_args: False)
    monkeypatch.setattr(windows.c, "get_last_error", lambda: 87)
    # Exercise private security rejects descriptor conversion failure and capture the expected
    # failure.
    with pytest.raises(OSError) as captured:  # noqa: PT011 - winerror is asserted.
        # Fail before yielding attributes from an invalid descriptor.
        with windows.private_security():
            pass
    assert captured.value.winerror == 87


def test_sid_text_rejects_conversion_failure_without_free(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No allocation is released when SID conversion never succeeded.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake SidToString, get_last_error, LocalFree calls.
    freed: list[int] = []
    monkeypatch.setattr(windows, "SidToString", lambda *_args: False)
    monkeypatch.setattr(windows.c, "get_last_error", lambda: 87)
    monkeypatch.setattr(windows, "LocalFree", lambda _value: freed.append(1))
    # Surface SID conversion failure without freeing an absent allocation.
    with pytest.raises(OSError) as captured:  # noqa: PT011 - winerror is asserted.
        windows.sid_text(windows.P(91))
    assert captured.value.winerror == 87
    assert freed == []


def test_token_sid_rejects_token_open_failure_without_close(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed token open has no acquired handle to close.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake GetCurrentProcess, OpenProcessToken, get_last_error and related calls.
    closed: list[int] = []
    monkeypatch.setattr(windows, "GetCurrentProcess", lambda: 17)
    monkeypatch.setattr(windows, "OpenProcessToken", lambda *_args: False)
    monkeypatch.setattr(windows.c, "get_last_error", lambda: 5)
    monkeypatch.setattr(windows, "CloseHandle", lambda _handle: closed.append(1))
    # Reject the failed token query and close only acquired handles.
    with pytest.raises(OSError) as captured:  # noqa: PT011 - winerror is asserted.
        windows.token_sid()
    # Check recorded closed against the required side effects.
    assert captured.value.winerror == 5
    assert closed == []
