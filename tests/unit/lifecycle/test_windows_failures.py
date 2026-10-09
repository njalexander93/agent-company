"""Model Windows NTFS API failure decisions on a native Windows interpreter."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agent_company.lifecycle._errors import WorkspaceError

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(
        sys.platform != "win32",
        reason="Windows native CreateFileW and NTFS handle APIs are unavailable on POSIX",
    ),
]


def test_extended_path_uses_local_namespace() -> None:
    """Preserve the exact drive path behind the extended local prefix."""
    from agent_company.lifecycle import _filesystem_windows as windows

    assert windows.extended(Path(r"C:\work\AGENT-30")) == r"\\?\C:\work\AGENT-30"


def test_open_handle_surfaces_native_failure_without_accepting_invalid_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raise the actual Windows error when CreateFileW rejects an entry."""
    from agent_company.lifecycle import _filesystem_windows as windows

    monkeypatch.setattr(windows, "CreateFile", lambda *_args: windows.INVALID_HANDLE)
    monkeypatch.setattr(windows.c, "get_last_error", lambda: 5)
    with pytest.raises(OSError) as captured:  # noqa: PT011 - winerror is the contract.
        windows.open_handle(Path(r"C:\work\AGENT-30"))
    assert captured.value.winerror == 5


def test_information_rejects_non_disk_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject a successful metadata call whose handle is not a disk file."""
    from agent_company.lifecycle import _filesystem_windows as windows

    monkeypatch.setattr(windows, "GetFileInformation", lambda *_args: True)
    monkeypatch.setattr(windows, "GetFileType", lambda _handle: 0)
    with pytest.raises(WorkspaceError) as captured:
        windows.information(7, directory=True)
    assert captured.value.code == "UNSAFE_PATH"
