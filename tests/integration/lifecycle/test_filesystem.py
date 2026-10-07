"""Exercise actual platform filesystem safety and process locks in disposable paths.

These tests run on all three native operating systems. Windows-only ACL and handle
checks report explicit skips elsewhere; a POSIX run never certifies that backend.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_company.lifecycle import task_workspace as core

pytestmark = pytest.mark.integration


def test_atomic_bytes_identity_and_quarantine(tmp_path: Path) -> None:
    """Preserve exact binary bytes and directory identity through quarantine and deletion."""
    root = tmp_path.resolve()
    with core.Directory.absolute(root) as directory:
        with directory.child("payload", True) as payload:
            identity = payload.identity
            payload.write("note.md", b"first\r\n\x00\xff")
            assert payload.read("note.md") == b"first\r\n\x00\xff"
            payload.write("note.md", b"second\n")
            assert payload.read("note.md") == b"second\n"
            assert payload.names() == ["note.md"]
        with directory.child("control", True) as control:
            directory.rename_directory("payload", control, "quarantine")
            assert not directory.exists("payload")
            with control.child("quarantine") as moved:
                assert moved.identity == identity
                assert moved.read("note.md") == b"second\n"
                moved.unlink("note.md")
            control.rmdir("quarantine")
            assert not control.exists("quarantine")


@pytest.mark.parametrize(
    "name",
    [
        "../outside",
        "x/y",
        "x\\y",
        "x:stream",
        "..",
        ".",
        "",
        "note.md.",
        "note.md ",
        "CON",
        "con.md",
        "aux.md",
        "NUL",
        "COM1.md",
        "LPT9",
        "COM¹",
        "x\0y",
        "x?y",
    ],
)
def test_portable_name_rejection(tmp_path: Path, name: str) -> None:
    """Reject portable aliases before any file, directory or lock side effect."""
    with core.Directory.absolute(tmp_path.resolve()) as directory:
        for operation in [
            lambda: directory.write(name, b"bad"),
            lambda: directory.child(name, True),
            lambda: directory.read(name),
            lambda: directory.exists(name),
        ]:
            with pytest.raises(core.WorkspaceError, match="UNSAFE_PATH"):
                operation()
        with pytest.raises(core.WorkspaceError, match="UNSAFE_PATH"), directory.lock(name):
            pass
        assert directory.names() == []


def test_hardlink_rejection_preserves_other_file(tmp_path: Path) -> None:
    """Refuse hardlinked content for reads, replacement and lock acquisition."""
    root = tmp_path.resolve()
    with core.Directory.absolute(root) as directory:
        directory.write("outside.md", b"sentinel")
        os.link(root / "outside.md", root / "linked.md")
        for operation in [
            lambda: directory.read("linked.md"),
            lambda: directory.write("linked.md", b"bad"),
        ]:
            with pytest.raises(core.WorkspaceError, match="UNSAFE_PATH"):
                operation()
        with pytest.raises(core.WorkspaceError, match="UNSAFE_PATH"), directory.lock("linked.md"):
            pass
    assert (root / "outside.md").read_bytes() == b"sentinel"


def test_issue_view_reparse_boundary_and_collision(tmp_path: Path) -> None:
    """Share exact issue bytes without allowing a view to become a canonical directory."""
    root = tmp_path.resolve()
    with core.Directory.absolute(root) as directory:
        with directory.child("target", True) as target:
            target.write("note.md", b"shared")
        directory.issue_view("TEST-1", str(root / "target"))
        directory.issue_view("TEST-1", str(root / "target"))
        assert (root / "TEST-1/note.md").read_bytes() == b"shared"
        if sys.platform == "win32":
            assert (root / "TEST-1").is_junction()
        else:
            assert (root / "TEST-1").is_symlink()
        with pytest.raises((core.WorkspaceError, OSError)):
            directory.child("TEST-1")
        with pytest.raises((core.WorkspaceError, OSError)):
            core.Directory.absolute(root / "TEST-1")
        with directory.child("foreign", True):
            pass
        with pytest.raises(core.WorkspaceError, match="UNSAFE_PATH"):
            directory.issue_view("TEST-1", str(root / "foreign"))
        with pytest.raises(core.WorkspaceError, match="UNSAFE_PATH"):
            directory.issue_view("foreign", str(root / "target"))
        assert (root / "target/note.md").read_bytes() == b"shared"


def test_lock_contention_and_process_exit_release(tmp_path: Path) -> None:
    """Prove cross-process exclusion, bounded BUSY, crash release and persistent lock identity."""
    root = tmp_path.resolve()
    script = """
import sys
from agent_company.lifecycle.task_workspace import Directory
with Directory.absolute(sys.argv[1]) as directory, directory.lock():
    print('locked', flush=True)
    sys.stdin.read()
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "locked"
        identity = (root / "lock").stat().st_ino
        with core.Directory.absolute(root) as directory:
            with pytest.raises(core.WorkspaceError, match="BUSY"), directory.lock(timeout=0.05):
                pass
        process.kill()
        process.wait(timeout=5)
        with core.Directory.absolute(root) as directory, directory.lock(timeout=1):
            assert (root / "lock").stat().st_ino == identity
        assert (root / "lock").exists()
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)


