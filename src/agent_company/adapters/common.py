"""Share host-bound assignment and exact bootstrap checks across native adapters."""

from __future__ import annotations

import json
import os
import re
import shlex
import signal
import sys
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import NoReturn

from agent_company.lifecycle import task_workspace as core

ROOT = Path(__file__).resolve().parents[3]

BOOTSTRAP_FIELDS = {
    "diagnose": set(),
    "register": {"main_worktree", "startup"},
    "bind": set(),
    "adopt": {"coordinator", "inventory", "owners", "evidence"},
    "resume": set(),
    "restore": {"observations"},
    "read": set(),
    "acknowledge": {"packet_digest"},
    "archive-index": {"observations"},
    "archive-verify": {"observations"},
    "archive-observe-save": {"document_id", "content_digest"},
    "cleanup-plan": set(),
    "cleanup-commit": {"observations", "cleanup_challenge"},
    "reopen": {"evidence"},
    "archive-prepare": {"seal"},
    "event-rollover": set(),
    "reconcile-files": {"inventory", "evidence"},
    "rebind": {"new_issue_id", "new_binding_generation", "evidence"},
}
COMMON_FIELDS = {
    "schema_version",
    "operation",
    "request_id",
    "worktree",
    "host",
    "session_id",
    "repo_id",
    "issue_id",
    "issue_uuid",
    "binding_generation",
    "expected_revision",
}
LIFECYCLE = ROOT / "src/agent_company/lifecycle/task_workspace.py"
PYTHON = str(ROOT / ".venv" / "bin" / "python")


def request_for(event: core.JSONObject, operation: str, host: str) -> core.JSONObject:
    """Resolve an actual hook session to its registered repository and binding.

    Args:
        event: Observed host hook input, including the actual session and tool identities.
        host: Explicit adapter identity; never taken from untrusted tool arguments.
        operation: Core operation requested for the observed session.

    Returns:
        A versioned core request carrying the observed session identity.

    Raises:
        core.WorkspaceError: If registration or binding validation fails.
        OSError: If the local store cannot be read.
    """
    # Build the request from explicit caller or observed session identities.
    request = {
        "schema_version": 1,
        "operation": operation,
        "request_id": str(uuid.uuid4()),
        "worktree": event["cwd"],
        "host": host,
        "session_id": event["session_id"],
    }
    diagnosis = core.execute({**request, "operation": "diagnose"})
    # Stop on the exact repository diagnostic before opening a bound session.
    if not diagnosis["ok"] or diagnosis["code"] != "REGISTERED":
        raise core.WorkspaceError(diagnosis["code"])
    # Carry the verified repository identity into the lifecycle request.
    request["repo_id"] = diagnosis["repo_id"]
    with core.Store(request) as store:
        # Read the existing session binding; never infer it from the prompt.
        binding = store.binding()
        # Leave an absent assignment unbound rather than inventing identity.
        if not binding:
            raise core.WorkspaceError("BINDING_MISSING")
        # Copy only the recorded issue and generation into the request.
        request.update(binding)
    return request


