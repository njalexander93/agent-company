"""Exercise lifecycle decisions with a bounded in-memory persistence adapter."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from agent_company.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit


def base_request(operation: str, **fields: object) -> dict[str, Any]:
    """Create one explicitly bound request for the modeled issue."""
    return {
        "schema_version": 1,
        "operation": operation,
        "request_id": "request-1",
        "host": "codex",
        "session_id": "session",
        "issue_id": "AGENT-30",
        "binding_generation": 2,
        **fields,
    }


class MemoryControl:
    """Expose only the cleanup-intent check used by normal operations."""

    def __init__(self) -> None:
        """Start without persistent control documents."""
        self.data: dict[str, object] = {}

    def exists(self, name: str) -> bool:
        """Report no recorded cleanup intent in this active issue."""
        assert name == "cleanup.json"
        return False

    def put(self, name: str, value: object) -> None:
        """Retain a control document for later read-back."""
        self.data[name] = copy.deepcopy(value)

    def json(self, name: str) -> Any:
        """Return the exact previously published control document."""
        return copy.deepcopy(self.data[name])


class MemoryStore:
    """Record binding and view effects without opening Git or filesystem state."""

    def __init__(self) -> None:
        """Hold the bound session and observed view calls."""
        self.registration = {"repo_id": "repo"}
        self.views: list[str] = []
        self.saved: list[str] = []
        self.task = None

    def binding(self) -> dict[str, object]:
        """Return the existing exact issue and generation binding."""
        return {"issue_id": "AGENT-30", "binding_generation": 2}

    def view(self, identifier: str) -> None:
        """Record that the issue view is exposed to this participant."""
        self.views.append(identifier)

    def save_binding(self, _state: dict[str, Any], key: str) -> None:
        """Record a binding persistence effect for attach flows."""
        self.saved.append(key)


class MemoryIssue:
    """Retain committed state and files while recording requested transactions."""

    def __init__(self, *, acknowledged: bool = True) -> None:
        """Start with one assigned participant and immutable note bytes."""
        self.id = "AGENT-30"
        self.control = MemoryControl()
        self.store = MemoryStore()
        self.recoveries = 0
        self.commits: list[tuple[str | None, dict[str, Any], dict[str, bytes]]] = []
        key = core.participant_key(base_request("read"))
        packet = [
            {
                "id": "roadmap",
                "locator": "roadmap.md",
                "sha256": core.sha(b"approved roadmap"),
                "required": True,
                "authority": "task-workspace",
                "reason": "issue-resume",
                "stage": "execution",
                "reader": key,
            }
        ]
        self.state: dict[str, Any] = {
            "schema_version": 1,
            "repo_id": "repo",
            "issue_id": self.id,
            "issue_uuid": "uuid",
            "revision": 7,
            "generation": 1,
            "disposition": "active",
            "storage": "present",
            "coordinator": key,
            "owners": {"roadmap.md": key, "context/note.md": key},
            "participants": {
                key: {
                    "generation": 2,
                    "status": "ready" if acknowledged else "attached",
                    "pending": {},
                    "packet": packet,
                    "ack": core.sha(core.canonical(packet)) if acknowledged else None,
                }
            },
            "files": {
                "roadmap.md": core.sha(b"approved roadmap"),
                "context/note.md": core.sha(b"old note"),
                "events.jsonl": core.sha(b""),
            },
            "seq": 0,
            "head": None,
            "requests": {},
            "provenance": {},
        }
        self.payload = {
            "roadmap.md": b"approved roadmap",
            "context/note.md": b"old note",
            "events.jsonl": b"",
        }

    def recover(self) -> None:
        """Record that recovery precedes every operation."""
        self.recoveries += 1

    def files(self) -> dict[str, bytes]:
        """Return the current committed payload as an isolated mutable copy."""
        return self.payload.copy()

    def committed_state(self) -> dict[str, Any]:
        """Return the currently committed control state."""
        return copy.deepcopy(self.state)

    def commit(
        self,
        state: dict[str, Any],
        files: dict[str, bytes],
        _request: dict[str, Any],
        result: dict[str, Any],
        event_type: str | None = None,
    ) -> dict[str, Any]:
        """Persist modeled bytes and expose the requested event and state effects."""
        self.commits.append((event_type, copy.deepcopy(state), files.copy()))
        self.state = copy.deepcopy(state)
        self.payload = files.copy()
        return {"ok": True, "code": "OK", **result}


def test_read_returns_only_assigned_matching_references_without_commit() -> None:
    """Read the packet and current revision without changing state."""
    store, issue = MemoryStore(), MemoryIssue()
    before = copy.deepcopy(issue.state)
    result = core.operate(store, issue, base_request("read"))  # type: ignore[arg-type]
    assert result["ok"] is True
    assert result["revision"] == 7
    assert [ref["id"] for ref in result["references"]] == ["roadmap"]
    assert result["references"][0]["available"] is True
    assert issue.state == before
    assert issue.commits == []
    assert issue.recoveries == 1


def test_diagnose_reports_absent_and_present_issue_without_binding_read() -> None:
    """Diagnosis reveals only committed issue presence and storage state."""
    store, issue = MemoryStore(), MemoryIssue()
    result = core.operate(store, issue, base_request("diagnose"))  # type: ignore[arg-type]
    assert result == {
        "ok": True,
        "code": "PRESENT",
        "repo_id": "repo",
        "issue_id": "AGENT-30",
        "revision": 7,
        "storage": "present",
    }
    issue.state = {}
    result = core.operate(store, issue, base_request("diagnose"))  # type: ignore[arg-type]
    assert result == {
        "ok": True,
        "code": "ABSENT",
        "repo_id": "repo",
        "issue_id": "AGENT-30",
        "revision": None,
        "storage": "absent",
    }
    assert issue.commits == []


def test_ready_rejects_unacknowledged_packet_without_commit() -> None:
    """Block ordinary tools until the assigned packet was acknowledged."""
    store, issue = MemoryStore(), MemoryIssue(acknowledged=False)
    before = copy.deepcopy(issue.state)
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, base_request("ready"))  # type: ignore[arg-type]
    assert captured.value.code == "NOT_READY"
    assert issue.state == before
    assert issue.commits == []


def test_acknowledge_updates_status_only_for_matching_packet_digest() -> None:
    """Commit readiness only when the caller names the current packet digest."""
    store, issue = MemoryStore(), MemoryIssue(acknowledged=False)
    digest = core.sha(core.canonical(next(iter(issue.state["participants"].values()))["packet"]))
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, base_request("acknowledge", packet_digest="wrong"))  # type: ignore[arg-type]
    assert captured.value.code == "SOURCE_STALE"
    assert issue.commits == []
    result = core.operate(store, issue, base_request("acknowledge", packet_digest=digest))  # type: ignore[arg-type]
    assert result["ok"] is True
    member = next(iter(issue.state["participants"].values()))
    assert (member["status"], member["ack"]) == ("ready", digest)
    assert issue.commits[-1][0] == "acknowledge"


def test_update_replaces_owned_note_with_attributable_provenance() -> None:
    """Commit exact replacement bytes and record their source and author."""
    store, issue = MemoryStore(), MemoryIssue()
    key = issue.state["coordinator"]
    source = {"id": "issue", "locator": "context/issue.md", "sha256": core.sha(b"source")}
    request = base_request(
        "update",
        expected_revision=7,
        path="context/note.md",
        old_digest=core.sha(b"old note"),
        content="new note é",
        provenance={"sources": [source], "applicability": "AGENT-30", "status": "draft"},
    )
    assert core.operate(store, issue, request)["ok"] is True  # type: ignore[arg-type]
    assert issue.payload["context/note.md"] == "new note é".encode()
    assert issue.state["provenance"]["context/note.md"]["author"] == key
    assert issue.state["provenance"]["context/note.md"]["sources"] == [source]
    assert issue.commits[-1][0] == "update"


def test_update_rejects_stale_digest_without_writing() -> None:
    """Leave payload and provenance unchanged for stale replacement content."""
    store, issue = MemoryStore(), MemoryIssue()
    before = copy.deepcopy(issue.state)
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(
            store,
            issue,
            base_request("update", expected_revision=7, path="context/note.md", old_digest="bad"),
        )  # type: ignore[arg-type]
    assert captured.value.code == "REVISION_CONFLICT"
    assert issue.state == before
    assert issue.commits == []


def test_tool_start_requires_ack_and_records_only_observed_id() -> None:
    """Reserve a supported tool after packet readiness and reject duplicates."""
    store, issue = MemoryStore(), MemoryIssue()
    request = base_request("tool-start", tool_id="tool-1")
    assert core.operate(store, issue, request)["ok"] is True  # type: ignore[arg-type]
    member = next(iter(issue.state["participants"].values()))
    assert member["pending"] == {"tool-1": {"status": "pending"}}
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, base_request("tool-start", tool_id="tool-1"))  # type: ignore[arg-type]
    assert captured.value.code == "REQUEST_CONFLICT"
    assert len(issue.commits) == 1


def test_tool_complete_requires_matching_pending_id_before_settlement() -> None:
    """Never infer completion for an unrecorded tool."""
    store, issue = MemoryStore(), MemoryIssue()
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, base_request("tool-complete", tool_id="other", completed=True))  # type: ignore[arg-type]
    assert captured.value.code == "UNKNOWN_OPERATION"
    assert issue.commits == []
    core.operate(store, issue, base_request("tool-start", tool_id="tool-1"))  # type: ignore[arg-type]
    result = core.operate(
        store, issue, base_request("tool-complete", tool_id="tool-1", completed=True)
    )  # type: ignore[arg-type]
    assert result["ok"] is True
    assert next(iter(issue.state["participants"].values()))["pending"] == {}


def test_tool_complete_retains_unknown_process_with_handle() -> None:
    """Keep asynchronous work pending under its observed handle."""
    store, issue = MemoryStore(), MemoryIssue()
    core.operate(store, issue, base_request("tool-start", tool_id="tool-1"))  # type: ignore[arg-type]
    core.operate(
        store,
        issue,
        base_request("tool-complete", tool_id="tool-1", completed=False, async_handle="pid-7"),
    )  # type: ignore[arg-type]
    member = next(iter(issue.state["participants"].values()))
    assert member["pending"] == {"tool-1": {"status": "unknown", "handle": "pid-7"}}


def test_scope_assigns_reader_packet_and_owned_note() -> None:
    """Replace one participant scope while retaining coordinator ownership."""
    store, issue = MemoryStore(), MemoryIssue()
    reader = core.participant_key({"host": "codex", "session_id": "reader"})
    packet = [
        {
            "id": "issue",
            "locator": "context/issue.md",
            "sha256": core.sha(b"issue"),
            "required": True,
            "authority": "approved-assignment",
            "reason": "step-5",
            "stage": "execution",
            "reader": reader,
        }
    ]
    result = core.operate(
        store,
        issue,
        base_request(
            "scope",
            expected_revision=7,
            target_participant=reader,
            packet=packet,
            owned_paths=["context/issue.md"],
        ),
    )  # type: ignore[arg-type]
    assert result["packet_digest"] == core.sha(core.canonical(packet))
    assert issue.state["assignments"][reader]["packet"] == packet
    assert issue.state["owners"]["context/issue.md"] == reader
    assert issue.state["owners"]["roadmap.md"] == issue.state["coordinator"]


def test_scope_updates_detached_reader_packet_without_reviving_generation() -> None:
    """A scope edit does not silently reattach a detached participant."""
    store, issue = MemoryStore(), MemoryIssue()
    reader = core.participant_key({"host": "codex", "session_id": "reader"})
    issue.state["participants"][reader] = {
        "generation": 4,
        "status": "detached",
        "pending": {},
        "packet": None,
        "ack": None,
    }
    packet = [
        {**issue.state["participants"][issue.state["coordinator"]]["packet"][0], "reader": reader}
    ]
    result = core.operate(
        store,
        issue,
        base_request("scope", expected_revision=7, target_participant=reader, packet=packet),
    )  # type: ignore[arg-type]
    assert result["ok"] is True
    assert issue.state["participants"][reader]["status"] == "detached"
    assert issue.state["participants"][reader]["packet"] == packet
    assert issue.state["participants"][reader]["ack"] is None


def test_join_grants_reader_only_roadmap_packet() -> None:
    """Attach a verified-ticket reader without transferring note or coordinator authority."""
    store, issue = MemoryStore(), MemoryIssue()
    request = base_request(
        "join",
        session_id="reader",
        issue_uuid="uuid",
        expected_revision=7,
    )
    result = core.operate(store, issue, request)  # type: ignore[arg-type]
    reader = core.participant_key(request)
    assert result["participant_id"] == reader
    assert issue.state["coordinator"] != reader
    assert reader not in issue.state["owners"].values()
    packet = issue.state["participants"][reader]["packet"]
    assert len(packet) == 1
    assert packet[0]["locator"] == "roadmap.md"
    assert packet[0]["reader"] == reader
    assert store.saved == [reader]


def test_scope_refuses_roadmap_transfer_without_commit() -> None:
    """Keep coordinator roadmap ownership outside scope grants."""
    store, issue = MemoryStore(), MemoryIssue()
    key = issue.state["coordinator"]
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(
            store,
            issue,
            base_request(
                "scope",
                expected_revision=7,
                target_participant=key,
                packet=next(iter(issue.state["participants"].values()))["packet"],
                owned_paths=["roadmap.md"],
            ),
        )  # type: ignore[arg-type]
    assert captured.value.code == "NOT_OWNER"
    assert issue.commits == []


def test_event_rejects_unbounded_payload_without_commit() -> None:
    """Allow only the bounded event vocabulary and public code field."""
    store, issue = MemoryStore(), MemoryIssue()
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(
            store, issue, base_request("event", event_type="arbitrary", event={"code": "OK"})
        )  # type: ignore[arg-type]
    assert captured.value.code == "INVALID_REQUEST"
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(
            store,
            issue,
            base_request("event", event_type="observation", event={"content": "secret"}),
        )  # type: ignore[arg-type]
    assert captured.value.code == "INVALID_REQUEST"
    assert issue.commits == []
    core.operate(
        store, issue, base_request("event", event_type="observation", event={"code": "UNKNOWN"})
    )  # type: ignore[arg-type]
    assert issue.commits[-1][0] == "observation"


def test_detach_requires_evidence_and_no_pending_work() -> None:
    """Refuse detachment while a tool is still unresolved."""
    store, issue = MemoryStore(), MemoryIssue()
    source = {"id": "handoff", "locator": "context/handoff.md", "sha256": core.sha(b"handoff")}
    core.operate(store, issue, base_request("tool-start", tool_id="tool-1"))  # type: ignore[arg-type]
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, base_request("detach", evidence=[source]))  # type: ignore[arg-type]
    assert captured.value.code == "PENDING_OPERATION"
    assert next(iter(issue.state["participants"].values()))["status"] == "ready"
    core.operate(store, issue, base_request("tool-complete", tool_id="tool-1", completed=True))  # type: ignore[arg-type]
    core.operate(store, issue, base_request("detach", evidence=[source]))  # type: ignore[arg-type]
    member = next(iter(issue.state["participants"].values()))
    assert (member["status"], member["ack"]) == ("detached", None)


def test_outcome_blocks_terminal_transition_with_pending_work() -> None:
    """Retain the active issue until admitted work settles."""
    store, issue = MemoryStore(), MemoryIssue()
    source = {"id": "decision", "locator": "context/decision.md", "sha256": core.sha(b"done")}
    core.operate(store, issue, base_request("tool-start", tool_id="tool-1"))  # type: ignore[arg-type]
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(
            store,
            issue,
            base_request(
                "outcome", expected_revision=7, disposition="cancelled", evidence=[source]
            ),
        )  # type: ignore[arg-type]
    assert captured.value.code == "PENDING_OPERATION"
    assert issue.state["disposition"] == "active"


def test_outcome_requires_abandonment_for_failed_issue() -> None:
    """Require explicit abandonment evidence before failed terminal status."""
    store, issue = MemoryStore(), MemoryIssue()
    source = {"id": "decision", "locator": "context/decision.md", "sha256": core.sha(b"failed")}
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(
            store,
            issue,
            base_request("outcome", expected_revision=7, disposition="failed", evidence=[source]),
        )  # type: ignore[arg-type]
    assert captured.value.code == "EVIDENCE_REQUIRED"
    assert issue.state["disposition"] == "active"
    core.operate(
        store,
        issue,
        base_request(
            "outcome", expected_revision=7, disposition="failed", evidence=[source], abandoned=True
        ),
    )  # type: ignore[arg-type]
    assert issue.state["disposition"] == "failed"
    assert issue.state["outcome"]["evidence"] == [source]


def test_poll_transport_requires_unique_parent_handle() -> None:
    """Reject a poll whose process handle was never observed."""
    store, issue = MemoryStore(), MemoryIssue()
    core.operate(store, issue, base_request("tool-start", tool_id="process"))  # type: ignore[arg-type]
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(
            store,
            issue,
            base_request("tool-start", tool_id="poll", poll_handle="pid-7"),
        )  # type: ignore[arg-type]
    assert captured.value.code == "UNKNOWN_OPERATION"
    assert next(iter(issue.state["participants"].values()))["pending"] == {
        "process": {"status": "pending"}
    }


def test_poll_transport_settles_without_losing_running_parent() -> None:
    """Remove a completed poll while retaining its still-running process."""
    store, issue = MemoryStore(), MemoryIssue()
    core.operate(store, issue, base_request("tool-start", tool_id="process"))  # type: ignore[arg-type]
    core.operate(
        store,
        issue,
        base_request("tool-complete", tool_id="process", completed=False, async_handle="pid-7"),
    )  # type: ignore[arg-type]
    core.operate(store, issue, base_request("tool-start", tool_id="poll", poll_handle="pid-7"))  # type: ignore[arg-type]
    core.operate(
        store,
        issue,
        base_request(
            "tool-complete", tool_id="poll", poll=True, async_handle="pid-7", completed=False
        ),
    )  # type: ignore[arg-type]
    member = next(iter(issue.state["participants"].values()))
    assert member["pending"] == {"process": {"status": "unknown", "handle": "pid-7"}}


def test_tool_complete_rejects_handle_change_without_mutating_pending() -> None:
    """Preserve the original handle when a later observation conflicts."""
    store, issue = MemoryStore(), MemoryIssue()
    core.operate(store, issue, base_request("tool-start", tool_id="process"))  # type: ignore[arg-type]
    core.operate(
        store,
        issue,
        base_request("tool-complete", tool_id="process", completed=False, async_handle="pid-7"),
    )  # type: ignore[arg-type]
    before = copy.deepcopy(issue.state)
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(
            store,
            issue,
            base_request("tool-complete", tool_id="process", completed=True, async_handle="pid-8"),
        )  # type: ignore[arg-type]
    assert captured.value.code == "REQUEST_CONFLICT"
    assert issue.state == before


def test_archive_prepare_freezes_current_payload_after_preparation_event() -> None:
    """Publish a reconstructable export only after the preparation commit."""
    store, issue = MemoryStore(), MemoryIssue()
    result = core.operate(store, issue, base_request("archive-prepare", expected_revision=7))  # type: ignore[arg-type]
    assert result["ok"] is True
    assert issue.commits[0][0] == "archive-prepare"
    assert issue.commits[1][0] is None
    assert issue.state["export"]["snapshot"] == result["snapshot"]
    assert issue.state["export"]["files"] == core.manifest(issue.payload)
    assert (
        issue.control.json("export-" + result["snapshot"] + ".json")["snapshot"]
        == result["snapshot"]
    )


def test_archive_prepare_refuses_pending_work_without_export() -> None:
    """Avoid freezing a snapshot while a recorded tool remains unresolved."""
    store, issue = MemoryStore(), MemoryIssue()
    core.operate(store, issue, base_request("tool-start", tool_id="tool-1"))  # type: ignore[arg-type]
    prior_count = len(issue.commits)
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, base_request("archive-prepare", expected_revision=7))  # type: ignore[arg-type]
    assert captured.value.code == "PENDING_OPERATION"
    assert len(issue.commits) == prior_count
    assert "export" not in issue.state


def test_archive_index_accepts_only_matching_readback_part() -> None:
    """Build the index from the saved export's exact part content."""
    store, issue = MemoryStore(), MemoryIssue()
    prepared = core.operate(store, issue, base_request("archive-prepare", expected_revision=7))  # type: ignore[arg-type]
    part = prepared["parts"][0]
    observation = {
        "id": "part-1",
        "url": "https://linear.app/team/document/part-1",
        "issue": "uuid",
        "updatedAt": "2026-10-08T00:00:00Z",
        "content": part["content"],
        "origin": "linear_get_document",
        "request_id": "readback",
    }
    index = core.operate(store, issue, base_request("archive-index", observations=[observation]))  # type: ignore[arg-type]
    parsed = core.parse_document(index["content"])
    assert parsed["snapshot"] == prepared["snapshot"]
    assert parsed["parts"][0]["id"] == "part-1"
    assert issue.state["index_request"]["content"] == index["content"]
    assert issue.commits[-1][0] is None