@pytest.mark.skipif(sys.platform != "win32", reason="Requires native Windows NTFS handles and ACLs")
def test_windows_case_alias_and_pinned_ancestor(tmp_path: Path) -> None:
    """Reject case-confused replacements and prevent ancestor rename while pinned."""
    root = tmp_path.resolve()
    with core.Directory.absolute(root) as directory:
        with directory.child("pinned", True) as pinned:
            pinned.write("note.md", b"safe")
            with pytest.raises(core.WorkspaceError, match="UNSAFE_PATH"):
                pinned.write("NOTE.md", b"bad")
            with pytest.raises(OSError):
                (root / "pinned").rename(root / "moved")
            with pinned.child("nested", True) as nested:
                nested.write("inside.md", b"contained")
                with pytest.raises(OSError):
                    (root / "pinned").rename(root / "substituted")
                assert nested.read("inside.md") == b"contained"
            assert pinned.read("note.md") == b"safe"
        (root / "pinned").rename(root / "moved")
        assert (root / "moved/note.md").read_bytes() == b"safe"


@pytest.mark.skipif(sys.platform != "win32", reason="Requires native Windows security descriptors")
def test_windows_foreign_write_acl_is_rejected(tmp_path: Path) -> None:
    """Reject a user-owned file after granting Everyone write permission, preserving bytes."""
    root = tmp_path.resolve()
    with core.Directory.absolute(root) as directory:
        directory.write("note.md", b"safe")
        subprocess.run(
            ["icacls", str(root / "note.md"), "/grant", "*S-1-1-0:(W)"],
            check=True,
            capture_output=True,
        )
        with pytest.raises(core.WorkspaceError, match="UNSAFE_PATH"):
            directory.read("note.md")
        with pytest.raises(core.WorkspaceError, match="UNSAFE_PATH"):
            directory.write("note.md", b"bad")
        assert (root / "note.md").read_bytes() == b"safe"


@pytest.mark.skipif(sys.platform != "win32", reason="Requires native Windows path namespaces")
@pytest.mark.parametrize(
    "path",
    [
        "C:relative",
        "\\\\server\\share\\store",
        "\\\\?\\C:\\store",
        "\\\\.\\pipe\\store",
        "C:\\store:stream",
        "C:\\..\\store",
    ],
)
def test_windows_noncanonical_roots_are_rejected(path: str) -> None:
    """Reject network/device/drive-relative/traversal roots before store access."""
    with pytest.raises(core.WorkspaceError, match="UNSAFE_PATH"):
        core.Directory.absolute(path)


@pytest.mark.skipif(sys.platform != "win32", reason="Requires native Windows cross-process sharing")
def test_windows_competing_process_cannot_redirect_held_ancestor(tmp_path: Path) -> None:
    """Fence leaf/ancestor replacement and keep writes away from an outside junction target."""
    root = tmp_path.resolve()
    ancestor, outside = root / "ancestor", root / "outside"
    with core.Directory.absolute(root) as directory:
        with directory.child("ancestor", True) as parent, parent.child("leaf", True) as leaf:
            leaf.write("note.md", b"original")
        with directory.child("outside", True) as target, target.child("leaf", True) as target_leaf:
            target_leaf.write("note.md", b"outside sentinel")
        # Only the leaf handle remains: its own retained chain must pin the ancestor.
        with core.Directory.absolute(ancestor / "leaf") as leaf:
            identity = leaf.identity
            script = """
import json
import sys
import subprocess
from pathlib import Path
from tests.platform_support import link_directory
ancestor, outside = map(Path, sys.argv[1:])
result = {}
for label, source in [('leaf', ancestor / 'leaf'), ('ancestor', ancestor)]:
    moved = source.with_name(source.name + '-moved')
    try:
        source.rename(moved)
    except OSError:
        result[label + '_rename_denied'] = True
    else:
        result[label + '_rename_denied'] = False
        if label == 'leaf':
            moved.rename(source)
try:
    link_directory(ancestor, outside)
except (OSError, subprocess.CalledProcessError):
    result['junction_denied'] = True
else:
    result['junction_denied'] = False
print(json.dumps(result))
"""
            observed = subprocess.run(
                [sys.executable, "-c", script, str(ancestor), str(outside)],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            result = json.loads(observed.stdout)
            # Attempt a normal write after the competing substitution, then check both trees.
            try:
                leaf.write("note.md", b"updated original")
            except (core.WorkspaceError, OSError):
                result["write_succeeded"] = False
            else:
                result["write_succeeded"] = True
            assert (outside / "leaf/note.md").read_bytes() == b"outside sentinel"
            assert sorted(p.name for p in (outside / "leaf").iterdir()) == ["note.md"]
            assert result == {
                "leaf_rename_denied": True,
                "ancestor_rename_denied": True,
                "junction_denied": True,
                "write_succeeded": True,
            }
            with core.Directory.absolute(ancestor / "leaf") as reopened:
                assert reopened.identity == identity
                assert reopened.read("note.md") == b"updated original"
        ancestor.rename(root / "released")
        assert (root / "released/leaf/note.md").read_bytes() == b"updated original"
