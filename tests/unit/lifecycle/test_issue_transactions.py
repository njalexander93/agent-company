"""Check durable transaction intent and event construction before native publication."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from agent_company.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit


def test_issue_loads_only_existing_control_state_and_exposes_committed_state() -> None:
    """Keep absent issues absent and return exactly the loaded committed state."""

    class Control:
        """Expose one optional state document under a validated control handle."""

        def __init__(self, present: bool) -> None:
            """Select whether a state document exists."""
            self.present = present
            self.reads: list[str] = []

        def exists(self, name: str) -> bool:
            """Report only the selected state entry."""
            assert name == "state.json"
            return self.present

        def json(self, name: str) -> dict[str, object]:
            """Record and return the one modeled committed document."""
            self.reads.append(name)
            return {"issue_id": "AGENT-30"}

    absent_control = Control(False)
    absent = core.Issue(object(), absent_control, "AGENT-30")  # type: ignore[arg-type]
    assert absent.state is None
    assert absent_control.reads == []
    with pytest.raises(AssertionError):
        absent.committed_state()
    present_control = Control(True)
    present = core.Issue(object(), present_control, "AGENT-30")  # type: ignore[arg-type]
    assert present.committed_state() == {"issue_id": "AGENT-30"}
    assert present_control.reads == ["state.json"]


class Control:
    """Retain only named control writes from the modeled transaction."""

    def __init__(self, order: list[str]) -> None:
        """Share an effect log with the modeled issue recovery."""
        self.order = order
        self.documents: dict[str, Any] = {}

    def put(self, name: str, content: object) -> None:
        """Record durable intent before any modeled payload application."""
        self.order.append("put:" + name)
        self.documents[name] = copy.deepcopy(content)


class Issue:
    """Provide minimal committed bytes around the real Issue.commit method."""

    def __init__(self) -> None:
        """Start with an empty event stream and one owned roadmap."""
        self.id = "AGENT-30"
        self.order: list[str] = []
        self.control = Control(self.order)
        self.payload = {"roadmap.md": b"old", "events.jsonl": b""}
        key = core.participant_key({"host": "codex", "session_id": "session"})
        self.state: dict[str, Any] = {
            "schema_version": 1,
            "repo_id": "repo",
            "issue_id": self.id,
            "revision": 4,
            "seq": 0,
            "head": None,
            "participants": {key: {"generation": 2, "status": "ready", "pending": {}}},
            "files": core.manifest(self.payload),
            "requests": {},
        }

    def files(self) -> dict[str, bytes]:
        """Return the committed payload before staging changes."""
        return self.payload.copy()

    def recover(self) -> None:
        """Model the later native publication from the written intent."""
        self.order.append("recover")
        tx = self.control.documents["transaction.json"]
        self.state = copy.deepcopy(tx["state"])
        self.payload = {
            **self.payload,
            **{path: core.decode(data) for path, data in tx["writes"].items()},
        }

    def commit(
        self,
        state: dict[str, Any],
        files: dict[str, bytes],
        request: dict[str, object],
        result: dict[str, Any],
        event_type: str | None = None,
    ) -> dict[str, Any]:
        """Delegate recursion to the real transaction implementation."""
        return core.Issue.commit(self, state, files, request, result, event_type)  # type: ignore[arg-type]


class Payload:
    """Retain staged file bytes through one modeled payload directory handle."""

    def __init__(self, files: dict[str, bytes], order: list[str]) -> None:
        """Bind exact file bytes and a publication log."""
        self.data = files.copy()
        self.identity = [1, 2]
        self.order = order

    def __enter__(self) -> Payload:
        """Expose this already opened modeled directory."""
        return self

    def __exit__(self, *_args: object) -> None:
        """Leave the modeled directory open for later assertions."""

    def close(self) -> None:
        """Record explicit child handle release."""
        self.order.append("close-context")

    def child(self, name: str, *_args: object, **_kwargs: object) -> Payload:
        """Expose the supported context child only."""
        assert name == "context"
        return self

    def flush(self) -> None:
        """Record payload flush before state publication."""
        self.order.append("payload-flush")

    def write(self, name: str, data: bytes) -> None:
        """Retain a corrected interrupted event stream."""
        self.order.append("write:" + name)
        self.data[name] = data


class Task:
    """Expose one already present issue payload to recovery."""

    def __init__(self, payload: Payload) -> None:
        """Retain the modeled issue directory."""
        self.payload = payload

    def exists(self, identifier: str) -> bool:
        """Report the existing issue payload."""
        assert identifier == "AGENT-30"
        return True

    def child(self, identifier: str, *_args: object, **_kwargs: object) -> Payload:
        """Open the same issue payload for validation and publication."""
        assert identifier == "AGENT-30"
        return self.payload


class RecoveryControl(Control):
    """Expose a durable intent and record final state publication/removal."""

    def exists(self, name: str) -> bool:
        """Report only an existing transaction intent."""
        return name in self.documents

    def json(self, name: str) -> Any:
        """Return a copy of the durable intent."""
        return copy.deepcopy(self.documents[name])

    def unlink(self, name: str) -> None:
        """Record removal after durable state publication."""
        self.order.append("unlink:" + name)
        del self.documents[name]

    def write(self, name: str, data: bytes) -> None:
        """Retain quarantined uncommitted tail bytes."""
        self.order.append("quarantine:" + name)
        self.documents[name] = data


def recovering_issue(files: dict[str, bytes]) -> tuple[Any, Payload, RecoveryControl, list[str]]:
    """Set one old manifest and one intended replacement for recovery tests."""
    order: list[str] = []
    payload = Payload(files, order)
    control = RecoveryControl(order)
    old_manifest = core.manifest({"roadmap.md": b"old", "events.jsonl": b""})
    intended = {"roadmap.md": b"new", "events.jsonl": b""}
    state: dict[str, Any] = {
        "files": core.manifest(intended),
        "directory_identity": [1, 2],
        "seq": 0,
        "head": None,
    }
    control.documents["transaction.json"] = {
        "base": old_manifest,
        "directory_identity": [1, 2],
        "state": state,
        "writes": {"roadmap.md": core.encode(b"new")},
    }
    issue = type("IssueHandle", (), {})()
    issue.id = "AGENT-30"
    issue.store = type("StoreHandle", (), {"task": Task(payload)})()
    issue.control = control
    issue.state = {"files": old_manifest}
    return issue, payload, control, order


def test_recover_publishes_only_old_or_intended_bytes_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validate old bytes before write, then publish state before removing intent."""
    issue, payload, control, order = recovering_issue({"roadmap.md": b"old", "events.jsonl": b""})
    monkeypatch.setattr(
        core, "inventory", lambda directory, require_roadmap=True: directory.data.copy()
    )

    def write(directory: Payload, path: str, data: bytes) -> None:
        """Record intended publication only after old-byte validation."""
        order.append("write:" + path)
        directory.data[path] = data

    monkeypatch.setattr(core, "write_payload", write)
    core.Issue.recover(issue)  # type: ignore[arg-type]
    assert payload.data == {"roadmap.md": b"new", "events.jsonl": b""}
    assert order == [
        "write:roadmap.md",
        "close-context",
        "payload-flush",
        "put:state.json",
        "unlink:transaction.json",
    ]
    assert "transaction.json" not in control.documents
    assert issue.state["files"] == core.manifest(payload.data)