def test_archive_index_rejects_changed_provider_part_without_commit() -> None:
    """Reject a provider read-back that differs from the frozen export."""
    store, issue = MemoryStore(), MemoryIssue()
    core.operate(store, issue, base_request("archive-prepare", expected_revision=7))  # type: ignore[arg-type]
    before_count = len(issue.commits)
    observation = {
        "id": "part-1",
        "url": "https://linear.app/team/document/part-1",
        "issue": "uuid",
        "updatedAt": "2026-10-08T00:00:00Z",
        "content": core.document({"kind": "part", "data": "changed"}, "# Changed"),
        "origin": "linear_get_document",
        "request_id": "readback",
    }
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, base_request("archive-index", observations=[observation]))  # type: ignore[arg-type]
    assert captured.value.code == "ARCHIVE_PENDING"
    assert len(issue.commits) == before_count


def test_archive_save_start_records_uncertainty_before_external_write() -> None:
    """Prevent an uncertain provider save from being blindly repeated."""
    store, issue = MemoryStore(), MemoryIssue()
    prepared = core.operate(store, issue, base_request("archive-prepare", expected_revision=7))  # type: ignore[arg-type]
    digest = core.sha(prepared["parts"][0]["content"].encode())
    core.operate(store, issue, base_request("archive-save-start", content_digest=digest))  # type: ignore[arg-type]
    assert issue.state["provider_saves"][digest] == {"status": "uncertain"}
    count = len(issue.commits)
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, base_request("archive-save-start", content_digest=digest))  # type: ignore[arg-type]
    assert captured.value.code == "ARCHIVE_PENDING"
    assert "Uncertain save" in captured.value.action
    assert len(issue.commits) == count


