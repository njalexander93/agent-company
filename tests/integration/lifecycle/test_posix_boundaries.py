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
    """A replaced path cannot redirect writes through an already opened descriptor."""
    root = tmp_path.resolve()
    with core.Directory.absolute(root) as directory:
        with directory.child("held", True) as held, directory.child("outside", True) as outside:
            held.write("note.md", b"original")
            outside.write("note.md", b"outside sentinel")
            (root / "held").rename(root / "quarantined")
            (root / "held").symlink_to(root / "outside", target_is_directory=True)

            held.write("note.md", b"updated original")
            assert held.read("note.md") == b"updated original"
            assert (root / "quarantined/note.md").read_bytes() == b"updated original"
            assert (root / "outside/note.md").read_bytes() == b"outside sentinel"
            with pytest.raises(OSError) as error:  # noqa: PT011 - errno is the portable contract.
                directory.child("held")
            assert error.value.errno in {errno.ELOOP, errno.ENOTDIR}


def test_group_writable_file_is_rejected_without_replacement(tmp_path: Path) -> None:
    """Unsafe permissions block both reads and atomic replacement of existing bytes."""
    root = tmp_path.resolve()
    with core.Directory.absolute(root) as directory:
        directory.write("note.md", b"original")
        note = root / "note.md"
        note.chmod(0o620)
        try:
            with pytest.raises(core.WorkspaceError, match="UNSAFE_PATH"):
                directory.read("note.md")
            with pytest.raises(core.WorkspaceError, match="UNSAFE_PATH"):
                directory.write("note.md", b"forbidden")
            assert note.read_bytes() == b"original"
            assert sorted(directory.names()) == ["note.md"]
        finally:
            note.chmod(0o600)
