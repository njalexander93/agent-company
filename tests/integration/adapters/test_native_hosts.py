"""Exercise native host wire contracts in disposable stores, not trusted live host sessions.

Payload references: https://code.claude.com/docs/en/hooks and
https://prod.cursor.com/docs/hooks. Inputs use each host's documented field names.
"""

import copy
import json
import os
import shlex
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Literal, cast

import pytest

from agent_company.adapters import claude, codex, cursor
from agent_company.lifecycle import task_workspace as core
from tests.support import ROOT, Fixture
from tests.types import JsonObject, JsonValue

pytestmark = pytest.mark.integration

type NativeHost = Literal["claude", "cursor"]
type NativeCase = tuple[NativeHost, Fixture]


@pytest.fixture(params=["claude", "cursor"])
def native(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[NativeCase]:
    """Create a disposable repository with a host-specific coordinator identity.

    Args:
        request: Pytest-selected native host name.
        monkeypatch: Scoped environment cleanup for host identity determinism.

    Yields:
        Native host and shared real-core fixture with cleanup registered.
    """
    # Remove imported-hook markers so each native fixture exercises its own entry point.
    monkeypatch.delenv("CURSOR_VERSION", raising=False)
    monkeypatch.delenv("CURSOR_CODE_REMOTE", raising=False)
    # Allocate independent Git state and select the native host before issue creation.
    case = Fixture()
    host = cast(NativeHost, request.param)
    case.setUp()
    case.base["host"] = {"claude": claude.HOST, "cursor": cursor.HOST}[host]
    case.base["coordinator"] = core.participant_key(case.base)
    # Always release disposable stores after assertions or setup-dependent failures.
    try:
        yield host, case
    finally:
        case.doCleanups()


def native_event(host: NativeHost, case: Fixture, name: str, **fields: JsonValue) -> JsonObject:
    """Build native event shapes without adding Codex-only shell/login parameters.

    Args:
        host: Native wire protocol to use.
        case: Disposable repository and session identity.
        name: Claude-style event name translated to Cursor's lower-camel event spelling.
        **fields: Event-specific overrides, including intentionally malformed test values.

    Returns:
        Native envelope with stable session identity and real disposable cwd.
    """
    # Populate common tool identity without granting any issue binding.
    event: JsonObject = {
        "hook_event_name": name,
        "cwd": str(case.root),
        "tool_use_id": "actual-tool",
        "tool_name": "Read" if host == "claude" else "Shell",
        "tool_input": (
            {"file_path": str(case.root / ".task/TEST-1/roadmap.md")}
            if host == "claude"
            else {"command": "true"}
        ),
    }
    # Use Claude's session identifier and native Bash arguments.
    if host == "claude":
        event["session_id"] = "coordinator"
    # Use Cursor's conversation identity and workspace roots.
    else:
        event.update(
            hook_event_name=(
                "beforeSubmitPrompt" if name == "UserPromptSubmit" else name[0].lower() + name[1:]
            ),
            conversation_id="coordinator",
            generation_id="generation-1",
            workspace_roots=[str(case.root)],
            cursor_version="fixture",
        )
    # Apply explicit mutations after building the ordinary native envelope.
    event.update(fields)
    return event


def dispatch(host: NativeHost, event: JsonObject) -> JsonObject:
    """Call the selected real native adapter without replacing lifecycle behavior.

    Args:
        host: Native adapter identity.
        event: Native host event to translate.

    Returns:
        The adapter's native decision or observation response.
    """
    return {"claude": claude, "cursor": cursor}[host].handle(event)


def assert_decision(host: NativeHost, response: JsonObject, allowed: bool) -> None:
    """Check native denial while preserving Claude's ordinary permission flow.

    Args:
        host: Native response protocol.
        response: Actual adapter response.
        allowed: Whether lifecycle readiness or bounded bootstrap should permit continuation.

    Raises:
        AssertionError: Native decision disagrees with the expected gate result.
    """
    # Cursor expresses permission directly in its native result.
    if host == "cursor":
        assert response["permission"] == ("allow" if allowed else "deny")
    # Claude must not override ordinary host permissions on a successful lifecycle check.
    elif allowed:
        assert response.get("hookSpecificOutput", {}).get("permissionDecision") is None
    # Claude expresses blocked tool use through its event-specific permission decision.
    else:
        assert response["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_native_admission_records_actual_tool_before_continuation(native: NativeCase) -> None:
    """Require a ready native participant and persist its actual tool identity.

    Args:
        native: Host-specific disposable repository fixture.

    Raises:
        AssertionError: Admission fails, skips pending state or aliases another host.
    """
    # Establish the native participant's explicit scope and acknowledgment.
    host, case = native
    case.create()
    case.ready()
    # Admit using native fields and inspect the real committed participant state.
    response = dispatch(host, native_event(host, case, "PreToolUse"))
    assert_decision(host, response, True)
    state = case.state()
    assert "actual-tool" in state["participants"][case.base["coordinator"]]["pending"]
    # Identical session text must never collide across any of the three hosts.
    keys = {
        core.participant_key({**case.base, "host": name})
        for name in ("codex", claude.HOST, cursor.HOST)
    }
    assert len(keys) == 3


@pytest.mark.parametrize("binding", ["missing", "malformed", "other_host"])
def test_native_denies_unusable_binding(native: NativeCase, binding: str) -> None:
    """Reject missing, corrupt or foreign-host authority before recording tool work.

    Args:
        native: Host-specific disposable repository fixture.
        binding: Binding failure to construct locally.

    Raises:
        AssertionError: Native pre-tool admission bypasses the binding boundary.
    """
    # Leave the native participant unbound, or create a corrupt binding at its exact key.
    host, case = native
    if binding == "malformed":
        case.create()
        case.ready()
        path = case.root / ".task/.bindings" / (str(case.base["coordinator"]) + ".json")
        path.write_text("{")
    # Establish only the Codex participant with the same session text.
    elif binding == "other_host":
        case.base["host"] = "codex"
        case.base["coordinator"] = core.participant_key(case.base)
        case.create()
        case.ready()
    # Require native denial without synthesizing root or foreign-host permissions.
    assert_decision(host, dispatch(host, native_event(host, case, "PreToolUse")), False)


def test_native_bootstrap_uses_exact_command_and_matching_identity(native: NativeCase) -> None:
    """Allow bounded recovery without accepting changed authority or shell wrappers.

    Args:
        native: Host-specific disposable repository fixture.

    Raises:
        AssertionError: A valid bootstrap is refused or an altered request is admitted.
    """
    # Create an unready participant and construct the exact four-argument recovery command.
    host, case = native
    case.create()
    # Record the explicit native task assignment required by the recovery command gate.
    dispatch(host, native_event(host, case, "UserPromptSubmit", prompt="Task: TEST-1"))
    request = case.req("read", coordinator=None)
    command = shlex.join(
        [codex.PYTHON, str(codex.LIFECYCLE), "--request-json", json.dumps(request)]
    )
    event = native_event(
        host,
        case,
        "PreToolUse",
        tool_name="Bash" if host == "claude" else "Shell",
        tool_input={"command": command},
    )
    assert_decision(host, dispatch(host, event), True)
    # Reject shell composition and alternative executable spellings before readiness.
    for altered in (
        "env " + command,
        command + "; true",
        command + " && true",
        command + " > output",
        command.replace(shlex.quote(codex.PYTHON), "python3", 1),
    ):
        changed = {**event, "tool_input": {"command": altered}}
        assert_decision(host, dispatch(host, changed), False)
    # Reject otherwise canonical commands that change any assigned identity boundary.
    for field, value in (
        ("host", "codex"),
        ("session_id", "other-session"),
        ("worktree", str(case.other)),
        ("issue_id", "OTHER-2"),
    ):
        changed_request = {**request, field: value}
        changed_command = shlex.join(
            [codex.PYTHON, str(codex.LIFECYCLE), "--request-json", json.dumps(changed_request)]
        )
        assert_decision(
            host, dispatch(host, {**event, "tool_input": {"command": changed_command}}), False
        )


def test_native_uncertain_completion_and_stop_retain_pending_work(native: NativeCase) -> None:
    """Keep uncertainty and lifecycle-end observations distinct from tool settlement.

    Args:
        native: Host-specific disposable repository fixture.

    Raises:
        AssertionError: An ambiguous response or end event settles pending work or the issue.
    """
    # Admit a real native tool whose eventual completion remains unknown.
    host, case = native
    case.create()
    case.ready()
    assert_decision(host, dispatch(host, native_event(host, case, "PreToolUse")), True)
    # Feed raw text without typed completion evidence through the native result field.
    field = "tool_response" if host == "claude" else "tool_output"
    dispatch(
        host,
        native_event(
            host, case, "PostToolUse", **{field: None if host == "claude" else "still running"}
        ),
    )
    # Stop and session-end events must retain the pending operation and active disposition.
    for name in ("Stop", "SessionEnd"):
        dispatch(host, native_event(host, case, name))
        state = case.state()
        assert state["disposition"] == "active"
        assert "actual-tool" in state["participants"][case.base["coordinator"]]["pending"]


def test_native_wrong_tool_completion_cannot_settle_original(native: NativeCase) -> None:
    """Match completion to the admitted tool rather than clearing unrelated pending work.

    Args:
        native: Host-specific disposable repository fixture.

    Raises:
        AssertionError: Foreign tool identity retires the admitted original operation.
    """
    # Admit the original tool and construct a native failure for another tool identity.
    host, case = native
    case.create()
    case.ready()
    inputs: JsonObject = {"file_path": str(case.root / ".task/TEST-1/roadmap.md")}
    dispatch(host, native_event(host, case, "PreToolUse", tool_name="Read", tool_input=inputs))
    failure = native_event(
        host,
        case,
        "PostToolUseFailure",
        tool_use_id="unrelated-tool",
        is_interrupt=False,
        tool_name="Read",
        tool_input=inputs,
    )
    # Use each host's documented failure-description field.
    failure["error" if host == "claude" else "error_message"] = "fixture failure"
    if host == "cursor":
        failure["failure_type"] = "error"
    dispatch(host, failure)
    # The unrelated failure must leave the original pending work intact.
    assert "actual-tool" in case.state()["participants"][case.base["coordinator"]]["pending"]
    # A matching terminal file-tool failure may settle only the actual admitted operation.
    failure["tool_use_id"] = "actual-tool"
    dispatch(host, failure)
    assert case.state()["participants"][case.base["coordinator"]]["pending"] == {}


def test_native_child_without_binding_cannot_inherit_root(native: NativeCase) -> None:
    """Reject a child tool call even when its root participant is ready.

    Args:
        native: Host-specific disposable repository fixture.

    Raises:
        AssertionError: An unbound child inherits the coordinator's tool authority.
    """
    # Make root permissions usable so the child identity is the only changed boundary.
    host, case = native
    case.create()
    case.ready()
    event = native_event(host, case, "PreToolUse")
    event["agent_id" if host == "claude" else "subagent_id"] = "unbound-child"
    # Require denial and no mutation of root pending operations.
    before = copy.deepcopy(case.state())
    assert_decision(host, dispatch(host, event), False)
    assert case.state() == before


@pytest.mark.parametrize(
    "response",
    [
        "not JSON",
        '"plain text"',
        '{"exitCode":null}',
        '{"exitCode":true}',
        '{"exitCode":0,"backgroundTaskId":"pending"}',
    ],
)
def test_cursor_ambiguous_result_strings_retain_work(response: str) -> None:
    """Require typed completion inside Cursor's JSON-stringified result payload.

    Args:
        response: Raw or JSON-encoded text without a valid terminal result.

    Raises:
        AssertionError: Ambiguous string data retires an admitted operation.
    """
    # Create a Cursor-owned ready issue independently of parameterized Claude fixtures.
    case = Fixture()
    case.setUp()
    case.base["host"] = "cursor"
    case.base["coordinator"] = core.participant_key(case.base)
    try:
        # Admit the actual native Shell operation before delivering ambiguous result data.
        case.create()
        case.ready()
        dispatch("cursor", native_event("cursor", case, "PreToolUse"))
        dispatch("cursor", native_event("cursor", case, "PostToolUse", tool_output=response))
        assert "actual-tool" in case.state()["participants"][case.base["coordinator"]]["pending"]
    # Release all disposable repositories after the uncertainty assertion.
    finally:
        case.doCleanups()


def test_native_invalid_cwd_cannot_borrow_ready_workspace(native: NativeCase) -> None:
    """Reject an ambiguous cwd value rather than falling back to ready workspace authority.

    Args:
        native: Host-specific disposable repository fixture.

    Raises:
        AssertionError: Invalid cwd permits tool admission under another location.
    """
    # Establish a ready participant so cwd is the only failing authority boundary.
    host, case = native
    case.create()
    case.ready()
    # Replace the native cwd with a malformed value without altering workspace roots.
    event = native_event(host, case, "PreToolUse", cwd=[str(case.root), str(case.other)])
    assert_decision(host, dispatch(host, event), False)


def test_cursor_ambiguous_workspace_roots_deny() -> None:
    """Reject multiple workspace roots when no single native authority is established.

    Raises:
        AssertionError: Multi-root ambiguity permits tool admission.
    """
    # Build two real worktrees and create readiness in only the first one.
    case = Fixture()
    case.setUp()
    case.base["host"] = "cursor"
    case.base["coordinator"] = core.participant_key(case.base)
    try:
        case.create()
        case.ready()
        # Remove cwd so workspace roots alone cannot identify one safe execution root.
        event = native_event("cursor", case, "PreToolUse")
        event.pop("cwd")
        event["workspace_roots"] = [str(case.root), str(case.other)]
        assert_decision("cursor", dispatch("cursor", event), False)
    # Remove all fixture state regardless of the decision result.
    finally:
        case.doCleanups()


def test_native_synchronous_completion_settles_only_admitted_work(native: NativeCase) -> None:
    """Accept explicit terminal results without changing the active issue disposition.

    Args:
        native: Host-specific disposable repository fixture.

    Raises:
        AssertionError: Valid completion leaves matching work pending or completes the issue.
    """
    # Admit a supported native operation using real readiness and pending state.
    host, case = native
    case.create()
    case.ready()
    assert_decision(host, dispatch(host, native_event(host, case, "PreToolUse")), True)
    # Use the documented native result representation for each host.
    fields: JsonObject = {"tool_response": {"content": "fixture file content"}}
    if host == "cursor":
        fields = {"tool_output": json.dumps({"exitCode": 0, "stdout": "finished"})}
    dispatch(host, native_event(host, case, "PostToolUse", **fields))
    # Tool completion retires only pending work, not the issue itself.
    state = case.state()
    assert state["participants"][case.base["coordinator"]]["pending"] == {}
    assert state["disposition"] == "active"


@pytest.mark.parametrize("tool", ["mcp__fixture__read", "Task", "Agent"])
def test_native_unsupported_provider_or_child_tool_denies(native: NativeCase, tool: str) -> None:
    """Keep provider and child execution outside this adapter's claimed synchronous scope.

    Args:
        native: Host-specific disposable repository fixture.
        tool: Unsupported provider or child-launch tool name.

    Raises:
        AssertionError: Unsupported work inherits root permissions or creates pending state.
    """
    # Make the coordinator ready before changing only the tool being requested.
    host, case = native
    case.create()
    case.ready()
    before = case.state()
    # Missing child identity must not be synthesized from the ready root session.
    result = dispatch(host, native_event(host, case, "PreToolUse", tool_name=tool, tool_input={}))
    assert_decision(host, result, False)
    assert case.state() == before


def test_claude_ordinary_shell_requires_foreground_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deny ordinary Bash without the explicit foreground-only host configuration.

    Args:
        monkeypatch: Scoped environment override removing the required foreground setting.

    Raises:
        AssertionError: Readiness grants an unsupported asynchronous shell path.
    """
    # Remove foreground guarantees before creating the real ready Claude participant.
    monkeypatch.delenv("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", raising=False)
    monkeypatch.delenv("CURSOR_VERSION", raising=False)
    case = Fixture()
    case.setUp()
    case.base["host"] = claude.HOST
    case.base["coordinator"] = core.participant_key(case.base)
    try:
        case.create()
        case.ready()
        # Native Bash inputs must be refused without invented Codex login/shell flags.
        result = claude.handle(
            native_event(
                "claude", case, "PreToolUse", tool_name="Bash", tool_input={"command": "true"}
            )
        )
        assert_decision("claude", result, False)
        assert case.state()["participants"][case.base["coordinator"]]["pending"] == {}
    # Release the temporary repository after either success or assertion failure.
    finally:
        case.doCleanups()


def test_cursor_imported_claude_hook_does_not_duplicate_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skip imported Claude hooks while the native Cursor hook records exactly one operation.

    Args:
        monkeypatch: Scoped environment override matching Cursor's imported-hook marker.

    Raises:
        AssertionError: The imported hook mutates state or duplicates native pending work.
    """
    # Create a ready Cursor issue and snapshot state before the imported hook runs.
    case = Fixture()
    case.setUp()
    case.base["host"] = cursor.HOST
    case.base["coordinator"] = core.participant_key(case.base)
    try:
        case.create()
        case.ready()
        before = case.state()
        monkeypatch.setenv("CURSOR_VERSION", "fixture")
        # Invoke the imported Claude translator and require a strict no-op.
        assert claude.handle(native_event("claude", case, "PreToolUse")) == {}
        assert case.state() == before
        # The native Cursor translator alone records the actual operation once.
        result = cursor.handle(native_event("cursor", case, "PreToolUse"))
        assert_decision("cursor", result, True)
        state = case.state()
        assert set(state["participants"][case.base["coordinator"]]["pending"]) == {"actual-tool"}
        assert state["seq"] == before["seq"] + 1
    # Restore the environment through pytest and remove only disposable fixture state.
    finally:
        case.doCleanups()


@pytest.mark.parametrize("host", ["claude", "cursor"])
@pytest.mark.parametrize("payload", ["{", "[]", "oversized"])
def test_native_process_rejects_invalid_or_oversized_json(host: NativeHost, payload: str) -> None:
    """Use the real process boundary to reject malformed or over-budget host envelopes.

    Args:
        host: Native adapter process to invoke.
        payload: Invalid input or selector for a valid oversized JSON envelope.

    Raises:
        AssertionError: Invalid input fails open or emits an unusable protocol response.
    """
    # Construct a valid envelope exceeding the native one-megabyte input budget.
    if payload == "oversized":
        payload = json.dumps({"hook_event_name": "PreToolUse", "padding": "x" * (1024 * 1024)})
    # Remove Cursor's imported-Claude marker so this process exercises its own parser.
    environment = os.environ.copy()
    environment.pop("CURSOR_VERSION", None)
    result = subprocess.run(
        [codex.PYTHON, "-m", "agent_company.adapters." + host],
        input=payload,
        text=True,
        capture_output=True,
        env=environment,
    )
    # Both native protocols recognize exit two as a blocking parser failure.
    assert result.returncode == 2
    assert isinstance(json.loads(result.stdout), dict)
    assert result.stderr.strip()
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("host", ["claude", "cursor"])
def test_native_config_quotes_checkout_and_reports_missing_environment(
    host: NativeHost, tmp_path: Path
) -> None:
    """Run actual checked-in hook commands under quoted roots and missing local interpreters.

    Args:
        host: Native configuration to inspect and execute.
        tmp_path: Disposable directory for a nested Git checkout with shell metacharacters.

    Raises:
        AssertionError: Quoting fails, native denial is lost or missing setup fails silently.
    """
    # Select the checked-in pre-tool command without translating or rewriting its shell text.
    if host == "claude":
        config = json.loads((ROOT / ".claude/settings.json").read_text())
        command = config["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    # Cursor additionally requires fail-closed configuration at the permission boundary.
    else:
        config = json.loads((ROOT / ".cursor/hooks.json").read_text())
        entry = config["hooks"]["preToolUse"][0]
        command = entry["command"]
        assert entry["failClosed"] is True
    # Make a quoted checkout containing only the real entry script and local interpreter link.
    root = tmp_path / "checkout with 'quotes'"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    nested = root / "nested" / "directory"
    nested.mkdir(parents=True)
    script = root / "src/agent_company/adapters" / (host + ".py")
    script.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "src/agent_company/adapters" / (host + ".py"), script)
    (root / ".venv").symlink_to(ROOT / ".venv", target_is_directory=True)
    # Use missing registration to obtain a native denial without mutating a live task.
    event: JsonObject = {
        "hook_event_name": "PreToolUse" if host == "claude" else "preToolUse",
        "session_id": "isolated",
        "conversation_id": "isolated",
        "workspace_roots": [str(root)],
        "cwd": str(nested),
        "tool_name": "Read",
        "tool_input": {"file_path": str(root / "unregistered.md")},
        "tool_use_id": "isolated-tool",
    }
    environment = os.environ.copy()
    environment.pop("CURSOR_VERSION", None)
    result = subprocess.run(
        command,
        shell=True,
        executable="/bin/sh",
        cwd=nested,
        input=json.dumps(event),
        text=True,
        capture_output=True,
        env=environment,
    )
    assert result.returncode == 0, result.stderr
    assert_decision(host, json.loads(result.stdout), False)
    # Remove only the disposable interpreter link and require a useful blocking setup error.
    (root / ".venv").unlink()
    missing = subprocess.run(
        command,
        shell=True,
        executable="/bin/sh",
        cwd=nested,
        input=json.dumps(event),
        text=True,
        capture_output=True,
        env=environment,
    )
    assert missing.returncode == 2
    assert "TASK_WORKSPACE_SETUP_REQUIRED" in missing.stderr
    assert "Poetry" in missing.stderr


def test_claude_foreground_shell_retains_ambiguous_results_then_settles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require foreground configuration and an unambiguous terminal shell result.

    Args:
        monkeypatch: Scoped configuration matching the checked-in Claude foreground policy.

    Raises:
        AssertionError: Uncertain results clear work or the complete foreground result fails.
    """
    # Confirm the checked-in configuration supplies the required host guarantee.
    config = json.loads((ROOT / ".claude/settings.json").read_text())
    assert config["env"]["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", "1")
    monkeypatch.delenv("CURSOR_VERSION", raising=False)
    case = Fixture()
    case.setUp()
    case.base["host"] = claude.HOST
    case.base["coordinator"] = core.participant_key(case.base)
    try:
        # Admit foreground Bash with only its native command arguments.
        case.create()
        case.ready()
        event = native_event(
            "claude", case, "PreToolUse", tool_name="Bash", tool_input={"command": "true"}
        )
        assert_decision("claude", claude.handle(event), True)
        # Reject missing certainty, interruption and explicit background handles.
        for result in (
            {"stdout": "", "stderr": ""},
            {"stdout": "", "stderr": "", "interrupted": True},
            {"stdout": "", "stderr": "", "interrupted": False, "backgroundTaskId": "pending"},
        ):
            claude.handle({**event, "hook_event_name": "PostToolUse", "tool_response": result})
            assert (
                "actual-tool" in case.state()["participants"][case.base["coordinator"]]["pending"]
            )
        # A matching explicit foreground completion may finally retire the pending operation.
        claude.handle(
            {
                **event,
                "hook_event_name": "PostToolUse",
                "tool_response": {"stdout": "done", "stderr": "", "interrupted": False},
            }
        )
        assert case.state()["participants"][case.base["coordinator"]]["pending"] == {}
        assert case.state()["disposition"] == "active"
    # Remove only the disposable fixture; pytest restores host environment variables.
    finally:
        case.doCleanups()


@pytest.mark.parametrize("identifier", [None, "", 0])
def test_native_admission_requires_actual_host_tool_id(
    native: NativeCase, identifier: JsonValue
) -> None:
    """Refuse to invent tool correlation identities when native admission input is incomplete.

    Args:
        native: Host-specific disposable repository fixture.
        identifier: Missing, empty or incorrectly typed host tool identity.

    Raises:
        AssertionError: Admission invents an operation ID or mutates pending work.
    """
    # Establish readiness so malformed native identity is the isolated failure condition.
    host, case = native
    case.create()
    case.ready()
    before = case.state()
    # Require native denial and exact state preservation without synthesizing a UUID.
    result = dispatch(host, native_event(host, case, "PreToolUse", tool_use_id=identifier))
    assert_decision(host, result, False)
    assert case.state() == before