def test_archive_observe_save_binds_document_id_to_content_digest() -> None:
    """Keep a read-back requirement tied to the immutable exported content."""
    store, issue = MemoryStore(), MemoryIssue()
    prepared = core.operate(store, issue, base_request("archive-prepare", expected_revision=7))  # type: ignore[arg-type]
    digest = core.sha(prepared["parts"][0]["content"].encode())
    core.operate(store, issue, base_request("archive-save-start", content_digest=digest))  # type: ignore[arg-type]
    core.operate(
        store,
        issue,
        base_request("archive-observe-save", content_digest=digest, document_id="part-1"),
    )  # type: ignore[arg-type]
    assert issue.state["provider_saves"][digest] == {
        "status": "readback-required",
        "id": "part-1",
    }
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(
            store,
            issue,
            base_request("archive-observe-save", content_digest=digest, document_id="other"),
        )  # type: ignore[arg-type]
    assert captured.value.code == "ARCHIVE_PENDING"


def readbacks(store: MemoryStore, issue: MemoryIssue) -> list[dict[str, object]]:
    """Build read-backs from the actual prepared part and index requests."""
    prepared = core.operate(store, issue, base_request("archive-prepare", expected_revision=7))  # type: ignore[arg-type]
    part = prepared["parts"][0]
    part_observation = {
        "id": "part-1",
        "url": "https://linear.app/team/document/part-1",
        "issue": "uuid",
        "updatedAt": "2026-10-08T00:00:00Z",
        "content": part["content"],
        "origin": "linear_get_document",
        "request_id": "readback",
    }
    index = core.operate(
        store, issue, base_request("archive-index", observations=[part_observation])
    )  # type: ignore[arg-type]
    root_observation = {
        "id": "root",
        "url": "https://linear.app/team/document/root",
        "issue": "uuid",
        "updatedAt": "2026-10-08T00:01:00Z",
        "content": index["content"],
        "origin": "linear_get_document",
        "request_id": "readback",
    }
    return [part_observation, root_observation]


