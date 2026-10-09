"""Check Codex event dispatch and exact admission/completion decisions."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_company.adapters import codex
from agent_company.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit


def test_codex_wrappers_fix_the_host_and_defer_to_shared_contracts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep Codex identity fixed in common request, prompt, and attach calls.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
        tmp_path: Disposable directory for repository or file fixtures.
    """
    seen: list[tuple[object, ...]] = []
    event = {"cwd": str(tmp_path / "checkout"), "session_id": "s"}
    monkeypatch.setattr(
        codex.common,
        "request_for",
        lambda native, operation, host: seen.append(("request", native, operation, host)) or {},
    )
    monkeypatch.setattr(
        codex.common,
        "automatic_attach",
        lambda native, identifier, host: seen.append(("attach", native, identifier, host)),
    )
    monkeypatch.setattr(
        codex.common,
        "prompt",
        lambda native, host, *, attempt_attach: (
            seen.append(("prompt", native, host, attempt_attach)) or {}
        ),
    )
    assert codex.request_for(event, "ready") == {}
    assert codex.automatic_attach(event, "AGENT-30") is None
    assert codex.prompt(event) == {}
    assert seen == [
        ("request", event, "ready", "codex"),
        ("attach", event, "AGENT-30", "codex"),
        ("prompt", event, "codex", False),
    ]


def test_codex_bootstrap_rejects_alternate_tool_shapes_before_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject malformed shell metadata without accepting a lifecycle command.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex.common, "canonical_bootstrap", lambda *_args: pytest.fail("parsed"))
    event = {
        "tool_name": "exec_command",
        "tool_input": {"cmd": "command", "login": False, "shell": "/bin/sh"},
    }
    assert codex.bootstrap({**event, "tool_name": "Read"}) is False
    assert codex.bootstrap({**event, "tool_input": {**event["tool_input"], "tty": True}}) is False
    assert (
        codex.bootstrap({**event, "tool_input": {**event["tool_input"], "command": "other"}})
        is False
    )


@pytest.mark.parametrize(
    ("platform", "shell", "foreign_shell"),
    [
        pytest.param("nt", "powershell.exe", "/bin/sh", id="windows-powershell"),
        pytest.param("posix", "/bin/sh", "powershell.exe", id="posix-sh"),
    ],
)
def test_codex_bootstrap_passes_exact_eligible_shell_call(
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    shell: str,
    foreign_shell: str,
) -> None:
    """Forward only the shell supported by the selected native adapter platform.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
        platform: Platform case selected by parametrization.
        shell: Shell executable selected for this case.
        foreign_shell: Shell executable that must be rejected.
    """
    captured: list[tuple[object, ...]] = []

    def canonical(*args: object) -> bool:
        """Capture the shared parser inputs.

        Args:
            args: Arguments supplied to the helper under test.

        Returns:
            The canonical serialized fixture value.
        """
        captured.append(args)
        return True

    monkeypatch.setattr(codex.common, "canonical_bootstrap", canonical)
    monkeypatch.setattr(codex, "os", SimpleNamespace(name=platform))
    event = {
        "tool_name": "exec_command",
        "tool_input": {"cmd": "command", "login": False, "shell": shell},
    }
    assert codex.bootstrap(event, ready=True) is True
    assert captured == [(event, "command", "codex", True, codex.PYTHON, codex.LIFECYCLE)]
    assert (
        codex.bootstrap({**event, "tool_input": {**event["tool_input"], "shell": foreign_shell}})
        is False
    )
    assert len(captured) == 1


def test_codex_pretool_denies_child_creation_without_binding_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Block child transport before creating pending lifecycle work.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "startup_lookup_state", lambda *_args: False)
    monkeypatch.setattr(codex, "bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(codex, "provider_gate", lambda *_args: False)
    monkeypatch.setattr(codex, "request_for", lambda *_args: pytest.fail("binding read"))
    response = codex.handle({"hook_event_name": "PreToolUse", "tool_name": "spawn_agent"})
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert (
        "HOST_UNSUPPORTED_CHILD_IDENTITY"
        in response["hookSpecificOutput"]["permissionDecisionReason"]
    )


def test_codex_posttool_keeps_unknown_completion_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not settle work from an untyped, handle-free response.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "request_for", lambda *_args: {"operation": "tool-complete"})
    monkeypatch.setattr(codex.core, "execute", lambda *_args: pytest.fail("settled work"))
    assert (
        codex.handle(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "exec_command",
                "tool_use_id": "tool-1",
                "tool_response": {"exit_code": "0"},
            }
        )
        == {}
    )


