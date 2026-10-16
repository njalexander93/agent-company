"""Verify effect admission across real stores and synthetic native callback envelopes.

These tests establish translator/lifecycle behavior, never installed-host hook delivery.
"""

import json
import subprocess
from pathlib import Path
from typing import Literal

import pytest

from nplet.adapters import claude, codex, cursor
from nplet.lifecycle import task_workspace as core
from tests.support import Fixture
from tests.types import JsonObject

pytestmark = pytest.mark.integration
type Host = Literal["codex", "claude-code", "cursor"]


def event(
    host: Host, root: Path, tool: str, args: JsonObject, name: str = "PreToolUse"
) -> JsonObject:
    """Build an observed host envelope with independent native call identity.

    Args:
        host: Native adapter under test.
        root: Disposable Git checkout.
        tool: Native tool spelling.
        args: Native structured arguments.
        name: Claude/Codex event name, translated for Cursor.

    Returns:
        A native event without inferred readiness or provider provenance.
    """
    # Supply actual callback fields; Cursor keys its identity by conversation.
    result: JsonObject = dict(
        hook_event_name=name,
        session_id="coordinator",
        cwd=str(root),
        tool_use_id="effect-call",
        tool_name=tool,
        tool_input=args,
    )
    if host == "cursor":
        result.update(
            conversation_id="coordinator",
            workspace_roots=[str(root)],
            hook_event_name=name[0].lower() + name[1:],
        )
    return result


def dispatch(host: Host, value: JsonObject) -> JsonObject:
    """Dispatch one synthetic envelope through its production native adapter.

    Args:
        host: Explicit adapter identity.
        value: Native envelope.

    Returns:
        Native response from the real adapter.
    """
    return {"codex": codex, "claude-code": claude, "cursor": cursor}[host].handle(value)


def denied(value: JsonObject) -> bool:
    """Return the native denial fact without conflating normal permissions."""
    return (
        value.get("permission") == "deny"
        or value.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
    )