def test_archive_verify_records_receipt_only_after_matching_provider_bytes() -> None:
    """Bind the provider root and part versions to the exact local files."""
    store, issue = MemoryStore(), MemoryIssue()
    observed = readbacks(store, issue)
    result = core.operate(store, issue, base_request("archive-verify", observations=observed))  # type: ignore[arg-type]
    assert result["receipt"]["root"]["id"] == "root"
    assert issue.state["archive"] == result["receipt"]
    assert issue.commits[-1][0] is None


def test_archive_verify_rejects_changed_local_file_manifest() -> None:
    """Reject a valid provider snapshot when local bytes changed after export."""
    store, issue = MemoryStore(), MemoryIssue()
    observed = readbacks(store, issue)
    issue.payload["context/note.md"] = b"changed after export"
    before_count = len(issue.commits)
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, base_request("archive-verify", observations=observed))  # type: ignore[arg-type]
    assert captured.value.code == "ARCHIVE_PENDING"
    assert len(issue.commits) == before_count


def test_cleanup_plan_issues_challenge_only_for_retained_terminal_archive() -> None:
    """Require completed work and verified export before a cleanup challenge."""
    store, issue = MemoryStore(), MemoryIssue()
    observed = readbacks(store, issue)
    core.operate(store, issue, base_request("archive-verify", observations=observed))  # type: ignore[arg-type]
    key = issue.state["coordinator"]
    issue.state["participants"][key]["status"] = "maintenance"
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, base_request("cleanup-plan"))  # type: ignore[arg-type]
    assert captured.value.code == "RETAINED"
    issue.state["disposition"] = "completed"
    issue.state["outcome"] = {"disposition": "completed"}
    result = core.operate(store, issue, base_request("cleanup-plan"))  # type: ignore[arg-type]
    assert result["code"] == "READBACK_REQUIRED"
    assert result["root"]["id"] == "root"
    assert result["cleanup_challenge"] == issue.state["cleanup_challenge"]


def test_reopen_terminal_issue_invalidates_acknowledgment() -> None:
    """Require a new packet acknowledgment after reopening terminal work."""
    store, issue = MemoryStore(), MemoryIssue()
    key = issue.state["coordinator"]
    issue.state["disposition"] = "cancelled"
    issue.state["participants"][key]["status"] = "maintenance"
    source = {"id": "reopen", "locator": "context/reopen.md", "sha256": core.sha(b"reason")}
    result = core.operate(
        store,
        issue,
        base_request("reopen", expected_revision=7, evidence=[source]),
    )  # type: ignore[arg-type]
    assert result["ok"] is True
    assert issue.state["disposition"] == "active"
    member = issue.state["participants"][key]
    assert (member["status"], member["ack"]) == ("attached", None)


def test_checkpoint_records_bounded_handoff_and_review_disposition() -> None:
    """Persist a complete handoff record before moving a submitted PR to review."""
    store, issue = MemoryStore(), MemoryIssue()
    source = {"id": "status", "locator": "context/status.md", "sha256": core.sha(b"status")}
    checkpoint = {
        "goal": "Finish step five",
        "constraints": [],
        "sources": [source],
        "progress": "unit coverage",
        "blockers": [],
        "handoff": "run platform CI",
        "candidate": "https://github.com/example/repo/pull/1",
    }
    result = core.operate(
        store,
        issue,
        base_request(
            "checkpoint",
            expected_revision=7,
            checkpoint=checkpoint,
            submitted_pr="https://github.com/example/repo/pull/1",
        ),
    )  # type: ignore[arg-type]
    assert result["ok"] is True
    assert issue.state["checkpoint"] == checkpoint
    assert issue.state["checkpoint_history"] == [checkpoint]
    assert issue.state["disposition"] == "in_review"
    assert issue.commits[-1][0] == "checkpoint"


def test_checkpoint_without_pr_records_progress_without_review_transition() -> None:
    """A complete checkpoint alone does not claim a submitted pull request."""
    store, issue = MemoryStore(), MemoryIssue()
    source = {"id": "status", "locator": "context/status.md", "sha256": core.sha(b"status")}
    checkpoint = {
        "goal": "Finish step five",
        "constraints": [],
        "sources": [source],
        "progress": "unit cases in progress",
        "blockers": [],
        "handoff": "pending review",
        "candidate": "",
    }
    result = core.operate(
        store,
        issue,
        base_request("checkpoint", expected_revision=7, checkpoint=checkpoint),
    )  # type: ignore[arg-type]
    assert result["ok"] is True
    assert issue.state["checkpoint"] == checkpoint
    assert issue.state["disposition"] == "active"
    assert "submitted_pr" not in issue.state


def test_checkpoint_rejects_incomplete_handoff_before_commit() -> None:
    """Reject missing handoff fields without mutating the recorded issue."""
    store, issue = MemoryStore(), MemoryIssue()
    before = copy.deepcopy(issue.state)
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(
            store,
            issue,
            base_request("checkpoint", expected_revision=7, checkpoint={"goal": "missing rest"}),
        )  # type: ignore[arg-type]
    assert captured.value.code == "EVIDENCE_REQUIRED"
    assert issue.state == before
    assert issue.commits == []


