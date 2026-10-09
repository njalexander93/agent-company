"""Validate native host envelopes and exact bootstrap commands."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_company.adapters import common
from agent_company.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("value", [None, 1, [], {}])
def test_native_token_rejects_nonstring_identity(value: object) -> None:
    """Native token rejects nonstring identity.

    Args:
        value: Non-string native call identity to reject.
    """
    # A native call ID must be a nonempty string before any correlation.
    with pytest.raises(core.WorkspaceError) as captured:
        common.native_token(value)
    assert captured.value.code == "INVALID_REQUEST"


def test_native_identity_preserves_claude_session_and_cwd(tmp_path: Path) -> None:
    """Native identity preserves claude session and cwd.

    Args:
        tmp_path: Absolute checkout path used in the native event.
    """
    event = {"session_id": "session", "cwd": str(tmp_path), "tool_use_id": "one"}
    normalized = common.native_identity(event, "claude-code")
    assert normalized == event
    assert normalized is not event


@pytest.mark.parametrize(
    ("extra", "code"),
    [
        ({"agent_id": "child"}, "HOST_UNSUPPORTED_CHILD_IDENTITY"),
        ({"subagent_id": "child"}, "HOST_UNSUPPORTED_CHILD_IDENTITY"),
        ({"is_background_agent": True}, "HOST_UNSUPPORTED_BACKGROUND"),
        ({"session_id": 2}, "INVALID_REQUEST"),
        ({"cwd": "relative"}, "REPOSITORY_MISMATCH"),
    ],
)
def test_native_identity_rejects_unbound_claude_context(
    tmp_path: Path, extra: dict[str, object], code: str
) -> None:
    """Native identity rejects unbound claude context.

    Args:
        tmp_path: Absolute checkout path used in the native event.
        extra: Additional request fields that must be rejected.
        code: Expected boundary diagnostic.
    """
    event = {"session_id": "session", "cwd": str(tmp_path), **extra}
    # Foreign child/background identity and malformed paths cannot bind this session.
    with pytest.raises(core.WorkspaceError) as captured:
        common.native_identity(event, "claude-code")
    assert captured.value.code == code


def test_native_identity_requires_single_cursor_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Native identity requires single cursor root.

    Args:
        tmp_path: Absolute checkout path used in the native event.
        monkeypatch: Replaces repository or lifecycle boundaries for the case.
    """
    monkeypatch.setattr(common.core, "repository", lambda path: (Path(path), None, []))
    event = {"conversation_id": "conversation", "workspace_roots": [str(tmp_path)]}
    normalized = common.native_identity(event, "cursor")
    assert normalized["session_id"] == "conversation"
    assert normalized["cwd"] == str(tmp_path)
    # Empty, multiple, and non-path roots violate Cursor's single-root contract.
    for roots in ([], [str(tmp_path), str(tmp_path)], [7]):
        # Every invalid root list must fail before a session is derived.
        with pytest.raises(core.WorkspaceError) as captured:
            common.native_identity({**event, "workspace_roots": roots}, "cursor")
        assert captured.value.code == "HOST_UNSUPPORTED_WORKSPACE"


def native_event(tool: str, args: dict[str, object] | None = None) -> dict[str, object]:
    """Build one native tool event for the shared adapter contract.

    Args:
        tool: Native tool name presented to the adapter.
        args: Arguments supplied to the helper under test.

    Returns:
        The host tool event with a stable call ID.
    """
    return {"tool_use_id": "tool-1", "tool_name": tool, "tool_input": {} if args is None else args}


@pytest.mark.parametrize(
    "host,tool", [("claude-code", "Read"), ("cursor", "Read"), ("cursor", "Delete")]
)
def test_native_tool_accepts_synchronous_file_tool(host: str, tool: str) -> None:
    """Native tool accepts synchronous file tool.

    Args:
        host: Claude Code or Cursor admission grammar.
        tool: Native tool name presented to the adapter.
    """
    assert common.native_tool(native_event(tool), host) == tool