def test_recover_refuses_foreign_bytes_before_any_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve an unexpected payload and its durable recovery intent."""
    issue, payload, control, order = recovering_issue(
        {"roadmap.md": b"foreign", "events.jsonl": b""}
    )
    monkeypatch.setattr(
        core, "inventory", lambda directory, require_roadmap=True: directory.data.copy()
    )
    monkeypatch.setattr(core, "write_payload", lambda *_args: pytest.fail("wrote foreign payload"))
    with pytest.raises(core.WorkspaceError) as captured:
        core.Issue.recover(issue)  # type: ignore[arg-type]
    assert captured.value.code == "UNTRACKED_CHANGE"
    assert payload.data["roadmap.md"] == b"foreign"
    assert "transaction.json" in control.documents
    assert order == []


def test_files_quarantines_only_provable_uncommitted_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retain exact tail bytes while restoring the committed event manifest."""
    issue, payload, control, order = recovering_issue(
        {"roadmap.md": b"old", "events.jsonl": b"unfinished"}
    )
    issue.state = {
        "storage": "present",
        "directory_identity": [1, 2],
        "files": core.manifest({"roadmap.md": b"old", "events.jsonl": b""}),
        "seq": 0,
        "head": None,
    }
    monkeypatch.setattr(
        core, "inventory", lambda directory, require_roadmap=True: directory.data.copy()
    )
    result = core.Issue.files(issue)  # type: ignore[arg-type]
    assert result["events.jsonl"] == b""
    assert payload.data["events.jsonl"] == b""
    assert control.documents["interrupted-tail-" + core.sha(b"unfinished")] == b"unfinished"
    assert control.documents["diagnostic-loss.json"]["discarded_tail_bytes"] == 10
    assert order[0].startswith("quarantine:interrupted-tail-")
    assert order[1] == "write:events.jsonl"