def automatic_attach(event: core.JSONObject, identifier: str, host: str) -> None:
    """Apply only an explicit startup assignment or an existing participant packet.

    Args:
        event: Observed host hook input, including the actual session and tool identities.
        host: Explicit adapter identity; never taken from untrusted tool arguments.
        identifier: Validated issue ID or explicit tool-call identity.

    Raises:
        core.WorkspaceError: If registration, assignment or lifecycle setup fails.
        OSError: If assignment or issue state cannot be accessed.
    """
    # Build the request from explicit caller or observed session identities.
    base = {
        "schema_version": 1,
        "request_id": "startup:" + event["session_id"],
        "operation": "diagnose",
        "worktree": event["cwd"],
        "host": host,
        "session_id": event["session_id"],
    }
    diagnosis = core.execute(base)
    if diagnosis.get("code") != "REGISTERED":
        return
    base.update(repo_id=diagnosis["repo_id"], issue_id=identifier)
    with core.Store(base) as store:
        # Read the existing session binding; never infer it from the prompt.
        binding = store.binding()
        if binding:
            core.require(binding["issue_id"] == identifier, "BINDING_CONFLICT")
        key = core.participant_key(base)
        filename = key + ".startup.json"
        setup = store.bindings.json(filename) if store.bindings.exists(filename) else None
        if binding:
            with store.issues.child(identifier) as control, control.lock():
                issue = core.Issue(store, control, identifier)
                issue.recover()
                member: Mapping[str, object] = issue.committed_state()["participants"].get(key, {})
                # Use only the assigned packet and its current acknowledgment.
                if member.get("packet") is not None:
                    return
    # Create only the explicitly assigned startup issue before applying its packet.
    if setup:
        core.require(setup["issue_id"] == identifier, "BINDING_CONFLICT")
        result = core.execute(
            {
                **base,
                "operation": "create",
                "coordinator": setup["coordinator"],
                "issue_uuid": setup["issue_uuid"],
            }
        )
        core.require(result["ok"], result["code"])
        # A previous scope failure may follow successful creation. Read current revision
        # and retry only the still-missing assignment, never replace a later packet.
        diagnostic = core.execute(
            {**base, "operation": "diagnose", "request_id": base["request_id"] + ":diagnose"}
        )
        result = core.execute(
            {
                **base,
                "operation": "scope",
                "request_id": base["request_id"] + ":scope",
                "binding_generation": result["binding_generation"],
                "expected_revision": diagnostic["revision"],
                "target_participant": key,
                "packet": setup["packet"],
            }
        )
        core.require(result["ok"], result["code"])
    # Leave an absent assignment unbound rather than inventing identity.
    elif not binding:
        # Existing coordinator assignment in shared state is required for a new join.
        result = core.execute({**base, "operation": "attach"})
        if not result["ok"] and result["code"] not in {"BINDING_MISSING", "SCOPE_MISSING"}:
            raise core.WorkspaceError(result["code"])


def prompt(event: core.JSONObject, host: str) -> core.JSONObject:
    """Record one explicit Task line and attempt only its assigned workspace setup.

    Args:
        event: Observed host hook input, including the actual session and tool identities.
        host: Explicit adapter identity; never taken from untrusted tool arguments.

    Returns:
        An empty response, prompt denial or bounded setup context.

    Raises:
        core.WorkspaceError: If workspace registration or setup fails.
        OSError: If assignment persistence fails.
    """
    # Extract only explicit Task lines from the submitted prompt.
    lines = [line for line in event.get("prompt", "").splitlines() if line.startswith("Task:")]
    # Leave ordinary prompts unchanged when no task identity was supplied.
    if not lines:
        return {}
    # Reject multiple or malformed Task lines before recording an assignment.
    if len(lines) != 1 or not re.fullmatch(r"Task: [A-Z][A-Z0-9]{0,15}-[1-9][0-9]{0,9}", lines[0]):
        return {
            "decision": "block",
            "reason": "BINDING_CONFLICT: Supply exactly one Task: ISSUE-ID line.",
        }
    # Build the request from explicit caller or observed session identities.
    identifier = lines[0][6:]
    request = {"host": host, "session_id": event["session_id"]}
    root, _, _ = core.repository(event["cwd"])
    with (
        core.Directory.absolute(root) as worktree,
        worktree.child(".task", True) as local,
        local.child(".bindings", True) as bindings,
        bindings.lock("assignment.lock"),
    ):
        # Derive the assignment key from the explicit host/session identity.
        key = core.participant_key(request)
        # Reject implicit task switches in either the live binding or the recorded assignment.
        for name in (key + ".json", key + ".assignment.json"):
            # Validate any existing entry before reusing or replacing it.
            if bindings.exists(name) and bindings.json(name)["issue_id"] != identifier:
                return {
                    "decision": "block",
                    "reason": "BINDING_CONFLICT: Explicit rebind is required.",
                }
        bindings.put(key + ".assignment.json", {"issue_id": identifier})
    # Attempt setup using only the recorded startup assignment or existing packet.
    automatic_attach(event, identifier, host)
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": (
                "Task identity recorded. Explicitly assigned workspace setup was "
                "attempted. Read and acknowledge the permitted packet before task "
                "tools; use the lifecycle diagnostic route if setup is missing."
            ),
        }
    }