def test_nonterminal_outcome_records_evidence_without_completion_obligations() -> None:
    """Blocked progress can be recorded while work remains pending."""
    store, issue = MemoryStore(), MemoryIssue()
    member = issue.state["participants"][issue.state["coordinator"]]
    member["pending"]["tool-1"] = {"status": "pending"}
    source = {"id": "status", "locator": "context/status.md", "sha256": core.sha(b"status")}
    result = core.operate(
        store,
        issue,
        base_request("outcome", expected_revision=7, disposition="blocked", evidence=[source]),
    )  # type: ignore[arg-type]
    assert result["ok"] is True
    assert issue.state["disposition"] == "blocked"
    assert issue.state["outcome"]["evidence"] == [source]
    assert issue.state["participants"][issue.state["coordinator"]]["pending"] == {
        "tool-1": {"status": "pending"}
    }


def test_repeated_request_id_requires_identical_body_and_replays_saved_result() -> None:
    """Return a prior transaction result exactly once and reject changed retry bodies."""
    store, issue = MemoryStore(), MemoryIssue()
    request = base_request("tool-start", tool_id="tool-1")
    initial = core.operate(store, issue, request)  # type: ignore[arg-type]
    issue.state["requests"][core.sha(request["request_id"].encode())] = {
        "digest": core.sha(core.canonical(request)),
        "result": initial,
    }
    count = len(issue.commits)
    assert core.operate(store, issue, request) == initial  # type: ignore[arg-type]
    assert len(issue.commits) == count
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, {**request, "tool_id": "tool-2"})  # type: ignore[arg-type]
    assert captured.value.code == "REQUEST_CONFLICT"
    assert len(issue.commits) == count


def test_existing_create_reuses_explicit_coordinator_binding() -> None:
    """Attach a coordinator retry through the current issue state and persist its binding."""
    store, issue = MemoryStore(), MemoryIssue()
    key = issue.state["coordinator"]
    request = base_request("create", issue_uuid="uuid", coordinator=key)
    result = core.operate(store, issue, request)  # type: ignore[arg-type]
    assert result["ok"] is True
    assert store.saved == [key]
    assert store.views[-1] == "AGENT-30"
    assert issue.commits[-1][0] == "attach"


def test_create_initializes_packaged_roadmap_only_for_absent_issue() -> None:
    """Publish an initial roadmap and empty history under an explicit coordinator."""
    store, issue = MemoryStore(), MemoryIssue()
    issue.state = None  # type: ignore[assignment]
    issue.payload = {}

    class Task:
        """Report the absence of a prior task payload."""

        def exists(self, identifier: str) -> bool:
            """Require the selected issue lookup and report it absent."""
            assert identifier == "AGENT-30"
            return False

    store.task = Task()
    key = core.participant_key(base_request("create"))
    request = base_request("create", issue_uuid="uuid", coordinator=key)
    result = core.operate(store, issue, request)  # type: ignore[arg-type]
    assert result["ok"] is True
    assert result["binding_generation"] == 1
    assert issue.payload["events.jsonl"] == b""
    assert b"AGENT-30" in issue.payload["roadmap.md"]
    assert issue.state["coordinator"] == key
    assert issue.commits[-1][0] == "create"
    assert store.saved == [key]


def test_absent_issue_requires_create_or_explicit_adopt_before_any_commit() -> None:
    """Do not manufacture a task workspace for an absent read or mismatched adopt."""
    store, issue = MemoryStore(), MemoryIssue()
    issue.state = None  # type: ignore[assignment]

    class Task:
        """Report an existing unadopted payload."""

        def exists(self, _identifier: str) -> bool:
            """Model one present prior directory."""
            return True

    store.task = Task()
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, base_request("read"))  # type: ignore[arg-type]
    assert captured.value.code == "BINDING_MISSING"
    key = core.participant_key(base_request("create"))
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, base_request("create", issue_uuid="uuid", coordinator=key))  # type: ignore[arg-type]
    assert captured.value.code == "ADOPTION_REQUIRED"
    assert issue.commits == []


def test_restore_reconstructs_only_tombstone_matching_provider_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restore exact archived files, advance generations, and bind the coordinator."""
    store, issue = MemoryStore(), MemoryIssue()
    key = issue.state["coordinator"]
    issue.state["storage"] = "cleaned"
    issue.state["disposition"] = "completed"
    issue.state["tombstone"] = {"snapshot": "snap-1", "archive": {"root": {"id": "root"}}}
    issue.state["participants"][key]["status"] = "maintenance"
    issue.state["files"] = core.manifest(issue.payload)

    class Task:
        """Report an absent payload directory before reconstructed publication."""

        def exists(self, _identifier: str) -> bool:
            """Require no existing issue payload."""
            return False

    store.task = Task()
    archived = issue.payload.copy()
    monkeypatch.setattr(
        core,
        "verify_provider",
        lambda _state, _observations: (
            {"provenance": {"roadmap.md": {"author": key}}},
            archived,
            {"snapshot": "snap-1"},
        ),
    )
    result = core.operate(store, issue, base_request("restore", observations=[{"id": "root"}]))  # type: ignore[arg-type]
    assert result["ok"] is True
    assert result["binding_generation"] == 3
    assert issue.state["storage"] == "present"
    assert issue.state["generation"] == 2
    assert issue.state["participants"][key]["status"] == "attached"
    assert issue.state["participants"][key]["ack"] is None
    assert issue.state["history"][0]["snapshot"] == "snap-1"
    assert issue.payload == archived
    assert issue.commits[-1][0] == "restore"
    assert store.saved == [key]


def test_restore_rejects_changed_archive_manifest_without_creating_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leave a cleaned tombstone intact when provider bytes differ from its files."""
    store, issue = MemoryStore(), MemoryIssue()
    issue.state["storage"] = "cleaned"
    issue.state["tombstone"] = {"snapshot": "snap-1"}
    before = copy.deepcopy(issue.state)
    monkeypatch.setattr(
        core,
        "verify_provider",
        lambda _state, _observations: ({}, {"roadmap.md": b"changed"}, {"snapshot": "snap-1"}),
    )
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, base_request("restore", observations=[]))  # type: ignore[arg-type]
    assert captured.value.code == "INTEGRITY_ERROR"
    assert issue.state == before
    assert issue.commits == []


def test_reconcile_imports_only_inspected_markdown_with_unchanged_event_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Commit explicit Markdown reconciliation after checking directory identity and events."""
    store, issue = MemoryStore(), MemoryIssue()
    issue.state["directory_identity"] = [4, 5]
    observed = {**issue.payload, "context/note.md": b"edited markdown"}

    class Payload:
        """Expose an exact opened payload-directory identity."""

        identity = [4, 5]

        def __enter__(self) -> Payload:
            """Hold the validated payload handle."""
            return self

        def __exit__(self, *_args: object) -> None:
            """Release the validated payload handle."""

    class Task:
        """Open the selected issue payload directly."""

        def child(self, identifier: str, *, private: bool) -> Payload:
            """Require the issue-specific non-private payload."""
            assert (identifier, private) == ("AGENT-30", False)
            return Payload()

    store.task = Task()
    monkeypatch.setattr(core, "inventory", lambda _payload: observed)
    source = {"id": "inspect", "locator": "context/inspection.md", "sha256": core.sha(b"ok")}
    previous = issue.state["files"].copy()
    result = core.operate(
        store,
        issue,
        base_request(
            "reconcile-files",
            expected_revision=7,
            evidence=[source],
            inventory=core.manifest(observed),
        ),
    )  # type: ignore[arg-type]
    assert result["ok"] is True
    assert issue.payload["context/note.md"] == b"edited markdown"
    assert issue.state["reconciliation"] == {"previous": previous, "evidence": [source]}
    assert issue.commits[-1][0] == "reconciliation"


def test_reconcile_rejects_changed_event_bytes_before_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forbid importing uncommitted event history through Markdown reconciliation."""
    store, issue = MemoryStore(), MemoryIssue()
    issue.state["directory_identity"] = [4, 5]
    observed = {**issue.payload, "events.jsonl": b"uncommitted event\n"}

    class Payload:
        """Expose an identity-matching payload directory."""

        identity = [4, 5]

        def __enter__(self) -> Payload:
            """Hold the modeled payload handle."""
            return self

        def __exit__(self, *_args: object) -> None:
            """Release the modeled payload handle."""

    class Task:
        """Open the selected issue payload for inspection."""

        def child(self, _identifier: str, *, private: bool) -> Payload:
            """Require non-private payload inspection."""
            assert private is False
            return Payload()

    store.task = Task()
    monkeypatch.setattr(core, "inventory", lambda _payload: observed)
    source = {"id": "inspect", "locator": "context/inspection.md", "sha256": core.sha(b"ok")}
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(
            store,
            issue,
            base_request(
                "reconcile-files",
                expected_revision=7,
                evidence=[source],
                inventory=core.manifest(observed),
            ),
        )  # type: ignore[arg-type]
    assert captured.value.code == "INTEGRITY_ERROR"
    assert issue.commits == []