@pytest.mark.parametrize(
    ("event", "code"),
    [
        (native_event("MCP:external"), "HOST_UNSUPPORTED_PROVIDER"),
        (native_event("Task"), "HOST_UNSUPPORTED_CHILD_IDENTITY"),
        (native_event("Unknown"), "HOST_UNSUPPORTED_TOOL"),
        (native_event("Read", {"async": True}), "HOST_UNSUPPORTED_ASYNC"),
        ({"tool_name": "Read", "tool_input": {}}, "INVALID_REQUEST"),
    ],
)
def test_native_tool_rejects_uncorrelated_or_async_work(
    event: dict[str, object], code: str
) -> None:
    """Native tool rejects uncorrelated or async work.

    Args:
        event: Hook event supplied to the adapter under test.
        code: Expected boundary diagnostic.
    """
    # Unsupported provider, child, unknown, async, and uncorrelated calls all fail admission.
    with pytest.raises(core.WorkspaceError) as captured:
        common.native_tool(event, "cursor")
    assert captured.value.code == code


def test_bootstrap_command_roundtrip_requires_exact_literal_spelling(tmp_path: Path) -> None:
    """Bootstrap command roundtrip requires exact literal spelling.

    Args:
        tmp_path: Absolute checkout path used in the native event.
    """
    request = {
        "schema_version": 1,
        "operation": "diagnose",
        "worktree": str(tmp_path),
        "host": "codex",
        "session_id": "s",
    }
    command = common.bootstrap_command(request, "codex")
    assert common.bootstrap_request(command, "codex", common.PYTHON, common.LIFECYCLE) == request
    # Select the host shell's exact literal command grammar.
    if os.name == "nt":
        alterations = (
            (" " + command, "Noncanonical PowerShell command"),
            (command + "; true", "Noncanonical PowerShell command"),
            (command.replace("--request-base64", "--other"), "Unexpected Windows bootstrap entry"),
        )
    else:
        # POSIX transport uses a JSON argument and shell quoting.
        alterations = (
            (" " + command, "Noncanonical shell command"),
            (command + "; true", "Unexpected bootstrap entry"),
            (command.replace("--request-json", "--other"), "Unexpected bootstrap entry"),
        )
    # Each injected spelling must fail roundtrip parsing.
    for altered, diagnostic in alterations:
        # Keep the expected diagnostic tied to the altered token.
        with pytest.raises(ValueError, match=diagnostic):
            common.bootstrap_request(altered, "codex", common.PYTHON, common.LIFECYCLE)


def test_bootstrap_parser_rejects_control_bytes_and_nonobject_request() -> None:
    """An observed shell command cannot carry control bytes or a scalar request."""
    # Newline and NUL cannot enter a canonical lifecycle command.
    for command in ("echo ok\n", "echo ok\0"):
        # Reject each control byte before parsing shell tokens.
        with pytest.raises(ValueError, match="Control character"):
            common.bootstrap_request(command, "codex", common.PYTHON, common.LIFECYCLE)
    scalar = common.bootstrap_command("text", "codex")
    # A scalar encoded request fails under the native host parser.
    if os.name == "nt":
        # Windows decoder reports an invalid lifecycle request.
        with pytest.raises(core.WorkspaceError) as captured:
            common.bootstrap_request(scalar, "codex", common.PYTHON, common.LIFECYCLE)
        assert captured.value.code == "INVALID_REQUEST"
    else:
        # POSIX parser rejects the scalar command object directly.
        with pytest.raises(ValueError, match="Invalid request object"):
            common.bootstrap_request(scalar, "codex", common.PYTHON, common.LIFECYCLE)


