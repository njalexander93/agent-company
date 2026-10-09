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
            """Select whether a state document exists.

            Args:
                present: Whether the fake reports a resource as present.
            """
            # Record whether control state exists and which files are read.
            self.present = present
            self.reads: list[str] = []

        def exists(self, name: str) -> bool:
            """Report only the selected state entry.

            Args:
                name: Requested file, directory, or control-entry name.

            Returns:
                Whether the requested test entry exists.
            """
            assert name == "state.json"
            return self.present

        def json(self, name: str) -> dict[str, object]:
            """Record and return the one modeled committed document.

            Args:
                name: Requested file, directory, or control-entry name.

            Returns:
                Decoded JSON value for the selected fake file.
            """
            self.reads.append(name)
            return {"issue_id": "AGENT-30"}

    # Model a missing control directory without inventing issue state.
    absent_control = Control(False)
    # Construct the issue from the selected control state.
    absent = core.Issue(object(), absent_control, "AGENT-30")  # type: ignore[arg-type]
    assert absent.state is None
    assert absent_control.reads == []
    # Exercise absent.committed_state and capture the expected failure.
    with pytest.raises(AssertionError):
        absent.committed_state()
    # Model an existing control directory with committed state.
    present_control = Control(True)
    # Construct the issue from the selected control state.
    present = core.Issue(object(), present_control, "AGENT-30")  # type: ignore[arg-type]
    # Confirm the fake issue remains bound to AGENT-30.
    assert present.committed_state() == {"issue_id": "AGENT-30"}
    assert present_control.reads == ["state.json"]


class Control:
    """Retain only named control writes from the modeled transaction."""

    def __init__(self, order: list[str]) -> None:
        """Share an effect log with the modeled issue recovery.

        Args:
            order: Expected order of operations or entries.
        """
        # Store control documents and record publication order.
        self.order = order
        self.documents: dict[str, Any] = {}

    def put(self, name: str, content: object) -> None:
        """Record durable intent before any modeled payload application.

        Args:
            name: Requested file, directory, or control-entry name.
            content: Document text supplied to the parser.
        """
        self.order.append("put:" + name)
        # Seed the control documents visible to recovery.
        self.documents[name] = copy.deepcopy(content)


class Issue:
    """Provide minimal committed bytes around the real Issue.commit method."""

    def __init__(self) -> None:
        """Start with an empty event stream and one owned roadmap."""
        # Build exact payload bytes for the transition.
        self.id = "AGENT-30"
        self.order: list[str] = []
        self.control = Control(self.order)
        self.payload = {"roadmap.md": b"old", "events.jsonl": b""}
        # Bind the modeled participant to its explicit host and session.
        key = core.participant_key({"host": "codex", "session_id": "session"})
        # Establish the issue state required by this transition.
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
        """Return the committed payload before staging changes.

        Returns:
            File mapping retained by the fake store.
        """
        return self.payload.copy()

    def recover(self) -> None:
        """Model the later native publication from the written intent."""
        self.order.append("recover")
        # Establish the issue state required by this transition.
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
        """Delegate recursion to the real transaction implementation.

        Args:
            state: Issue state used by this scenario.
            files: File contents used to build or inspect the workspace.
            request: Lifecycle request sent to the operation.
            result: Response returned by the fake collaborator.
            event_type: Event type selected by the case.

        Returns:
            Committed operation result returned by the real transaction boundary.
        """
        return core.Issue.commit(self, state, files, request, result, event_type)  # type: ignore[arg-type]


class Payload:
    """Retain staged file bytes through one modeled payload directory handle."""

    def __init__(self, files: dict[str, bytes], order: list[str]) -> None:
        """Bind exact file bytes and a publication log.

        Args:
            files: File contents used to build or inspect the workspace.
            order: Expected order of operations or entries.
        """
        # Seed payload bytes and record native identity and operation order.
        self.data = files.copy()
        self.identity = [1, 2]
        self.order = order

    def __enter__(self) -> Payload:
        """Expose this already opened modeled directory.

        Returns:
            This fake context manager for the enclosed operation.
        """
        return self

    def __exit__(self, *_args: object) -> None:
        """Leave the modeled directory open for later assertions.

        Args:
            *_args: Exception details supplied by the context-manager protocol.
        """

    def close(self) -> None:
        """Record explicit child handle release."""
        self.order.append("close-context")

    def child(self, name: str, *_args: object, **_kwargs: object) -> Payload:
        """Expose the supported context child only.

        Args:
            name: Requested file, directory, or control-entry name.
            *_args: Extra arguments accepted to match the collaborator signature.
            **_kwargs: Extra options accepted to match the collaborator signature.

        Returns:
            Fake child directory or issue context for the caller.
        """
        assert name == "context"
        return self

    def flush(self) -> None:
        """Record payload flush before state publication."""
        self.order.append("payload-flush")

    def write(self, name: str, data: bytes) -> None:
        """Retain a corrected interrupted event stream.

        Args:
            name: Requested file, directory, or control-entry name.
            data: Bytes passed to the simulated write.
        """
        self.order.append("write:" + name)
        # Seed the payload bytes visible to the fake directory.
        self.data[name] = data


