#!/usr/bin/env python3
"""Bounded Codex wire adapter. Host trust/coverage is a separate acceptance gate."""

from __future__ import annotations

import json
import signal
import sys
from pathlib import Path
from typing import NoReturn

from agent_company.adapters import common
from agent_company.lifecycle import task_workspace as core

ROOT = Path(__file__).resolve().parents[3]

BOOTSTRAP_FIELDS = common.BOOTSTRAP_FIELDS
COMMON_FIELDS = common.COMMON_FIELDS
LIFECYCLE = common.LIFECYCLE
PYTHON = common.PYTHON


def denial(code: str) -> core.JSONObject:
    """Construct a supported explicit PreToolUse denial without granting permissions.

    Args:
        code: Public bounded diagnostic code.

    Returns:
        The hook-specific denial object.
    """
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "TASK_WORKSPACE_NOT_READY: "
            + code
            + ". Use the exact lifecycle --request-json command for the assigned session.",
        }
    }


def async_handle(value: object) -> str | None:
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


def request_for(event: core.JSONObject, operation: str) -> core.JSONObject:
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
    return common.request_for(event, operation, "codex")


def bootstrap(event: core.JSONObject, ready: bool = False) -> bool:
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
    if (
        not isinstance(args, dict)
        or args.get("tty")
        or args.get("login") is not False
        or args.get("shell") != "/bin/sh"
        or not set(args)
        <= {
            "command",
            "cmd",
            "login",
            "shell",
            "workdir",
            "yield_time_ms",
            "max_output_tokens",
            "sandbox_permissions",
            "justification",
            "prefix_rule",
        }
    ):
        return False
    # Select and decode the documented command input.
    command = args.get("command", args.get("cmd"))
    return common.canonical_bootstrap(event, command, "codex", ready, PYTHON, LIFECYCLE)


def provider_gate(event: core.JSONObject) -> bool:
    """Permit only exact coordinator-bound provider save/get requests.

    Args:
        event: Observed host hook input, including the actual session and tool identities.

    Returns:
        Whether this provider call is allowed by the pending archive request.

    Raises:
        core.WorkspaceError: If binding, ownership or archive integrity validation fails.
        OSError: If archive state cannot be read.
    """
    tool = event.get("tool_name", "")
    if tool not in {
        "mcp__codex_apps__linear_save_document",
        "mcp__codex_apps__linear_get_document",
    }:
        return False
    # Build the request from explicit caller or observed session identities.
    request = request_for(event, "ready")
    with (
        core.Store(request) as store,
        store.issues.child(request["issue_id"]) as control,
        control.lock(),
    ):
        issue = core.Issue(store, control, request["issue_id"])
        issue.recover()
        state = issue.committed_state()
        core.authorize(state, request, coordinator=True, maintenance=True)
        if state["storage"] != "cleaned":
            issue.files()
        # Select and decode the documented command input.
        args = event.get("tool_input", {})
        # Permit reads only of document IDs already recorded for this issue archive.
        if tool.endswith("linear_get_document"):
            identifiers = {x["id"] for x in state.get("provider_saves", {}).values() if x.get("id")}
            if state.get("archive"):
                identifiers.add(state["archive"]["root"]["id"])
                identifiers.update(x["id"] for x in state["archive"]["parts"])
            return set(args) == {"id"} and args["id"] in identifiers
        # A cleaned issue may read its archive but cannot start another save.
        if state["storage"] == "cleaned":
            return False
        export = control.json("export-" + state["export"]["snapshot"] + ".json")
        documents = export["parts"] + (
            [state["index_request"]] if state.get("index_request") else []
        )
        # Reject provider writes that differ from the immutable export request.
        if args not in documents:
            return False
        # Identify the exact content whose save uncertainty must be recorded.
        digest = core.sha(args["content"].encode())
    # Record save uncertainty before allowing the external provider write.
    result = core.execute({**request, "operation": "archive-save-start", "content_digest": digest})
    allowed: bool = result["ok"]
    return allowed


def automatic_attach(event: core.JSONObject, identifier: str) -> None:
    """Apply only an explicit startup assignment or an existing participant packet.

    Args:
        event: Observed host hook input, including the actual session and tool identities.
        identifier: Validated issue ID or explicit tool-call identity.

    Raises:
        core.WorkspaceError: If registration, assignment or lifecycle setup fails.
        OSError: If assignment or issue state cannot be accessed.
    """
    common.automatic_attach(event, identifier, "codex")


def prompt(event: core.JSONObject) -> core.JSONObject:
    """Record one explicit Task line and attempt only its assigned workspace setup.

    Args:
        event: Observed host hook input, including the actual session and tool identities.

    Returns:
        An empty response, prompt denial or bounded setup context.

    Raises:
        core.WorkspaceError: If workspace registration or setup fails.
        OSError: If assignment persistence fails.
    """
    return common.prompt(event, "codex")