def test_cleanup_commit_records_intent_only_after_fresh_matching_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Commit a cleanup intent after matching archive versions and challenge replies."""
    store, issue = MemoryStore(), MemoryIssue()
    key = issue.state["coordinator"]
    issue.state["participants"][key]["status"] = "maintenance"
    issue.state["disposition"] = "completed"
    issue.state["outcome"] = {"disposition": "completed"}
    issue.state["directory_identity"] = [4, 5]
    issue.state["export"] = {
        "snapshot": "snap-1",
        "files": core.manifest(issue.payload),
        "revision": 7,
    }
    issue.state["archive"] = {"root": {"id": "root", "updatedAt": "old"}}
    issue.state["cleanup_challenge"] = "challenge-1"
    receipt = {"snapshot": "snap-1", "root": {"id": "root", "updatedAt": "old"}}
    monkeypatch.setattr(
        core,
        "verify_provider",
        lambda _state, _observations: ({"revision": 7}, issue.payload.copy(), receipt),
    )
    finish_calls: list[bool] = []

    def finish(current: MemoryIssue) -> None:
        """Record only a durable cleanup intent observed after provider checks."""
        finish_calls.append("cleanup.json" in current.control.data)

    monkeypatch.setattr(core, "finish_cleanup", finish)
    request = base_request(
        "cleanup-commit",
        observations=[{"request_id": "challenge-1"}],
        cleanup_challenge="challenge-1",
    )
    result = core.operate(store, issue, request)  # type: ignore[arg-type]
    assert result["code"] == "CLEANED"
    intent = issue.control.data["cleanup.json"]
    assert intent["directory_identity"] == [4, 5]
    assert intent["files"] == core.manifest(issue.payload)
    assert intent["request_id"] == "request-1"
    assert finish_calls == [False, True]
    assert issue.commits == []


def test_cleanup_commit_rejects_stale_challenge_without_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never quarantine files from an old provider read-back challenge."""
    store, issue = MemoryStore(), MemoryIssue()
    key = issue.state["coordinator"]
    issue.state["participants"][key]["status"] = "maintenance"
    issue.state["disposition"] = "completed"
    issue.state["outcome"] = {"disposition": "completed"}
    issue.state["export"] = {
        "snapshot": "snap-1",
        "files": core.manifest(issue.payload),
        "revision": 7,
    }
    issue.state["archive"] = {"root": {"id": "root", "updatedAt": "old"}}
    issue.state["cleanup_challenge"] = "challenge-1"
    receipt = {"snapshot": "snap-1", "root": {"id": "root", "updatedAt": "old"}}
    monkeypatch.setattr(
        core,
        "verify_provider",
        lambda _state, _observations: ({"revision": 7}, issue.payload.copy(), receipt),
    )
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(
            store,
            issue,
            base_request(
                "cleanup-commit",
                observations=[{"request_id": "old-challenge"}],
                cleanup_challenge="challenge-1",
            ),
        )  # type: ignore[arg-type]
    assert captured.value.code == "ARCHIVE_PENDING"
    assert issue.control.data == {}
    assert issue.commits == []


def test_completed_outcome_requires_verified_local_evidence_and_provider_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Set completed only after local evidence bytes and typed provider state match."""
    store, issue = MemoryStore(), MemoryIssue()
    source = {
        "id": "acceptance",
        "locator": "/evidence/acceptance.md",
        "sha256": core.sha(b"accepted"),
    }
    opened: list[str] = []

    class Parent:
        """Expose one exact owned evidence file."""

        def __enter__(self) -> Parent:
            """Hold the modeled no-follow parent handle."""
            return self

        def __exit__(self, *_args: object) -> None:
            """Release the modeled parent handle."""

        def read(self, name: str, limit: int) -> bytes:
            """Return exact evidence bytes after name and size inspection."""
            assert (name, limit) == ("acceptance.md", core.MAX_FILE)
            return b"accepted"

    monkeypatch.setattr(
        core.Directory,
        "absolute",
        lambda path: opened.append(str(path)) or Parent(),
    )
    provider = {
        "origin": "linear_get_issue",
        "issue_uuid": "uuid",
        "status_type": "completed",
        "completed_at": "2026-10-08T00:00:00Z",
        "request_id": "provider-read-1",
    }
    result = core.operate(
        store,
        issue,
        base_request(
            "outcome",
            expected_revision=7,
            disposition="completed",
            evidence=[source],
            completion={
                "human_acceptance": [source],
                "merge": [source],
                "obligations": [source],
            },
            provider_status=provider,
        ),
    )  # type: ignore[arg-type]
    assert result["ok"] is True
    assert opened == ["/evidence"] * 3
    assert issue.state["disposition"] == "completed"
    assert issue.state["provider_completion"] == provider
    assert issue.commits[-1][0] == "outcome"


def test_completed_outcome_rejects_provider_status_without_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not treat unverified provider status as issue completion."""
    store, issue = MemoryStore(), MemoryIssue()
    source = {
        "id": "acceptance",
        "locator": "/evidence/acceptance.md",
        "sha256": core.sha(b"accepted"),
    }

    class Parent:
        """Expose exact local evidence bytes."""

        def __enter__(self) -> Parent:
            """Hold the modeled source handle."""
            return self

        def __exit__(self, *_args: object) -> None:
            """Release the modeled source handle."""

        def read(self, _name: str, _limit: int) -> bytes:
            """Return the expected local evidence bytes."""
            return b"accepted"

    monkeypatch.setattr(core.Directory, "absolute", lambda _path: Parent())
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(
            store,
            issue,
            base_request(
                "outcome",
                expected_revision=7,
                disposition="completed",
                evidence=[source],
                completion={
                    "human_acceptance": [source],
                    "merge": [source],
                    "obligations": [source],
                },
                provider_status={
                    "origin": "linear_get_issue",
                    "issue_uuid": "uuid",
                    "status_type": "started",
                    "completed_at": "2026-10-08T00:00:00Z",
                    "request_id": "provider-read-1",
                },
            ),
        )  # type: ignore[arg-type]
    assert captured.value.code == "EVIDENCE_REQUIRED"
    assert issue.state["disposition"] == "active"
    assert issue.commits == []