def test_files_rejects_changed_committed_prefix_without_quarantine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not truncate or quarantine when committed bytes differ."""
    issue, payload, control, order = recovering_issue(
        {"roadmap.md": b"foreign", "events.jsonl": b"unfinished"}
    )
    issue.state = {
        "storage": "present",
        "directory_identity": [1, 2],
        "files": core.manifest({"roadmap.md": b"old", "events.jsonl": b""}),
        "seq": 0,
        "head": None,
    }
    monkeypatch.setattr(
        core, "inventory", lambda directory, require_roadmap=True: directory.data.copy()
    )
    with pytest.raises(core.WorkspaceError) as captured:
        core.Issue.files(issue)  # type: ignore[arg-type]
    assert captured.value.code == "UNTRACKED_CHANGE"
    assert payload.data == {"roadmap.md": b"foreign", "events.jsonl": b"unfinished"}
    assert "diagnostic-loss.json" not in control.documents
    assert order == []


def test_files_returns_exact_committed_payload_without_quarantine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return verified bytes directly when manifest and event head already match."""
    exact = {"roadmap.md": b"old", "events.jsonl": b""}
    issue, payload, control, order = recovering_issue(exact)
    issue.state = {
        "storage": "present",
        "directory_identity": [1, 2],
        "files": core.manifest(exact),
        "seq": 0,
        "head": None,
    }
    monkeypatch.setattr(core, "inventory", lambda directory: directory.data.copy())
    assert core.Issue.files(issue) == exact  # type: ignore[arg-type]
    assert payload.data == exact
    assert "diagnostic-loss.json" not in control.documents
    assert order == []


def request(**changes: object) -> dict[str, object]:
    """Supply one bound operation with an exact request identity."""
    return {
        "operation": "update",
        "request_id": "request-1",
        "host": "codex",
        "session_id": "session",
        **changes,
    }


def test_commit_writes_intent_before_recovery_and_chains_event() -> None:
    """Store exact intended bytes and a valid event before applying payload."""
    issue = Issue()
    state = copy.deepcopy(issue.state)
    files = {**issue.payload, "roadmap.md": b"new"}
    result = core.Issue.commit(issue, state, files, request(), {}, "update")  # type: ignore[arg-type]
    assert issue.order == ["put:transaction.json", "recover"]
    tx = issue.control.documents["transaction.json"]
    assert tx["base"] == core.manifest({"roadmap.md": b"old", "events.jsonl": b""})
    assert core.decode(tx["writes"]["roadmap.md"]) == b"new"
    assert core.validate_events(issue.payload["events.jsonl"]) == (1, state["head"])
    assert result["revision"] == 5
    assert issue.state["files"] == core.manifest(issue.payload)
    key = core.sha(b"request-1")
    assert issue.state["requests"][key]["digest"] == core.sha(core.canonical(request()))


def test_commit_rejects_external_event_content_before_intent() -> None:
    """Keep caller-supplied event bytes out of a normal update transaction."""
    issue = Issue()
    before = copy.deepcopy(issue.state)
    with pytest.raises(core.WorkspaceError) as captured:
        core.Issue.commit(
            issue,
            copy.deepcopy(issue.state),
            issue.payload.copy(),
            request(event={"code": "OK"}),
            {},
            "update",
        )  # type: ignore[arg-type]
    assert captured.value.code == "INVALID_REQUEST"
    assert issue.control.documents == {}
    assert issue.state == before