def canonical_bootstrap(
    event: core.JSONObject,
    command: object,
    host: str,
    ready: bool,
    python: str = PYTHON,
    lifecycle: Path = LIFECYCLE,
) -> bool:
    """Recognize one canonical lifecycle invocation bound to this host and session.

    Args:
        event: Observed session and worktree identities.
        command: Native shell command value to inspect without executing it.
        host: Explicit adapter identity.
        ready: Whether ordinary readiness was already established.
        python: Exact project interpreter accepted by this adapter.
        lifecycle: Exact lifecycle entry point accepted by this adapter.

    Returns:
        Whether the command satisfies the exact identity and recovery schema.
    """
    if not isinstance(command, str) or len(command.encode()) > 65536:
        return False
    try:
        # Parse shell arguments for exact interpreter, entry point and JSON matching.
        argv = shlex.split(command)
        # Require one reviewed interpreter invocation with one JSON argument.
        if len(argv) != 4 or argv[:3] != [python, str(lifecycle), "--request-json"]:
            return False
        # Reject shell spellings that could introduce expansion or wrappers.
        if command != shlex.join(argv):
            return False
        # Decode the sole lifecycle request before checking its allowed fields.
        request = core.strict_json(argv[3])
        if not isinstance(request, dict):
            return False
        # Select the operation-specific bootstrap field policy.
        operation = request.get("operation")
        # Limit unready sessions to the documented recovery operation schemas.
        if not ready and (
            operation not in BOOTSTRAP_FIELDS
            or not set(request) <= COMMON_FIELDS | BOOTSTRAP_FIELDS[operation]
        ):
            return False
        # Bind bootstrap execution to the actual hook session and host.
        if request.get("session_id") != event["session_id"] or request.get("host", "codex") != host:
            return False
        # Require the bootstrap worktree to match the observed hook worktree.
        if core.repository(request["worktree"])[0] != core.repository(event["cwd"])[0]:
            return False
        # Keep registration and diagnostics free of arbitrary issue/tool fields.
        if request["operation"] in {"register", "diagnose"}:
            return set(request) <= {
                "schema_version",
                "operation",
                "request_id",
                "worktree",
                "host",
                "session_id",
                "main_worktree",
                "startup",
            }
        # Require the bootstrap issue to match the session assignment stored in this worktree.
        with (
            core.Directory.absolute(core.repository(event["cwd"])[0]) as root,
            root.child(".task") as local,
        ):
            with local.child(".bindings") as bindings:
                assignment = bindings.json(core.participant_key(request) + ".assignment.json")
        matches_assignment: bool = request.get("issue_id") == assignment["issue_id"]
        return matches_assignment
    except (core.WorkspaceError, OSError, ValueError, KeyError, TypeError):
        return False


def native_token(value: object) -> str:
    """Validate a native identity without coercing absent or malformed values.

    Args:
        value: Native session, event, or tool identifier.

    Returns:
        The validated identity token.

    Raises:
        core.WorkspaceError: If the value is not a bounded string token.
    """
    if not isinstance(value, str):
        raise core.WorkspaceError("INVALID_REQUEST")
    return core.token(value)


