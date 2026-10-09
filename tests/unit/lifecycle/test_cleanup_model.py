"""Model cleanup's identity and complete-read guards before native deletion."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from agent_company.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit


class Payload:
    """Hold one quarantinable issue payload and observe direct file effects."""

    def __init__(self, files: dict[str, bytes], effects: list[str]) -> None:
        """Retain exact file bytes and a shared ordered effect log."""
        self.files = files.copy()
        self.identity = [1, 2]
        self.effects = effects
        self.context: Payload | None = None

    def __enter__(self) -> Payload:
        """Open the modeled issue directory."""
        return self

    def __exit__(self, *_args: object) -> None:
        """Close the modeled issue directory."""

    def names(self) -> list[str]:
        """List only direct regular payload entries."""
        return [*self.files, *(["context"] if self.context is not None else [])]

    def read(self, name: str, _limit: int = core.MAX_FILE) -> bytes:
        """Record validation before any deletion."""
        self.effects.append("read:" + name)
        return self.files[name]

    def unlink(self, name: str) -> None:
        """Delete a previously validated direct file."""
        self.effects.append("unlink:" + name)
        del self.files[name]

    def exists(self, name: str) -> bool:
        """Check a direct file after cleanup."""
        return name in self.files or (name == "context" and self.context is not None)

    def child(self, name: str, *, private: bool) -> Payload:
        """Open only a direct context directory after validating the expected mode."""
        assert name == "context" and private is False and self.context is not None
        return self.context

    def rmdir(self, name: str) -> None:
        """Remove the emptied direct context directory after its notes are deleted."""
        assert name == "context" and self.context is not None and self.context.files == {}
        self.effects.append("rmdir:context")
        self.context = None

    def flush(self) -> None:
        """Record flush before quarantine removal."""
        self.effects.append("payload-flush")


class Control:
    """Hold cleanup intent, quarantine, and a modeled tombstone."""

    def __init__(self, intent: dict[str, Any], effects: list[str]) -> None:
        """Keep the durable cleanup intent before any mutation."""
        self.docs: dict[str, Any] = {"cleanup.json": copy.deepcopy(intent)}
        self.quarantine: Payload | None = None
        self.effects = effects

    def exists(self, name: str) -> bool:
        """Report a stored control document or quarantine directory."""
        return name in self.docs or (name == "quarantine-1" and self.quarantine is not None)

    def json(self, name: str) -> Any:
        """Read the durable intent or state document."""
        return copy.deepcopy(self.docs[name])

    def child(self, name: str, **_kwargs: object) -> Payload:
        """Open only the recorded quarantine directory."""
        assert name == "quarantine-1" and self.quarantine is not None
        return self.quarantine

    def rmdir(self, name: str) -> None:
        """Remove the empty quarantine after payload deletion."""
        assert name == "quarantine-1"
        assert self.quarantine is not None and self.quarantine.files == {}
        self.effects.append("rmdir:quarantine-1")
        self.quarantine = None

    def names(self) -> list[str]:
        """List direct control documents for export pruning."""
        return list(self.docs)

    def read(self, name: str) -> bytes:
        """Validate a stored export before removal."""
        self.effects.append("read:" + name)
        return b"export"

    def unlink(self, name: str) -> None:
        """Remove a validated control document."""
        self.effects.append("unlink:" + name)
        del self.docs[name]

    def put(self, name: str, value: object) -> None:
        """Publish the cleaned tombstone only after quarantine removal."""
        self.effects.append("put:" + name)
        self.docs[name] = copy.deepcopy(value)


class Task:
    """Expose and quarantine one canonical issue directory."""

    def __init__(self, payload: Payload, effects: list[str]) -> None:
        """Retain the current canonical payload."""
        self.payload: Payload | None = payload
        self.effects = effects

    def exists(self, identifier: str) -> bool:
        """Report the canonical payload only before quarantine."""
        assert identifier == "AGENT-30"
        return self.payload is not None

    def child(self, identifier: str, **_kwargs: object) -> Payload:
        """Open the canonical issue payload for identity validation."""
        assert identifier == "AGENT-30" and self.payload is not None
        return self.payload

    def rename_directory(self, identifier: str, control: Control, name: str) -> None:
        """Move only the validated issue into its recorded quarantine."""
        assert identifier == "AGENT-30" and name == "quarantine-1"
        self.effects.append("rename:quarantine-1")
        control.quarantine, self.payload = self.payload, None


def issue_with_cleanup(files: dict[str, bytes]) -> tuple[Any, Payload, Control, list[str]]:
    """Build one exact cleanup intent and a matching payload identity."""
    effects: list[str] = []
    payload = Payload(files, effects)
    state = {
        "storage": "present",
        "requests": {core.sha(b"request-1"): {"result": {"ok": True}}},
        "export": {"snapshot": "snapshot"},
        "generation": 3,
        "checkpoint": {"text": "private"},
        "provenance": {"roadmap.md": {"source": "private"}},
    }
    intent = {
        "quarantine": "quarantine-1",
        "directory_identity": [1, 2],
        "files": core.manifest({"roadmap.md": b"approved", "events.jsonl": b""}),
        "request_id": "request-1",
        "state": state,
    }
    control = Control(intent, effects)
    issue = type("IssueHandle", (), {})()
    issue.id = "AGENT-30"
    issue.control = control
    issue.store = type("StoreHandle", (), {"task": Task(payload, effects)})()
    issue.state = copy.deepcopy(state)
    return issue, payload, control, effects


def test_cleanup_rejects_changed_payload_before_quarantine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve every file when the on-disk manifest differs from the intent."""
    issue, payload, control, effects = issue_with_cleanup(
        {"roadmap.md": b"changed", "events.jsonl": b""}
    )
    monkeypatch.setattr(core, "inventory", lambda directory: directory.files.copy())
    with pytest.raises(core.WorkspaceError) as captured:
        core.finish_cleanup(issue)  # type: ignore[arg-type]
    assert captured.value.code == "UNTRACKED_CHANGE"
    assert payload.files["roadmap.md"] == b"changed"
    assert control.quarantine is None
    assert effects == []