def test_event_rollover_preserves_checkpointed_history_as_immutable_segment() -> None:
    """Move only archive-verified active event bytes into a named retained segment."""
    store, issue = MemoryStore(), MemoryIssue()
    old_events = core.canonical({"seq": 1, "type": "event"}) + b"\n"
    issue.payload["events.jsonl"] = old_events
    issue.state["files"] = core.manifest(issue.payload)
    issue.state["seq"] = 1
    issue.state["head"] = "head-1"
    issue.state["archive"] = {"snapshot": "snap-1"}
    issue.state["export"] = {
        "snapshot": "snap-1",
        "files": core.manifest(issue.payload),
        "revision": 7,
    }
    result = core.operate(store, issue, base_request("event-rollover", expected_revision=7))  # type: ignore[arg-type]
    segment = "events-000000000001-000000000001.jsonl"
    assert result["segment"] == segment
    assert result["snapshot"] == "snap-1"
    assert issue.payload[segment] == old_events
    assert issue.state["event_segments"][0] == {
        "path": segment,
        "first": 1,
        "last": 1,
        "head": "head-1",
        "sha256": core.sha(old_events),
        "archive": {"snapshot": "snap-1"},
    }
    assert issue.commits[-1][0] == "event-rollover"


def test_event_rollover_rejects_unverified_export_without_segment() -> None:
    """Retain the active event stream when no exact verified archive covers it."""
    store, issue = MemoryStore(), MemoryIssue()
    issue.payload["events.jsonl"] = core.canonical({"seq": 1}) + b"\n"
    before = issue.payload.copy()
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, base_request("event-rollover", expected_revision=7))  # type: ignore[arg-type]
    assert captured.value.code == "ARCHIVE_PENDING"
    assert issue.payload == before
    assert issue.commits == []


def test_adopt_requires_exact_existing_payload_inventory_and_owners(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adopt previously inspected bytes without replacing the existing roadmap."""
    store, issue = MemoryStore(), MemoryIssue()
    files = issue.payload.copy()
    issue.state = None  # type: ignore[assignment]
    key = core.participant_key(base_request("adopt"))

    class Payload:
        """Expose the selected existing issue payload as one direct directory."""

        def __enter__(self) -> Payload:
            """Hold the modeled payload directory."""
            return self

        def __exit__(self, *_args: object) -> None:
            """Release the modeled payload directory."""

    class Task:
        """Report an existing unregistered payload for explicit adoption."""

        def exists(self, identifier: str) -> bool:
            """Require the selected issue and report its directory present."""
            assert identifier == "AGENT-30"
            return True

        def child(self, identifier: str, *, private: bool) -> Payload:
            """Open only the selected non-private issue payload."""
            assert (identifier, private) == ("AGENT-30", False)
            return Payload()

    store.task = Task()
    monkeypatch.setattr(core, "inventory", lambda _payload: files)
    source = {"id": "inspection", "locator": "/evidence/inspection.md", "sha256": core.sha(b"ok")}
    request = base_request(
        "adopt",
        issue_uuid="uuid",
        coordinator=key,
        evidence=[source],
        inventory=core.manifest(files),
        owners={"roadmap.md": key, "context/note.md": key},
    )
    result = core.operate(store, issue, request)  # type: ignore[arg-type]
    assert result["ok"] is True
    assert issue.payload == files
    assert issue.state["adoption"] == {"inventory": core.manifest(files), "evidence": [source]}
    assert issue.state["owners"] == {"roadmap.md": key, "context/note.md": key}
    assert issue.commits[-1][0] == "adopt"


def test_adopt_rejects_changed_existing_payload_before_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leave prior files unregistered when the supplied inspection digest is stale."""
    store, issue = MemoryStore(), MemoryIssue()
    files = issue.payload.copy()
    issue.state = None  # type: ignore[assignment]
    key = core.participant_key(base_request("adopt"))

    class Payload:
        """Expose a selected existing payload directory."""

        def __enter__(self) -> Payload:
            """Hold the modeled directory."""
            return self

        def __exit__(self, *_args: object) -> None:
            """Release the modeled directory."""

    class Task:
        """Report an existing issue payload."""

        def exists(self, _identifier: str) -> bool:
            """Report the existing directory."""
            return True

        def child(self, _identifier: str, *, private: bool) -> Payload:
            """Open only a non-private existing payload."""
            assert private is False
            return Payload()

    store.task = Task()
    monkeypatch.setattr(core, "inventory", lambda _payload: files)
    source = {"id": "inspection", "locator": "/evidence/inspection.md", "sha256": core.sha(b"ok")}
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(
            store,
            issue,
            base_request(
                "adopt",
                issue_uuid="uuid",
                coordinator=key,
                evidence=[source],
                inventory={"roadmap.md": "stale"},
                owners={"roadmap.md": key, "context/note.md": key},
            ),
        )  # type: ignore[arg-type]
    assert captured.value.code == "UNTRACKED_CHANGE"
    assert issue.commits == []


def test_join_existing_reader_refreshes_only_roadmap_digest() -> None:
    """Refresh a preassigned reader packet without granting ownership or coordinator scope."""
    store, issue = MemoryStore(), MemoryIssue()
    request = base_request("join", session_id="reader", issue_uuid="uuid", expected_revision=7)
    reader = core.participant_key(request)
    previous = {
        "id": "roadmap",
        "locator": "roadmap.md",
        "sha256": core.sha(b"older roadmap"),
        "required": True,
        "authority": "task-workspace",
        "reason": "issue-resume",
        "stage": "planning",
        "reader": reader,
    }
    issue.state["participants"][reader] = {
        "generation": 2,
        "status": "ready",
        "pending": {},
        "packet": [previous],
        "ack": core.sha(core.canonical([previous])),
    }
    issue.state["assignments"] = {reader: {"packet": [previous]}}
    result = core.operate(store, issue, request)  # type: ignore[arg-type]
    current = issue.state["participants"][reader]
    assert result["participant_id"] == reader
    assert current["packet"][0]["sha256"] == issue.state["files"]["roadmap.md"]
    assert current["ack"] is None
    assert reader not in issue.state["owners"].values()
    assert issue.state["coordinator"] != reader
    assert issue.commits[-1][0] == "attach"


def test_transfer_coordinator_moves_roadmap_ownership_with_evidence() -> None:
    """Move the coordinator role and canonical roadmap owner in one transaction."""
    store, issue = MemoryStore(), MemoryIssue()
    reader = core.participant_key({"host": "codex", "session_id": "reader"})
    issue.state["participants"][reader] = {
        "generation": 1,
        "status": "attached",
        "pending": {},
        "packet": None,
        "ack": None,
    }
    source = {"id": "transfer", "locator": "context/transfer.md", "sha256": core.sha(b"vote")}
    result = core.operate(
        store,
        issue,
        base_request(
            "transfer-coordinator",
            expected_revision=7,
            target_participant=reader,
            evidence=[source],
        ),
    )  # type: ignore[arg-type]
    assert result["ok"] is True
    assert issue.state["coordinator"] == reader
    assert issue.state["owners"]["roadmap.md"] == reader
    assert issue.commits[-1][0] == "transfer-coordinator"


def test_reconcile_participant_detaches_selected_generation_after_pending_clear() -> None:
    """Retire exactly the targeted stale participant generation with evidence."""
    store, issue = MemoryStore(), MemoryIssue()
    reader = core.participant_key({"host": "codex", "session_id": "reader"})
    issue.state["participants"][reader] = {
        "generation": 3,
        "status": "ready",
        "pending": {},
        "packet": None,
        "ack": "old-digest",
    }
    source = {"id": "reconcile", "locator": "context/reconcile.md", "sha256": core.sha(b"clear")}
    result = core.operate(
        store,
        issue,
        base_request(
            "reconcile-participant",
            expected_revision=7,
            target_participant=reader,
            target_generation=3,
            evidence=[source],
        ),
    )  # type: ignore[arg-type]
    assert result["ok"] is True
    assert issue.state["participants"][reader]["status"] == "detached"
    assert issue.state["participants"][reader]["ack"] is None
    assert issue.commits[-1][0] == "reconcile-participant"


def test_archive_prepare_seal_requires_terminal_issue_and_detached_readers() -> None:
    """Freeze a terminal issue only after every other participant has detached."""
    store, issue = MemoryStore(), MemoryIssue()
    key = issue.state["coordinator"]
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, base_request("archive-prepare", expected_revision=7, seal=True))  # type: ignore[arg-type]
    assert captured.value.code == "RETAINED"
    assert issue.commits == []
    reader = core.participant_key({"host": "codex", "session_id": "reader"})
    issue.state["participants"][reader] = {
        "generation": 1,
        "status": "detached",
        "pending": {},
        "packet": None,
        "ack": None,
    }
    issue.state["disposition"] = "completed"
    result = core.operate(
        store, issue, base_request("archive-prepare", expected_revision=7, seal=True)
    )  # type: ignore[arg-type]
    assert result["ok"] is True
    assert issue.state["participants"][key]["status"] == "maintenance"
    assert issue.state["participants"][reader]["status"] == "detached"
    assert issue.commits[0][0] == "archive-prepare"


