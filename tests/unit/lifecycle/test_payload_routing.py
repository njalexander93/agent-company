"""Check supported issue payload enumeration and direct-directory routing."""

from __future__ import annotations

import pytest

from agent_company.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit


class PayloadDirectory:
    """Keep one level of direct files with explicit metadata and child access."""

    def __init__(
        self, files: dict[str, bytes], children: dict[str, PayloadDirectory] | None = None
    ):
        """Store byte-exact direct entries and optional direct child directories.

        Args:
            files: File contents used to build or inspect the workspace.
            children: Child-directory responses keyed by requested name.
        """
        # Build file contents for the simulated directory.
        self.files = files
        self.children = children or {}
        self.reads: list[tuple[str, int]] = []
        self.writes: list[tuple[str, bytes]] = []
        self.opens: list[tuple[str, bool, bool]] = []

    def __enter__(self) -> PayloadDirectory:
        """Expose a modeled owned child directory.

        Returns:
            This fake context manager for the enclosed operation.
        """
        return self

    def __exit__(self, *_args: object) -> None:
        """Close the modeled child directory.

        Args:
            *_args: Exception details supplied by the context-manager protocol.
        """

    def names(self) -> list[str]:
        """List only direct entries of the selected directory.

        Returns:
            Names currently visible in the fake directory.
        """
        return [*self.files, *self.children]

    def read(self, name: str, limit: int = core.MAX_FILE) -> bytes:
        """Record exact reads, including excluded metadata validation.

        Args:
            name: Requested file, directory, or control-entry name.
            limit: Maximum size or count allowed by the operation.

        Returns:
            Bytes returned by the fake file or provider read.
        """
        self.reads.append((name, limit))
        return self.files[name]

    def write(self, name: str, data: bytes) -> None:
        """Capture a direct atomic publication target and its exact bytes.

        Args:
            name: Requested file, directory, or control-entry name.
            data: Bytes passed to the simulated write.
        """
        self.writes.append((name, data))

    def child(self, name: str, create: bool = False, private: bool = True) -> PayloadDirectory:
        """Open one direct child with the requested create and privacy rules.

        Args:
            name: Requested file, directory, or control-entry name.
            create: Whether the native call requests file creation.
            private: Whether the resource should be private to this issue.

        Returns:
            Fake child directory or issue context for the caller.
        """
        self.opens.append((name, create, private))
        return self.children[name]


def test_directory_json_and_put_use_strict_canonical_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decode strict JSON and publish sorted canonical JSON through native methods.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Route directory reads and writes through in-memory byte stores.
    values = {"entry": b'{"z":1,"a":2}'}
    monkeypatch.setattr(core.NativeDirectory, "read", lambda _self, name: values[name])
    writes: list[tuple[str, bytes]] = []
    monkeypatch.setattr(
        core.NativeDirectory,
        "write",
        lambda _self, name, data: writes.append((name, data)),
    )
    # Allocate a directory object without opening native resources.
    directory = core.Directory.__new__(core.Directory)
    # Confirm the fake directory decoded the stored JSON bytes.
    assert directory.json("entry") == {"z": 1, "a": 2}
    # Serialize the structured value into canonical file bytes.
    directory.put("entry", {"z": 1, "a": 2})
    # Check the stored JSON matches canonical serialization.
    assert writes == [("entry", core.canonical({"z": 1, "a": 2}))]
    # Replace the stored entry with malformed JSON content.
    values["entry"] = b'{"same":1,"same":2}'
    # Exercise directory.json and capture the expected failure.
    with pytest.raises(core.WorkspaceError) as captured:
        directory.json("entry")
    # Confirm the rejected operation reports INVALID_REQUEST.
    assert captured.value.code == "INVALID_REQUEST"


def test_inventory_reads_only_supported_payload_and_validates_excluded_metadata() -> None:
    """Validate metadata bytes while keeping them out of the archive inventory."""
    # Resolve the disposable root for the filesystem boundary.
    notes = PayloadDirectory({"step-1.md": b"step", "._step-1.md": b"finder"})
    root = PayloadDirectory(
        {"roadmap.md": b"roadmap", "events.jsonl": b"", ".DS_Store": b"metadata"},
        {"context": notes},
    )
    # Check only canonical issue files appear in the inventory.
    assert core.inventory(root) == {
        "roadmap.md": b"roadmap",
        "events.jsonl": b"",
        "context/step-1.md": b"step",
    }
    assert root.reads == [
        ("roadmap.md", core.MAX_FILE),
        ("events.jsonl", core.MAX_FILE),
        (".DS_Store", core.MAX_FILE),
    ]
    assert notes.reads == [("step-1.md", core.MAX_FILE), ("._step-1.md", core.MAX_FILE)]
    assert root.opens == [("context", False, False)]


def test_inventory_requires_roadmap_only_when_requested() -> None:
    """Allow recovery snapshots without a roadmap but reject a live absent roadmap."""
    # Resolve the disposable root for the filesystem boundary.
    root = PayloadDirectory({"events.jsonl": b"history"})
    # Check only canonical issue files appear in the inventory.
    assert core.inventory(root, require_roadmap=False) == {"events.jsonl": b"history"}
    # Reject the unsafe payload path before accepting its inventory.
    with pytest.raises(core.WorkspaceError) as captured:
        core.inventory(root)
    # Confirm the rejected operation reports RECOVERY_REQUIRED.
    assert captured.value.code == "RECOVERY_REQUIRED"


def test_inventory_rejects_unsupported_nested_payload_without_descending() -> None:
    """Reject arbitrary root entries before opening or reading their contents."""
    # Resolve the disposable root for the filesystem boundary.
    root = PayloadDirectory({"roadmap.md": b"roadmap", "secrets.txt": b"private"})
    # Reject the unsafe payload path before accepting its inventory.
    with pytest.raises(core.WorkspaceError) as captured:
        core.inventory(root)
    # Confirm the rejected operation reports UNSAFE_PATH.
    assert captured.value.code == "UNSAFE_PATH"
    assert ("secrets.txt", core.MAX_FILE) not in root.reads


def test_write_payload_routes_context_through_its_direct_handle() -> None:
    """Publish context notes through the context descriptor and root notes at root."""
    # Resolve the disposable root for the filesystem boundary.
    notes = PayloadDirectory({})
    root = PayloadDirectory({}, {"context": notes})
    # Apply the validated payload mapping to the fake directory.
    core.write_payload(root, "context/step-1.md", b"step")
    core.write_payload(root, "roadmap.md", b"roadmap")
    assert root.opens == [("context", True, False)]
    assert notes.writes == [("step-1.md", b"step")]
    assert root.writes == [("roadmap.md", b"roadmap")]


def test_write_payload_rejects_invalid_path_before_opening_child() -> None:
    """Perform no write or child creation for path traversal."""
    # Resolve the disposable root for the filesystem boundary.
    root = PayloadDirectory({})
    # Reject unsafe payload paths before any write reaches the fake.
    with pytest.raises(core.WorkspaceError) as captured:
        core.write_payload(root, "context/../outside", b"data")
    # Confirm the rejected operation reports UNSAFE_PATH.
    assert captured.value.code == "UNSAFE_PATH"
    assert root.opens == []
    assert root.writes == []
