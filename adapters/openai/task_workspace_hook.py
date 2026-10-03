#!/usr/bin/env python3
"""Bounded Codex wire adapter. Host trust/coverage is a separate acceptance gate."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import shlex
import signal
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from operations.memory import task_workspace as core

BOOTSTRAP_FIELDS = {
    "diagnose": set(), "register": {"main_worktree", "startup"},
    "bind": set(), "adopt": {"coordinator", "inventory", "owners", "evidence"},
    "resume": set(), "restore": {"observations"},
    "read": set(), "acknowledge": {"packet_digest"},
    "archive-index": {"observations"}, "archive-verify": {"observations"},
    "archive-observe-save": {"document_id", "content_digest"},
    "cleanup-plan": set(), "cleanup-commit": {"observations", "cleanup_challenge"},
    "reopen": {"evidence"},
    "archive-prepare": {"seal"}, "event-rollover": set(),
    "reconcile-files": {"inventory", "evidence"},
    "rebind": {"new_issue_id", "new_binding_generation", "evidence"},
}
COMMON_FIELDS = {"schema_version", "operation", "request_id", "worktree", "host", "session_id",
                 "repo_id", "issue_id", "issue_uuid", "binding_generation", "expected_revision"}
LIFECYCLE = ROOT / "operations/memory/task_workspace.py"
PYTHON = "/usr/bin/python3"


def denial(code):
    """Construct a supported explicit PreToolUse denial without granting permissions.

    Args:
        code: Public bounded diagnostic code.

    Returns:
        The hook-specific denial object.
    """
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": "TASK_WORKSPACE_NOT_READY: " + code +
            ". Use the exact lifecycle --request-json command for the assigned session."}}


def async_handle(value):
    """Normalize optional host handles without disguising malformed metadata.

    Args:
        value: An absent/null handle, or a nonempty string or integer identifier.

    Returns:
        The validated string handle, or None only for absent/null metadata.

    Raises:
        core.WorkspaceError: If an explicitly supplied handle has an invalid type or token.
    """
    # Preserve absence, but do not equate malformed supplied metadata with absence.
    if value is None:
        return None
    # Booleans are not integer process identities despite Python's subtype relation.
    core.require(type(value) in {str, int}, "ASYNC_HANDLE_CONFLICT")
    return core.token(str(value))


def request_for(event, operation):
    """Resolve an actual hook session to its registered repository and binding.

    Args:
        event: Observed host hook input, including the actual session and tool identities.
        operation: Core operation requested for the observed session.

    Returns:
        A versioned core request carrying the observed session identity.

    Raises:
        core.WorkspaceError: If registration or binding validation fails.
        OSError: If the local store cannot be read.
    """
    # Build the request from explicit caller or observed session identities.
    request = {"schema_version": 1, "operation": operation, "request_id": str(uuid.uuid4()),
               "worktree": event["cwd"], "host": "codex", "session_id": event["session_id"]}
    diagnosis = core.execute({**request, "operation": "diagnose"})
    # Stop on the exact repository diagnostic before opening a bound session.
    if not diagnosis["ok"] or diagnosis["code"] != "REGISTERED":
        raise core.WorkspaceError(diagnosis["code"])
    # Carry the verified repository identity into the lifecycle request.
    request["repo_id"] = diagnosis["repo_id"]
    # Keep filesystem resources scoped to this read or publication phase.
    with core.Store(request) as store:
        # Read the existing session binding; never infer it from the prompt.
        binding = store.binding()
        # Leave an absent assignment unbound rather than inventing identity.
        if not binding:
            raise core.WorkspaceError("BINDING_MISSING")
        # Copy only the recorded issue and generation into the request.
        request.update(binding)
    return request


def bootstrap(event, ready=False):
    """Recognize only the exact scoped lifecycle command and canonical shell spelling.

    Args:
        event: Observed host hook input, including the actual session and tool identities.
        ready: Whether the caller already proved ordinary readiness.

    Returns:
        Whether the tool request is an allowed lifecycle bootstrap.
    """
    # Restrict bootstrap handling to observable shell tool calls.
    if event.get("tool_name") not in {"Bash", "exec_command"}:
        return False
    # Select and decode the documented command input.
    args = event.get("tool_input", {})
    # Check the supported input shape before interpreting its fields.
    if (not isinstance(args, dict) or args.get("tty") or args.get("login") is not False
            or args.get("shell") != "/bin/sh" or not set(args) <= {
                "command", "cmd", "login", "shell", "workdir", "yield_time_ms", "max_output_tokens",
                "sandbox_permissions", "justification", "prefix_rule"}):
        return False
    # Select and decode the documented command input.
    command = args.get("command", args.get("cmd"))
    # Check the supported input shape before interpreting its fields.
    if not isinstance(command, str) or len(command.encode()) > 65536:
        return False
    # Attempt this phase while retaining its error and cleanup paths.
    try:
        # Parse shell arguments for exact interpreter, entry point and JSON matching.
        argv = shlex.split(command)
        # Require one reviewed interpreter invocation with one JSON argument.
        if len(argv) != 4 or argv[:3] != [PYTHON, str(LIFECYCLE), "--request-json"]:
            return False
        # Reject shell spellings that could introduce expansion or wrappers.
        if command != shlex.join(argv):
            return False
        # Build the request from explicit caller or observed session identities.
        request = core.strict_json(argv[3])
        # Check the supported input shape before interpreting its fields.
        if not isinstance(request, dict):
            return False
        # Select the operation-specific bootstrap field policy.
        operation = request.get("operation")
        # Limit unready sessions to the documented recovery operation schemas.
        if not ready and (operation not in BOOTSTRAP_FIELDS or
                          not set(request) <= COMMON_FIELDS | BOOTSTRAP_FIELDS[operation]):
            return False
        # Bind bootstrap execution to the actual hook session and host.
        if request.get("session_id") != event["session_id"] or request.get("host", "codex") != "codex":
            return False
        # Require the bootstrap worktree to match the observed hook worktree.
        if core.repository(request["worktree"])[0] != core.repository(event["cwd"])[0]:
            return False
        # Keep registration and diagnostics free of arbitrary issue/tool fields.
        if request["operation"] in {"register", "diagnose"}:
            return set(request) <= {"schema_version", "operation", "request_id", "worktree", "host",
                                    "session_id", "main_worktree", "startup"}
        # Keep filesystem resources scoped to this read or publication phase.
        with core.Directory.absolute(core.repository(event["cwd"])[0]) as root, root.child(".task") as local:
            # Keep filesystem resources scoped to this read or publication phase.
            with local.child(".bindings") as bindings:
                assignment = bindings.json(core.participant_key(request) + ".assignment.json")
        return request.get("issue_id") == assignment["issue_id"]
    except (core.WorkspaceError, OSError, ValueError, KeyError, TypeError):
        # Preserve or translate this failure according to the enclosing contract.
        return False



def provider_gate(event):
    """Permit only exact coordinator-bound provider save/get requests.

    Args:
        event: Observed host hook input, including the actual session and tool identities.

    Returns:
        Whether this provider call is allowed by the pending archive request.

    Raises:
        core.WorkspaceError: If binding, ownership or archive integrity validation fails.
        OSError: If archive state cannot be read.
    """
    # Prepare sequence and event values without dropping prior history.
    tool = event.get("tool_name", "")
    # Choose the supported branch; reject incompatible state or arguments.
    if tool not in {"mcp__codex_apps__linear_save_document", "mcp__codex_apps__linear_get_document"}:
        return False
    # Build the request from explicit caller or observed session identities.
    request = request_for(event, "ready")
    # Hold the required locks while validating or publishing shared state.
    with core.Store(request) as store, store.issues.child(request["issue_id"]) as control, control.lock():
        # Prepare the provider_gate values for the next contract boundary.
        issue = core.Issue(store, control, request["issue_id"])
        issue.recover()
        state = issue.state
        core.authorize(state, request, coordinator=True, maintenance=True)
        # Handle the case state['storage'] != 'cleaned'.
        if state["storage"] != "cleaned":
            issue.files()
        # Select and decode the documented command input.
        args = event.get("tool_input", {})
        # Handle the case tool.endswith('linear_get_document').
        if tool.endswith("linear_get_document"):
            # Prepare the provider_gate values for the next contract boundary.
            identifiers = {x["id"] for x in state.get("provider_saves", {}).values() if x.get("id")}
            # Check the recorded archive state before allowing the next archive phase.
            if state.get("archive"):
                # Stage the verified lifecycle changes in the issue state.
                identifiers.add(state["archive"]["root"]["id"])
                identifiers.update(x["id"] for x in state["archive"]["parts"])
            return set(args) == {"id"} and args["id"] in identifiers
        # Handle the case state['storage'] == 'cleaned'.
        if state["storage"] == "cleaned":
            return False
        # Read provider_gate inputs through the scoped file interface.
        export = control.json("export-" + state["export"]["snapshot"] + ".json")
        # Stage the verified lifecycle changes in the issue state.
        documents = export["parts"] + ([state["index_request"]] if state.get("index_request") else [])
        # Reject provider writes that differ from the immutable export request.
        if args not in documents:
            return False
        # Identify the exact content whose save uncertainty must be recorded.
        digest = core.sha(args["content"].encode())
    # Invoke the core boundary and retain its structured result.
    result = core.execute({**request, "operation": "archive-save-start", "content_digest": digest})
    return result["ok"]



def automatic_attach(event, identifier):
    """Apply only an explicit startup assignment or an existing participant packet.

    Args:
        event: Observed host hook input, including the actual session and tool identities.
        identifier: Validated issue ID or explicit tool-call identity.

    Raises:
        core.WorkspaceError: If registration, assignment or lifecycle setup fails.
        OSError: If assignment or issue state cannot be accessed.
    """
    # Build the request from explicit caller or observed session identities.
    base = {"schema_version": 1, "request_id": "startup:" + event["session_id"],
            "operation": "diagnose", "worktree": event["cwd"], "host": "codex", "session_id": event["session_id"]}
    diagnosis = core.execute(base)
    # Handle the case diagnosis.get('code') != 'REGISTERED'.
    if diagnosis.get("code") != "REGISTERED":
        return
    # Prepare the automatic_attach values for the next contract boundary.
    base.update(repo_id=diagnosis["repo_id"], issue_id=identifier)
    # Keep filesystem resources scoped to this read or publication phase.
    with core.Store(base) as store:
        # Read the existing session binding; never infer it from the prompt.
        binding = store.binding()
        # Keep binding and generation checks ahead of the requested action.
        if binding:
            core.require(binding["issue_id"] == identifier, "BINDING_CONFLICT")
        # Prepare the automatic_attach values for the next contract boundary.
        key = core.participant_key(base)
        filename = key + ".startup.json"
        # Read automatic_attach inputs through the scoped file interface.
        setup = store.bindings.json(filename) if store.bindings.exists(filename) else None
        # Keep binding and generation checks ahead of the requested action.
        if binding:
            # Hold the required locks while validating or publishing shared state.
            with store.issues.child(identifier) as control, control.lock():
                # Stage the verified lifecycle changes in the issue state.
                issue = core.Issue(store, control, identifier)
                issue.recover()
                member = issue.state["participants"].get(key, {})
                # Use only the assigned packet and its current acknowledgment.
                if member.get("packet") is not None:
                    return
    # Handle the case setup.
    if setup:
        # Enforce BINDING_CONFLICT boundaries.
        core.require(setup["issue_id"] == identifier, "BINDING_CONFLICT")
        # Invoke the core boundary and retain its structured result.
        result = core.execute({**base, "operation": "create", "coordinator": setup["coordinator"],
                               "issue_uuid": setup["issue_uuid"]})
        # Enforce the explicit identity and input contract.
        core.require(result["ok"], result["code"])
        # A previous scope failure may follow successful creation. Read current revision
        # and retry only the still-missing assignment, never replace a later packet.
        diagnostic = core.execute({**base, "operation": "diagnose", "request_id": base["request_id"] + ":diagnose"})
        result = core.execute({**base, "operation": "scope", "request_id": base["request_id"] + ":scope",
                               "binding_generation": result["binding_generation"],
                               "expected_revision": diagnostic["revision"], "target_participant": key,
                               "packet": setup["packet"]})
        # Enforce the explicit identity and input contract.
        core.require(result["ok"], result["code"])
    # Leave an absent assignment unbound rather than inventing identity.
    elif not binding:
        # Existing coordinator assignment in shared state is required for a new join.
        result = core.execute({**base, "operation": "attach"})
        # Separate successful lifecycle results from recovery diagnostics.
        if not result["ok"] and result["code"] not in {"BINDING_MISSING", "SCOPE_MISSING"}:
            raise core.WorkspaceError(result["code"])


def prompt(event):
    """Record one explicit Task line and attempt only its assigned workspace setup.

    Args:
        event: Observed host hook input, including the actual session and tool identities.

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
        return {"decision": "block", "reason": "BINDING_CONFLICT: Supply exactly one Task: ISSUE-ID line."}
    # Build the request from explicit caller or observed session identities.
    identifier = lines[0][6:]
    request = {"host": "codex", "session_id": event["session_id"]}
    root, _, _ = core.repository(event["cwd"])
    # Hold the required locks while validating or publishing shared state.
    with core.Directory.absolute(root) as worktree, worktree.child(".task", True) as local, \
            local.child(".bindings", True) as bindings, bindings.lock("assignment.lock"):
        # Derive the assignment key from the explicit host/session identity.
        key = core.participant_key(request)
        # Process each name under the same validation boundary.
        for name in (key + ".json", key + ".assignment.json"):
            # Validate any existing entry before reusing or replacing it.
            if bindings.exists(name) and bindings.json(name)["issue_id"] != identifier:
                return {"decision": "block", "reason": "BINDING_CONFLICT: Explicit rebind is required."}
        # Publish validated content and flush the required filesystem boundary.
        bindings.put(key + ".assignment.json", {"issue_id": identifier})
    # Attempt setup using only the recorded startup assignment or existing packet.
    automatic_attach(event, identifier)
    return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext":
            "Task identity recorded. Explicitly assigned workspace setup was attempted. Read and acknowledge the permitted packet before task tools; use the lifecycle diagnostic route if setup is missing."}}