def test_codex_posttool_settles_typed_failure_without_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Treat an explicit handle-free shell error as finished failed work.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(codex, "request_for", lambda *_args: {"operation": "tool-complete"})
    monkeypatch.setattr(
        codex.core, "execute", lambda request: calls.append(request) or {"ok": True}
    )
    assert (
        codex.handle(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "exec_command",
                "tool_use_id": "tool-1",
                "tool_response": {"isError": True},
            }
        )
        == {}
    )
    assert calls[0]["tool_id"] == "tool-1"
    assert calls[0]["completed"] is True
    assert calls[0]["async_handle"] is None


def test_codex_failure_response_matches_event_contract() -> None:
    """Render denial, prompt block, or advisory result for the exact event."""
    assert codex.failure("PreToolUse", "BINDING_MISSING") == codex.denial("BINDING_MISSING")
    assert codex.failure("UserPromptSubmit", "BINDING_MISSING") == {
        "decision": "block",
        "reason": "TASK_WORKSPACE_NOT_READY: BINDING_MISSING",
    }
    assert codex.failure("SessionStart", "BINDING_MISSING") == {
        "systemMessage": "TASK_WORKSPACE_NOT_READY: BINDING_MISSING"
    }


def test_codex_main_uses_exit_zero_supervision(monkeypatch: pytest.MonkeyPatch) -> None:
    """Preserve the host's zero-exit wire convention for bounded failures.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    observed: list[tuple[object, object, int]] = []

    def run(handler: object, failure: object, *, error_status: int) -> int:
        """Record the runner contract without reading standard input.

        Args:
            handler: Hook handler passed to the supervised runner.
            failure: Failure callback or result selected by this case.
            error_status: Expected process status for the failure mode.

        Returns:
            The simulated subprocess or hook result.
        """
        observed.append((handler, failure, error_status))
        return 0

    monkeypatch.setattr(codex.runner, "run", run)
    assert codex.main() == 0
    assert observed == [(codex.handle, codex.failure, 0)]


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        (set(), False),
        ({".task"}, False),
        ({".task", ".bindings", "lookup-required"}, True),
    ],
)
def test_startup_lookup_reads_only_explicit_session_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, entries: set[str], expected: bool
) -> None:
    """Check the selected worktree's assignment marker through direct handles.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
        tmp_path: Disposable directory for repository or file fixtures.
        entries: Existing binding or task entries for this case.
        expected: Expected outcome for this case.
    """
    root = tmp_path / "checkout"
    key = core.participant_key({"host": "codex", "session_id": "s"})

    class Node:
        """Expose one direct registered task or binding level."""

        def __init__(self, level: int = 0) -> None:
            """Select root, task, or bindings lookup depth.

            Args:
                level: Boundary severity level selected for this case.
            """
            self.level = level

        def __enter__(self) -> Node:
            """Hold the modeled directory.

            Returns:
                The active disposable context manager fixture.
            """
            return self

        def __exit__(self, *_args: object) -> None:
            """Release the modeled directory.

            Args:
                _args: Ignored positional arguments accepted by this test callback.
            """

        def exists(self, name: str) -> bool:
            """Report only the configured direct marker for this level.

            Args:
                name: Event or case name selected for this test.

            Returns:
                Whether the requested fixture entry exists.
            """
            # Resolve the Task directory before descending into marker state.
            if self.level == 0:
                assert name == ".task"
                return ".task" in entries
            # Resolve the private binding directory at the next level.
            if self.level == 1:
                assert name == ".bindings"
                return ".bindings" in entries
            assert name == key + ".lookup-required.json"
            return "lookup-required" in entries

        def child(self, name: str) -> Node:
            """Move only to the expected direct child level.

            Args:
                name: Event or case name selected for this test.

            Returns:
                The requested child directory fixture.
            """
            assert name == (".task" if self.level == 0 else ".bindings")
            return Node(self.level + 1)

    monkeypatch.setattr(codex.core, "repository", lambda _cwd: (root, None, [root]))
    monkeypatch.setattr(codex.core.Directory, "absolute", lambda _path: Node())
    assert (
        codex.startup_lookup_state({"cwd": str(root), "session_id": "s"}, ".lookup-required.json")
        is expected
    )


def test_codex_pretool_preserves_failed_core_readiness_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deny ordinary tool admission when the lifecycle reports missing source scope.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "startup_lookup_state", lambda *_args: False)
    monkeypatch.setattr(codex, "bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(codex, "provider_gate", lambda *_args: False)
    monkeypatch.setattr(codex, "request_for", lambda *_args: {"operation": "ready"})
    calls: list[str] = []

    def execute(request: dict[str, Any]) -> dict[str, Any]:
        """Record only readiness before denying the tool.

        Args:
            request: Pytest fixture selecting the parameterized case.

        Returns:
            The simulated lifecycle result for this request.
        """
        calls.append(request["operation"])
        return {"ok": False, "code": "SOURCE_STALE"}

    monkeypatch.setattr(codex.core, "execute", execute)
    response = codex.handle({"hook_event_name": "PreToolUse", "tool_name": "Read"})
    assert response == codex.denial("SOURCE_STALE")
    assert calls == ["ready"]


def test_codex_pretool_rejects_poll_without_observed_process_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never reserve a poll when its session handle is absent.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "startup_lookup_state", lambda *_args: False)
    monkeypatch.setattr(codex, "bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(codex, "provider_gate", lambda *_args: False)
    monkeypatch.setattr(codex, "request_for", lambda *_args: {"operation": "ready"})
    calls: list[str] = []

    def execute(request: dict[str, Any]) -> dict[str, Any]:
        """Allow readiness but record forbidden later dispatch.

        Args:
            request: Pytest fixture selecting the parameterized case.

        Returns:
            The simulated lifecycle result for this request.
        """
        calls.append(request["operation"])
        return {"ok": True, "code": "OK"}

    monkeypatch.setattr(codex.core, "execute", execute)
    response = codex.handle(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "write_stdin",
            "tool_input": {},
            "tool_use_id": "poll-1",
        }
    )
    assert response == codex.denial("ASYNC_HANDLE_CONFLICT")
    assert calls == ["ready"]


def test_codex_posttool_rejects_mismatched_poll_response_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the pending process when the observed poll response names another handle.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "request_for", lambda *_args: {"operation": "tool-complete"})
    monkeypatch.setattr(codex.core, "execute", lambda *_args: pytest.fail("settled poll"))
    response = codex.handle(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "write_stdin",
            "tool_input": {"session_id": "process-1"},
            "tool_response": {"session_id": "process-2", "exit_code": 0},
            "tool_use_id": "poll-1",
        }
    )
    assert response == {"systemMessage": "TASK_WORKSPACE_NOT_READY: ASYNC_HANDLE_CONFLICT"}


def test_codex_pretool_requires_recorded_ticket_lookup_before_other_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Admit only the exact issue read while a recorded Task lookup is outstanding.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "startup_lookup_state", lambda *_args: True)
    monkeypatch.setattr(codex, "ticket_lookup", lambda *_args: {})
    assert codex.handle({"hook_event_name": "PreToolUse", "tool_name": "Read"}) == codex.denial(
        "TICKET_READ_REQUIRED"
    )
    assert (
        codex.handle(
            {"hook_event_name": "PreToolUse", "tool_name": "mcp__codex_apps__linear_get_issue"}
        )
        == {}
    )


def test_codex_pretool_preserves_ticket_lookup_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep a failed direct issue read bounded and do not admit later work.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "startup_lookup_state", lambda *_args: True)
    monkeypatch.setattr(
        codex,
        "ticket_lookup",
        lambda *_args: (_ for _ in ()).throw(core.WorkspaceError("BINDING_CONFLICT")),
    )
    assert codex.handle(
        {"hook_event_name": "PreToolUse", "tool_name": "mcp__codex_apps__linear_get_issue"}
    ) == codex.denial("BINDING_CONFLICT")


def test_codex_pretool_admits_exact_provider_gate_without_tool_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Allow a separately verified archive provider call without pending-tool mutation.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "startup_lookup_state", lambda *_args: False)
    monkeypatch.setattr(codex, "bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(codex, "provider_gate", lambda *_args: True)
    monkeypatch.setattr(codex, "request_for", lambda *_args: pytest.fail("opened tool binding"))
    assert (
        codex.handle(
            {"hook_event_name": "PreToolUse", "tool_name": "mcp__codex_apps__linear_get_document"}
        )
        == {}
    )


def test_codex_pretool_records_ready_tool_or_exact_core_denial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Record observed tool identity only after readiness and return core admission failure.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "startup_lookup_state", lambda *_args: False)
    monkeypatch.setattr(codex, "bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(codex, "provider_gate", lambda *_args: False)
    monkeypatch.setattr(codex, "request_for", lambda *_args: {"operation": "ready"})
    requests: list[dict[str, Any]] = []

    def execute(request: dict[str, Any]) -> dict[str, Any]:
        """Permit readiness but reject the explicit tool-start transaction.

        Args:
            request: Pytest fixture selecting the parameterized case.

        Returns:
            The simulated lifecycle result for this request.
        """
        requests.append(request)
        return (
            {"ok": True, "code": "READY"}
            if request["operation"] == "ready"
            else {
                "ok": False,
                "code": "REQUEST_CONFLICT",
            }
        )

    monkeypatch.setattr(codex.core, "execute", execute)
    response = codex.handle(
        {"hook_event_name": "PreToolUse", "tool_name": "Read", "tool_use_id": "read-1"}
    )
    assert response == codex.denial("REQUEST_CONFLICT")
    assert [item["operation"] for item in requests] == ["ready", "tool-start"]
    assert requests[-1]["tool_id"] == "read-1"


def test_codex_posttool_ticket_lookup_reports_exact_missing_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve confirmed provider absence with the requested issue identifier.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "startup_lookup_state", lambda *_args: True)
    monkeypatch.setattr(
        codex,
        "ticket_lookup",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(core.WorkspaceError("ISSUE_NOT_FOUND")),
    )
    response = codex.handle(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "mcp__codex_apps__linear_get_issue",
            "tool_input": {"id": "AGENT-30"},
        }
    )
    assert response == {"systemMessage": "TASK_WORKSPACE_NOT_READY: ISSUE_NOT_FOUND for AGENT-30"}


def test_codex_posttool_forwards_verified_ticket_startup_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The successful direct issue read returns its verified startup result unchanged.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "startup_lookup_state", lambda *_args: True)
    expected = {"systemMessage": "TASK_WORKSPACE_READY: assigned packet acknowledged"}
    calls: list[bool] = []
    monkeypatch.setattr(
        codex,
        "ticket_lookup",
        lambda _event, complete=False: calls.append(complete) or expected,
    )
    event = {"hook_event_name": "PostToolUse", "tool_name": "mcp__codex_apps__linear_get_issue"}
    assert codex.handle(event) is expected
    assert calls == [True]


def test_codex_session_and_endings_report_readiness_without_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expose bounded startup context and keep Stop as an advisory observation.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(
        codex,
        "request_for",
        lambda _event, operation: {"operation": operation},
    )
    requests: list[dict[str, Any]] = []
    monkeypatch.setattr(
        codex.core,
        "execute",
        lambda request: requests.append(request) or {"ok": True, "code": "READY"},
    )
    start = codex.handle({"hook_event_name": "SessionStart"})
    assert start["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "Task binding checked" in start["hookSpecificOutput"]["additionalContext"]
    assert codex.handle({"hook_event_name": "PreCompact"}) == {}
    assert codex.handle({"hook_event_name": "Stop"}) == {}
    assert [item["operation"] for item in requests] == ["ready", "ready", "event"]
    assert requests[-1]["event"] == {"code": "UNKNOWN"}


def test_codex_posttool_records_observed_async_handle_without_inferred_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep asynchronous shell work pending under its observed process handle.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "request_for", lambda *_args: {"operation": "tool-complete"})
    requests: list[dict[str, Any]] = []
    monkeypatch.setattr(
        codex.core,
        "execute",
        lambda request: requests.append(request) or {"ok": True, "code": "OK"},
    )
    response = codex.handle(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "exec_command",
            "tool_use_id": "shell-1",
            "tool_response": '{"session_id":77}',
        }
    )
    assert response == {}
    assert requests[0]["async_handle"] == "77"
    assert requests[0]["completed"] is False
    assert requests[0]["poll"] is False


def test_codex_posttool_preserves_core_correlation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Surface a failed completion transaction instead of hiding it as success.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "request_for", lambda *_args: {"operation": "tool-complete"})
    monkeypatch.setattr(
        codex.core, "execute", lambda _request: {"ok": False, "code": "UNKNOWN_OPERATION"}
    )
    response = codex.handle(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Read",
            "tool_use_id": "read-1",
            "tool_response": {"isError": False},
        }
    )
    assert response == {"systemMessage": "TASK_WORKSPACE_NOT_READY: UNKNOWN_OPERATION"}


def test_codex_pretool_keeps_clarification_available_without_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unready session can ask the user for an identity clarification.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "startup_lookup_state", lambda *_args: False)
    monkeypatch.setattr(codex, "bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(codex, "provider_gate", lambda *_args: False)
    monkeypatch.setattr(codex, "request_for", lambda *_args: pytest.fail("read binding"))
    # Neither interactive request tool can bypass a required ticket read.
    for tool in ("request_user_input", "request_user_input_async"):
        assert codex.handle({"hook_event_name": "PreToolUse", "tool_name": tool}) == {}


def test_codex_pretool_admits_exact_unready_bootstrap_before_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The canonical recovery command is available before normal readiness.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "startup_lookup_state", lambda *_args: False)
    monkeypatch.setattr(codex, "bootstrap", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(codex, "request_for", lambda *_args: pytest.fail("read binding"))
    assert codex.handle({"hook_event_name": "PreToolUse", "tool_name": "exec_command"}) == {}


def test_codex_pretool_rechecks_ready_bootstrap_without_tool_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ready lifecycle command remains outside external pending-tool tracking.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "startup_lookup_state", lambda *_args: False)
    monkeypatch.setattr(codex, "bootstrap", lambda _event, ready=False: ready)
    monkeypatch.setattr(codex, "provider_gate", lambda *_args: False)
    monkeypatch.setattr(codex, "request_for", lambda *_args: {"operation": "ready"})
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        codex.core,
        "execute",
        lambda request: calls.append(request) or {"ok": True, "code": "READY"},
    )
    assert codex.handle({"hook_event_name": "PreToolUse", "tool_name": "exec_command"}) == {}
    assert [call["operation"] for call in calls] == ["ready"]


def test_codex_pretool_records_correlated_poll_handle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reserve a poll against the exact observed process handle.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "startup_lookup_state", lambda *_args: False)
    monkeypatch.setattr(codex, "bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(codex, "provider_gate", lambda *_args: False)
    monkeypatch.setattr(codex, "request_for", lambda *_args: {"operation": "ready"})
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        codex.core,
        "execute",
        lambda request: calls.append(request) or {"ok": True, "code": "READY"},
    )
    event = {
        "hook_event_name": "PreToolUse",
        "tool_name": "write_stdin",
        "tool_use_id": "poll-1",
        "tool_input": {"session_id": 77},
    }
    assert codex.handle(event) == {}
    assert calls[-1]["operation"] == "tool-start"
    assert calls[-1]["poll_handle"] == "77"


def test_codex_posttool_uses_poll_input_when_response_omits_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed poll retains the input handle for original-operation correlation.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "request_for", lambda *_args: {"operation": "tool-complete"})
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        codex.core,
        "execute",
        lambda request: calls.append(request) or {"ok": True, "code": "OK"},
    )
    event = {
        "hook_event_name": "PostToolUse",
        "tool_name": "write_stdin",
        "tool_use_id": "poll-1",
        "tool_input": {"session_id": 77},
        "tool_response": {"exit_code": 0},
    }
    assert codex.handle(event) == {}
    assert calls[-1]["poll"] is True
    assert calls[-1]["completed"] is True
    assert calls[-1]["async_handle"] == "77"


def test_codex_posttool_does_not_infer_completion_from_bad_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed serialized provider output has no completion evidence.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "request_for", lambda *_args: {"operation": "tool-complete"})
    monkeypatch.setattr(codex.core, "execute", lambda *_args: pytest.fail("settled work"))
    event = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Read",
        "tool_use_id": "read-1",
        "tool_response": "{",
    }
    assert codex.handle(event) == {}


def test_codex_session_reports_denial_and_ending_failure_stays_advisory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed session readiness stays visible; ending observation grants nothing.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "request_for", lambda _event, op: {"operation": op})
    monkeypatch.setattr(
        codex.core, "execute", lambda _request: {"ok": False, "code": "SOURCE_STALE"}
    )
    result = codex.handle({"hook_event_name": "SessionStart"})
    assert (
        "TASK_WORKSPACE_NOT_READY: SOURCE_STALE"
        in result["hookSpecificOutput"]["additionalContext"]
    )
    assert codex.handle({"hook_event_name": "Interrupt"}) == {}


def test_codex_prompt_permission_and_child_hooks_keep_distinct_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only prompt dispatch records task intent; host permission and child hooks grant none.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(
        codex, "prompt", lambda _event: {"decision": "block", "reason": "BINDING_CONFLICT"}
    )
    assert codex.handle({"hook_event_name": "UserPromptSubmit"}) == {
        "decision": "block",
        "reason": "BINDING_CONFLICT",
    }
    assert codex.handle({"hook_event_name": "PermissionRequest"}) == {}
    assert "HOST_UNSUPPORTED" in codex.handle({"hook_event_name": "SubagentStart"})["systemMessage"]


def test_codex_provider_gate_failure_falls_through_to_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed archive gate never bypasses normal readiness enforcement.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "startup_lookup_state", lambda *_args: False)
    monkeypatch.setattr(codex, "bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        codex,
        "provider_gate",
        lambda *_args: (_ for _ in ()).throw(core.WorkspaceError("ARCHIVE_BLOCKED")),
    )
    monkeypatch.setattr(codex, "request_for", lambda *_args: {"operation": "ready"})
    monkeypatch.setattr(
        codex.core, "execute", lambda _request: {"ok": False, "code": "SOURCE_STALE"}
    )
    assert codex.handle(
        {"hook_event_name": "PreToolUse", "tool_name": "mcp__codex_apps__linear_get_document"}
    ) == codex.denial("SOURCE_STALE")


def test_codex_posttool_rejects_scalar_response_without_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A serialized scalar cannot prove a tool completed.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "request_for", lambda *_args: {"operation": "tool-complete"})
    monkeypatch.setattr(codex.core, "execute", lambda *_args: pytest.fail("settled work"))
    assert (
        codex.handle(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "Read",
                "tool_use_id": "read-1",
                "tool_response": "42",
            }
        )
        == {}
    )


def test_codex_posttool_linear_missing_reference_names_requested_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The observed Linear missing-reference envelope yields its distinct diagnostic.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "startup_lookup_state", lambda *_args: True)
    monkeypatch.setattr(
        codex,
        "ticket_lookup",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            core.WorkspaceError("LINEAR_ISSUE_UNRESOLVED")
        ),
    )
    response = codex.handle(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "mcp__codex_apps__linear_get_issue",
            "tool_input": {"id": "AGENT-30"},
        }
    )
    assert response == {
        "systemMessage": (
            "TASK_WORKSPACE_NOT_READY: LINEAR_ISSUE_UNRESOLVED "
            "Linear could not find requested issue AGENT-30."
        )
    }


def test_codex_session_binding_error_and_optional_ending_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Session errors remain visible while optional ending failures stay advisory.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(
        codex,
        "request_for",
        lambda *_args: (_ for _ in ()).throw(core.WorkspaceError("BINDING_MISSING")),
    )
    result = codex.handle({"hook_event_name": "SessionStart"})
    assert (
        "TASK_WORKSPACE_NOT_READY: BINDING_MISSING"
        in result["hookSpecificOutput"]["additionalContext"]
    )
    assert codex.handle({"hook_event_name": "SessionEnd"}) == {}


def test_codex_unknown_hook_has_no_lifecycle_side_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unrecognized host event grants no readiness or completion.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex.core, "execute", lambda *_args: pytest.fail("executed lifecycle"))
    assert codex.handle({"hook_event_name": "FutureHostEvent"}) == {}