class Task:
    """Expose one already present issue payload to recovery."""

    def __init__(self, payload: Payload) -> None:
        """Retain the modeled issue directory.

        Args:
            payload: Payload passed to the operation.
        """
        self.payload = payload

    def exists(self, identifier: str) -> bool:
        """Report the existing issue payload.

        Args:
            identifier: Issue or participant identifier used in this scenario.

        Returns:
            Whether the requested test entry exists.
        """
        # Confirm the fake issue remains bound to AGENT-30.
        assert identifier == "AGENT-30"
        return True

    def child(self, identifier: str, *_args: object, **_kwargs: object) -> Payload:
        """Open the same issue payload for validation and publication.

        Args:
            identifier: Issue or participant identifier used in this scenario.
            *_args: Extra arguments accepted to match the collaborator signature.
            **_kwargs: Extra options accepted to match the collaborator signature.

        Returns:
            Fake child directory or issue context for the caller.
        """
        # Confirm the fake issue remains bound to AGENT-30.
        assert identifier == "AGENT-30"
        return self.payload


class RecoveryControl(Control):
    """Expose a durable intent and record final state publication/removal."""

    def exists(self, name: str) -> bool:
        """Report only an existing transaction intent.

        Args:
            name: Requested file, directory, or control-entry name.

        Returns:
            Whether the requested test entry exists.
        """
        return name in self.documents

    def json(self, name: str) -> Any:
        """Return a copy of the durable intent.

        Args:
            name: Requested file, directory, or control-entry name.

        Returns:
            Decoded JSON value for the selected fake file.
        """
        return copy.deepcopy(self.documents[name])

    def unlink(self, name: str) -> None:
        """Record removal after durable state publication.

        Args:
            name: Requested file, directory, or control-entry name.
        """
        self.order.append("unlink:" + name)
        # Remove the named entry from the fake directory state.
        del self.documents[name]

    def write(self, name: str, data: bytes) -> None:
        """Retain quarantined uncommitted tail bytes.

        Args:
            name: Requested file, directory, or control-entry name.
            data: Bytes passed to the simulated write.
        """
        self.order.append("quarantine:" + name)
        # Seed the durable recovery intent and committed state.
        self.documents[name] = data


def recovering_issue(files: dict[str, bytes]) -> tuple[Any, Payload, RecoveryControl, list[str]]:
    """Set one old manifest and one intended replacement for recovery tests.

    Args:
        files: File contents used to build or inspect the workspace.

    Returns:
        Issue model configured for transaction recovery.
    """
    # Build exact payload bytes for the transition.
    order: list[str] = []
    payload = Payload(files, order)
    control = RecoveryControl(order)
    # Hash the committed file bytes for the transaction baseline.
    old_manifest = core.manifest({"roadmap.md": b"old", "events.jsonl": b""})
    # Establish the issue state required by this transition.
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
    """Validate old bytes before write, then publish state before removing intent.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Make inventory report the selected committed or staged bytes.
    issue, payload, control, order = recovering_issue({"roadmap.md": b"old", "events.jsonl": b""})
    monkeypatch.setattr(
        core, "inventory", lambda directory, require_roadmap=True: directory.data.copy()
    )

    def write(directory: Payload, path: str, data: bytes) -> None:
        """Record intended publication only after old-byte validation.

        Args:
            directory: Directory handle used by the test.
            path: Filesystem path passed to the operation.
            data: Bytes passed to the simulated write.
        """
        order.append("write:" + path)
        # Stage the selected bytes in the fake payload directory.
        directory.data[path] = data

    # Record payload writes to verify intent and state publication order.
    monkeypatch.setattr(core, "write_payload", write)
    # Resume the durable transaction from its recorded intent.
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
    """Preserve an unexpected payload and its durable recovery intent.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake inventory, write_payload calls.
    issue, payload, control, order = recovering_issue(
        {"roadmap.md": b"foreign", "events.jsonl": b""}
    )
    monkeypatch.setattr(
        core, "inventory", lambda directory, require_roadmap=True: directory.data.copy()
    )
    monkeypatch.setattr(core, "write_payload", lambda *_args: pytest.fail("wrote foreign payload"))
    # Reject recovery when stored bytes violate the intent.
    with pytest.raises(core.WorkspaceError) as captured:
        core.Issue.recover(issue)  # type: ignore[arg-type]
    # Confirm the rejected operation reports UNTRACKED_CHANGE.
    assert captured.value.code == "UNTRACKED_CHANGE"
    assert payload.data["roadmap.md"] == b"foreign"
    assert "transaction.json" in control.documents
    assert order == []