def test_optional_observation_suppression_preserves_settlement_space(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drop a dispensable observation before exhausting reserved event capacity."""
    issue = Issue()
    monkeypatch.setattr(core, "MAX_EVENTS", 8192)
    result = core.Issue.commit(
        issue,
        copy.deepcopy(issue.state),
        issue.payload.copy(),
        request(operation="event", event={"code": "UNKNOWN"}),
        {},
        "observation",
    )  # type: ignore[arg-type]
    assert result["code"] == "OPTIONAL_SUPPRESSED"
    assert issue.state["optional_loss_count"] == 1
    assert issue.payload["events.jsonl"] == b""
    assert issue.order == ["put:transaction.json", "recover"]


def test_recover_returns_without_intent_or_payload_access() -> None:
    """Leave committed state and bytes untouched when no transaction exists."""
    issue, payload, control, order = recovering_issue({"roadmap.md": b"old", "events.jsonl": b""})
    del control.documents["transaction.json"]
    core.Issue.recover(issue)  # type: ignore[arg-type]
    assert payload.data["roadmap.md"] == b"old"
    assert order == []


def test_recover_creates_absent_payload_only_for_initial_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Initialize a missing issue directory only when the durable intent has no base."""
    issue, payload, control, order = recovering_issue({})
    issue.state = None
    intended = {"roadmap.md": b"new", "events.jsonl": b""}
    control.documents["transaction.json"] = {
        "base": None,
        "directory_identity": None,
        "state": {"files": core.manifest(intended), "seq": 0, "head": None},
        "writes": {path: core.encode(data) for path, data in intended.items()},
    }

    class AbsentTask:
        """Expose a freshly created direct issue directory after initial recovery."""

        def __init__(self) -> None:
            """Start without a published issue payload."""
            self.present = False

        def exists(self, identifier: str) -> bool:
            """Check only the selected issue directory."""
            assert identifier == "AGENT-30"
            return self.present

        def child(self, identifier: str, create: bool = False, *, private: bool) -> Payload:
            """Require explicit creation before opening the new payload."""
            assert identifier == "AGENT-30" and private is False
            if create:
                order.append("create-payload")
                self.present = True
            assert self.present
            return payload

    issue.store.task = AbsentTask()
    monkeypatch.setattr(
        core, "inventory", lambda directory, require_roadmap=True: directory.data.copy()
    )

    def publish(directory: Payload, path: str, data: bytes) -> None:
        """Record each intended file after initial directory creation."""
        order.append("write:" + path)
        directory.data[path] = data

    monkeypatch.setattr(core, "write_payload", publish)
    core.Issue.recover(issue)  # type: ignore[arg-type]
    assert order[0] == "create-payload"
    assert payload.data == intended
    assert issue.state["files"] == core.manifest(intended)
    assert "transaction.json" not in control.documents


def test_recover_refuses_missing_payload_for_noninitial_transaction() -> None:
    """Retain intent instead of recreating a missing prior issue directory."""
    issue, _payload, control, order = recovering_issue({})

    class AbsentTask:
        """Report a missing previously committed issue payload."""

        def exists(self, _identifier: str) -> bool:
            """Keep the previously committed directory absent."""
            return False

        def child(self, *_args: object, **_kwargs: object) -> None:
            """Fail if recovery attempts to manufacture the lost directory."""
            pytest.fail("recreated committed payload")

    issue.store.task = AbsentTask()
    with pytest.raises(core.WorkspaceError) as captured:
        core.Issue.recover(issue)  # type: ignore[arg-type]
    assert captured.value.code == "RECOVERY_REQUIRED"
    assert "transaction.json" in control.documents
    assert order == []


def test_commit_persists_large_archive_result_by_reference() -> None:
    """Keep archive parts in their control document instead of request-id receipt state."""
    issue = Issue()
    state = copy.deepcopy(issue.state)
    result = {"snapshot": "a" * 64, "parts": [{"content": "archived bytes"}]}
    committed = core.Issue.commit(
        issue,
        state,
        issue.payload.copy(),
        request(operation="archive-prepare"),
        result,
    )  # type: ignore[arg-type]
    assert committed["parts"] == [{"content": "archived bytes"}]
    receipt = issue.state["requests"][core.sha(b"request-1")]["result"]
    assert "parts" not in receipt
    assert receipt["result_ref"] == "export-" + "a" * 64 + ".json"