def test_replayed_create_restores_saved_result_and_binding_without_new_commit() -> None:
    """Replay an identical creation receipt and its referenced response payload."""
    store, issue = MemoryStore(), MemoryIssue()
    key = issue.state["coordinator"]
    request = base_request("create", issue_uuid="uuid", coordinator=key)
    issue.control.put("result.json", {"binding_generation": 2, "participant_id": key})
    issue.state["requests"][core.sha(request["request_id"].encode())] = {
        "digest": core.sha(core.canonical(request)),
        "result": {"ok": True, "code": "OK", "result_ref": "result.json"},
    }
    result = core.operate(store, issue, request)  # type: ignore[arg-type]
    assert result == {
        "ok": True,
        "code": "OK",
        "binding_generation": 2,
        "participant_id": key,
    }
    assert store.saved == [key]
    assert store.views == ["AGENT-30"]
    assert issue.commits == []


def test_restore_commit_failure_keeps_prior_cleaned_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restore the prior in-memory tombstone if reconstruction publication fails."""
    store, issue = MemoryStore(), MemoryIssue()
    issue.state["storage"] = "cleaned"
    issue.state["tombstone"] = {"snapshot": "snap-1"}
    issue.state["files"] = core.manifest(issue.payload)
    prior = copy.deepcopy(issue.state)

    class Task:
        """Report no duplicate issue payload."""

        def exists(self, _identifier: str) -> bool:
            """Keep the payload absent before restore."""
            return False

    store.task = Task()
    monkeypatch.setattr(
        core,
        "verify_provider",
        lambda _state, _observations: ({}, issue.payload.copy(), {"snapshot": "snap-1"}),
    )

    def fail_commit(*_args: object, **_kwargs: object) -> None:
        """Model failed durable transaction publication."""
        raise OSError("storage failure")

    monkeypatch.setattr(issue, "commit", fail_commit)
    with pytest.raises(OSError, match="storage failure"):
        core.operate(store, issue, base_request("restore", observations=[]))  # type: ignore[arg-type]
    assert issue.state == prior
    assert store.saved == []


def test_archive_prepare_reuses_identical_durable_preparation_receipt() -> None:
    """Retry export publication without appending a second preparation event."""
    store, issue = MemoryStore(), MemoryIssue()
    request = base_request("archive-prepare", expected_revision=7)
    prepared_request = {**request, "request_id": request["request_id"] + ":prepare"}
    issue.state["requests"][core.sha(prepared_request["request_id"].encode())] = {
        "digest": core.sha(core.canonical(prepared_request)),
        "result": {"ok": True},
    }
    result = core.operate(store, issue, request)  # type: ignore[arg-type]
    assert result["ok"] is True
    assert [event for event, _state, _files in issue.commits] == [None]


def test_unsupported_operation_never_commits_issue_state() -> None:
    """Return the bounded unsupported diagnostic without publishing payload bytes."""
    store, issue = MemoryStore(), MemoryIssue()
    before = copy.deepcopy(issue.state)
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, base_request("unrecognized"))  # type: ignore[arg-type]
    assert captured.value.code == "UNSUPPORTED_OPERATION"
    assert issue.state == before
    assert issue.commits == []


def test_poll_without_pre_hook_correlates_unique_pending_parent() -> None:
    """Settle only one participant-local process when a host omits polling admission."""
    store, issue = MemoryStore(), MemoryIssue()
    core.operate(store, issue, base_request("tool-start", tool_id="process"))  # type: ignore[arg-type]
    core.operate(
        store,
        issue,
        base_request("tool-complete", tool_id="process", completed=False, async_handle="pid-7"),
    )  # type: ignore[arg-type]
    result = core.operate(
        store,
        issue,
        base_request(
            "tool-complete", tool_id="poll", poll=True, async_handle="pid-7", completed=True
        ),
    )  # type: ignore[arg-type]
    assert result["ok"] is True
    assert next(iter(issue.state["participants"].values()))["pending"] == {}


def test_poll_transport_can_retire_after_its_parent_already_completed() -> None:
    """Remove a previously admitted poll without reviving its settled parent process."""
    store, issue = MemoryStore(), MemoryIssue()
    core.operate(store, issue, base_request("tool-start", tool_id="process"))  # type: ignore[arg-type]
    core.operate(
        store,
        issue,
        base_request("tool-complete", tool_id="process", completed=False, async_handle="pid-7"),
    )  # type: ignore[arg-type]
    core.operate(store, issue, base_request("tool-start", tool_id="poll", poll_handle="pid-7"))  # type: ignore[arg-type]
    core.operate(
        store,
        issue,
        base_request("tool-complete", tool_id="process", completed=True, async_handle="pid-7"),
    )  # type: ignore[arg-type]
    result = core.operate(
        store,
        issue,
        base_request(
            "tool-complete", tool_id="poll", poll=True, async_handle="pid-7", completed=True
        ),
    )  # type: ignore[arg-type]
    assert result["ok"] is True
    assert next(iter(issue.state["participants"].values()))["pending"] == {}


def test_unfinished_tool_without_handle_cannot_be_settled() -> None:
    """Keep a pending call when the host supplies neither completion nor process handle."""
    store, issue = MemoryStore(), MemoryIssue()
    core.operate(store, issue, base_request("tool-start", tool_id="process"))  # type: ignore[arg-type]
    before = copy.deepcopy(issue.state)
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(
            store, issue, base_request("tool-complete", tool_id="process", completed=False)
        )  # type: ignore[arg-type]
    assert captured.value.code == "UNKNOWN_OPERATION"
    assert issue.state == before


def test_repeated_unknown_process_observation_does_not_append_event() -> None:
    """Refresh one unchanged async handle without consuming an event slot."""
    store, issue = MemoryStore(), MemoryIssue()
    core.operate(store, issue, base_request("tool-start", tool_id="process"))  # type: ignore[arg-type]
    core.operate(
        store,
        issue,
        base_request("tool-complete", tool_id="process", completed=False, async_handle="pid-7"),
    )  # type: ignore[arg-type]
    core.operate(
        store,
        issue,
        base_request("tool-complete", tool_id="process", completed=False, async_handle="pid-7"),
    )  # type: ignore[arg-type]
    assert issue.commits[-1][0] is None
    assert next(iter(issue.state["participants"].values()))["pending"] == {
        "process": {"status": "unknown", "handle": "pid-7"}
    }
