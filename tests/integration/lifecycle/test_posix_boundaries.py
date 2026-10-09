"""Exercise POSIX descriptor and permission boundaries with disposable files."""

import errno
import os
from pathlib import Path

import pytest

from agent_company.lifecycle import task_workspace as core

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.name != "posix", reason="Requires native POSIX directory descriptors"),
]


def test_held_directory_stays_on_original_inode_after_path_replacement(tmp_path: Path) -> None:
    """A replaced path cannot redirect writes through an already opened descriptor.

    Args:
        tmp_path: Disposable directory owned by this test.
    """
    # Resolve the disposable root before opening directory descriptors.
    root = tmp_path.resolve()
    # Hold the original directory descriptor while its pathname changes.
    with core.Directory.absolute(root) as directory:
        # Hold both directories open while replacing one pathname.
        with directory.child("held", True) as held, directory.child("outside", True) as outside:
            # Seed distinct bytes so a redirected write would be visible.
            held.write("note.md", b"original")
            outside.write("note.md", b"outside sentinel")
            # Move the original path and replace it with a link to the outside tree.
            (root / "held").rename(root / "quarantined")
            (root / "held").symlink_to(root / "outside", target_is_directory=True)

            # Write through the held descriptor and verify only its inode changes.
            held.write("note.md", b"updated original")
            assert held.read("note.md") == b"updated original"
            assert (root / "quarantined/note.md").read_bytes() == b"updated original"
            assert (root / "outside/note.md").read_bytes() == b"outside sentinel"
            # Reject reopening the replacement link as a canonical child.
            with pytest.raises(OSError) as error:  # noqa: PT011 - errno is the portable contract.
                directory.child("held")
            assert error.value.errno in {errno.ELOOP, errno.ENOTDIR}


def test_group_writable_file_is_rejected_without_replacement(tmp_path: Path) -> None:
    """Unsafe permissions block both reads and atomic replacement of existing bytes.

    Args:
        tmp_path: Disposable directory owned by this test.
    """
    # Resolve the disposable root and create a safe file first.
    root = tmp_path.resolve()
    # Create safe bytes before weakening their permissions.
    with core.Directory.absolute(root) as directory:
        directory.write("note.md", b"original")
        # Grant group write permission to make the existing file unsafe.
        note = root / "note.md"
        note.chmod(0o620)
        # Reject both read and replacement while retaining original bytes.
        try:
            # Reject reads from the newly unsafe existing file.
            with pytest.raises(core.WorkspaceError, match="UNSAFE_PATH"):
                directory.read("note.md")
            # Reject replacement without changing the original bytes.
            with pytest.raises(core.WorkspaceError, match="UNSAFE_PATH"):
                directory.write("note.md", b"forbidden")
            assert note.read_bytes() == b"original"
            assert sorted(directory.names()) == ["note.md"]
        finally:
            # Restore permissions so disposable cleanup can remove the file.
            note.chmod(0o600)