def test_files_quarantines_only_provable_uncommitted_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retain exact tail bytes while restoring the committed event manifest.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Make inventory report the selected committed or staged bytes.
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
    # Read committed files and reconcile any uncommitted event tail.
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
    """Do not truncate or quarantine when committed bytes differ.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Make inventory report the selected committed or staged bytes.
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
    # Refuse an unprovable committed-prefix change.
    with pytest.raises(core.WorkspaceError) as captured:
        core.Issue.files(issue)  # type: ignore[arg-type]
    # Confirm the rejected operation reports UNTRACKED_CHANGE.
    assert captured.value.code == "UNTRACKED_CHANGE"
    assert payload.data == {"roadmap.md": b"foreign", "events.jsonl": b"unfinished"}
    assert "diagnostic-loss.json" not in control.documents
    assert order == []


def test_files_returns_exact_committed_payload_without_quarantine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return verified bytes directly when manifest and event head already match.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Make inventory report the selected committed or staged bytes.
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
    # Confirm only exact committed bytes remain visible.
    assert core.Issue.files(issue) == exact  # type: ignore[arg-type]
    assert payload.data == exact
    assert "diagnostic-loss.json" not in control.documents
    assert order == []


def request(**changes: object) -> dict[str, object]:
    """Supply one bound operation with an exact request identity.

    Args:
        **changes: State fields overridden for this scenario.

    Returns:
        Request mapping with the selected operation and overrides.
    """
    return {
        "operation": "update",
        "request_id": "request-1",
        "host": "codex",
        "session_id": "session",
        **changes,
    }


def test_commit_writes_intent_before_recovery_and_chains_event() -> None:
    """Store exact intended bytes and a valid event before applying payload."""
    # Establish the issue state required by this transition.
    issue = Issue()
    state = copy.deepcopy(issue.state)
    files = {**issue.payload, "roadmap.md": b"new"}
    # Commit the intended bytes through the durable transaction.
    result = core.Issue.commit(issue, state, files, request(), {}, "update")  # type: ignore[arg-type]
    assert issue.order == ["put:transaction.json", "recover"]
    # Inspect the transaction intent written before payload mutation.
    tx = issue.control.documents["transaction.json"]
    # Check the committed manifest matches the intended bytes.
    assert tx["base"] == core.manifest({"roadmap.md": b"old", "events.jsonl": b""})
    assert core.decode(tx["writes"]["roadmap.md"]) == b"new"
    assert core.validate_events(issue.payload["events.jsonl"]) == (1, state["head"])
    assert result["revision"] == 5
    assert issue.state["files"] == core.manifest(issue.payload)
    # Hash the exact committed event bytes.
    key = core.sha(b"request-1")
    # Confirm the event chain and manifest use that digest.
    assert issue.state["requests"][key]["digest"] == core.sha(core.canonical(request()))


def test_commit_rejects_external_event_content_before_intent() -> None:
    """Keep caller-supplied event bytes out of a normal update transaction."""
    # Snapshot issue state before the rejected commit.
    issue = Issue()
    before = copy.deepcopy(issue.state)
    # Reject invalid commit input before writing an intent.
    with pytest.raises(core.WorkspaceError) as captured:
        core.Issue.commit(
            issue,
            copy.deepcopy(issue.state),
            issue.payload.copy(),
            request(event={"code": "OK"}),
            {},
            "update",
        )  # type: ignore[arg-type]
    # Confirm the rejected operation reports INVALID_REQUEST.
    assert captured.value.code == "INVALID_REQUEST"
    assert issue.control.documents == {}
    assert issue.state == before


def test_optional_observation_suppression_preserves_settlement_space(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drop a dispensable observation before exhausting reserved event capacity.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake MAX_EVENTS calls.
    issue = Issue()
    monkeypatch.setattr(core, "MAX_EVENTS", 8192)
    # Commit the intended bytes through the durable transaction.
    result = core.Issue.commit(
        issue,
        copy.deepcopy(issue.state),
        issue.payload.copy(),
        request(operation="event", event={"code": "UNKNOWN"}),
        {},
        "observation",
    )  # type: ignore[arg-type]
    # Confirm the rejected operation reports OPTIONAL_SUPPRESSED.
    assert result["code"] == "OPTIONAL_SUPPRESSED"
    assert issue.state["optional_loss_count"] == 1
    assert issue.payload["events.jsonl"] == b""
    assert issue.order == ["put:transaction.json", "recover"]


def test_recover_returns_without_intent_or_payload_access() -> None:
    """Leave committed state and bytes untouched when no transaction exists."""
    # Build exact payload bytes for the transition.
    issue, payload, control, order = recovering_issue({"roadmap.md": b"old", "events.jsonl": b""})
    del control.documents["transaction.json"]
    # Resume the durable transaction from its recorded intent.
    core.Issue.recover(issue)  # type: ignore[arg-type]
    assert payload.data["roadmap.md"] == b"old"
    assert order == []


def test_recover_creates_absent_payload_only_for_initial_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Initialize a missing issue directory only when the durable intent has no base.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Establish the issue state required by this transition.
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
            """Check only the selected issue directory.

            Args:
                identifier: Issue or participant identifier used in this scenario.

            Returns:
                Whether the requested test entry exists.
            """
            # Confirm the fake issue remains bound to AGENT-30.
            assert identifier == "AGENT-30"
            return self.present

        def child(self, identifier: str, create: bool = False, *, private: bool) -> Payload:
            """Require explicit creation before opening the new payload.

            Args:
                identifier: Issue or participant identifier used in this scenario.
                create: Whether the native call requests file creation.
                private: Whether the resource should be private to this issue.

            Returns:
                Fake child directory or issue context for the caller.
            """
            # Confirm the fake issue remains bound to AGENT-30.
            assert identifier == "AGENT-30" and private is False
            # Check the create outcome and its alternative.
            if create:
                # Create the absent payload only for an initial transaction.
                order.append("create-payload")
                # Keep the issue payload absent until the initial transaction creates it.
                self.present = True
            assert self.present
            return payload

    # Make inventory report the selected committed or staged bytes.
    issue.store.task = AbsentTask()
    monkeypatch.setattr(
        core, "inventory", lambda directory, require_roadmap=True: directory.data.copy()
    )

    def publish(directory: Payload, path: str, data: bytes) -> None:
        """Record each intended file after initial directory creation.

        Args:
            directory: Directory handle used by the test.
            path: Filesystem path passed to the operation.
            data: Bytes passed to the simulated write.
        """
        order.append("write:" + path)
        # Stage the selected bytes in the fake payload directory.
        directory.data[path] = data

    # Record payload writes to verify intent and state publication order.
    monkeypatch.setattr(core, "write_payload", publish)
    # Resume the durable transaction from its recorded intent.
    core.Issue.recover(issue)  # type: ignore[arg-type]
    assert order[0] == "create-payload"
    assert payload.data == intended
    assert issue.state["files"] == core.manifest(intended)
    assert "transaction.json" not in control.documents


def test_recover_refuses_missing_payload_for_noninitial_transaction() -> None:
    """Retain intent instead of recreating a missing prior issue directory."""
    # Build exact payload bytes for the transition.
    issue, _payload, control, order = recovering_issue({})

    class AbsentTask:
        """Report a missing previously committed issue payload."""

        def exists(self, _identifier: str) -> bool:
            """Keep the previously committed directory absent.

            Args:
                _identifier: Unused identifier argument accepted by this fake.

            Returns:
                Whether the requested test entry exists.
            """
            return False

        def child(self, *_args: object, **_kwargs: object) -> None:
            """Fail if recovery attempts to manufacture the lost directory.

            Args:
                *_args: Extra arguments accepted to match the collaborator signature.
                **_kwargs: Extra options accepted to match the collaborator signature.
            """
            pytest.fail("recreated committed payload")

    # Mark the canonical issue payload as missing.
    issue.store.task = AbsentTask()
    # Reject recovery when stored bytes violate the intent.
    with pytest.raises(core.WorkspaceError) as captured:
        core.Issue.recover(issue)  # type: ignore[arg-type]
    # Confirm the rejected operation reports RECOVERY_REQUIRED.
    assert captured.value.code == "RECOVERY_REQUIRED"
    assert "transaction.json" in control.documents
    assert order == []


def test_commit_persists_large_archive_result_by_reference() -> None:
    """Keep archive parts in their control document instead of request-id receipt state."""
    # Establish the issue state required by this transition.
    issue = Issue()
    state = copy.deepcopy(issue.state)
    result = {"snapshot": "a" * 64, "parts": [{"content": "archived bytes"}]}
    # Commit the intended bytes through the durable transaction.
    committed = core.Issue.commit(
        issue,
        state,
        issue.payload.copy(),
        request(operation="archive-prepare"),
        result,
    )  # type: ignore[arg-type]
    assert committed["parts"] == [{"content": "archived bytes"}]
    # Inspect the bounded receipt after the archive commit.
    receipt = issue.state["requests"][core.sha(b"request-1")]["result"]
    assert "parts" not in receipt
    assert receipt["result_ref"] == "export-" + "a" * 64 + ".json"
