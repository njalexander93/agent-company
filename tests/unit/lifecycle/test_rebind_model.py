"""Check two-issue binding transfer order with in-memory issue adapters."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from agent_company.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit


class Control:
    """Provide the held issue directory and lock context for rebind."""

    def __init__(self, identifier: str, order: list[str]) -> None:
        """Name the issue whose lock is held.

        Args:
            identifier: Issue or participant identifier used in this scenario.
            order: Expected order of operations or entries.
        """
        # Track control identity and transaction order.
        self.identifier = identifier
        self.order = order

    def __enter__(self) -> Control:
        """Record opening and lock acquisition.

        Returns:
            This fake context manager for the enclosed operation.
        """
        self.order.append("lock:" + self.identifier)
        return self

    def __exit__(self, *_args: object) -> None:
        """Release the modeled issue lock.

        Args:
            *_args: Exception details supplied by the context-manager protocol.
        """

    def lock(self) -> Control:
        """Return this issue's lock context.

        Returns:
            Context manager for the fake issue lock.
        """
        return self


class Issues:
    """Expose two issue control handles in stable order."""

    def __init__(self, order: list[str]) -> None:
        """Share the observable operation order.

        Args:
            order: Expected order of operations or entries.
        """
        self.order = order

    def child(self, identifier: str) -> Control:
        """Open a selected issue control.

        Args:
            identifier: Issue or participant identifier used in this scenario.

        Returns:
            Fake child directory or issue context for the caller.
        """
        return Control(identifier, self.order)


class Bindings:
    """Retain an existing explicit assignment that may be updated."""

    def __init__(self) -> None:
        """Start with no assignment marker."""
        self.data: dict[str, object] = {}

    def exists(self, name: str) -> bool:
        """Check only an existing assignment marker.

        Args:
            name: Requested file, directory, or control-entry name.

        Returns:
            Whether the requested test entry exists.
        """
        return name in self.data

    def put(self, name: str, value: object) -> None:
        """Record a switched explicit assignment.

        Args:
            name: Requested file, directory, or control-entry name.
            value: Document value stored by the fake control boundary.
        """
        self.data[name] = value


class Store:
    """Record views and binding writes across the two issues."""

    def __init__(self) -> None:
        """Create a single shared operation log."""
        # Track issue access, binding writes, and commit order.
        self.order: list[str] = []
        self.issues = Issues(self.order)
        self.bindings = Bindings()

    def view(self, identifier: str) -> None:
        """Record the new issue view after commit.

        Args:
            identifier: Issue or participant identifier used in this scenario.
        """
        self.order.append("view:" + identifier)

    def save_binding(self, _state: dict[str, Any], _key: str) -> None:
        """Record binding publication only after the target commit.

        Args:
            _state: Unused state argument accepted by this fake.
            _key: Unused key argument accepted by this fake.
        """
        self.order.append("save-binding")


class Issue:
    """Keep the exact old or target state and record its commit."""

    registry: dict[str, Issue] = {}

    def __new__(cls, _store: Store, _control: Control, identifier: str) -> Issue:
        """Reuse the preconfigured issue state for this identifier.

        Args:
            _store: Unused store argument accepted by this fake.
            _control: Unused control argument accepted by this fake.
            identifier: Issue or participant identifier used in this scenario.

        Returns:
            New fake object used by the rebind test.
        """
        return cls.registry[identifier]

    def recover(self) -> None:
        """Model completed recovery before evaluating either state."""

    def files(self) -> dict[str, bytes]:
        """Return the committed bytes without a native filesystem.

        Returns:
            File mapping retained by the fake store.
        """
        return {"roadmap.md": b"approved", "events.jsonl": b""}

    def committed_state(self) -> dict[str, Any]:
        """Return the last committed issue state.

        Returns:
            State currently committed by the fake issue.
        """
        return self.state

    def commit(
        self,
        state: dict[str, Any],
        _files: dict[str, bytes],
        _request: dict[str, Any],
        result: dict[str, Any],
        event_type: str,
    ) -> dict[str, Any]:
        """Record the rebind transition and retain its state.

        Args:
            state: Issue state used by this scenario.
            _files: Unused files argument accepted by this fake.
            _request: Unused request argument accepted by this fake.
            result: Response returned by the fake collaborator.
            event_type: Event type selected by the case.

        Returns:
            Committed revision returned by the fake persistence boundary.
        """
        self.store.order.append("commit:" + self.id)
        assert event_type == "rebind"
        # Establish the issue state required by this transition.
        self.state = copy.deepcopy(state)
        return {"ok": True, "code": "OK", **result}


def fixture_state(store: Store) -> tuple[Issue, Issue, str]:
    """Create a coordinator old issue and assigned-reader target issue.

    Args:
        store: Store used to exercise lifecycle operations.

    Returns:
        Preconfigured issue state for rebind cases.
    """
    # Resolve the participant identity from its exact host and session.
    key = core.participant_key({"host": "codex", "session_id": "session"})
    # Establish the issue state required by this transition.
    old = object.__new__(Issue)
    old.store, old.id = store, "AGENT-30"
    old.state = {
        "coordinator": key,
        "participants": {key: {"generation": 1, "status": "ready", "pending": {}, "ack": "digest"}},
        "requests": {},
    }
    new = object.__new__(Issue)
    new.store, new.id = store, "AGENT-31"
    new.state = {
        "coordinator": "other-coordinator",
        "disposition": "active",
        "storage": "present",
        "participants": {},
        "assignments": {key: {"packet": []}},
        "requests": {},
    }
    Issue.registry = {old.id: old, new.id: new}
    return old, new, key


def rebind_request(**changes: object) -> dict[str, Any]:
    """Supply explicit old/new issue and session identities.

    Args:
        **changes: State fields overridden for this scenario.

    Returns:
        Rebind request bound to the selected host and session.
    """
    return {
        "operation": "rebind",
        "issue_id": "AGENT-30",
        "new_issue_id": "AGENT-31",
        "host": "codex",
        "session_id": "session",
        "binding_generation": 1,
        "new_binding_generation": None,
        "request_id": "rebind-1",
        "evidence": [
            {"id": "handoff", "locator": "context/handoff.md", "sha256": core.sha(b"approved")}
        ],
        **changes,
    }


def test_rebind_retires_old_participant_before_new_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Commit the old detachment before publishing the target binding.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake Issue calls.
    store = Store()
    old, new, key = fixture_state(store)
    assignment = key + ".assignment.json"
    store.bindings.data[assignment] = {"issue_id": "AGENT-30"}
    monkeypatch.setattr(core, "Issue", Issue)
    # Transfer the selected participant between old and target issues.
    result = core.rebind(store, rebind_request())  # type: ignore[arg-type]
    # Confirm the new binding targets AGENT-31 after old retirement.
    assert result["participant_id"] == key
    assert old.state["participants"][key]["status"] == "detached"
    assert old.state["participants"][key]["ack"] is None
    assert new.state["participants"][key]["status"] == "attached"
    assert store.order.index("commit:AGENT-30") < store.order.index("commit:AGENT-31")
    assert store.order.index("commit:AGENT-31") < store.order.index("save-binding")
    assert store.bindings.data[assignment] == {"issue_id": "AGENT-31"}


def test_rebind_rejects_pending_old_work_without_target_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep both issues bound as before while old work is unresolved.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake Issue calls.
    store = Store()
    old, new, key = fixture_state(store)
    old.state["participants"][key]["pending"] = {"tool-1": {"status": "pending"}}
    monkeypatch.setattr(core, "Issue", Issue)
    # Reject rebind while the old issue cannot safely release the participant.
    with pytest.raises(core.WorkspaceError) as captured:
        core.rebind(store, rebind_request())  # type: ignore[arg-type]
    # Confirm the rejected operation reports PENDING_OPERATION.
    assert captured.value.code == "PENDING_OPERATION"
    assert old.state["participants"][key]["status"] == "ready"
    assert new.state["participants"] == {}
    assert not any(item.startswith("commit:") for item in store.order)


def test_rebind_retry_after_old_retirement_commits_only_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crash after old retirement resumes target attachment without duplicate retire.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Build old and target issues with one explicit participant binding.
    store = Store()
    old, new, key = fixture_state(store)
    # Put pending work on the old participant before rebind.
    old.state["participants"][key].update(status="detached", ack=None)
    # Install fake Issue calls.
    monkeypatch.setattr(core, "Issue", Issue)
    # Transfer the selected participant between old and target issues.
    result = core.rebind(store, rebind_request())  # type: ignore[arg-type]
    assert result["ok"] is True
    assert "commit:AGENT-30" not in store.order
    assert "commit:AGENT-31" in store.order
    assert new.state["participants"][key]["status"] == "attached"


def test_rebind_without_assignment_marker_preserves_its_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Normal rebind updates existing explicit markers only; it invents none.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake Issue calls.
    store = Store()
    _old, new, key = fixture_state(store)
    monkeypatch.setattr(core, "Issue", Issue)
    # Transfer the selected participant between old and target issues.
    result = core.rebind(store, rebind_request())  # type: ignore[arg-type]
    assert result["ok"] is True
    assert new.state["participants"][key]["status"] == "attached"
    assert key + ".assignment.json" not in store.bindings.data


def test_rebind_replay_finishes_assignment_publication_after_interruption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry a committed target transaction after the assignment write was interrupted.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake Issue calls.
    store = Store()
    old, new, key = fixture_state(store)
    old.state["participants"][key]["status"] = "detached"
    old.state["participants"][key]["ack"] = None
    new.state["participants"][key] = {
        "generation": 1,
        "status": "attached",
        "pending": {},
        "packet": [],
        "ack": None,
    }
    request = rebind_request()
    target_request = {
        **request,
        "operation": "rebind",
        "issue_id": "AGENT-31",
        "old_issue_id": "AGENT-30",
        "binding_generation": None,
    }
    new.state["requests"][core.sha(request["request_id"].encode())] = {
        "digest": core.sha(core.canonical(target_request)),
        "result": {"ok": True, "code": "OK", "participant_id": key, "binding_generation": 1},
    }
    assignment = key + ".assignment.json"
    store.bindings.data[assignment] = {"issue_id": "AGENT-30"}
    monkeypatch.setattr(core, "Issue", Issue)
    # Transfer the selected participant between old and target issues.
    result = core.rebind(store, request)  # type: ignore[arg-type]
    # Confirm the new binding targets AGENT-31 after old retirement.
    assert result["participant_id"] == key
    assert store.bindings.data[assignment] == {"issue_id": "AGENT-31"}
    assert not any(item.startswith("commit:") for item in store.order)


def test_rebind_replay_does_not_invent_absent_assignment_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep an unrecorded assignment absent while restoring the committed binding.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake Issue calls.
    store = Store()
    old, new, key = fixture_state(store)
    old.state["participants"][key]["status"] = "detached"
    new.state["participants"][key] = {
        "generation": 1,
        "status": "attached",
        "pending": {},
        "packet": [],
        "ack": None,
    }
    request = rebind_request()
    target_request = {
        **request,
        "operation": "rebind",
        "issue_id": "AGENT-31",
        "old_issue_id": "AGENT-30",
        "binding_generation": None,
    }
    new.state["requests"][core.sha(request["request_id"].encode())] = {
        "digest": core.sha(core.canonical(target_request)),
        "result": {"ok": True, "code": "OK", "participant_id": key, "binding_generation": 1},
    }
    monkeypatch.setattr(core, "Issue", Issue)
    # Check target attachment, old detachment, and commit order.
    assert core.rebind(store, request)["ok"] is True  # type: ignore[arg-type]
    assert key + ".assignment.json" not in store.bindings.data
    assert "save-binding" in store.order


def test_rebind_replay_can_finish_after_assignment_write_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry a target committed before its explicit assignment update succeeded.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Build both issues and the existing assignment marker.
    store = Store()
    old, new, key = fixture_state(store)
    assignment = key + ".assignment.json"
    store.bindings.data[assignment] = {"issue_id": "AGENT-30"}
    request = rebind_request()
    original_put = store.bindings.put

    def fail_put(_name: str, _value: object) -> None:
        """Model the one interrupted assignment publication.

        Args:
            _name: Unused name argument accepted by this fake.
            _value: Unused value argument accepted by this fake.

        Raises:
            OSError: When the injected native or persistence failure occurs.
        """
        raise OSError("assignment write failed")

    # Install fake put, Issue calls.
    monkeypatch.setattr(store.bindings, "put", fail_put)
    monkeypatch.setattr(core, "Issue", Issue)
    # Reject rebind while the old issue cannot safely release the participant.
    with pytest.raises(OSError, match="assignment write failed"):
        core.rebind(store, request)  # type: ignore[arg-type]
    # Confirm the old issue retains its exact identity during replay.
    assert old.state["participants"][key]["status"] == "detached"
    assert new.state["participants"][key]["status"] == "attached"
    assert store.bindings.data[assignment] == {"issue_id": "AGENT-30"}
    # Install fake put calls.
    target_request = {
        **request,
        "operation": "rebind",
        "issue_id": "AGENT-31",
        "old_issue_id": "AGENT-30",
        "binding_generation": None,
    }
    new.state["requests"][core.sha(request["request_id"].encode())] = {
        "digest": core.sha(core.canonical(target_request)),
        "result": {"ok": True, "code": "OK", "participant_id": key, "binding_generation": 1},
    }
    monkeypatch.setattr(store.bindings, "put", original_put)
    # Confirm the new binding targets AGENT-31 after old retirement.
    assert core.rebind(store, request)["ok"] is True  # type: ignore[arg-type]
    assert store.bindings.data[assignment] == {"issue_id": "AGENT-31"}