def native_identity(event: core.JSONObject, host: str) -> core.JSONObject:
    """Validate native session and worktree identity without manufacturing either.

    Args:
        event: Native host hook envelope.
        host: Either claude-code or cursor, selected by the entry point.

    Returns:
        A normalized envelope retaining native tool IDs and inputs.

    Raises:
        core.WorkspaceError: If identity is absent, ambiguous, or belongs to a child.
    """
    # Children and remote/background sessions have no supported binding here.
    core.require(
        event.get("agent_id") is None
        and event.get("subagent_id") is None
        and event.get("parent_conversation_id") is None,
        "HOST_UNSUPPORTED_CHILD_IDENTITY",
    )
    core.require(event.get("is_background_agent", False) is False, "HOST_UNSUPPORTED_BACKGROUND")
    normalized = event.copy()
    if host == "cursor":
        session = native_token(event.get("conversation_id"))
        core.require(event.get("session_id", session) == session, "BINDING_CONFLICT")
        roots = event.get("workspace_roots")
        if not isinstance(roots, list) or len(roots) != 1 or not isinstance(roots[0], str):
            raise core.WorkspaceError("HOST_UNSUPPORTED_WORKSPACE")
        cwd = event.get("cwd", roots[0])
        core.require(
            isinstance(cwd, str) and core.repository(cwd)[0] == core.repository(roots[0])[0],
            "REPOSITORY_MISMATCH",
        )
    else:
        session = native_token(event.get("session_id"))
        cwd = event.get("cwd")
        core.require(isinstance(cwd, str) and Path(cwd).is_absolute(), "REPOSITORY_MISMATCH")
    normalized.update(session_id=session, cwd=cwd)
    return normalized


def native_bootstrap(event: core.JSONObject, host: str, ready: bool = False) -> bool:
    """Check native shell arguments before the shared canonical command parser.

    Args:
        event: Identity-validated native envelope.
        host: Explicit adapter identity.
        ready: Whether the core already established readiness.

    Returns:
        Whether this exact native tool call is a scoped lifecycle invocation.
    """
    # Native shells do not expose Codex's login/shell fields; accept only documented inputs.
    shell = "Bash" if host == "claude-code" else "Shell"
    if event.get("tool_name") != shell:
        return False
    args = event.get("tool_input")
    allowed = (
        {"command", "description", "timeout", "run_in_background"}
        if host == "claude-code"
        else {"command", "working_directory"}
    )
    if not isinstance(args, dict) or not set(args) <= allowed:
        return False
    if args.get("run_in_background", False) is not False:
        return False
    # A native working-directory override cannot redirect the bootstrap to another repository.
    try:
        if "working_directory" in args and (
            not isinstance(args["working_directory"], str)
            or core.repository(args["working_directory"])[0] != core.repository(event["cwd"])[0]
        ):
            return False
        return canonical_bootstrap(event, args.get("command"), host, ready)
    except (core.WorkspaceError, OSError, ValueError, KeyError, TypeError):
        return False


CLAUDE_SYNC_TOOLS = frozenset({"Read", "Write", "Edit", "Glob", "Grep", "NotebookEdit"})
CURSOR_SYNC_TOOLS = frozenset({"Read", "Write", "Edit", "Grep", "Delete"})


def native_tool(event: core.JSONObject, host: str) -> str:
    """Limit admission to tools whose synchronous completion can be correlated.

    Args:
        event: Native envelope carrying the actual tool-use identifier.
        host: Explicit adapter identity.

    Returns:
        The native tool name after validating its supported input shape.

    Raises:
        core.WorkspaceError: If the tool, child, provider, or async path is unsupported.
    """
    # Tool identity comes only from the host, never from a generated request UUID.
    native_token(event.get("tool_use_id"))
    tool = native_token(event.get("tool_name"))
    args = event.get("tool_input")
    if not isinstance(args, dict):
        raise core.WorkspaceError("INVALID_REQUEST")
    if tool.startswith(("mcp__", "MCP:")) or event.get("mcp_server") is not None:
        raise core.WorkspaceError("HOST_UNSUPPORTED_PROVIDER")
    if tool in {"Agent", "Task", "TaskOutput", "TaskStop", "SpawnAgent"}:
        raise core.WorkspaceError("HOST_UNSUPPORTED_CHILD_IDENTITY")
    # The project disables Claude auto-backgrounding; never assume an unconfigured host did so.
    if host == "claude-code" and tool == "Bash":
        core.require(
            os.environ.get("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS") == "1", "HOST_UNSUPPORTED_ASYNC"
        )
        core.require(
            set(args) <= {"command", "description", "timeout", "run_in_background"}
            and isinstance(args.get("command"), str)
            and args.get("run_in_background", False) is False,
            "HOST_UNSUPPORTED_ASYNC",
        )
        return tool
    if host == "cursor" and tool == "Shell":
        core.require(
            set(args) <= {"command", "working_directory"} and isinstance(args.get("command"), str),
            "HOST_UNSUPPORTED_ASYNC",
        )
        if "working_directory" in args:
            core.require(
                core.repository(args["working_directory"])[0] == core.repository(event["cwd"])[0],
                "REPOSITORY_MISMATCH",
            )
        return tool
    supported = CLAUDE_SYNC_TOOLS if host == "claude-code" else CURSOR_SYNC_TOOLS
    core.require(tool in supported, "HOST_UNSUPPORTED_TOOL")
    core.require(
        not any(
            key in args for key in ("run_in_background", "background", "is_background", "async")
        ),
        "HOST_UNSUPPORTED_ASYNC",
    )
    return tool