def handle(event):
    """Translate one observed host lifecycle event into scoped core actions.

    Args:
        event: Observed host hook input, including the actual session and tool identities.

    Returns:
        The event-specific hook response without inferred readiness or completion.

    Raises:
        core.WorkspaceError: If required binding or store validation fails.
        OSError: If the registered store cannot be accessed.
    """
    # Select the event-specific host response and lifecycle action.
    name = event.get("hook_event_name")
    # Handle the UserPromptSubmit hook event.
    if name == "UserPromptSubmit":
        return prompt(event)
    # Handle the PermissionRequest hook event.
    if name == "PermissionRequest":
        return {}  # Never grant host permissions.
    # Handle the PreToolUse hook event.
    if name == "PreToolUse":
        # Allow only the exact scoped bootstrap route before ordinary readiness checks.
        if bootstrap(event):
            return {}
        # Attempt this phase while retaining its error and cleanup paths.
        try:
            # Allow the exact pending archive provider call only after gate validation.
            if provider_gate(event):
                return {}
        except core.WorkspaceError:
            # Preserve or translate this failure according to the enclosing contract.
            pass
        # Prepare sequence and event values without dropping prior history.
        tool = event.get("tool_name", "")
        # Deny covered child creation until host child identity can be verified.
        if any(s in tool for s in ("spawn_agent", "create_thread", "fork_thread", "send_message_to_thread", "followup_task")):
            return denial("HOST_UNSUPPORTED_CHILD_IDENTITY")
        # Keep identity and permission clarification available to unready sessions.
        if tool in {"request_user_input", "request_user_input_async"}:
            return {}
        # Build the request from explicit caller or observed session identities.
        request = request_for(event, "ready")
        result = core.execute(request)
        # Preserve the precise core failure in the host response.
        if not result["ok"]:
            return denial(result["code"])
        # Avoid counting the lifecycle transaction itself as pending external work.
        if bootstrap(event, ready=True):
            return {}  # Local lifecycle owns its transaction; don't count itself as pending external work.
        # Bind an observed polling transport to its unique original operation.
        polling = tool == "write_stdin"
        poll_handle = None
        if polling:
            try:
                poll_handle = async_handle(event.get("tool_input", {}).get("session_id"))
                core.require(poll_handle is not None, "ASYNC_HANDLE_CONFLICT")
            except core.WorkspaceError as error:
                return denial(error.code)
        # Reserve and record the transport separately when the host emits its pre-hook.
        result = core.execute({**request, "operation": "tool-start", "request_id": "pre:" + event["tool_use_id"],
                               "tool_id": event["tool_use_id"], "poll_handle": poll_handle})
        return {} if result["ok"] else denial(result["code"])
    # Handle the PostToolUse hook event.
    if name == "PostToolUse":
        # Build the request from explicit caller or observed session identities.
        request = request_for(event, "tool-complete")
        response = event.get("tool_response", {})
        # Check the supported input shape before interpreting its fields.
        if isinstance(response, str):
            # Attempt this phase while retaining its error and cleanup paths.
            try:
                response = json.loads(response)
            except ValueError:
                # Preserve or translate this failure according to the enclosing contract.
                response = {}
        # Check the supported input shape before interpreting its fields.
        if not isinstance(response, dict):
            response = {}
        # Unknown response shapes keep the operation pending; never infer completion.
        tool_name = event.get("tool_name", "")
        polling = tool_name == "write_stdin"
        # Validate supplied handle types before any completion or correlation decision.
        try:
            handle = async_handle(response.get("session_id"))
            if polling:
                input_handle = async_handle(event.get("tool_input", {}).get("session_id"))
                core.require(input_handle is not None and (handle is None or handle == input_handle),
                             "ASYNC_HANDLE_CONFLICT")
                handle = input_handle
        except core.WorkspaceError as error:
            return {"systemMessage": "TASK_WORKSPACE_NOT_READY: " + error.code}
        # Normalize response metadata without inferring completion from missing values.
        exit_code = response.get("exit_code")
        completed = type(exit_code) is int
        # Accept typed isError completion only outside asynchronous shell transport.
        if tool_name not in {"Bash", "exec_command", "write_stdin"}:
            completed = completed or type(response.get("isError")) is bool
        # Treat an explicit handle-free shell error as a finished failed call.
        elif response.get("isError") is True and handle is None:
            completed = True
        # Retain work when the response proves neither completion nor a valid handle.
        if not completed and handle is None:
            return {}
        # Key idempotency by completion facts so a later final response is not an old retry.
        observation = [event["tool_use_id"], completed, handle, polling]
        result = core.execute({**request, "request_id": "post:" + core.sha(core.canonical(observation)),
                               "tool_id": event["tool_use_id"], "poll": polling,
                               "completed": completed, "async_handle": handle})
        # Preserve the precise core failure in the host response.
        if not result["ok"]:
            return {"systemMessage": "TASK_WORKSPACE_NOT_READY: " + result["code"]}
        return {}
    # Handle the SubagentStart hook event.
    if name == "SubagentStart":
        return {"systemMessage": "HOST_UNSUPPORTED: Native child-to-tool identity is not verified. No child readiness is established."}
    # Allow lifecycle recovery while preserving tool-level readiness enforcement.
    if name in {"SessionStart", "PreCompact", "PostCompact"}:
        # Attempt this phase while retaining its error and cleanup paths.
        try:
            result = core.execute(request_for(event, "ready"))
        except core.WorkspaceError as error:
            # Preserve or translate this failure according to the enclosing contract.
            result = {"ok": False, "code": error.code}
        # Leave successful compaction hooks unobtrusive after checking readiness.
        if result["ok"] and name in {"PreCompact", "PostCompact"}:
            return {}
        # Expose successful binding context without copying task-note content.
        if result["ok"]:
            return {"hookSpecificOutput": {"hookEventName": name, "additionalContext":
                    "Task binding checked. Retrieve the permitted packet through the lifecycle read operation."}}
        return {"hookSpecificOutput": {"hookEventName": name, "additionalContext":
                "TASK_WORKSPACE_NOT_READY: " + result["code"] +
                ". Ordinary task tools remain denied. Use only the assigned lifecycle "
                "diagnostic/bootstrap routes to register, resume, read and acknowledge "
                "the permitted packet, or repair the reported condition. Readiness was not granted."}}
    # Record only bounded optional facts for advisory lifecycle endings.
    if name in {"Stop", "Interrupt", "SessionEnd", "SubagentStop"}:
        # Attempt this phase while retaining its error and cleanup paths.
        try:
            # Build the request from explicit caller or observed session identities.
            request = request_for(event, "event")
            core.execute({**request, "event_type": "observation", "event": {
                "code": "INTERRUPTED" if name == "Interrupt" else "UNKNOWN"}})
        except (core.WorkspaceError, OSError):
            # Preserve or translate this failure according to the enclosing contract.
            pass  # Optional observations cannot establish completion or retirement.
    return {}


