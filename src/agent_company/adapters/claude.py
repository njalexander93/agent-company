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
    message = common.recovery(code, HOST)
    if name == "PreToolUse":
        return {
            "hookSpecificOutput": {
                "hookEventName": name,
                "permissionDecision": "deny",
                "permissionDecisionReason": message,
            }
        }
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
    try:
        # Never reuse a coordinator binding for a child or fabricate a missing identity.
        observed = common.native_identity(event, HOST)
        if name == "PreToolUse":
            common.native_pre(observed, HOST)
            return {}  # No decision: Claude still applies its normal permission flow.
        # Translate completed native calls without inventing asynchronous completion.
        if name in {"PostToolUse", "PostToolUseFailure"}:
            common.native_post(observed, HOST, failed=name == "PostToolUseFailure")
            return {}
        # Task selection is explicit and host-bound, never inferred from session startup.
        if name == "UserPromptSubmit":
            return common.prompt(observed, HOST)
        # Lifecycle context preserves recovery access without granting tool readiness.
        if name in {"SessionStart", "PostCompact"}:
            return {
                "hookSpecificOutput": {
                    "hookEventName": name,
                    "additionalContext": common.native_context(observed, HOST),
                }
            }
        if name == "PreCompact":
            return {"systemMessage": common.native_context(observed, HOST)}
        # Endings are observations, not settlement, acceptance, or cleanup triggers.
        if name in {"Stop", "SessionEnd"}:
            common.native_observe(observed, HOST)
            return {}
        if name in {"SubagentStart", "SubagentStop"}:
            return failure(name, "HOST_UNSUPPORTED_CHILD_IDENTITY")
        if name == "PermissionRequest":
            return {}  # This adapter never supplies allow or changes permission settings.
        return failure(name, "HOST_UNSUPPORTED_EVENT")
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


if __name__ == "__main__":
    sys.exit(main())