def native_pre(event: core.JSONObject, host: str) -> None:
    """Enforce readiness and record supported native tool admission.

    Args:
        event: Identity-validated native envelope.
        host: Explicit adapter identity.

    Raises:
        core.WorkspaceError: If recovery scope, readiness, or admission fails.
    """
    # Recovery accepts only the assigned exact command before ordinary readiness.
    native_token(event.get("tool_use_id"))
    if native_bootstrap(event, host):
        return
    request = request_for(event, "ready", host)
    result = core.execute(request)
    core.require(result["ok"], result["code"])
    if native_bootstrap(event, host, ready=True):
        return
    # Native permission approval remains separate from recording pending work.
    native_tool(event, host)
    result = core.execute(
        {
            **request,
            "operation": "tool-start",
            "request_id": "pre:" + event["tool_use_id"],
            "tool_id": event["tool_use_id"],
        }
    )
    core.require(result["ok"], result["code"])


def native_post(event: core.JSONObject, host: str, failed: bool) -> None:
    """Settle only a documented synchronous result for the observed native tool ID.

    Args:
        event: Identity-validated success or failure envelope.
        host: Explicit adapter identity.
        failed: Whether this is the host's failure event.

    Raises:
        core.WorkspaceError: If completion is ambiguous or core correlation fails.
    """
    # Lifecycle calls own their transaction and are never admitted as external work.
    if native_bootstrap(event, host, ready=True):
        return
    tool = native_tool(event, host)
    # Require the documented success/failure payload before treating an event as completion.
    if failed:
        field = "error" if host == "claude-code" else "error_message"
        core.require(isinstance(event.get(field), str), "INVALID_REQUEST")
        core.require(type(event.get("is_interrupt", False)) is bool, "INVALID_REQUEST")
        if host == "cursor":
            core.require(
                event.get("failure_type") in {"error", "timeout", "permission_denied"},
                "INVALID_REQUEST",
            )
    elif host == "cursor":
        core.require(isinstance(event.get("tool_output"), str), "INVALID_REQUEST")
    else:
        core.require(event.get("tool_response") is not None, "INVALID_REQUEST")
    if host == "claude-code" and tool == "Bash":
        core.require(not failed, "HOST_UNSUPPORTED_ASYNC")
        output = event["tool_response"]
        core.require(
            isinstance(output, dict)
            and output.get("interrupted") is False
            and isinstance(output.get("stdout"), str)
            and isinstance(output.get("stderr"), str)
            and not any(
                key in output
                for key in (
                    "backgroundTaskId",
                    "task_id",
                    "session_id",
                    "sessionId",
                    "background",
                    "run_in_background",
                    "is_background",
                    "async",
                )
            ),
            "HOST_UNSUPPORTED_ASYNC",
        )
    if host == "cursor" and tool == "Shell":
        # A failure/timeout notification does not prove the spawned process terminated.
        core.require(not failed, "HOST_UNSUPPORTED_ASYNC")
        output = core.strict_json(event.get("tool_output", ""))
        core.require(
            isinstance(output, dict)
            and type(output.get("exitCode")) is int
            and not any(
                key in output
                for key in (
                    "session_id",
                    "sessionId",
                    "task_id",
                    "backgroundTaskId",
                    "background",
                    "run_in_background",
                    "is_background",
                    "async",
                )
            ),
            "HOST_UNSUPPORTED_ASYNC",
        )
    # Success/failure hook events settle the supported synchronous file tools themselves.
    request = request_for(event, "tool-complete", host)
    result = core.execute(
        {
            **request,
            "request_id": "native-post:" + core.sha(core.canonical([event["tool_use_id"], failed])),
            "tool_id": event["tool_use_id"],
            "completed": True,
        }
    )
    core.require(result["ok"], result["code"])