def main():
    """Run one hook with bounded input, execution time and event-specific error output.

    Returns:
        Zero after emitting a supported JSON response; failures become bounded diagnostics.
    """
    # Prepare the main values for the next contract boundary.
    name = None
    def timeout(*_):
        """Interrupt a hook that exceeds its process-local execution deadline.

        Args:
            _: Signal handler arguments; no payload is inspected.

        Raises:
            core.WorkspaceError: Always, with the bounded BUSY diagnostic.
        """
        # Stop with the original failure rather than continue with incomplete state.
        raise core.WorkspaceError("BUSY")
    # Prepare the main values for the next contract boundary.
    signal.signal(signal.SIGALRM, timeout)
    signal.setitimer(signal.ITIMER_REAL, 2.0)
    # Attempt this phase while retaining its error and cleanup paths.
    try:
        # Read main inputs through the scoped file interface.
        raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        # Enforce SIZE_LIMIT boundaries.
        core.require(len(raw) <= 1024 * 1024, "SIZE_LIMIT")
        # Prepare sequence and event values without dropping prior history.
        event = core.strict_json(raw)
        name = event.get("hook_event_name")
        result = handle(event)
    except Exception as error:
        # Preserve or translate this failure according to the enclosing contract.
        code = error.code if isinstance(error, core.WorkspaceError) else "RECOVERY_REQUIRED"
        result = denial(code) if name == "PreToolUse" else (
            {"decision": "block", "reason": "TASK_WORKSPACE_NOT_READY: " + code} if name == "UserPromptSubmit" else
            {"systemMessage": "TASK_WORKSPACE_NOT_READY: " + code})
    finally:
        # Release temporary resources even after a partial failure.
        signal.setitimer(signal.ITIMER_REAL, 0)
    # Prepare the main values for the next contract boundary.
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