def test_windows_claude_bootstrap_uses_exact_posix_parser_without_global_os_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claude's Bash transport stays literal even when the host is Windows.

    Args:
        monkeypatch: Replaces repository or lifecycle boundaries for the case.
    """
    monkeypatch.setattr(common, "os", SimpleNamespace(name="nt"))
    request = {"operation": "diagnose", "session_id": "s"}
    command = common.bootstrap_command(request, "claude-code")
    assert (
        common.bootstrap_request(command, "claude-code", common.PYTHON, common.LIFECYCLE) == request
    )
    # Claude keeps Bash transport even while the process models Windows.
    with pytest.raises(ValueError, match="Unexpected bootstrap entry"):
        common.bootstrap_request(
            command.replace("--request-json", "--other"),
            "claude-code",
            common.PYTHON,
            common.LIFECYCLE,
        )
    # Leading whitespace changes the canonical shell spelling.
    with pytest.raises(ValueError, match="Noncanonical shell command"):
        common.bootstrap_request(" " + command, "claude-code", common.PYTHON, common.LIFECYCLE)
    scalar = common.bootstrap_command("text", "claude-code")
    # Scalar payloads cannot be decoded as lifecycle requests.
    with pytest.raises(ValueError, match="Invalid request object"):
        common.bootstrap_request(scalar, "claude-code", common.PYTHON, common.LIFECYCLE)


def test_windows_bootstrap_rejects_unsafe_native_python_argument(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject a PowerShell command if its interpreter path cannot be quoted safely.

    Args:
        monkeypatch: Replaces repository or lifecycle boundaries for the case.
    """
    monkeypatch.setattr(common, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(common, "PYTHON", 'C:\\unsafe"path\\python.exe')
    # PowerShell argument generation must reject the unquotable interpreter path.
    with pytest.raises(ValueError, match="Unsupported PowerShell argument"):
        common.bootstrap_command({"operation": "diagnose"}, "codex")


def test_bootstrap_command_rejects_oversized_or_controlled_arguments() -> None:
    """Generated commands stay within the host hook's literal command bound."""
    # Command generation enforces its byte bound before reaching the host.
    with pytest.raises(ValueError, match="size or control"):
        common.bootstrap_command({"operation": "diagnose", "padding": "x" * 66000}, "codex")
    escaped = common.bootstrap_command({"operation": "diagnose", "padding": "bad\nvalue"}, "codex")
    assert "\n" not in escaped
    assert common.bootstrap_request(escaped, "codex", common.PYTHON, common.LIFECYCLE) == {
        "operation": "diagnose",
        "padding": "bad\nvalue",
    }


def test_native_tool_claude_bash_requires_background_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claude shell admission requires the documented foreground-only host setting.

    Args:
        monkeypatch: Replaces repository or lifecycle boundaries for the case.
    """
    shell = native_event("Bash", {"command": "echo ok"})
    monkeypatch.delenv("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", raising=False)
    # Without the foreground setting, Claude Bash cannot prove synchronous completion.
    with pytest.raises(core.WorkspaceError) as captured:
        common.native_tool(shell, "claude-code")
    assert captured.value.code == "HOST_UNSUPPORTED_ASYNC"
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", "1")
    assert common.native_tool(shell, "claude-code") == "Bash"
    # A per-call background override is still rejected after global foreground setup.
    with pytest.raises(core.WorkspaceError) as captured:
        common.native_tool(
            native_event("Bash", {"command": "echo ok", "run_in_background": True}), "claude-code"
        )
    assert captured.value.code == "HOST_UNSUPPORTED_ASYNC"


def test_native_tool_cursor_shell_rejects_foreign_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cursor shell work cannot redirect to an unbound repository.

    Args:
        monkeypatch: Replaces repository or lifecycle boundaries for the case.
    """
    monkeypatch.setattr(common.core, "repository", lambda path: (Path(path), None, []))
    checkout = Path(Path.cwd().anchor) / "checkout"
    other = Path(Path.cwd().anchor) / "other"
    native = {
        **native_event("Shell", {"command": "echo ok", "working_directory": str(other)}),
        "cwd": str(checkout),
    }
    # Tool-supplied working directory cannot redirect to another checkout.
    with pytest.raises(core.WorkspaceError) as captured:
        common.native_tool(native, "cursor")
    assert captured.value.code == "REPOSITORY_MISMATCH"
    native["tool_input"] = {"command": "echo ok", "working_directory": str(checkout)}
    assert common.native_tool(native, "cursor") == "Shell"


def test_canonical_bootstrap_rejects_foreign_session_before_store_access(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Canonical bootstrap rejects foreign session before store access.

    Args:
        monkeypatch: Replaces repository or lifecycle boundaries for the case.
        tmp_path: Absolute checkout path used in the native event.
    """
    # Compare the observed session to a command naming a foreign session.
    event = {"cwd": str(tmp_path), "session_id": "actual"}
    command = common.bootstrap_command(
        {
            "operation": "diagnose",
            "worktree": str(tmp_path),
            "host": "codex",
            "session_id": "foreign",
        },
        "codex",
    )
    monkeypatch.setattr(
        common.core,
        "repository",
        lambda _path: (_ for _ in ()).throw(AssertionError("repository touched")),
    )
    assert common.canonical_bootstrap(event, command, "codex", ready=False) is False


def test_canonical_bootstrap_rejects_oversized_or_unparseable_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Command validation fails closed before any binding directory is opened.

    Args:
        monkeypatch: Replaces repository or lifecycle boundaries for the case.
        tmp_path: Absolute checkout path used in the native event.
    """
    # Reject malformed commands before any assignment directory opens.
    event = {"cwd": str(tmp_path / "checkout"), "session_id": "session"}
    monkeypatch.setattr(
        common.core.Directory, "absolute", lambda _path: pytest.fail("opened binding")
    )
    assert common.canonical_bootstrap(event, None, "codex", ready=False) is False
    assert common.canonical_bootstrap(event, "x" * 65537, "codex", ready=False) is False
    assert (
        common.canonical_bootstrap(event, "not a lifecycle command", "codex", ready=False) is False
    )


def test_native_tool_rejects_nonobject_arguments_before_transport_checks() -> None:
    """A malformed tool input cannot reach provider, child, or file admission."""
    # Tool input must be an object before transport or permission validation.
    with pytest.raises(core.WorkspaceError) as captured:
        common.native_tool(
            {"tool_use_id": "tool-1", "tool_name": "Read", "tool_input": None}, "cursor"
        )
    assert captured.value.code == "INVALID_REQUEST"


def test_recovery_message_preserves_exact_diagnostic_without_task_content() -> None:
    """Recovery message preserves exact diagnostic without task content."""
    message = common.recovery("SOURCE_STALE", "cursor")
    assert "TASK_WORKSPACE_NOT_READY: SOURCE_STALE" in message
    assert "host=cursor" in message
    assert "Pending work is retained" in message


@pytest.mark.parametrize(
    ("code", "host", "fragments"),
    [
        (
            "TICKET_READ_REQUIRED",
            "claude-code",
            ["get_issue call with exactly", "ToolSearch", "AGENTS.md", "contributor-workflow.md"],
        ),
        ("TICKET_READ_REQUIRED", "cursor", ["get_issue call with exactly"]),
        ("BINDING_MISSING", "claude-code", ["`Task: <issue-id>`", "get_issue"]),
        (
            "SOURCE_STALE",
            "claude-code",
            ["Coordinator:", "run read, then acknowledge", "Reader:", "scope"],
        ),
        (
            "HOST_UNSUPPORTED_PROVIDER",
            "claude-code",
            ["exact selected-ticket get_issue", "save_issue and save_comment"],
        ),
        ("HOST_UNSUPPORTED_PROVIDER", "cursor", ["exact selected-ticket get_issue"]),
        ("HOST_UNSUPPORTED_TOOL", "claude-code", ["ToolSearch or Skill"]),
        ("HOST_UNSUPPORTED_TOOL", "cursor", ["Delete or foreground Shell"]),
        ("HOST_UNSUPPORTED_CHILD_IDENTITY", "claude-code", ["cannot reuse the parent binding"]),
        ("BUSY", "claude-code", ["Retry the same operation once", "diagnose"]),
        ("NOT_ACKNOWLEDGED", "cursor", ["diagnose, register, resume, read and acknowledge"]),
    ],
)
def test_recovery_names_the_admitted_next_operation_per_code(
    code: str, host: str, fragments: list[str]
) -> None:
    """Name each diagnostic's admitted next operation while keeping the code visible.

    Args:
        code: Diagnostic whose recovery text is checked.
        host: Native adapter identity selecting host-specific tool names.
        fragments: Phrases naming that code's admitted next operation.
    """
    message = common.recovery(code, host)
    # Every message keeps the exact code, host and retained-state statement.
    assert message.startswith(f"TASK_WORKSPACE_NOT_READY: {code}. ")
    assert f"host={host}" in message
    assert "Pending work is retained" in message
    # The route names the specific operation that can change this outcome.
    for fragment in fragments:
        assert fragment in message
    # Host-specific Claude routes are not advertised to Cursor.
    if host == "cursor":
        assert "ToolSearch" not in message
        assert "save_comment" not in message


def test_native_post_does_not_settle_cursor_without_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require a documented completion payload before reading binding state.

    Args:
        monkeypatch: Replaces repository or lifecycle boundaries for the case.
    """
    monkeypatch.setattr(common, "native_bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(common, "request_for", lambda *_args: pytest.fail("binding read"))
    event = {**native_event("Read"), "hook_event_name": "postToolUse"}
    # A completion without provider output cannot settle the pending tool.
    with pytest.raises(core.WorkspaceError) as captured:
        common.native_post(event, "cursor", failed=False)
    assert captured.value.code == "INVALID_REQUEST"


def test_native_post_records_exact_synchronous_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Settle only the observed supported tool identifier.

    Args:
        monkeypatch: Replaces repository or lifecycle boundaries for the case.
    """
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(common, "native_bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        common, "request_for", lambda _event, operation, _host: {"operation": operation}
    )

    def execute(request: dict[str, object]) -> dict[str, object]:
        """Capture the exact lifecycle completion request.

        Args:
            request: Lifecycle operation emitted by the adapter.

        Returns:
            Successful lifecycle response for the captured operation.
        """
        calls.append(request)
        return {"ok": True, "code": "OK"}

    monkeypatch.setattr(common.core, "execute", execute)
    event = {**native_event("Read"), "hook_event_name": "postToolUse", "tool_output": "file bytes"}
    assert common.native_post(event, "cursor", failed=False) is None
    assert len(calls) == 1
    assert calls[0]["operation"] == "tool-complete"
    assert calls[0]["tool_id"] == "tool-1"
    assert calls[0]["completed"] is True


def test_native_pre_rejects_unsupported_tool_before_tool_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep unsupported tools out of pending lifecycle state.

    Args:
        monkeypatch: Replaces repository or lifecycle boundaries for the case.
    """
    calls: list[str] = []
    monkeypatch.setattr(common, "native_bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        common, "request_for", lambda _event, operation, _host: {"operation": operation}
    )

    def execute(request: dict[str, object]) -> dict[str, object]:
        """Capture only the readiness check for this denied tool.

        Args:
            request: Lifecycle operation emitted by the adapter.

        Returns:
            Successful lifecycle response for the captured operation.
        """
        calls.append(str(request["operation"]))
        return {"ok": True, "code": "OK"}

    monkeypatch.setattr(common.core, "execute", execute)
    # Readiness may be checked, but unsupported child work cannot be recorded.
    with pytest.raises(core.WorkspaceError) as captured:
        common.native_pre(native_event("Task"), "cursor")
    assert captured.value.code == "HOST_UNSUPPORTED_CHILD_IDENTITY"
    assert calls == ["ready"]


def test_native_pre_records_supported_tool_after_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """Record a supported tool only after lifecycle readiness succeeds.

    Args:
        monkeypatch: Replaces repository or lifecycle boundaries for the case.
    """
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(common, "native_bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        common, "request_for", lambda _event, operation, _host: {"operation": operation}
    )

    def execute(request: dict[str, object]) -> dict[str, object]:
        """Record ordered readiness and admission operations.

        Args:
            request: Lifecycle operation emitted by the adapter.

        Returns:
            Successful lifecycle response for the captured operation.
        """
        calls.append(request)
        return {"ok": True, "code": "OK"}

    monkeypatch.setattr(common.core, "execute", execute)
    assert common.native_pre(native_event("Read"), "cursor") is None
    assert [item["operation"] for item in calls] == ["ready", "tool-start"]
    assert calls[-1]["tool_id"] == "tool-1"