def native_context(event: core.JSONObject, host: str) -> str:
    """Report readiness or a recovery route without granting tool permission.

    Args:
        event: Identity-validated session envelope.
        host: Explicit adapter identity.

    Returns:
        Bounded task context with the exact core diagnostic when unavailable.
    """
    try:
        result = core.execute(request_for(event, "ready", host))
        core.require(result["ok"], result["code"])
    except core.WorkspaceError as error:
        return recovery(error.code, host)
    return "Task binding checked. Read the assigned packet through the lifecycle read operation."


def recovery(code: str, host: str) -> str:
    """Describe the supported recovery route without copying task content.

    Args:
        code: Bounded diagnostic selected by the adapter or core.
        host: Explicit adapter identity required in lifecycle commands.

    Returns:
        A bounded explanation of the retained state and allowed recovery route.
    """
    return (
        f"TASK_WORKSPACE_NOT_READY: {code}. Use the exact project Python lifecycle "
        f"--request-json command with host={host} and the observed session ID to diagnose, "
        "register, resume, read and acknowledge the assigned packet. "
        "Unsupported child/provider/background work must use a separately verified route. "
        "Pending work is retained; no completion or readiness was inferred."
    )


def native_observe(event: core.JSONObject, host: str) -> None:
    """Record an advisory ending without settling operations or retiring the session.

    Args:
        event: Identity-validated native envelope.
        host: Explicit adapter identity.
    """
    try:
        request = request_for(event, "event", host)
        core.execute({**request, "event_type": "observation", "event": {"code": "UNKNOWN"}})
    except (core.WorkspaceError, OSError):
        pass  # Advisory host endings are not completion or cleanup evidence.


def run_native(
    handler: Callable[[core.JSONObject], core.JSONObject],
    failure: Callable[[str, str], core.JSONObject],
) -> int:
    """Bound native hook input and runtime while exposing parser errors as exit two.

    Args:
        handler: Native event translator for the selected host.
        failure: Native diagnostic renderer for the selected host.

    Returns:
        Zero for a translated event; two for parser, timeout, or protocol failure.
    """
    name = ""

    def timeout(*_: object) -> NoReturn:
        """Interrupt a hook before the host's longer external timeout.

        Args:
            _: Unused signal-handler arguments.

        Raises:
            core.WorkspaceError: Always, with the bounded BUSY diagnostic.
        """
        raise core.WorkspaceError("BUSY")

    # Keep output bounded and contain normal exceptions before the host's fail-open boundary.
    signal.signal(signal.SIGALRM, timeout)
    signal.setitimer(signal.ITIMER_REAL, 2.0)
    status = 0
    try:
        raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        core.require(len(raw) <= 1024 * 1024, "SIZE_LIMIT")
        event = core.strict_json(raw)
        core.require(isinstance(event, dict), "INVALID_REQUEST")
        name = native_token(event.get("hook_event_name"))
        result = handler(event)
    except Exception as error:
        code = error.code if isinstance(error, core.WorkspaceError) else "RECOVERY_REQUIRED"
        result = failure(name, code)
        status = 2
        print(recovery(code, "the selected native host"), file=sys.stderr)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    print(json.dumps(result, separators=(",", ":")))
    return status
