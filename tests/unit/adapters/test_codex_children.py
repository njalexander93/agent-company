"""Verify child authorization decisions with storage and lifecycle boundaries mocked.

Real persistent lifecycle contracts remain in the adapter integration suite.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from nplet.adapters import codex_children as subject
from nplet.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit
AGENT = "12345678-1234-4234-8234-123456789abc"
SESSION = "parent/agent/" + AGENT
KEY = core.participant_key({"host": "codex", "session_id": SESSION})


@pytest.fixture
def boundary(monkeypatch):
    """Replace persistent boundaries while retaining real authorization predicates.

    Args:
        monkeypatch: Scoped replacement manager.

    Returns:
        Isolated events, registry records and observable boundary calls.
    """
    # Supply explicit host identities and a ready lifecycle response.
    parent = {
        "host": "codex",
        "session_id": "parent",
        "issue_id": "TEST-1",
        "binding_generation": 2,
        "worktree": str(Path.cwd()),
        "operation": "ready",
    }
    event = {
        "session_id": SESSION,
        "parent_session_id": "parent",
        "agent_id": AGENT,
        "child": True,
        "cwd": str(Path.cwd()),
        "tool_name": "Bash",
        "tool_use_id": "call",
        "tool_input": {"command": "cat -- /assigned"},
    }
    state = {"issue_id": "TEST-1", "launches": {}, "children": {KEY: {"status": "joined"}}}
    packet = {
        "ok": True,
        "code": "OK",
        "packet_digest": "packet",
        "references": [{"available": True, "locator": "/assigned", "sha256": core.sha(b"body")}],
    }
    bindings = Mock()
    bindings.exists.return_value = False
    execute = Mock(return_value=packet)
    issue = {
        "coordinator": core.participant_key(parent),
        "owners": {},
        "participants": {
            KEY: {"pending": {"call": {}}},
            core.participant_key(parent): {"pending": {"call": {}, "control": {}}},
        },
    }

    # Keep persistence out of the unit while exposing forbidden writes.
    @contextmanager
    def registry(_parent):
        """Yield the test registry without filesystem access.

        Args:
            _parent: Resolved parent identity used to select the isolated registry.

        Yields:
            The observable binding writer, record name and cohort state.
        """
        yield bindings, "registry.json", state

    def request_for(native, operation, _host):
        """Resolve only the supplied identity into an isolated request.

        Args:
            native: Normalized native event with explicit session identity.
            operation: Adapter function or lifecycle operation under examination.
            _host: Host selector supplied by the adapter.

        Returns:
            An isolated lifecycle request preserving the native session.
        """
        return {**parent, "session_id": native["session_id"], "operation": operation}

    monkeypatch.setattr(subject, "registry", registry)
    monkeypatch.setattr(subject.common, "request_for", request_for)
    monkeypatch.setattr(subject.core, "execute", execute)
    monkeypatch.setattr(subject.common, "_issue_state", Mock(return_value=issue))
    monkeypatch.setattr(
        subject, "source_paths", Mock(return_value={"/assigned": core.sha(b"body")})
    )
    monkeypatch.setattr(subject.common, "canonical_bootstrap", Mock(return_value=True))
    monkeypatch.setattr(
        subject.common, "bootstrap_request", Mock(return_value={"operation": "read"})
    )
    return SimpleNamespace(
        parent=parent,
        event=event,
        state=state,
        packet=packet,
        bindings=bindings,
        execute=execute,
        issue=issue,
    )


@pytest.mark.parametrize(
    "extra,code",
    [
        ({"is_background_agent": True}, "HOST_UNSUPPORTED_CHILD_IDENTITY"),
        ({"subagent_id": "other"}, "HOST_UNSUPPORTED_CHILD_IDENTITY"),
        ({"parent_conversation_id": "other"}, "HOST_UNSUPPORTED_CHILD_IDENTITY"),
        ({"session_id": SESSION}, "HOST_UNSUPPORTED_CHILD_IDENTITY"),
        ({"cwd": "relative"}, "REPOSITORY_MISMATCH"),
        ({"agent_id": "bad"}, "HOST_UNSUPPORTED_CHILD_IDENTITY"),
        ({"agent_id": 3}, "HOST_UNSUPPORTED_CHILD_IDENTITY"),
        ({"agent_id": AGENT.upper()}, "HOST_UNSUPPORTED_CHILD_IDENTITY"),
        ({"agent_type": "worker"}, "HOST_UNSUPPORTED_CHILD_IDENTITY"),
    ],
)
def test_identity_rejects_ambiguous_native_identity(extra, code):
    """Reject synthetic, unsupported and malformed identities before admission.

    Args:
        extra: Native envelope fields that exercise the selected rejection.
        code: Expected public authorization failure code.
    """
    # Attempt normalization without mutating caller data.
    event = {"session_id": "parent", "cwd": str(Path.cwd()), **extra}
    before = dict(event)
    with pytest.raises(core.WorkspaceError) as failure:
        subject.identity(event)
    assert failure.value.code == code
    assert event == before


def test_identity_discards_claimed_authority_and_separates_child():
    """Derive child authority solely from the observed UUID."""
    # Caller flags cannot promote a parent into a child.
    event = {
        "session_id": "parent",
        "cwd": str(Path.cwd()),
        "child": True,
        "parent_session_id": "foreign",
    }
    assert subject.identity(event) == {"session_id": "parent", "cwd": str(Path.cwd())}
    # An actual UUID retains its native parent separately.
    child = subject.identity({**event, "agent_id": AGENT})
    assert child["session_id"] == SESSION
    assert child["parent_session_id"] == "parent"
    assert child["child"] is True


def test_parent_request_uses_parent_without_mutating_child(boundary):
    """Resolve cohort authority without borrowing it for child identity.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
    """
    assert subject.parent_request(boundary.event) == boundary.parent
    assert boundary.event["session_id"] == SESSION


def spawn_event(boundary, **arguments):
    """Build a fresh independent native launch envelope.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
        **arguments: Overrides for the native launch arguments.

    Returns:
        A parent launch envelope with explicit fresh-history arguments.
    """
    return {
        **boundary.event,
        "session_id": "parent",
        "tool_name": subject.SPAWN,
        "child": False,
        "tool_input": {
            "task_name": "reader",
            "message": "Read assigned input",
            "fork_turns": "none",
            **arguments,
        },
    }


@pytest.mark.parametrize(
    "arguments,code",
    [
        ({"fork_turns": "all"}, "HOST_UNSUPPORTED_CHILD_IDENTITY"),
        ({"message": None}, "HOST_UNSUPPORTED_CHILD_IDENTITY"),
        ({"unknown": 1}, "HOST_UNSUPPORTED_CHILD_IDENTITY"),
        ({"task_name": "nested/name"}, "INVALID_REQUEST"),
    ],
)
def test_spawn_rejects_unbounded_inputs_without_transactions(boundary, arguments, code):
    """Reject inherited or malformed launches before touching lifecycle state.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
        arguments: Launch arguments that must fail admission.
        code: Expected public authorization failure code.
    """
    with pytest.raises(core.WorkspaceError) as failure:
        subject.admit_spawn(spawn_event(boundary, **arguments))
    assert failure.value.code == code
    boundary.execute.assert_not_called()
    boundary.bindings.put.assert_not_called()


def test_spawn_reserves_once_and_rejects_changed_replay(boundary):
    """An exact replay cannot reserve twice or change its assigned task.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
    """
    # Admit a new launch and replay its identical callback.
    event = spawn_event(boundary)
    subject.admit_spawn(event)
    subject.admit_spawn(event)
    starts = [
        call.args[0]
        for call in boundary.execute.call_args_list
        if call.args[0]["operation"] == "tool-start"
    ]
    assert len(starts) == 1
    assert starts[0]["session_id"] == "parent"
    assert starts[0]["tool_id"] == "call"
    boundary.bindings.put.assert_called_once()
    # Altered replay cannot overwrite the receipt.
    with pytest.raises(core.WorkspaceError) as failure:
        subject.admit_spawn(spawn_event(boundary, message="Changed"))
    assert failure.value.code == "REQUEST_CONFLICT"
    boundary.bindings.put.assert_called_once()


@pytest.mark.parametrize(
    "mode,code",
    [
        ("owner", "NOT_OWNER"),
        ("capacity", "HOST_CHILD_CAPACITY_EXHAUSTED"),
        ("duplicate", "REQUEST_CONFLICT"),
        ("unready", "STALE_CONTEXT"),
    ],
)
def test_spawn_denials_do_not_start_work(boundary, mode, code):
    """Deny foreign coordinators, exhausted cohorts and duplicate names.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
        mode: Independent failure condition under examination.
        code: Expected public authorization failure code.
    """
    # Arrange each independent authorization failure at its actual boundary.
    if mode == "owner":
        boundary.issue["coordinator"] = "foreign"
    elif mode == "capacity":
        boundary.state["launches"] = {
            str(i): {"task_name": str(i)} for i in range(subject.MAX_LAUNCHES)
        }
    elif mode == "duplicate":
        boundary.state["launches"] = {"older": {"task_name": "reader"}}
    else:
        boundary.execute.return_value = {"ok": False, "code": code}
    # No denied admission may create pending native work.
    with pytest.raises(core.WorkspaceError) as failure:
        subject.admit_spawn(spawn_event(boundary))
    assert failure.value.code == code
    assert all(call.args[0]["operation"] == "ready" for call in boundary.execute.call_args_list)
    boundary.bindings.put.assert_not_called()


@pytest.mark.parametrize(
    "member,tool,args,code",
    [
        (False, "Bash", {"command": "cat -- /assigned"}, "BINDING_MISSING"),
        (True, "Other", {}, "HOST_UNSUPPORTED_TOOL"),
        (True, "Bash", {"command": "read", "timeout": 1}, "INVALID_REQUEST"),
    ],
)
def test_child_pre_rejects_unjoined_or_unsupported_calls(boundary, member, tool, args, code):
    """Reject foreign members and extra shell authority without transactions.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
        member: Whether the child has observed cohort membership.
        tool: Native tool name under examination.
        args: Native argument shape under examination.
        code: Expected public authorization failure code.
    """
    # Remove the observation or alter the tool envelope.
    if not member:
        boundary.state["children"].clear()
    event = {**boundary.event, "tool_name": tool, "tool_input": args}
    with pytest.raises(core.WorkspaceError) as failure:
        subject.child_pre(event)
    assert failure.value.code == code
    boundary.execute.assert_not_called()
    boundary.bindings.put.assert_not_called()


def test_source_admission_tracks_child_pending_before_receipt(boundary):
    """Source reads reserve authoritative work under the child's own identity.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
    """
    subject.child_pre(boundary.event)
    started = boundary.execute.call_args.args[0]
    assert started["operation"] == "source-read-start"
    assert started["session_id"] == SESSION
    assert started["source_locator"] == "/assigned"
    assert boundary.state["children"][KEY]["source_reads"] == {
        "call": {"path": "/assigned", "packet_digest": "packet"}
    }
    boundary.bindings.put.assert_called_once()


@pytest.mark.parametrize(
    "mode,code",
    [
        ("completed", "REQUEST_CONFLICT"),
        ("changed", "REQUEST_CONFLICT"),
        ("capacity", "RECOVERY_REQUIRED"),
        ("settled", "REQUEST_CONFLICT"),
    ],
)
def test_source_read_rejections_preserve_uncertain_work(boundary, mode, code):
    """Reject replay, overload and settled core receipts without admitting execution.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
        mode: Independent failure condition under examination.
        code: Expected public authorization failure code.
    """
    # Preserve pending receipts while selecting an independent invalid case.
    reads = {"call": {"path": "/assigned", "packet_digest": "packet"}}
    if mode == "completed":
        reads["call"]["completed"] = True
    elif mode == "changed":
        reads["call"]["path"] = "/foreign"
    elif mode == "capacity":
        reads = {str(i): {"path": "/assigned"} for i in range(subject.MAX_PENDING_READS)}
    else:
        reads = {}
        boundary.issue["participants"][KEY]["pending"].clear()
    boundary.state["children"][KEY]["source_reads"] = reads
    before = dict(reads)
    # Rejection cannot persist a fresh native receipt.
    with pytest.raises(core.WorkspaceError) as failure:
        subject.child_pre(boundary.event)
    assert failure.value.code == code
    if mode != "settled":
        assert reads == before
    boundary.bindings.put.assert_not_called()


@pytest.mark.parametrize(
    "operation,code",
    [("scope", "NOT_OWNER"), ("acknowledge", "SOURCE_DELIVERY_REQUIRED"), ("update", "NOT_OWNER")],
)
def test_child_lifecycle_forbids_unearned_authority(boundary, operation, code):
    """Readiness alone grants neither source acknowledgment nor write ownership.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
        operation: Adapter function or lifecycle operation under examination.
        code: Expected public authorization failure code.
    """
    subject.common.bootstrap_request.return_value = {
        "operation": operation,
        "path": "context/note.md",
    }
    event = {**boundary.event, "tool_input": {"command": "lifecycle"}}
    with pytest.raises(core.WorkspaceError) as failure:
        subject.child_pre(event)
    assert failure.value.code == code
    boundary.bindings.put.assert_not_called()


@pytest.mark.parametrize("operation", ["read", "ready", "diagnose", "acknowledge", "update"])
def test_child_lifecycle_accepts_only_evidenced_scope(boundary, operation):
    """Accept own bounded operations after their distinct prerequisites hold.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
        operation: Adapter function or lifecycle operation under examination.
    """
    # Supply current source bytes and explicit ownership, never inferred scope.
    boundary.state["children"][KEY]["source_reads"] = {
        "old": {
            "path": "/assigned",
            "packet_digest": "packet",
            "completed": True,
            "content_sha256": core.sha(b"body"),
        }
    }
    boundary.issue["owners"]["context/note.md"] = KEY
    subject.common.bootstrap_request.return_value = {
        "operation": operation,
        "path": "context/note.md",
    }
    subject.child_pre({**boundary.event, "tool_input": {"command": "lifecycle"}})
    boundary.bindings.put.assert_not_called()


@pytest.mark.parametrize(
    "change",
    [
        {"tool_use_id": "unknown"},
        {"tool_name": "Other"},
        {"tool_response": None},
        {"tool_input": {"command": "cat -- /foreign"}},
    ],
)
def test_post_ignores_uncorrelated_delivery(boundary, change):
    """Unmatched output must not settle work or establish delivered bytes.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
        change: Completion-envelope field that breaks native correlation.
    """
    reads = {"call": {"path": "/assigned", "packet_digest": "packet"}}
    boundary.state["children"][KEY]["source_reads"] = reads
    subject.child_post({**boundary.event, "tool_response": "body", **change})
    assert reads["call"].get("completed") is None
    boundary.execute.assert_not_called()
    boundary.bindings.put.assert_not_called()


def test_delayed_post_keeps_current_packet_and_pending_reads(boundary):
    """An old packet's final bytes cannot erase newer or unfinished evidence.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
    """
    # Include obsolete duplicate evidence and newer packet delivery.
    reads = {
        "call": {"path": "/assigned", "packet_digest": "old"},
        "duplicate": {"path": "/assigned", "packet_digest": "old", "completed": True},
        "current": {"path": "/assigned", "packet_digest": "packet", "completed": True},
        "pending": {"path": "/assigned", "packet_digest": "old"},
    }
    boundary.state["children"][KEY]["source_reads"] = reads
    subject.child_post({**boundary.event, "tool_response": "body"})
    assert set(reads) == {"call", "current", "pending"}
    assert reads["call"]["content_sha256"] == core.sha(b"body")
    assert reads["call"]["completed"] is True
    settled = boundary.execute.call_args.args[0]
    assert settled["session_id"] == SESSION
    assert settled["operation"] == "tool-complete"
    assert settled["tool_id"] == "call"


def test_end_is_advisory_and_preserves_pending_source(boundary):
    """Ending observations do not infer completion of unresolved native work.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
    """
    member = boundary.state["children"][KEY]
    member["source_reads"] = {"call": {"path": "/assigned"}}
    subject.child_end({**boundary.event, "hook_event_name": "Stop", "turn_id": "turn"})
    assert member["last_ending"] == {"event": "Stop", "turn_id": "turn"}
    assert member["source_reads"] == {"call": {"path": "/assigned"}}
    boundary.execute.assert_not_called()


@pytest.mark.parametrize("function", [subject.child_end, subject.child_post])
def test_foreign_child_cannot_end_or_complete(boundary, function):
    """Completion signals cannot create missing cohort membership.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
        function: Child completion callback under examination.
    """
    boundary.state["children"].clear()
    with pytest.raises(core.WorkspaceError) as failure:
        function(boundary.event)
    assert failure.value.code == "BINDING_MISSING"
    boundary.execute.assert_not_called()
    boundary.bindings.put.assert_not_called()


@pytest.mark.parametrize(
    "target,allowed", [("/root/reader", True), ("reader", False), ("/other/reader", False)]
)
def test_control_requires_observed_exact_target(boundary, target, allowed):
    """Model-facing names and foreign addresses cannot authorize control.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
        target: Native control address supplied by the caller.
        allowed: Whether the exact observed address authorizes control.
    """
    boundary.state["launches"] = {"spawn": {"target": "/root/reader"}}
    event = {
        **spawn_event(boundary),
        "tool_name": "collaborationinterrupt_agent",
        "tool_input": {"target": target},
    }
    # Only the observed address may acquire parent pending work.
    if allowed:
        subject.admit_control(event)
        assert boundary.execute.call_args.args[0]["operation"] == "tool-start"
        assert boundary.execute.call_args.args[0]["session_id"] == "parent"
    else:
        with pytest.raises(core.WorkspaceError) as failure:
            subject.admit_control(event)
        assert failure.value.code == "HOST_UNSUPPORTED_CHILD_IDENTITY"
        assert boundary.execute.call_count == 1
    if allowed:
        boundary.bindings.put.assert_called_once()
    else:
        boundary.bindings.put.assert_not_called()


@pytest.mark.parametrize(
    "response,outcome",
    [
        ('{"task_name": "/root/reader"}', "target_observed"),
        ({"task_name": "/root/reader"}, "target_observed"),
        ("malformed", "unknown"),
        ({"error": "failed"}, "unknown"),
    ],
)
@pytest.mark.parametrize("tool_name", ["collaborationspawn_agent"])
def test_parent_post_retains_ambiguous_launch_capacity(boundary, response, outcome, tool_name):
    """A final response settles its parent call without proving no child exists.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
        tool_name: Exact observed or legacy native launch spelling.
        response: Native completion payload under examination.
        outcome: Expected persisted certainty of the launch outcome.
    """
    launch = {"task_name": "reader"}
    boundary.state["launches"]["call"] = launch
    subject.parent_post(
        {**spawn_event(boundary), "tool_name": tool_name, "tool_response": response}
    )
    assert launch["outcome"] == outcome
    assert launch["completed"] is True
    assert "call" in boundary.state["launches"]
    assert boundary.execute.call_args.args[0]["session_id"] == "parent"
    assert boundary.execute.call_args.args[0]["operation"] == "tool-complete"


@pytest.mark.parametrize(
    "response,existing,code",
    [
        (None, None, "INVALID_REQUEST"),
        ({"task_name": "/root/foreign"}, None, "BINDING_CONFLICT"),
        ({"task_name": "/root/reader"}, "/other/reader", "REQUEST_CONFLICT"),
    ],
)
@pytest.mark.parametrize("tool_name", ["collaborationspawn_agent"])
def test_parent_post_rejects_ambiguous_identity_without_settlement(
    boundary, response, existing, code, tool_name
):
    """Conflicting completion cannot overwrite a target or settle pending work.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
        tool_name: Exact observed or legacy native launch spelling.
        response: Native completion payload under examination.
        existing: Whether an existing record or target is present.
        code: Expected public authorization failure code.
    """
    launch = {"task_name": "reader"}
    if existing:
        launch["target"] = existing
    boundary.state["launches"]["call"] = launch
    with pytest.raises(core.WorkspaceError) as failure:
        subject.parent_post(
            {**spawn_event(boundary), "tool_name": tool_name, "tool_response": response}
        )
    assert failure.value.code == code
    boundary.execute.assert_not_called()
    boundary.bindings.put.assert_not_called()


def test_source_paths_uses_canonical_store_and_skips_unavailable(monkeypatch):
    """Managed locators resolve in the registered main checkout.

    Args:
        monkeypatch: Scoped replacements for external and persistent boundaries.
    """
    store = MagicMock()
    store.__enter__.return_value.registration = {"main": str(Path.cwd() / "canonical")}
    monkeypatch.setattr(core, "Store", Mock(return_value=store))
    packet = {
        "references": [
            {"available": True, "locator": "context/note.md", "sha256": "managed"},
            {"available": True, "locator": str(Path.cwd() / "external"), "sha256": "external"},
            {"available": False, "locator": "missing"},
        ]
    }
    assert subject.source_paths({"issue_id": "TEST-1"}, packet) == {
        str(Path.cwd() / "canonical/.task/TEST-1/context/note.md"): "managed",
        str(Path.cwd() / "external"): "external",
    }


@pytest.mark.parametrize("existing,foreign", [(False, False), (True, False), (True, True)])
def test_registry_is_generation_bound_and_rejects_foreign_issue(monkeypatch, existing, foreign):
    """Select the exact parent's generation under a lock without adopting another issue.

    Args:
        monkeypatch: Scoped replacements for external and persistent boundaries.
        existing: Whether an existing record or target is present.
        foreign: Whether stored issue identity conflicts with the current binding.
    """
    # Mock OS directory/lock boundaries; exercise real record selection and validation.
    parent = {
        "host": "codex",
        "session_id": "parent",
        "issue_id": "TEST-1",
        "binding_generation": 4,
        "worktree": str(Path.cwd()),
    }
    directory = MagicMock()
    directory.__enter__.return_value = directory
    directory.child.return_value = directory
    directory.exists.return_value = existing
    stored = {
        "issue_id": "FOREIGN" if foreign else "TEST-1",
        "launches": {"call": {}},
        "children": {},
    }
    directory.json.return_value = stored
    monkeypatch.setattr(core, "repository", Mock(return_value=(Path.cwd(), None, None)))
    monkeypatch.setattr(core.Directory, "absolute", Mock(return_value=directory))
    # Foreign issue data must never escape the registry boundary.
    if foreign:
        with pytest.raises(core.WorkspaceError) as failure:
            with subject.registry(parent):
                pytest.fail("Foreign state escaped authorization")
        assert failure.value.code == "BINDING_CONFLICT"
    else:
        with subject.registry(parent) as (bindings, name, state):
            assert bindings is directory
            assert name == core.participant_key(parent) + ".codex-children-4.json"
            assert state == (
                stored if existing else {"issue_id": "TEST-1", "launches": {}, "children": {}}
            )
    directory.lock.assert_called_once_with("codex-children.lock")
    directory.put.assert_not_called()


@pytest.mark.parametrize("existing", [False, True])
def test_start_joins_reader_and_preserves_existing_scope(boundary, monkeypatch, existing):
    """A reserved UUID joins without selecting write ownership by launch order.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
        monkeypatch: Scoped replacements for external and persistent boundaries.
        existing: Whether an existing record or target is present.
    """
    # Admit one reader slot; returning membership remains explicit.
    boundary.state["launches"]["spawn"] = {"task_name": "reader"}
    if not existing:
        boundary.state["children"].clear()
    boundary.bindings.exists.return_value = existing
    boundary.bindings.json.return_value = {"issue_id": "TEST-1"}
    join = Mock()
    monkeypatch.setattr(subject.common, "_join_child", join)
    monkeypatch.setattr(subject, "bootstrap_context", Mock(return_value="reader commands"))
    assert subject.start(boundary.event) == "reader commands"
    assert boundary.state["children"][KEY]["status"] == "joined"
    assert boundary.issue["owners"] == {}
    join.assert_called_once()
    assert join.call_args.args[:3] == (boundary.event, "codex", boundary.parent)
    assert any(
        call.args == (KEY + ".assignment.json", {"issue_id": "TEST-1"})
        for call in boundary.bindings.put.call_args_list
    )


@pytest.mark.parametrize(
    "mode,code",
    [
        ("nested", "HOST_UNSUPPORTED_CHILD_IDENTITY"),
        ("capacity", "HOST_UNSUPPORTED_CHILD_IDENTITY"),
        ("foreign", "BINDING_CONFLICT"),
        ("unready", "STALE_CONTEXT"),
    ],
)
def test_start_rejects_unadmitted_or_foreign_children(boundary, monkeypatch, mode, code):
    """A UUID alone never authorizes joining a cohort or stealing an assignment.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
        monkeypatch: Scoped replacements for external and persistent boundaries.
        mode: Independent failure condition under examination.
        code: Expected public authorization failure code.
    """
    # Select each rejection without invoking the real join transaction.
    event = dict(boundary.event)
    join = Mock()
    monkeypatch.setattr(subject.common, "_join_child", join)
    if mode == "nested":
        event["child"] = False
    elif mode == "capacity":
        boundary.state["children"].clear()
    elif mode == "foreign":
        boundary.bindings.exists.return_value = True
        boundary.bindings.json.return_value = {"issue_id": "FOREIGN"}
    else:
        boundary.execute.return_value = {"ok": False, "code": code}
    with pytest.raises(core.WorkspaceError) as failure:
        subject.start(event)
    assert failure.value.code == code
    join.assert_not_called()
    boundary.bindings.put.assert_not_called()


def test_bootstrap_context_orders_current_packet_commands(boundary, monkeypatch):
    """Instructions bind acknowledgment to the current packet and child's identity.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
        monkeypatch: Scoped replacements for external and persistent boundaries.
    """
    # Observe generated lifecycle requests independently of shell serialization.
    command = Mock(side_effect=["read-command", "ack-command", "ready-command"])
    monkeypatch.setattr(subject.common, "bootstrap_command", command)
    result = subject.bootstrap_context(boundary.event)
    assert "1. read-command\n2. cat -- /assigned\n3. ack-command\n4. ready-command" in result
    assert "session_id=" + SESSION in result
    requests = [call.args[0] for call in command.call_args_list]
    assert [request["operation"] for request in requests] == ["read", "acknowledge", "ready"]
    assert all(request["session_id"] == SESSION for request in requests)
    assert requests[1]["packet_digest"] == "packet"
    assert requests[1]["request_id"] != requests[2]["request_id"]
    boundary.bindings.put.assert_not_called()


def test_source_replay_preserves_pending_and_prunes_only_completed_old_packets(boundary):
    """Exact pending replay retains uncertainty while dropping obsolete delivery evidence.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
    """
    reads = {
        "call": {"path": "/assigned", "packet_digest": "packet"},
        "old": {"path": "/assigned", "packet_digest": "old", "completed": True},
        "uncertain": {"path": "/assigned", "packet_digest": "old"},
    }
    boundary.state["children"][KEY]["source_reads"] = reads
    subject.child_pre(boundary.event)
    assert set(reads) == {"call", "uncertain"}
    assert boundary.execute.call_count == 1
    assert boundary.execute.call_args.args[0]["operation"] == "read"


def test_control_post_settles_only_parent_call_and_compacts_receipt(boundary):
    """Control completion cannot infer any child work has ended.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
    """
    # Correlate the completion to an admitted target-bearing control.
    boundary.state["launches"]["spawn"] = {"target": "/root/reader"}
    event = {
        **spawn_event(boundary),
        "tool_name": "collaborationinterrupt_agent",
        "tool_input": {"target": "/root/reader"},
    }
    subject.admit_control(event)
    boundary.bindings.put.reset_mock()
    subject.parent_post({**event, "tool_response": {"status": "interrupted"}})
    settled = boundary.execute.call_args.args[0]
    assert settled["operation"] == "tool-complete"
    assert settled["session_id"] == "parent"
    assert settled["tool_id"] == "call"
    assert boundary.state["children"][KEY] == {"status": "joined"}
    assert boundary.state["controls"] == {}
    boundary.bindings.put.assert_called_once()


@pytest.mark.parametrize(
    "operation",
    [
        subject.child_pre,
        subject.child_post,
        subject.bootstrap_context,
        subject.admit_control,
        subject.parent_post,
    ],
)
def test_failed_core_operation_propagates_without_persisting(boundary, operation):
    """Core rejection must not be translated into successful hook bookkeeping.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
        operation: Adapter function or lifecycle operation under examination.
    """
    # Reach each operation's first core boundary with valid native correlation.
    boundary.execute.return_value = {"ok": False, "code": "STALE_CONTEXT"}
    boundary.state["children"][KEY]["source_reads"] = {
        "call": {"path": "/assigned", "packet_digest": "packet"}
    }
    event = {**boundary.event, "tool_response": "body"}
    if operation in {subject.admit_control, subject.parent_post}:
        event = {**event, "child": False, "session_id": "parent"}
    if operation is subject.parent_post:
        event["tool_name"] = "collaborationlist_agents"
        event["tool_input"] = {}
        boundary.state["controls"] = {"call": subject.control_receipt(event)}
    with pytest.raises(core.WorkspaceError) as failure:
        operation(event)
    assert failure.value.code == "STALE_CONTEXT"
    boundary.bindings.put.assert_not_called()


def test_arbitrary_shell_command_is_not_a_lifecycle_operation(boundary):
    """A shell command cannot borrow the lifecycle-command exception.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
    """
    subject.common.canonical_bootstrap.return_value = False
    with pytest.raises(core.WorkspaceError) as failure:
        subject.child_pre({**boundary.event, "tool_input": {"command": "rm /assigned"}})
    assert failure.value.code == "HOST_UNSUPPORTED_TOOL"
    subject.common.bootstrap_request.assert_not_called()
    boundary.bindings.put.assert_not_called()


@pytest.mark.parametrize(
    "operation", [subject.admit_spawn, subject.admit_control, subject.child_pre]
)
def test_pending_reservation_failure_never_persists_admission(boundary, operation):
    """A rejected pending-work reservation must stop native execution admission.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
        operation: Adapter function or lifecycle operation under examination.
    """
    # Make readiness succeed but fail the subsequent pending reservation.
    boundary.execute.side_effect = [boundary.packet, {"ok": False, "code": "RECOVERY_REQUIRED"}]
    boundary.state["launches"] = {"old": {"task_name": "other", "target": "/root/other"}}
    event = spawn_event(boundary)
    if operation is subject.admit_control:
        event["tool_input"] = {"target": "/root/other"}
        event["tool_name"] = "collaborationinterrupt_agent"
    elif operation is subject.child_pre:
        event = boundary.event
    # No local receipt may make a rejected core reservation look admitted.
    with pytest.raises(core.WorkspaceError) as failure:
        operation(event)
    assert failure.value.code == "RECOVERY_REQUIRED"
    boundary.bindings.put.assert_not_called()
    assert "call" not in boundary.state["launches"]
    assert "call" not in boundary.state["children"][KEY].get("source_reads", {})


@pytest.mark.parametrize(
    "evidence",
    [
        {"packet_digest": "old", "completed": True, "content_sha256": core.sha(b"body")},
        {"packet_digest": "packet", "completed": False, "content_sha256": core.sha(b"body")},
        {"packet_digest": "packet", "completed": True, "content_sha256": core.sha(b"truncated")},
    ],
)
def test_acknowledge_rejects_stale_incomplete_or_wrong_bytes(boundary, evidence):
    """Only completed exact bytes from the current packet authorize acknowledgment.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
        evidence: Delivery receipt whose authority must be checked.
    """
    # Supply one deceptive delivery receipt at a time.
    boundary.state["children"][KEY]["source_reads"] = {"old": {"path": "/assigned", **evidence}}
    subject.common.bootstrap_request.return_value = {"operation": "acknowledge"}
    with pytest.raises(core.WorkspaceError) as failure:
        subject.child_pre({**boundary.event, "tool_input": {"command": "lifecycle"}})
    assert failure.value.code == "SOURCE_DELIVERY_REQUIRED"
    boundary.bindings.put.assert_not_called()


def test_join_failure_leaves_recoverable_joining_receipt(boundary, monkeypatch):
    """A failed join retains admission uncertainty without claiming joined readiness.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
        monkeypatch: Scoped replacements for external and persistent boundaries.
    """
    # Reserve a native slot and fail at the persistent join boundary.
    boundary.state["children"].clear()
    boundary.state["launches"]["spawn"] = {"task_name": "reader"}
    monkeypatch.setattr(
        subject.common, "_join_child", Mock(side_effect=core.WorkspaceError("RECOVERY_REQUIRED"))
    )
    bootstrap = Mock()
    monkeypatch.setattr(subject, "bootstrap_context", bootstrap)
    with pytest.raises(core.WorkspaceError) as failure:
        subject.start(boundary.event)
    assert failure.value.code == "RECOVERY_REQUIRED"
    assert boundary.state["children"][KEY] == {"agent_id": AGENT, "status": "joining"}
    bootstrap.assert_not_called()


@pytest.mark.parametrize("tool_name", ["collaborationspawn_agent"])
def test_unknown_spawn_completion_cannot_settle_unadmitted_work(boundary, tool_name):
    """A host result without an admitted launch cannot attach a control target.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
        tool_name: Exact observed or legacy native launch spelling.
    """
    with pytest.raises(core.WorkspaceError) as failure:
        subject.parent_post(
            {
                **spawn_event(boundary),
                "tool_name": tool_name,
                "tool_response": {"task_name": "/root/reader"},
            }
        )
    assert failure.value.code == "UNKNOWN_OPERATION"
    boundary.execute.assert_not_called()
    boundary.bindings.put.assert_not_called()


@pytest.mark.parametrize(
    "extra,code",
    [
        ({"child": True}, "HOST_UNSUPPORTED_CHILD_IDENTITY"),
        ({"tool_input": None}, "INVALID_REQUEST"),
    ],
)
def test_control_rejects_nested_or_malformed_envelopes(boundary, extra, code):
    """Child nesting and malformed control requests cannot reserve parent work.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
        extra: Native envelope fields that exercise the selected rejection.
        code: Expected public authorization failure code.
    """
    with pytest.raises(core.WorkspaceError) as failure:
        subject.admit_control({**spawn_event(boundary), **extra})
    assert failure.value.code == code
    assert all(call.args[0]["operation"] == "ready" for call in boundary.execute.call_args_list)
    boundary.bindings.put.assert_not_called()


def test_owned_update_still_requires_current_readiness(boundary):
    """Reject an owned write when the readiness check rejects current context.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
    """
    # Ownership alone cannot replace the core readiness decision.
    boundary.issue["owners"]["context/note.md"] = KEY
    subject.common.bootstrap_request.return_value = {
        "operation": "update",
        "path": "context/note.md",
    }
    boundary.execute.side_effect = [boundary.packet, {"ok": False, "code": "STALE_CONTEXT"}]
    with pytest.raises(core.WorkspaceError) as failure:
        subject.child_pre({**boundary.event, "tool_input": {"command": "lifecycle"}})
    assert failure.value.code == "STALE_CONTEXT"
    boundary.bindings.put.assert_not_called()


def test_source_store_validation_failure_is_not_hidden(monkeypatch):
    """Do not return guessed paths when canonical storage cannot be validated.

    Args:
        monkeypatch: Scoped replacements for external and persistent boundaries.
    """
    # Fail the real storage constructor boundary before any locator is resolved.
    monkeypatch.setattr(core, "Store", Mock(side_effect=core.WorkspaceError("REPOSITORY_MISMATCH")))
    with pytest.raises(core.WorkspaceError) as failure:
        subject.source_paths({"issue_id": "TEST-1"}, {"references": []})
    assert failure.value.code == "REPOSITORY_MISMATCH"


@pytest.mark.parametrize("settled_in_registry", [True, False])
def test_spawn_replay_cannot_resurrect_settled_native_work(boundary, settled_in_registry):
    """Settled core or registry evidence prevents a second launch admission.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
        settled_in_registry: Whether terminal completion is also recorded locally.
    """
    # Establish a legitimate original reservation, then independently settle it.
    event = spawn_event(boundary)
    subject.admit_spawn(event)
    boundary.issue["participants"][core.participant_key(boundary.parent)]["pending"].clear()
    if settled_in_registry:
        boundary.state["launches"]["call"]["completed"] = True
    boundary.execute.reset_mock()
    boundary.bindings.put.reset_mock()
    # The prior arguments and receipt cannot restore permission to execute.
    with pytest.raises(core.WorkspaceError) as failure:
        subject.admit_spawn(event)
    assert failure.value.code == "REQUEST_CONFLICT"
    boundary.bindings.put.assert_not_called()
    assert all(call.args[0]["operation"] == "ready" for call in boundary.execute.call_args_list)


def test_exact_child_diagnosis_remains_available_before_packet_read(boundary):
    """A valid diagnostic command can recover when assigned sources are stale.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
    """
    # A source-dependent core read would fail; diagnosis must not attempt it.
    boundary.execute.side_effect = core.WorkspaceError("SOURCE_STALE")
    subject.common.bootstrap_request.return_value = {"operation": "diagnose"}
    event = {**boundary.event, "tool_input": {"command": "canonical-diagnose"}}
    subject.child_pre(event)
    subject.common.canonical_bootstrap.assert_called_once_with(
        event, "canonical-diagnose", "codex", False
    )
    boundary.execute.assert_not_called()
    subject.source_paths.assert_not_called()
    boundary.bindings.put.assert_not_called()


def test_diagnostic_claim_cannot_bypass_canonical_identity_validation(boundary):
    """Unverified diagnostic text has no recovery exception.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
    """
    # Model a parser rejecting the command's claimed identity or extra fields.
    subject.common.canonical_bootstrap.return_value = False
    subject.common.bootstrap_request.return_value = {"operation": "diagnose"}
    with pytest.raises(core.WorkspaceError) as failure:
        subject.child_pre({**boundary.event, "tool_input": {"command": "foreign-diagnose"}})
    assert failure.value.code == "HOST_UNSUPPORTED_TOOL"
    subject.common.bootstrap_request.assert_not_called()
    boundary.bindings.put.assert_not_called()


def native_spawn_event(boundary, **arguments):
    """Build the observed native spawn schema without legacy task-name fields.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
        **arguments: Overrides for the native launch arguments.

    Returns:
        A native spawn event with explicit fresh-context intent.
    """
    return {
        **spawn_event(boundary),
        "tool_name": "spawn_agent",
        "tool_input": {"message": "Read assigned source", "fork_context": False, **arguments},
    }


def test_native_launch_admits_exact_schema_and_tracks_pending_work(boundary):
    """The observed native schema reserves a reader without legacy task-name fields.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
    """
    # Native launch arguments use fresh context explicitly.
    subject.admit_spawn(native_spawn_event(boundary))
    assert "call" in boundary.state["launches"]
    request = boundary.execute.call_args.args[0]
    assert request["operation"] == "tool-start"
    assert request["session_id"] == "parent"
    assert request["tool_id"] == "call"
    boundary.bindings.put.assert_called_once()


@pytest.mark.parametrize(
    "arguments",
    [
        {"fork_context": True},
        {"fork_context": 0},
        {"task_name": "reader"},
        {"fork_turns": "none"},
        {"items": [{"type": "text", "text": "Read"}]},
        {"message": None},
    ],
)
def test_native_launch_rejects_inherited_or_unimplemented_shapes(boundary, arguments):
    """Schema variants outside the bounded message route cannot acquire authority.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
        arguments: Unsupported native launch fields.
    """
    with pytest.raises(core.WorkspaceError) as failure:
        subject.admit_spawn(native_spawn_event(boundary, **arguments))
    assert failure.value.code == "HOST_UNSUPPORTED_CHILD_IDENTITY"
    boundary.execute.assert_not_called()
    boundary.bindings.put.assert_not_called()


@pytest.mark.parametrize(
    "response",
    [
        {"agent_id": AGENT, "nickname": "Reader"},
        '{"agent_id": "12345678-1234-4234-8234-123456789abc", "nickname": null}',
    ],
)
def test_native_spawn_response_registers_uuid_control_target(boundary, response):
    """Only the returned agent UUID becomes the native control address.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
        response: Observed native response representation.
    """
    # Reserve with the actual native schema before delivering its completion.
    event = native_spawn_event(boundary)
    subject.admit_spawn(event)
    subject.parent_post({**event, "tool_response": response})
    assert boundary.state["launches"]["call"]["target"] == AGENT
    assert boundary.state["launches"]["call"]["outcome"] == "target_observed"
    assert boundary.execute.call_args.args[0]["operation"] == "tool-complete"
    assert boundary.issue["owners"] == {}


@pytest.mark.parametrize("agent_id", ["bad", AGENT.upper(), 3, "/root/reader"])
def test_native_spawn_response_rejects_invalid_uuid_without_settlement(boundary, agent_id):
    """Malformed native addresses cannot become targets or settle parent work.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
        agent_id: Malformed or noncanonical native UUID.
    """
    # Clear admission observations to isolate completion effects.
    event = native_spawn_event(boundary)
    subject.admit_spawn(event)
    boundary.execute.reset_mock()
    boundary.bindings.put.reset_mock()
    with pytest.raises(core.WorkspaceError) as failure:
        subject.parent_post({**event, "tool_response": {"agent_id": agent_id}})
    assert failure.value.code == "HOST_UNSUPPORTED_CHILD_IDENTITY"
    boundary.execute.assert_not_called()
    boundary.bindings.put.assert_not_called()
    assert "target" not in boundary.state["launches"]["call"]


@pytest.mark.parametrize("tool_name", ["multi_agent_v1send_input", "multi_agent_v1close_agent"])
@pytest.mark.parametrize(
    "target,allowed",
    [(AGENT, True), ("87654321-4321-4321-8321-abcdefabcdef", False), ("/root/reader", False)],
)
def test_native_controls_require_own_observed_uuid(boundary, tool_name, target, allowed):
    """Native input and close operations cannot target another parent's agent.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
        tool_name: Observed native control operation.
        target: Supplied control target.
        allowed: Whether the UUID belongs to this launch cohort.
    """
    # Establish native address evidence through the actual admission/completion helpers.
    event = native_spawn_event(boundary)
    subject.admit_spawn(event)
    subject.parent_post({**event, "tool_response": {"agent_id": AGENT}})
    boundary.execute.reset_mock()
    boundary.bindings.put.reset_mock()
    control = {
        **event,
        "tool_name": tool_name,
        "tool_use_id": "control",
        "tool_input": {
            "target": target,
            **(
                {"message": "Continue", "interrupt": True}
                if tool_name == "multi_agent_v1send_input"
                else {}
            ),
        },
    }
    if allowed:
        subject.admit_control(control)
        assert boundary.execute.call_args.args[0]["operation"] == "tool-start"
        assert boundary.execute.call_args.args[0]["tool_id"] == "control"
    else:
        with pytest.raises(core.WorkspaceError) as failure:
            subject.admit_control(control)
        assert failure.value.code == "HOST_UNSUPPORTED_CHILD_IDENTITY"
        assert all(call.args[0]["operation"] == "ready" for call in boundary.execute.call_args_list)
    if allowed:
        boundary.bindings.put.assert_called_once()
    else:
        boundary.bindings.put.assert_not_called()


def test_native_independent_identical_prompts_are_separate_launches(boundary):
    """Native call identity separates two readers even when their prompts match.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
    """
    # Native schemas have no task name; message equality cannot collapse reservations.
    first = native_spawn_event(boundary)
    second = {**first, "tool_use_id": "second"}
    subject.admit_spawn(first)
    subject.admit_spawn(second)
    assert set(boundary.state["launches"]) == {"call", "second"}
    starts = [
        call.args[0]
        for call in boundary.execute.call_args_list
        if call.args[0]["operation"] == "tool-start"
    ]
    assert {request["tool_id"] for request in starts} == {"call", "second"}


@pytest.mark.parametrize("completed", [True, False])
def test_native_spawn_replay_retains_pending_requirement(boundary, completed):
    """Native schema support cannot bypass the settled-replay authorization gate.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
        completed: Whether the core pending reservation has settled.
    """
    # Keep arguments identical while changing the authoritative work status.
    event = native_spawn_event(boundary)
    subject.admit_spawn(event)
    boundary.bindings.put.reset_mock()
    boundary.execute.reset_mock()
    if completed:
        boundary.issue["participants"][core.participant_key(boundary.parent)]["pending"].clear()
        with pytest.raises(core.WorkspaceError) as failure:
            subject.admit_spawn(event)
        assert failure.value.code == "REQUEST_CONFLICT"
    else:
        subject.admit_spawn(event)
    assert all(call.args[0]["operation"] == "ready" for call in boundary.execute.call_args_list)
    boundary.bindings.put.assert_not_called()


@pytest.mark.parametrize(
    "response", [{"error": "uncertain"}, "invalid JSON", {"task_name": "/root/legacy"}]
)
def test_native_unknown_response_cannot_create_target_or_release_capacity(boundary, response):
    """Native error and legacy-shaped responses preserve uncertain launch capacity.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
        response: Completion payload without a canonical native agent ID.
    """
    event = native_spawn_event(boundary)
    subject.admit_spawn(event)
    subject.parent_post({**event, "tool_response": response})
    launch = boundary.state["launches"]["call"]
    assert launch["outcome"] == "unknown"
    assert "target" not in launch
    assert launch["completed"] is True
    assert boundary.execute.call_args.args[0]["operation"] == "tool-complete"


@pytest.mark.parametrize(
    "arguments,code",
    [
        ({"targets": [AGENT], "timeout_ms": 1.5}, None),
        ({"targets": [AGENT]}, None),
        ({"targets": [AGENT, "foreign"]}, "HOST_UNSUPPORTED_CHILD_IDENTITY"),
        ({"targets": []}, "INVALID_REQUEST"),
        ({"targets": [3]}, "INVALID_REQUEST"),
        ({"targets": [AGENT], "timeout_ms": True}, "INVALID_REQUEST"),
        ({"targets": [AGENT], "extra": 1}, "INVALID_REQUEST"),
    ],
)
def test_native_wait_requires_all_targets_in_own_cohort(boundary, arguments, code):
    """Wait admission cannot mix a legitimate child with an unrelated target.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
        arguments: Native wait payload.
        code: Expected failure code, or None for authorized waiting.
    """
    # Observe a native target before admitting read-only waiting.
    event = native_spawn_event(boundary)
    subject.admit_spawn(event)
    subject.parent_post({**event, "tool_response": {"agent_id": AGENT}})
    boundary.execute.reset_mock()
    boundary.bindings.put.reset_mock()
    control = {**event, "tool_name": "multi_agent_v1wait_agent", "tool_input": arguments}
    if code is None:
        subject.admit_control(control)
        assert boundary.execute.call_args.args[0]["operation"] == "tool-start"
    else:
        with pytest.raises(core.WorkspaceError) as failure:
            subject.admit_control(control)
        assert failure.value.code == code
        assert all(call.args[0]["operation"] == "ready" for call in boundary.execute.call_args_list)
    if code is None:
        boundary.bindings.put.assert_called_once()
    else:
        boundary.bindings.put.assert_not_called()


@pytest.mark.parametrize(
    "tool_name,arguments",
    [
        ("multi_agent_v1send_input", {"target": AGENT}),
        ("multi_agent_v1send_input", {"target": AGENT, "message": "  "}),
        ("multi_agent_v1send_input", {"target": AGENT, "message": "read", "interrupt": 1}),
        ("multi_agent_v1send_input", {"target": AGENT, "message": "read", "items": []}),
        ("multi_agent_v1close_agent", {"target": AGENT, "extra": 1}),
    ],
)
def test_native_controls_reject_unimplemented_arguments(boundary, tool_name, arguments):
    """Control-schema validation rejects extra authority before reserving work.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
        tool_name: Native control operation.
        arguments: Unsupported control payload.
    """
    with pytest.raises(core.WorkspaceError) as failure:
        subject.admit_control(
            {**native_spawn_event(boundary), "tool_name": tool_name, "tool_input": arguments}
        )
    assert failure.value.code == "INVALID_REQUEST"
    assert all(call.args[0]["operation"] == "ready" for call in boundary.execute.call_args_list)
    boundary.bindings.put.assert_not_called()


@pytest.mark.parametrize("native", [True, False])
def test_completion_cannot_reinterpret_other_protocol_receipt(boundary, native):
    """Native and legacy callback schemas cannot steal one another's reservations.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
        native: Whether the original launch uses the new native protocol.
    """
    # Reserve under one protocol and deliver a result claimed by the other.
    event = native_spawn_event(boundary) if native else spawn_event(boundary)
    subject.admit_spawn(event)
    boundary.execute.reset_mock()
    boundary.bindings.put.reset_mock()
    response = {"task_name": "call"} if native else {"agent_id": AGENT}
    tool_name = subject.SPAWN if native else "spawn_agent"
    with pytest.raises(core.WorkspaceError) as failure:
        subject.parent_post({**event, "tool_name": tool_name, "tool_response": response})
    assert failure.value.code == "BINDING_CONFLICT"
    boundary.execute.assert_not_called()
    boundary.bindings.put.assert_not_called()


def test_native_completion_cannot_replace_observed_agent(boundary):
    """A replay cannot change the UUID associated with an existing native call.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
    """
    event = native_spawn_event(boundary)
    subject.admit_spawn(event)
    subject.parent_post({**event, "tool_response": {"agent_id": AGENT}})
    boundary.execute.reset_mock()
    boundary.bindings.put.reset_mock()
    with pytest.raises(core.WorkspaceError) as failure:
        subject.parent_post(
            {**event, "tool_response": {"agent_id": "87654321-4321-4321-8321-abcdefabcdef"}}
        )
    assert failure.value.code == "REQUEST_CONFLICT"
    assert boundary.state["launches"]["call"]["target"] == AGENT
    boundary.execute.assert_not_called()
    boundary.bindings.put.assert_not_called()


@pytest.mark.parametrize(
    "tool_name,arguments",
    [
        ("multi_agent_v1send_input", {"target": AGENT, "message": "continue"}),
        ("multi_agent_v1close_agent", {"target": AGENT}),
        ("multi_agent_v1wait_agent", {"targets": [AGENT]}),
    ],
)
def test_native_control_replay_cannot_resurrect_settled_work(boundary, tool_name, arguments):
    """A cached reservation response cannot reauthorize a settled control call.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
        tool_name: Native control operation.
        arguments: Valid own-cohort control arguments.
    """
    # Observe a valid target, then simulate already-settled authoritative work.
    event = native_spawn_event(boundary)
    subject.admit_spawn(event)
    subject.parent_post({**event, "tool_response": {"agent_id": AGENT}})
    boundary.issue["participants"][core.participant_key(boundary.parent)]["pending"].clear()
    boundary.bindings.put.reset_mock()
    with pytest.raises(core.WorkspaceError) as failure:
        subject.admit_control({**event, "tool_name": tool_name, "tool_input": arguments})
    assert failure.value.code == "REQUEST_CONFLICT"
    assert boundary.issue["participants"][core.participant_key(boundary.parent)]["pending"] == {}
    boundary.bindings.put.assert_not_called()


def observed_native_control(boundary):
    """Prepare two observed native targets and one pending control envelope.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.

    Returns:
        A send-input event whose target belongs to the parent's native cohort.
    """
    # Observe both targets independently so changed-target failures test correlation.
    first = native_spawn_event(boundary)
    second = {**first, "tool_use_id": "second"}
    subject.admit_spawn(first)
    subject.parent_post({**first, "tool_response": {"agent_id": AGENT}})
    subject.admit_spawn(second)
    subject.parent_post(
        {**second, "tool_response": {"agent_id": "87654321-4321-4321-8321-abcdefabcdef"}}
    )
    return {
        **first,
        "tool_name": "multi_agent_v1send_input",
        "tool_use_id": "control",
        "tool_input": {"target": AGENT, "message": "Read assigned source"},
    }


@pytest.mark.parametrize("changed", ["message", "target", "tool"])
@pytest.mark.parametrize("completion", [False, True])
def test_control_identity_cannot_change_while_original_call_pending(boundary, changed, completion):
    """Pending call IDs cannot be reused with another tool, message or valid target.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
        changed: Correlated property changed on the replayed envelope.
        completion: Whether to replay pre-admission or deliver a mismatched post.
    """
    # Admit the original exact invocation while its native call remains pending.
    original = observed_native_control(boundary)
    subject.admit_control(original)
    altered = {**original, "tool_input": dict(original["tool_input"])}
    if changed == "message":
        altered["tool_input"]["message"] = "Do something different"
    elif changed == "target":
        altered["tool_input"]["target"] = "87654321-4321-4321-8321-abcdefabcdef"
    else:
        altered["tool_name"] = "multi_agent_v1close_agent"
        altered["tool_input"] = {"target": AGENT}
    boundary.execute.reset_mock()
    boundary.bindings.put.reset_mock()
    # Neither admission nor settlement may reinterpret the original authorization.
    if completion:
        with pytest.raises(core.WorkspaceError) as failure:
            subject.parent_post({**altered, "tool_response": {"status": "completed"}})
    else:
        with pytest.raises(core.WorkspaceError) as failure:
            subject.admit_control(altered)
    assert failure.value.code == "REQUEST_CONFLICT"
    assert all(call.args[0]["operation"] == "ready" for call in boundary.execute.call_args_list)
    boundary.bindings.put.assert_not_called()
    assert (
        "control"
        in boundary.issue["participants"][core.participant_key(boundary.parent)]["pending"]
    )


def test_exact_pending_control_replay_and_correlated_completion_succeed(boundary):
    """The same authorized control can retry admission and settle its own receipt.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
    """
    # Exact invocation replay preserves the intended target and message.
    event = observed_native_control(boundary)
    subject.admit_control(event)
    subject.admit_control(event)
    subject.parent_post({**event, "tool_response": {"submission_id": "submitted"}})
    settled = boundary.execute.call_args.args[0]
    assert settled["operation"] == "tool-complete"
    assert settled["tool_id"] == "control"
    assert settled["session_id"] == "parent"
    assert boundary.state["children"][KEY] == {"status": "joined"}


@pytest.mark.parametrize("tool_name", ["collaborationwait_agent", "collaborationlist_agents"])
def test_legacy_observation_controls_track_exact_calls_without_target(boundary, tool_name):
    """Legacy observation tools retain pending correlation without a target field.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
        tool_name: Supported legacy observation tool.
    """
    event = {**spawn_event(boundary), "tool_name": tool_name, "tool_input": {}}
    subject.admit_control(event)
    assert boundary.execute.call_args.args[0]["operation"] == "tool-start"
    assert boundary.state["controls"]["call"]["tool_name"] == tool_name
    subject.parent_post({**event, "tool_response": {}})
    assert boundary.state["controls"] == {}


@pytest.mark.parametrize(
    "event,code",
    [
        ({"tool_name": "resume_agent", "tool_input": {}}, "HOST_UNSUPPORTED_TOOL"),
        ({"tool_name": "collaborationlist_agents", "tool_input": None}, "INVALID_REQUEST"),
    ],
)
def test_control_receipt_rejects_unsupported_or_malformed_calls(boundary, event, code):
    """A control signature cannot legitimize an unsupported tool or argument shape.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
        event: Invalid callback fields under examination.
        code: Expected public failure code.
    """
    with pytest.raises(core.WorkspaceError) as failure:
        subject.control_receipt(event)
    assert failure.value.code == code
    boundary.execute.assert_not_called()
    boundary.bindings.put.assert_not_called()


def test_control_receipt_retains_digest_without_private_message(boundary):
    """Persistent correlation must not retain the original message body.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
    """
    event = {
        "tool_name": "multi_agent_v1send_input",
        "tool_input": {"target": AGENT, "message": "private body"},
    }
    receipt = subject.control_receipt(event)
    assert set(receipt) == {"tool_name", "arguments_digest"}
    assert receipt["tool_name"] == "multi_agent_v1send_input"
    assert len(receipt["arguments_digest"]) == 64
    assert "private body" not in str(receipt)
    assert (
        subject.control_receipt(
            {**event, "tool_input": {"message": "private body", "target": AGENT}}
        )
        == receipt
    )
    assert (
        subject.control_receipt({**event, "tool_input": {"message": "changed", "target": AGENT}})
        != receipt
    )
    boundary.execute.assert_not_called()


def test_unadmitted_control_post_cannot_settle_existing_pending_call(boundary):
    """A completion without adapter admission cannot settle unrelated core work.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
    """
    event = {
        **spawn_event(boundary),
        "tool_name": "collaborationlist_agents",
        "tool_input": {},
        "tool_response": {},
    }
    with pytest.raises(core.WorkspaceError) as failure:
        subject.parent_post(event)
    assert failure.value.code == "UNKNOWN_OPERATION"
    boundary.execute.assert_not_called()
    boundary.bindings.put.assert_not_called()


def test_compacted_control_post_replays_only_exact_core_completion(boundary):
    """Compaction retains idempotency through a signature-bound core request ID.

    Args:
        boundary: Isolated lifecycle responses, cohort state and boundary spies.
    """
    # Complete a real admitted envelope before removing its pending core observation.
    event = observed_native_control(boundary)
    subject.admit_control(event)
    post = {**event, "tool_response": {"submission_id": "one"}}
    subject.parent_post(post)
    original = boundary.execute.call_args.args[0]
    boundary.issue["participants"][core.participant_key(boundary.parent)]["pending"].clear()
    subject.parent_post(post)
    assert boundary.execute.call_args.args[0] == original
    # A changed signature must use another ID and preserve the core's rejection.
    boundary.execute.return_value = {"ok": False, "code": "UNKNOWN_OPERATION"}
    boundary.bindings.put.reset_mock()
    with pytest.raises(core.WorkspaceError) as failure:
        subject.parent_post({**post, "tool_input": {**post["tool_input"], "message": "changed"}})
    assert failure.value.code == "UNKNOWN_OPERATION"
    assert boundary.execute.call_args.args[0]["request_id"] != original["request_id"]
    boundary.bindings.put.assert_not_called()
