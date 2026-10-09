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
        """Store byte-exact direct entries and optional direct child directories."""
        self.files = files
        self.children = children or {}
        self.reads: list[tuple[str, int]] = []
        self.writes: list[tuple[str, bytes]] = []
        self.opens: list[tuple[str, bool, bool]] = []

    def __enter__(self) -> PayloadDirectory:
        """Expose a modeled owned child directory."""
        return self

    def __exit__(self, *_args: object) -> None:
        """Close the modeled child directory."""

    def names(self) -> list[str]:
        """List only direct entries of the selected directory."""
        return [*self.files, *self.children]

    def read(self, name: str, limit: int = core.MAX_FILE) -> bytes:
        """Record exact reads, including excluded metadata validation."""
        self.reads.append((name, limit))
        return self.files[name]

    def write(self, name: str, data: bytes) -> None:
        """Capture a direct atomic publication target and its exact bytes."""
        self.writes.append((name, data))

    def child(self, name: str, create: bool = False, private: bool = True) -> PayloadDirectory:
        """Open one direct child with the requested create and privacy rules."""
        self.opens.append((name, create, private))
        return self.children[name]


def test_directory_json_and_put_use_strict_canonical_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decode strict JSON and publish sorted canonical JSON through native methods."""
    values = {"entry": b'{"z":1,"a":2}'}
    monkeypatch.setattr(core.NativeDirectory, "read", lambda _self, name: values[name])
    writes: list[tuple[str, bytes]] = []
    monkeypatch.setattr(
        core.NativeDirectory,
        "write",
        lambda _self, name, data: writes.append((name, data)),
    )
    directory = core.Directory.__new__(core.Directory)
    assert directory.json("entry") == {"z": 1, "a": 2}
    directory.put("entry", {"z": 1, "a": 2})
    assert writes == [("entry", core.canonical({"z": 1, "a": 2}))]
    values["entry"] = b'{"same":1,"same":2}'
    with pytest.raises(core.WorkspaceError) as captured:
        directory.json("entry")
    assert captured.value.code == "INVALID_REQUEST"


def test_inventory_reads_only_supported_payload_and_validates_excluded_metadata() -> None:
    """Validate metadata bytes while keeping them out of the archive inventory."""
    notes = PayloadDirectory({"step-1.md": b"step", "._step-1.md": b"finder"})
    root = PayloadDirectory(
        {"roadmap.md": b"roadmap", "events.jsonl": b"", ".DS_Store": b"metadata"},
        {"context": notes},
    )
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
    root = PayloadDirectory({"events.jsonl": b"history"})
    assert core.inventory(root, require_roadmap=False) == {"events.jsonl": b"history"}
    with pytest.raises(core.WorkspaceError) as captured:
        core.inventory(root)
    assert captured.value.code == "RECOVERY_REQUIRED"


def test_inventory_rejects_unsupported_nested_payload_without_descending() -> None:
    """Reject arbitrary root entries before opening or reading their contents."""
    root = PayloadDirectory({"roadmap.md": b"roadmap", "secrets.txt": b"private"})
    with pytest.raises(core.WorkspaceError) as captured:
        core.inventory(root)
    assert captured.value.code == "UNSAFE_PATH"
    assert ("secrets.txt", core.MAX_FILE) not in root.reads


def test_write_payload_routes_context_through_its_direct_handle() -> None:
    """Publish context notes through the context descriptor and root notes at root."""
    notes = PayloadDirectory({})
    root = PayloadDirectory({}, {"context": notes})
    core.write_payload(root, "context/step-1.md", b"step")
    core.write_payload(root, "roadmap.md", b"roadmap")
    assert root.opens == [("context", True, False)]
    assert notes.writes == [("step-1.md", b"step")]
    assert root.writes == [("roadmap.md", b"roadmap")]


def test_write_payload_rejects_invalid_path_before_opening_child() -> None:
    """Perform no write or child creation for path traversal."""
    root = PayloadDirectory({})
    with pytest.raises(core.WorkspaceError) as captured:
        core.write_payload(root, "context/../outside", b"data")
    assert captured.value.code == "UNSAFE_PATH"
    assert root.opens == []
    assert root.writes == []