def test_cleanup_reads_all_quarantined_bytes_before_deleting_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Publish a tombstone only after validated bytes and quarantine are removed."""
    issue, payload, control, effects = issue_with_cleanup(
        {"roadmap.md": b"approved", "events.jsonl": b""}
    )
    monkeypatch.setattr(core, "inventory", lambda directory: directory.files.copy())
    core.finish_cleanup(issue)  # type: ignore[arg-type]
    assert payload.files == {}
    assert issue.store.task.payload is None
    assert issue.state["storage"] == "cleaned"
    assert issue.state["tombstone"]["snapshot"] == "snapshot"
    assert "checkpoint" not in issue.state
    assert "provenance" not in issue.state
    assert "cleanup.json" not in control.docs
    assert effects.index("read:roadmap.md") < effects.index("unlink:roadmap.md")
    assert effects.index("read:events.jsonl") < effects.index("unlink:events.jsonl")
    assert effects.index("rmdir:quarantine-1") < effects.index("put:state.json")


def test_cleanup_resumes_recorded_quarantine_without_second_rename(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resume only the recorded quarantine after the canonical payload already moved."""
    issue, payload, control, effects = issue_with_cleanup(
        {"roadmap.md": b"approved", "events.jsonl": b""}
    )
    issue.store.task.payload = None
    control.quarantine = payload
    monkeypatch.setattr(core, "inventory", lambda directory: directory.files.copy())
    core.finish_cleanup(issue)  # type: ignore[arg-type]
    assert "rename:quarantine-1" not in effects
    assert "rmdir:quarantine-1" in effects


def test_cleanup_publishes_tombstone_after_quarantine_already_removed() -> None:
    """Replay after durable deletion finishes state publication without a second unlink."""
    issue, _payload, control, effects = issue_with_cleanup(
        {"roadmap.md": b"approved", "events.jsonl": b""}
    )
    issue.store.task.payload = None
    assert control.quarantine is None
    core.finish_cleanup(issue)  # type: ignore[arg-type]
    assert issue.state["storage"] == "cleaned"
    assert "cleanup.json" not in control.docs
    assert not any(effect.startswith(("rename:", "unlink:roadmap.md")) for effect in effects)
    assert "put:state.json" in effects
    assert issue.state["storage"] == "cleaned"


def test_cleanup_rejects_unlisted_quarantine_entry_before_any_unlink() -> None:
    """Keep all bytes when quarantine contains a path outside the supported grammar."""
    issue, payload, control, effects = issue_with_cleanup(
        {"roadmap.md": b"approved", "events.jsonl": b"", "secrets.txt": b"keep"}
    )
    issue.store.task.payload = None
    control.quarantine = payload
    with pytest.raises(core.WorkspaceError) as captured:
        core.finish_cleanup(issue)  # type: ignore[arg-type]
    assert captured.value.code == "UNSAFE_PATH"
    assert payload.files["secrets.txt"] == b"keep"
    assert not any(effect.startswith("unlink:") for effect in effects)


def test_cleanup_validates_export_before_pruning_and_publishing_tombstone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read each exact export document before deleting its local control copy."""
    issue, _payload, control, effects = issue_with_cleanup(
        {"roadmap.md": b"approved", "events.jsonl": b""}
    )
    export_name = "export-" + "a" * 64 + ".json"
    control.docs[export_name] = {"content": "local copy"}
    monkeypatch.setattr(core, "inventory", lambda directory: directory.files.copy())
    core.finish_cleanup(issue)  # type: ignore[arg-type]
    assert export_name not in control.docs
    assert effects.index("read:" + export_name) < effects.index("unlink:" + export_name)
    assert effects.index("unlink:" + export_name) < effects.index("put:state.json")


def test_cleanup_reads_context_note_before_any_delete_and_removes_empty_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Delete nested notes only through their validated context handle."""
    issue, payload, control, effects = issue_with_cleanup(
        {"roadmap.md": b"approved", "events.jsonl": b""}
    )
    payload.context = Payload({"note.md": b"scoped note"}, effects)
    control.docs["cleanup.json"]["files"] = core.manifest(  # type: ignore[index]
        {"roadmap.md": b"approved", "events.jsonl": b"", "context/note.md": b"scoped note"}
    )

    def inventory(directory: Payload) -> dict[str, bytes]:
        """Expose only the inspected root and direct context note bytes."""
        return {**directory.files, "context/note.md": directory.context.files["note.md"]}  # type: ignore[union-attr]

    monkeypatch.setattr(core, "inventory", inventory)
    core.finish_cleanup(issue)  # type: ignore[arg-type]
    assert payload.context is None
    assert effects.index("read:note.md") < effects.index("unlink:note.md")
    assert effects.index("unlink:note.md") < effects.index("rmdir:context")
    assert effects.index("rmdir:context") < effects.index("rmdir:quarantine-1")