@pytest.fixture(params=["codex", "claude-code", "cursor"])
def fresh(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Host, Path]:
    """Allocate an unregistered checkout with no lifecycle storage.

    Args:
        request: Selected host parameter.
        tmp_path: Disposable filesystem root.
        monkeypatch: Environment restoration fixture.

    Returns:
        Host identity and fresh Git root.
    """
    # Native import/background settings must not accidentally select another surface.
    monkeypatch.delenv("CURSOR_VERSION", raising=False)
    monkeypatch.delenv("CURSOR_CODE_REMOTE", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", "1")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return request.param, tmp_path


@pytest.mark.parametrize(
    "tool,args",
    [
        ("Read", {"file_path": "src/main.py"}),
        ("Grep", {"pattern": "class", "path": "src"}),
        ("Write", {"file_path": "planning.md", "content": "notes"}),
        ("Edit", {"file_path": "notes.txt", "old_string": "a", "new_string": "b"}),
    ],
)
def test_task_free_native_calls_leave_unregistered_checkout_untouched(
    fresh: tuple[Host, Path], tool: str, args: JsonObject
) -> None:
    """Admit inspection and prose edits without registration, binding or pending records.

    Args:
        fresh: Host and unregistered checkout.
        tool: Native inspection or prose-edit tool.
        args: Complete target arguments.
    """
    # A free pre/post pair must leave the lifecycle namespace absent.
    host, root = fresh
    pre = event(host, root, tool, args)
    assert not denied(dispatch(host, pre))
    post = {
        **pre,
        "hook_event_name": "postToolUse" if host == "cursor" else "PostToolUse",
        "tool_response": {"result": "done", "isError": False},
        "tool_output": "{}",
    }
    assert dispatch(host, post) == {}
    assert not (root / ".task").exists()


@pytest.mark.parametrize(
    "path",
    [
        "src/main.py",
        "tests/test_main.py",
        "scripts/run.py",
        "AGENTS.md",
        "docs/runtime/contributor-workflow.md",
        ".codex/settings.json",
    ],
)
def test_protected_native_edit_denies_before_any_task_setup(
    fresh: tuple[Host, Path], path: str
) -> None:
    """Deny implementation and instruction edits while leaving targets untouched.

    Args:
        fresh: Host and unregistered checkout.
        path: Independently chosen protected destination.
    """
    # Admission alone must neither create the destination nor initialize a task.
    host, root = fresh
    response = dispatch(
        host, event(host, root, "Write", {"file_path": str(root / path), "content": "change"})
    )
    assert denied(response)
    assert not (root / path).exists()
    assert not (root / ".task").exists()


@pytest.mark.parametrize(
    "command",
    [
        "cat -- AGENTS.md",
        "pwd",
        "git --no-pager --no-optional-locks -c core.fsmonitor=false status --short",
    ],
)
def test_task_free_shell_contract_preserves_native_transport(
    fresh: tuple[Host, Path], command: str
) -> None:
    """Admit bounded inspection shell forms on each supported foreground transport.

    Args:
        fresh: Host and unregistered checkout.
        command: Independently selected shared read form.
    """
    # Shell transport validation still applies before the shared free decision.
    host, root = fresh
    tool = "Shell" if host == "cursor" else "Bash"
    assert not denied(dispatch(host, event(host, root, tool, {"command": command})))
    assert not (root / ".task").exists()


def test_unknown_execution_names_actionable_alternative(fresh: tuple[Host, Path]) -> None:
    """Explain unsupported execution rather than silently admitting an opaque wrapper.

    Args:
        fresh: Host and unregistered checkout.
    """
    # Opaque execution cannot create task state or hide behind a read-looking script name.
    host, root = fresh
    tool = "Shell" if host == "cursor" else "Bash"
    response = dispatch(host, event(host, root, tool, {"command": "python inspect.py"}))
    assert denied(response)
    assert "UNKNOWN_" in json.dumps(response)
    assert "supported" in json.dumps(response)
    assert not (root / ".task").exists()


def test_pending_ticket_does_not_block_or_settle_research(fresh: tuple[Host, Path]) -> None:
    """Keep unrelated research available while an explicit ticket lookup remains pending.

    Args:
        fresh: Host and unregistered checkout.
    """
    # Establish only the explicit Task marker, then retain its exact bytes across research.
    host, root = fresh
    prompt = event(host, root, "Read", {})
    prompt.update(
        hook_event_name="beforeSubmitPrompt" if host == "cursor" else "UserPromptSubmit",
        prompt="Task: TEST-1",
    )
    assert not denied(dispatch(host, prompt))
    markers = {p.name: p.read_bytes() for p in (root / ".task/.bindings").glob("*.json")}
    research = event(host, root, "Read", {"file_path": "src/main.py"})
    assert not denied(dispatch(host, research))
    research.update(
        hook_event_name="postToolUse" if host == "cursor" else "PostToolUse",
        tool_response={"result": "source"},
        tool_output="{}",
    )
    assert dispatch(host, research) == {}
    assert markers == {p.name: p.read_bytes() for p in (root / ".task/.bindings").glob("*.json")}


def test_registered_pending_edit_settles_after_destination_becomes_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use exact recorded admission when a native callback's current target changes role.

    Args:
        monkeypatch: Native environment restoration fixture.
    """
    # Register each host independently and make its coordinator ready for implementation.
    monkeypatch.delenv("CURSOR_VERSION", raising=False)
    for host in ("codex", "claude-code", "cursor"):
        case = Fixture()
        case.setUp()
        try:
            case.base.update(
                host=host,
                coordinator=core.participant_key({"host": host, "session_id": "coordinator"}),
            )
            case.create()
            case.ready()
            pre = event(host, case.root, "Write", {"file_path": "source.py", "content": "code"})
            assert not denied(dispatch(host, pre))
            key = core.participant_key(case.base)
            assert "effect-call" in case.state()["participants"][key]["pending"]
            # Native call ID outranks reclassification of a callback's altered target shape.
            post = {
                **pre,
                "hook_event_name": "postToolUse" if host == "cursor" else "PostToolUse",
                "tool_input": {"file_path": "ordinary.md", "content": "code"},
                "tool_response": {"result": "done", "isError": False},
                "tool_output": "{}",
            }
            assert dispatch(host, post) == {}
            assert not case.state()["participants"][key]["pending"]
        finally:
            # Release every disposable store even when one host fails its assertions.
            case.doCleanups()


@pytest.mark.parametrize("host", ["codex", "claude-code", "cursor"])
def test_linear_task_free_contract_keeps_provider_provenance(
    tmp_path: Path, host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Admit known external administration and verify Cursor's independent server gate.

    Args:
        tmp_path: Disposable unregistered checkout.
        host: Native adapter identity.
        monkeypatch: Environment restoration fixture.
    """
    # Build exact native provider names; Claude and Cursor use different provenance shapes.
    monkeypatch.delenv("CURSOR_VERSION", raising=False)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    tool = {
        "codex": "mcp__codex_apps__linear__save_issue",
        "claude-code": "mcp__linear-server__save_issue",
        "cursor": "MCP:save_issue",
    }[host]
    pre = event(host, tmp_path, tool, {"id": "OTHER-2", "priority": 2})
    if host == "claude-code":
        pre["mcp_server"] = {"name": "linear-server", "source": "user"}
    assert not denied(dispatch(host, pre))
    if host == "cursor":
        # Generic permission is provisional; the mandatory specific hook denies bad provenance.
        specific = {
            **pre,
            "hook_event_name": "beforeMCPExecution",
            "tool_name": "save_issue",
            "tool_input": json.dumps(pre["tool_input"]),
            "mcp_server_name": "linear-server",
            "mcp_server_url": "https://mcp.linear.app/mcp",
        }
        assert not denied(dispatch(host, specific))
        for changes in (
            {"mcp_server_name": "foreign"},
            {"mcp_server_url": "https://example.invalid/mcp"},
            {"tool_name": "execute_code"},
        ):
            assert denied(dispatch(host, {**specific, **changes}))
    assert not (tmp_path / ".task").exists()


def test_claude_auto_fix_monitor_requires_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prevent a source-correction monitor from inheriting an administrative exemption.

    Args:
        tmp_path: Disposable unregistered checkout.
        monkeypatch: Environment restoration fixture.
    """
    # The pinned app identity cannot erase automatic implementation effects.
    monkeypatch.delenv("CURSOR_VERSION", raising=False)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    pre = event(
        "claude-code",
        tmp_path,
        "mcp__ccd_pr__set_monitor",
        {"auto_fix": True, "url": "https://example.invalid/pr/1"},
    )
    pre["mcp_server"] = {"name": "ccd_pr", "source": "sdk"}
    assert denied(dispatch("claude-code", pre))
    assert not (tmp_path / ".task").exists()


def test_fresh_startup_context_is_positive_and_creates_no_store(fresh: tuple[Host, Path]) -> None:
    """Treat ordinary unbound startup as task-free rather than a readiness failure.

    Args:
        fresh: Native host and unregistered checkout.
    """
    # A normal startup must not tell the model that all ordinary work is blocked.
    host, root = fresh
    response = dispatch(host, event(host, root, "Read", {}, "SessionStart"))
    assert "available without a task" in json.dumps(response)
    assert "TASK_WORKSPACE_NOT_READY" not in json.dumps(response)
    ending = event(host, root, "Read", {}, "SessionEnd")
    assert dispatch(host, ending) == {}
    assert not (root / ".task").exists()


@pytest.mark.parametrize("chars", [None, ""])
def test_codex_unbound_empty_poll_reads_without_lifecycle_state(
    tmp_path: Path, chars: str | None
) -> None:
    """Allow polling a task-free read transport without creating an issue binding.

    Args:
        tmp_path: Disposable checkout root.
        chars: Omitted or explicitly empty native input.
    """
    # Pure polling has no source mutation or lifecycle completion to record.
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    arguments: JsonObject = {"session_id": 71}
    if chars is not None:
        arguments["chars"] = chars
    pre = event("codex", tmp_path, "write_stdin", arguments)
    assert dispatch("codex", pre) == {}
    post = {
        **pre,
        "hook_event_name": "PostToolUse",
        "tool_response": {"exit_code": 0, "output": "read result"},
    }
    assert dispatch("codex", post) == {}
    assert not (tmp_path / ".task").exists()


@pytest.mark.parametrize(
    "arguments",
    [
        {"session_id": 71, "chars": "mutating input\n"},
        {"session_id": True},
        {"session_id": 71, "chars": None},
    ],
)
def test_codex_unbound_polling_cannot_drive_unknown_process(
    tmp_path: Path, arguments: JsonObject
) -> None:
    """Gate typed input and malformed polling identities as opaque execution.

    Args:
        tmp_path: Disposable unregistered checkout.
        arguments: Native polling input with possible process-driving effect.
    """
    # Neither malformed identity nor stdin writes receive a read-only exemption.
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert denied(dispatch("codex", event("codex", tmp_path, "write_stdin", arguments)))
    assert not (tmp_path / ".task").exists()


def test_codex_protected_async_handle_settles_only_its_own_empty_poll() -> None:
    """Preserve original pending-handle correlation across task-free polling decisions."""
    # A protected command begins under a ready task and returns an uncertain process handle.
    case = Fixture()
    case.setUp()
    try:
        case.create()
        case.ready()
        pre = event(
            "codex",
            case.root,
            "exec_command",
            {"cmd": "python build.py", "login": False, "shell": "/bin/sh"},
        )
        assert dispatch("codex", pre) == {}
        assert (
            dispatch(
                "codex",
                {**pre, "hook_event_name": "PostToolUse", "tool_response": {"session_id": 71}},
            )
            == {}
        )
        key = core.participant_key(case.base)
        original = case.state()["participants"][key]["pending"]
        assert original == {"effect-call": {"status": "unknown", "handle": "71"}}
        # An unrelated read-only handle cannot settle or overwrite the protected operation.
        foreign = {
            **event("codex", case.root, "write_stdin", {"session_id": 72}),
            "tool_use_id": "foreign-poll",
        }
        assert dispatch("codex", foreign) == {}
        assert (
            dispatch(
                "codex",
                {**foreign, "hook_event_name": "PostToolUse", "tool_response": {"exit_code": 0}},
            )
            == {}
        )
        assert case.state()["participants"][key]["pending"] == original
        # The matching handle follows the existing reserved polling transport and settles both.
        matching = {
            **event("codex", case.root, "write_stdin", {"session_id": 71, "chars": ""}),
            "tool_use_id": "matching-poll",
        }
        assert dispatch("codex", matching) == {}
        pending = case.state()["participants"][key]["pending"]
        assert pending["matching-poll"]["poll_parent"] == "effect-call"
        assert (
            dispatch(
                "codex",
                {**matching, "hook_event_name": "PostToolUse", "tool_response": {"exit_code": 0}},
            )
            == {}
        )
        assert not case.state()["participants"][key]["pending"]
    finally:
        # Release the disposable store even when native correlation assertions fail.
        case.doCleanups()


def test_codex_stale_packet_allows_inspection_but_denies_implementation() -> None:
    """Preserve stale-task state while ordinary inspection remains usable."""
    # Establish a real required external source and acknowledge the current bytes.
    case = Fixture()
    case.setUp()
    try:
        case.create()
        source = case.root / "AGENTS.md"
        source.write_text("original instructions", encoding="utf-8")
        key = core.participant_key(case.base)
        packet = [
            {
                "id": "governing",
                "locator": str(source),
                "sha256": core.sha(source.read_bytes()),
                "required": True,
                "authority": "repository",
                "reason": "test",
                "stage": "implementation",
                "reader": key,
            }
        ]
        case.call(
            "scope",
            expected_revision=case.state()["revision"],
            target_participant=key,
            packet=packet,
        )
        read = case.call("read")
        case.call("acknowledge", packet_digest=read["packet_digest"])
        # Changing actual governing bytes invalidates protected admission without blocking research.
        source.write_text("changed instructions", encoding="utf-8")
        before = case.state()
        assert (
            dispatch("codex", event("codex", case.root, "Read", {"file_path": str(source)})) == {}
        )
        assert case.state() == before
        protected = dispatch(
            "codex",
            event("codex", case.root, "Write", {"file_path": "src/main.py", "content": "code"}),
        )
        assert denied(protected)
        assert "SOURCE_STALE" in json.dumps(protected)
        assert case.state() == before
    finally:
        # Remove only this disposable repository and its independent lifecycle state.
        case.doCleanups()