def handle(event: core.JSONObject) -> core.JSONObject:
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
    # Record explicit task identity before attempting assigned setup.
    if name == "UserPromptSubmit":
        return prompt(event)
    # Leave permission decisions to the host.
    if name == "PermissionRequest":
        return {}  # Never grant host permissions.
    # Require readiness or an exact recovery route before admitting tool work.
    if name == "PreToolUse":
        # Allow only the exact scoped bootstrap route before ordinary readiness checks.
        if bootstrap(event):
            return {}
        try:
            # Allow the exact pending archive provider call only after gate validation.
            if provider_gate(event):
                return {}
        except core.WorkspaceError:
            pass
        tool = event.get("tool_name", "")
        # Deny covered child creation until host child identity can be verified.
        if any(
            s in tool
            for s in (
                "spawn_agent",
                "create_thread",
                "fork_thread",
                "send_message_to_thread",
                "followup_task",
            )
        ):
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
            return {}
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
        result = core.execute(
            {
                **request,
                "operation": "tool-start",
                "request_id": "pre:" + event["tool_use_id"],
                "tool_id": event["tool_use_id"],
                "poll_handle": poll_handle,
            }
        )
        return {} if result["ok"] else denial(result["code"])
    # Settle work only from typed completion facts and validated handle correlation.
    if name == "PostToolUse":
        # Build the request from explicit caller or observed session identities.
        request = request_for(event, "tool-complete")
        response = event.get("tool_response", {})
        # Decode serialized tool responses; malformed shapes supply no completion evidence.
        if isinstance(response, str):
            try:
                response = json.loads(response)
            except ValueError:
                response = {}
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
                core.require(
                    input_handle is not None and (handle is None or handle == input_handle),
                    "ASYNC_HANDLE_CONFLICT",
                )
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
        result = core.execute(
            {
                **request,
                "request_id": "post:" + core.sha(core.canonical(observation)),
                "tool_id": event["tool_use_id"],
                "poll": polling,
                "completed": completed,
                "async_handle": handle,
            }
        )
        # Preserve the precise core failure in the host response.
        if not result["ok"]:
            return {"systemMessage": "TASK_WORKSPACE_NOT_READY: " + result["code"]}
        return {}
    # Report the unsupported child-identity boundary without granting readiness.
    if name == "SubagentStart":
        return {
            "systemMessage": (
                "HOST_UNSUPPORTED: Native child-to-tool identity is not verified. "
                "No child readiness is established."
            )
        }
    # Allow lifecycle recovery while preserving tool-level readiness enforcement.
    if name in {"SessionStart", "PreCompact", "PostCompact"}:
        try:
            result = core.execute(request_for(event, "ready"))
        except core.WorkspaceError as error:
            result = {"ok": False, "code": error.code}
        # Leave successful compaction hooks unobtrusive after checking readiness.
        if result["ok"] and name in {"PreCompact", "PostCompact"}:
            return {}
        # Expose successful binding context without copying task-note content.
        if result["ok"]:
            return {
                "hookSpecificOutput": {
                    "hookEventName": name,
                    "additionalContext": (
                        "Task binding checked. Retrieve the permitted packet through the "
                        "lifecycle read operation."
                    ),
                }
            }
        return {
            "hookSpecificOutput": {
                "hookEventName": name,
                "additionalContext": "TASK_WORKSPACE_NOT_READY: "
                + result["code"]
                + ". Ordinary task tools remain denied. Use only the assigned lifecycle "
                "diagnostic/bootstrap routes to register, resume, read and acknowledge "
                "the permitted packet, or repair the reported condition. Readiness was not "
                "granted.",
            }
        }
    # Record only bounded optional facts for advisory lifecycle endings.
    if name in {"Stop", "Interrupt", "SessionEnd", "SubagentStop"}:
        try:
            # Build the request from explicit caller or observed session identities.
            request = request_for(event, "event")
            core.execute(
                {
                    **request,
                    "event_type": "observation",
                    "event": {"code": "INTERRUPTED" if name == "Interrupt" else "UNKNOWN"},
                }
            )
        except (core.WorkspaceError, OSError):
            pass  # Optional observations cannot establish completion or retirement.
    return {}


def main() -> int:
    """Run one hook with bounded input, execution time and event-specific error output.

    Returns:
        Zero after emitting a supported JSON response; failures become bounded diagnostics.
    """
    name = None

    def timeout(*_: object) -> NoReturn:
        """Interrupt a hook that exceeds its process-local execution deadline.

        Args:
            _: Signal handler arguments; no payload is inspected.

        Raises:
            core.WorkspaceError: Always, with the bounded BUSY diagnostic.
        """
        raise core.WorkspaceError("BUSY")

    # Bound the entire hook, including input reads and filesystem checks, to two seconds.
    signal.signal(signal.SIGALRM, timeout)
    signal.setitimer(signal.ITIMER_REAL, 2.0)
    try:
        # Reject oversized input before parsing or dispatching the host event.
        raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        core.require(len(raw) <= 1024 * 1024, "SIZE_LIMIT")
        event = core.strict_json(raw)
        name = event.get("hook_event_name")
        result = handle(event)
    # Deny pre-tool failures; report other failures using the event-specific response shape.
    except Exception as error:
        code = error.code if isinstance(error, core.WorkspaceError) else "RECOVERY_REQUIRED"
        result = (
            denial(code)
            if name == "PreToolUse"
            else (
                {"decision": "block", "reason": "TASK_WORKSPACE_NOT_READY: " + code}
                if name == "UserPromptSubmit"
                else {"systemMessage": "TASK_WORKSPACE_NOT_READY: " + code}
            )
        )
    finally:
        # Cancel the alarm even when decoding or hook handling fails.
        signal.setitimer(signal.ITIMER_REAL, 0)
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
