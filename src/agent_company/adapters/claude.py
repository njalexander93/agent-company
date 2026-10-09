"""Translate local Claude Code hooks without granting host permissions."""

from __future__ import annotations

import os
import sys

from agent_company.adapters import common
from agent_company.lifecycle import task_workspace as core

HOST = "claude-code"
PYTHON = common.PYTHON
LIFECYCLE = common.LIFECYCLE


def failure(name: str, code: str) -> core.JSONObject:
    """Render a native denial or advisory diagnostic for this Claude hook.

    Args:
        name: Native hook event name.
        code: Bounded lifecycle or adapter diagnostic.

    Returns:
        Claude's event-specific response without a permission grant.
    """
    # Compaction diagnostics cannot deliver recovery context through these events.
    if name in {"PreCompact", "PostCompact"}:
        return {}
    message = common.recovery(code, HOST)
    # Pre-tool failures must deny the pending native call.
    if name == "PreToolUse":
        return {
            "hookSpecificOutput": {
                "hookEventName": name,
                "permissionDecision": "deny",
                "permissionDecisionReason": message,
            }
        }
    # Prompt admission can block submission without changing tool permissions.
    if name == "UserPromptSubmit":
        return {"decision": "block", "reason": message}
    return {"systemMessage": message}


def handle(event: core.JSONObject) -> core.JSONObject:
    """Translate a native Claude envelope into host-bound lifecycle actions.

    Args:
        event: JSON received from a local Claude Code hook.

    Returns:
        Native JSON preserving normal permissions and reporting unsupported paths.
    """
    # Cursor imports Claude settings by default; its native adapter alone owns those events.
    if os.environ.get("CURSOR_VERSION"):
        return {}
    name = event.get("hook_event_name", "")
    # Normalize the native identity before considering any lifecycle action.
    try:
        # Never reuse a coordinator binding for a child or fabricate a missing identity.
        observed = common.native_identity(event, HOST)
        # The first issue-provider call after Task selection must read the selected ticket.
        if name == "PreToolUse":
            # An explicit Task permits its exact native Linear issue read first.
            if common.lookup_required(observed, HOST):
                # Preparation reads keep the marker and leave Claude's permission flow.
                if common.preparation_tool(observed, HOST):
                    return {}
                core.require(
                    common.native_ticket_lookup(observed, HOST) is not None, "TICKET_READ_REQUIRED"
                )
                return {}
            common.native_pre(observed, HOST)
            return {}  # No decision: Claude still applies its normal permission flow.
        # Translate completed native calls without inventing asynchronous completion.
        if name in {"PostToolUse", "PostToolUseFailure"}:
            # A pending ticket read settles only through its native tool result.
            if common.lookup_required(observed, HOST):
                # Completed preparation reads were never recorded and settle nothing.
                if common.preparation_tool(observed, HOST):
                    return {}
                # Failed provider calls release correlation for a later read.
                if name == "PostToolUseFailure":
                    common.native_ticket_failed(observed, HOST)
                    return failure(name, "PROVIDER_ERROR")
                result = common.native_ticket_lookup(observed, HOST, complete=True)
                # A nonmatching result cannot authorize workspace creation.
                if result is None:
                    raise core.WorkspaceError("TICKET_READ_REQUIRED")
                return {
                    "systemMessage": "TASK_WORKSPACE_READY: Linear ticket "
                    + result["issue_id"]
                    + " verified."
                }
            common.native_post(observed, HOST, failed=name == "PostToolUseFailure")
            return {}
        # Task selection is explicit and host-bound, never inferred from session startup.
        if name == "UserPromptSubmit":
            return common.prompt(observed, HOST)
        # Lifecycle context preserves recovery access without granting tool readiness.
        if name == "SessionStart":
            return {
                "hookSpecificOutput": {
                    "hookEventName": name,
                    "additionalContext": common.native_context(observed, HOST),
                }
            }
        # Compaction is observation-only; recovery arrives at SessionStart or pre-tool denial.
        if name in {"PreCompact", "PostCompact"}:
            common.native_observe(observed, HOST)
            return {}
        # Endings are observations, not settlement, acceptance, or cleanup triggers.
        if name in {"Stop", "SessionEnd"}:
            common.native_observe(observed, HOST)
            return {}
        # Child hooks lack the independent identity required for a join.
        if name in {"SubagentStart", "SubagentStop"}:
            return failure(name, "HOST_UNSUPPORTED_CHILD_IDENTITY")
        # Leave host permission decisions with Claude Code.
        if name == "PermissionRequest":
            return {}  # This adapter never supplies allow or changes permission settings.
        return failure(name, "HOST_UNSUPPORTED_EVENT")
    # Convert adapter and lifecycle errors into native recovery output.
    except Exception as error:
        code = error.code if isinstance(error, core.WorkspaceError) else "RECOVERY_REQUIRED"
        return failure(name, code)


def main() -> int:
    """Run the bounded native protocol, skipping Cursor-imported duplicate hooks.

    Returns:
        Zero for a handled event or two for a malformed/unreadable input envelope.
    """
    # Exit silently before parsing or mutating state when Cursor imported this entry.
    if os.environ.get("CURSOR_VERSION"):
        return 0
    return common.run_native(handle, failure)


# Execute the hook protocol only when invoked as its entry point.
if __name__ == "__main__":
    sys.exit(main())
